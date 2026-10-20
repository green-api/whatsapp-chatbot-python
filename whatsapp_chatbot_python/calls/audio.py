"""PCM/RTC boundary. Audio tracks depend on narrow source/sink interfaces."""
from __future__ import annotations

import asyncio
from fractions import Fraction
from math import ceil, isfinite
from time import monotonic
from typing import Protocol

from aiortc import AudioStreamTrack
from aiortc.mediastreams import MediaStreamError
from av import AudioFrame, AudioResampler
from whatsapp_api_client_python.tools.voip.audio import CallAudio

from .models import EventSink, InputAudioOptions, MediaFailed, PcmChunk, PCM, PCM16_SAMPLE_BYTES
from .recorder import CallRecorder, Track


class AudioSource(Protocol):
    def read_frame(self) -> bytes: ...
    def replace_track(self) -> None: ...


class AudioSink(Protocol):
    async def write_audio(self, data: bytes) -> None: ...


class RecordingTap:
    """Align each track once, then advance by sample count, not arrival jitter."""
    def __init__(self, recorder: CallRecorder) -> None:
        self._recorder = recorder
        self._epoch: float | None = None
        self._positions: dict[Track, int] = {}
        self._media_origins: dict[Track, tuple[float, int]] = {}

    def activate(self) -> None:
        if self._epoch is None:
            self._epoch = monotonic()

    def write(self, track: Track, data: bytes, media_time: float | None = None) -> None:
        if self._epoch is None:
            return

        position = self._positions.setdefault(
            track,
            max(0, int((monotonic() - self._epoch) * PCM.rate))
        )

        if media_time is not None and isfinite(media_time):
            origin_time, origin_position = self._media_origins.setdefault(
                track,
                (media_time, position),
            )

            position = max(position, origin_position + round((media_time - origin_time) * PCM.rate))

        try:
            self._recorder.write(track, PcmChunk(position, data))
        except Exception as error:
            self._recorder.disable(error)
            return

        self._positions[track] = position + len(data) // PCM16_SAMPLE_BYTES

    def replace_tracks(self) -> None:
        self._positions.clear()
        self._media_origins.clear()


class BotTrack(AudioStreamTrack):
    def __init__(self, source: AudioSource, tap: RecordingTap, emit: EventSink) -> None:
        super().__init__()
        self._source = source
        self._tap = tap
        self._emit = emit
        self._position = 0
        self._epoch = monotonic()

    async def recv(self) -> AudioFrame:
        if self.readyState != "live":
            raise MediaStreamError

        try:
            return await self._next_frame()
        except Exception as error:
            self._emit(MediaFailed(error))
            raise

    async def _next_frame(self) -> AudioFrame:
        due = self._epoch + self._position / PCM.rate

        await asyncio.sleep(max(0, due - monotonic()))

        data = self._source.read_frame()

        if len(data) != PCM.bytes_per_frame:
            raise ValueError("AudioSource must return exactly one PCM frame")

        self._tap.write(Track.BOT, data)

        frame = AudioFrame(
            format=PCM.av_format,
            layout=PCM.layout,
            samples=PCM.samples_per_frame,
        )

        frame.planes[0].update(data)

        frame.sample_rate = PCM.rate
        frame.pts = self._position
        frame.time_base = Fraction(1, PCM.rate)
        self._position += PCM.samples_per_frame

        return frame


class CallerSink:
    def __init__(
        self, sink: AudioSink, tap: RecordingTap, emit: EventSink,
        options: InputAudioOptions | None = None,
    ) -> None:
        self._sink = sink
        self._tap = tap
        self._emit = emit
        self._options = options or InputAudioOptions()
        self._tasks: set[asyncio.Task] = set()
        self._closing = False
        self._failure_reported = False

    async def attach(self, track) -> None:
        if self._closing:
            raise RuntimeError("Caller audio sink is closing")

        task = asyncio.create_task(self._consume(track))
        self._tasks.add(task)
        task.add_done_callback(self._done)

    async def _consume(self, track) -> None:
        resampler = AudioResampler(format=PCM.av_format, layout=PCM.layout, rate=PCM.rate)
        capacity = max(1, ceil(self._options.buffer_seconds / PCM.frame_seconds))
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=capacity)
        finished = asyncio.Event()
        sender = asyncio.create_task(self._send(queue, finished), name="call:input-send")
        reader = asyncio.current_task()
        sender.add_done_callback(lambda done: self._sender_done(done, reader))
        stream_ended = False

        try:
            while True:
                for frame in resampler.resample(await track.recv()):
                    if sender.done():
                        return

                    size = frame.samples * PCM16_SAMPLE_BYTES
                    data = bytes(frame.planes[0])[:size]

                    media_time = (
                        float(frame.pts * frame.time_base)
                        if frame.pts is not None and frame.time_base is not None else None
                    )

                    self._tap.write(Track.CALLER, data, media_time)
                    self._enqueue(queue, data)
        except MediaStreamError:
            stream_ended = True
        finally:
            finished.set()

            if not stream_ended:
                sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)

    def _sender_done(self, sender: asyncio.Task, reader: asyncio.Task) -> None:
        if sender.cancelled() or self._closing:
            return

        error = sender.exception()

        if error is not None:
            self._report_failure(error)

            if not reader.done():
                reader.cancel()

    def _report_failure(self, error: Exception) -> None:
        if not self._failure_reported:
            self._failure_reported = True
            self._emit(MediaFailed(error))

    @staticmethod
    def _enqueue(queue: asyncio.Queue[bytes], data: bytes) -> None:
        for start in range(0, len(data), PCM.bytes_per_frame):
            try:
                queue.put_nowait(data[start:start + PCM.bytes_per_frame])
            except asyncio.QueueFull as error:
                raise BufferError("Inbound audio buffer exceeded its time limit") from error

    async def _send(self, queue: asyncio.Queue[bytes], finished: asyncio.Event) -> None:
        while not finished.is_set() or not queue.empty():
            try:
                data = await asyncio.wait_for(queue.get(), PCM.frame_seconds)
            except TimeoutError:
                continue

            await asyncio.wait_for(self._sink.write_audio(data), self._options.send_seconds)

    def _done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)

        if task.cancelled():
            return

        error = task.exception()

        if error is not None:
            self._report_failure(error)

    async def close(self) -> None:
        self._closing = True
        tasks = tuple(self._tasks)

        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)


class AudioBridge:
    def __init__(
        self,
        source: AudioSource,
        sink: AudioSink,
        recorder: CallRecorder,
        emit: EventSink,
        input_options: InputAudioOptions | None = None,
    ) -> None:
        self._source = source
        self._sink = sink
        self._tap = RecordingTap(recorder)
        self._emit = emit
        self._input_options = input_options
        self._audio: CallAudio | None = None
        self._closed = False

    def activate_recording(self) -> None:
        self._tap.activate()

    async def create(self) -> CallAudio:
        if self._closed:
            raise RuntimeError("Audio bridge is closing")

        await self._close_current()

        if self._closed:
            raise RuntimeError("Audio bridge is closing")

        self._source.replace_track()
        self._tap.replace_tracks()

        local = BotTrack(self._source, self._tap, self._emit)
        remote = CallerSink(self._sink, self._tap, self._emit, self._input_options)

        async def close_pair() -> None:
            local.stop()
            await remote.close()

        self._audio = CallAudio(local, remote.attach, close_pair)

        return self._audio

    async def close(self) -> None:
        self._closed = True
        await self._close_current()

    async def _close_current(self) -> None:
        audio, self._audio = self._audio, None

        if audio is not None:
            await audio.close()
