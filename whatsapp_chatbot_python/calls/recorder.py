"""Recording ownership: capture --> export --> explicit transfer to the caller."""
from __future__ import annotations

import asyncio
import logging
import sys
from array import array
from concurrent.futures import Future
from contextlib import ExitStack
from enum import Enum
from pathlib import Path
from queue import Full, Queue
from tempfile import NamedTemporaryFile, TemporaryFile
from threading import Event, Lock, Thread
from typing import BinaryIO, Callable, TypeVar

from .models import (
    PcmChunk,
    RecordingOptions,
    PCM,
    PCM16_MAX_VALUE,
    PCM16_MIN_VALUE,
    PCM16_SAMPLE_BYTES,
)

MP3_ENCODER = "libmp3lame"

MP3_CONTAINER = "mp3"

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Track(Enum):
    CALLER = "caller"
    BOT = "bot"


class PcmCapture:
    """One writer owns two temporary tracks. Producers never block on disk."""
    def __init__(self, options: RecordingOptions) -> None:
        self._options = options
        self._files = ExitStack()
        self._tracks: dict[Track, BinaryIO] = {}
        self._queue: Queue[tuple[Track, PcmChunk] | None] = Queue(options.max_pending_chunks)
        self._thread: Thread | None = None
        self._error: Exception | None = None
        self._closed = False
        self.has_samples = False

    def start(self) -> None:
        if self._thread is not None:
            return

        for track in Track:
            self._tracks[track] = self._files.enter_context(
                TemporaryFile(dir=self._options.directory))

        self._thread = Thread(target=self._write_loop, name="call-pcm-writer", daemon=True)

        self._thread.start()

    def write(self, track: Track, chunk: PcmChunk) -> None:
        if self._closed:
            raise RuntimeError("PCM capture is closed")

        if self._error is not None:
            raise RuntimeError("PCM capture failed") from self._error

        if not chunk.data:
            return

        self.start()

        try:
            self._queue.put_nowait((track, chunk))
        except Full as error:
            raise BufferError("Recording writer cannot keep up") from error

        self.has_samples = True

    def _write_loop(self) -> None:
        try:
            while (entry := self._queue.get()) is not None:
                track, chunk = entry
                output = self._tracks[track]
                output.seek(chunk.position * PCM16_SAMPLE_BYTES)
                output.write(chunk.data)
        except Exception as error:
            self._error = error

    def seal(self) -> dict[Track, BinaryIO]:
        """Blocking: join the writer, then return tracks borrowed until close."""
        self._closed = True

        if self._thread is None:
            return {}

        self._stop_writer()

        if self._error is not None:
            raise RuntimeError("PCM writer failed") from self._error

        for file in self._tracks.values():
            file.seek(0)

        return self._tracks

    def _stop_writer(self) -> None:
        while self._thread.is_alive():
            try:
                self._queue.put(None, timeout=self._options.writer_poll_seconds)
                break
            except Full:
                continue

        self._thread.join()

    def close(self) -> None:
        try:
            if not self._closed:
                self.seal()
        finally:
            self._files.close()


class Mp3Encoder:
    """Synchronous exporter. Input tracks stay owned by PcmCapture."""
    def __init__(self, options: RecordingOptions) -> None:
        self._options = options

    def encode(self, tracks: dict[Track, BinaryIO], path: Path) -> None:
        import av

        with av.open(str(path), mode="w", format=MP3_CONTAINER) as container:
            stream = container.add_stream(MP3_ENCODER, rate=PCM.rate)
            stream.layout = PCM.layout
            stream.bit_rate = self._options.bitrate_bps
            position = 0

            while data := self._mix_block(tracks):
                frame = self._frame(data, position)

                for packet in stream.encode(frame):
                    container.mux(packet)

                position += frame.samples
            for packet in stream.encode(None):
                container.mux(packet)

    def _mix_block(self, tracks: dict[Track, BinaryIO]) -> bytes:
        size = self._options.export_block_samples * PCM16_SAMPLE_BYTES
        blocks = [file.read(size) for file in tracks.values()]
        length = max(map(len, blocks), default=0)

        if not length:
            return b""

        samples = [self._samples(block.ljust(length, b"\0")) for block in blocks]

        mixed = array("h", (
            max(PCM16_MIN_VALUE, min(PCM16_MAX_VALUE, sum(values)))
            for values in zip(*samples)
        ))

        if sys.byteorder != "little":
            mixed.byteswap()

        return mixed.tobytes()

    @staticmethod
    def _samples(data: bytes) -> array:
        samples = array("h")
        samples.frombytes(data)

        if sys.byteorder != "little":
            samples.byteswap()

        return samples

    @staticmethod
    def _frame(data: bytes, position: int):
        import av
        from fractions import Fraction

        frame = av.AudioFrame(
            format=PCM.av_format,
            layout=PCM.layout,
            samples=len(data) // PCM16_SAMPLE_BYTES,
        )

        frame.planes[0].update(data)
        frame.sample_rate = PCM.rate
        frame.pts = position
        frame.time_base = Fraction(1, PCM.rate)

        return frame


class CallRecorder:
    """Async facade. close deletes unclaimed output; take transfers ownership."""
    def __init__(self, options: RecordingOptions) -> None:
        self._options = options
        self._capture = PcmCapture(options)
        self._encoder = Mp3Encoder(options)
        self._export: Future[Path | None] | None = None
        self._cleanup: asyncio.Task | None = None
        self._path: Path | None = None
        self._path_lock = Lock()
        self._export_worker_finished = Event()
        self._closed = False
        self._failed = False
        self._discard_requested = False

    def write(self, track: Track, chunk: PcmChunk) -> None:
        if self._closed or self._export is not None or self._failed:
            return

        if self._options.enabled:
            try:
                self._capture.write(track, chunk)
            except Exception as error:
                self.disable(error)

    def disable(self, error: Exception) -> None:
        if not self._failed:
            self._failed = True
            logger.warning("VoIP recording disabled: error=%s", type(error).__name__)

    async def finish(self) -> Path | None:
        if self._export is None:
            self._export = self._start_worker(self._finish_blocking, "call-mp3-export")

        return await asyncio.shield(asyncio.wrap_future(self._export))

    def _finish_blocking(self) -> Path | None:
        path = None

        try:
            path = self._export_recording()
        except Exception as error:
            self.discard()
            logger.warning("VoIP recording export failed: error=%s", type(error).__name__)
        finally:
            try:
                self._capture.close()
            except Exception as error:
                logger.warning("VoIP recording cleanup failed: error=%s", type(error).__name__)
            finally:
                self._export_worker_finished.set()
                self._delete_discarded_file()

        with self._path_lock:
            return None if self._discard_requested else path

    def _export_recording(self) -> Path | None:
        tracks = self._capture.seal()

        if self._failed or not self._capture.has_samples:
            return None

        with NamedTemporaryFile(
            suffix=".mp3", dir=self._options.directory, delete=False,
        ) as output:
            path = Path(output.name)

        with self._path_lock:
            self._path = path

        self._encoder.encode(tracks, path)

        return path

    @staticmethod
    def _start_worker(work: Callable[[], T], name: str) -> Future[T]:
        future: Future[T] = Future()

        future.set_running_or_notify_cancel()

        def run() -> None:
            try:
                future.set_result(work())
            except BaseException as error:
                future.set_exception(error)

        Thread(target=run, name=name, daemon=True).start()

        return future

    def take(self) -> Path | None:
        if self._export is None or not self._export.done():
            raise RuntimeError("Finish recording before taking ownership")

        self._export.result()

        with self._path_lock:
            if self._discard_requested:
                return None

            path, self._path = self._path, None

            return path

    def take_ready_or_discard(self) -> Path | None:
        """Transfer a finished MP3, or discard it after its worker finishes."""
        if self._export is None or not self._export.done():
            self.discard()
            return None

        try:
            return self.take()
        except Exception as error:
            self.discard()
            logger.error("VoIP recording result unavailable: error=%s", type(error).__name__)
            return None

    @property
    def export_running(self) -> bool:
        return self._export is not None and not self._export_worker_finished.is_set()

    def discard(self) -> None:
        with self._path_lock:
            self._discard_requested = True

        if self._export is None or self._export_worker_finished.is_set():
            self._delete_discarded_file()

    def _delete_discarded_file(self) -> None:
        with self._path_lock:
            if not self._discard_requested or self._path is None:
                return

            path, self._path = self._path, None

        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            logger.warning("VoIP recording removal failed: error=%s", type(error).__name__)

            with self._path_lock:
                self._path = path

    async def close(self) -> None:
        """Own cleanup even if this particular waiter is cancelled."""
        if self._cleanup is None:
            self._closed = True
            self._cleanup = asyncio.create_task(self._close_owned())
            self._cleanup.add_done_callback(self._observe_cleanup)

        await asyncio.shield(self._cleanup)

    async def _close_owned(self) -> None:
        try:
            if self._export is not None:
                await asyncio.wrap_future(self._export)
            else:
                await asyncio.wrap_future(
                    self._start_worker(self._capture.close, "call-pcm-close")
                )
        except Exception as error:
            logger.warning("VoIP recording cleanup failed: error=%s", type(error).__name__)
        finally:
            self.discard()

    @staticmethod
    def _observe_cleanup(task: asyncio.Task) -> None:
        # Retrieve errors for cancelled waiters; awaiting close still raises them.
        if not task.cancelled():
            task.exception()
