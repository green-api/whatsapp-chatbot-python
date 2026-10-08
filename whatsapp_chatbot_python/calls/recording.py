"""Best-effort recording of both sides of one call."""

from __future__ import annotations
from array import array
from pathlib import Path
from queue import Full, Queue
from tempfile import TemporaryFile, NamedTemporaryFile
from threading import Event, Thread
from time import monotonic
import logging
import av


# Constants

RATE = 24 * 1000

MAX_PENDING_CHUNKS = 256

MIX_SAMPLES = 24 * 1000


class CallRecorder:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._queue: Queue[tuple[str, int, bytes] | None] = Queue(MAX_PENDING_CHUNKS)
        self._started_at: float | None = None
        self._generation = 0
        self._failed = Event()
        self._thread: Thread | None = None
        self._caller = TemporaryFile()
        self._bot = TemporaryFile()
        self._lengths = {"caller": 0, "bot": 0}
        self._has_audio = False

    def start(self) -> None:
        if self._started_at is None:
            self._started_at = monotonic()
            self._thread = Thread(target=self._write, name="voip-recording", daemon=True)

            self._thread.start()

    def set_generation(self, generation: int) -> None:
        self._generation = generation

    def add_caller(self, pcm: bytes, generation: int) -> None:
        self._add("caller", pcm, generation)

    def add_bot(self, pcm: bytes, generation: int) -> None:
        self._add("bot", pcm, generation)

    def _add(self, side: str, pcm: bytes, generation: int) -> None:
        if self._started_at is None or self._failed.is_set() or generation != self._generation or not pcm:
            return

        if len(pcm) % 2:
            self._failed.set()
            return

        # Incoming frames arrive after capture; place their start at the beginning
        # of the received interval. The outgoing frame starts when recv() yields it.
        elapsed = monotonic() - self._started_at

        if side == "caller":
            elapsed -= len(pcm) / (2 * RATE)

        offset = max(0, round(elapsed * RATE))

        try:
            self._queue.put_nowait((side, offset, pcm))
        except Full:
            self._failed.set()
            self._logger.warning("VoIP recording queue overflow")

    def _write(self) -> None:
        streams = {"caller": self._caller, "bot": self._bot}

        try:
            while (item := self._queue.get()) is not None:
                side, offset, pcm = item
                stream = streams[side]
                stream.seek(offset * 2)
                stream.write(pcm)
                self._lengths[side] = max(self._lengths[side], offset + len(pcm) // 2)
                self._has_audio = True
                self._queue.task_done()

            self._queue.task_done()
        except Exception:
            self._failed.set()
            self._logger.exception("VoIP recording write failed")

    def finish(self) -> Path | None:
        """Finish queued writes and return an MP3 owned by the caller."""

        if self._thread is not None:
            while self._thread.is_alive():
                try:
                    self._queue.put(None, timeout=0.1)
                    break
                except Full:
                    pass

            self._thread.join()
        try:
            if self._failed.is_set() or not self._has_audio:
                return None

            with NamedTemporaryFile(prefix="voip-call-", suffix=".mp3", delete=False) as output:
                path = Path(output.name)

            try:
                self._encode(path)
                return path
            except Exception:
                path.unlink(missing_ok=True)
                self._logger.exception("VoIP recording encoding failed")

                return None
        finally:
            self._caller.close()
            self._bot.close()

    def _encode(self, path: Path) -> None:
        length = max(self._lengths.values())

        self._caller.seek(0)
        self._bot.seek(0)

        with av.open(str(path), "w") as container:
            stream = container.add_stream("libmp3lame", rate=RATE)
            stream.layout = "mono"

            for _ in range(0, length, MIX_SAMPLES):
                count = min(MIX_SAMPLES, length - _)
                caller = array("h")
                bot = array("h")

                caller.frombytes(self._caller.read(count * 2).ljust(count * 2, b"\0"))
                bot.frombytes(self._bot.read(count * 2).ljust(count * 2, b"\0"))

                mixed = array("h", (max(-32768, min(32767, a + b)) for a, b in zip(caller, bot)))
                frame = av.AudioFrame(format="s16", layout="mono", samples=count)

                frame.planes[0].update(mixed.tobytes())
                frame.sample_rate = RATE

                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
