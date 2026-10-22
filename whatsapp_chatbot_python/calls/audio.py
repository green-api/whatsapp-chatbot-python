"""PCM/RTC boundary. Audio tracks depend on narrow source/sink interfaces."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from fractions import Fraction
from math import isfinite
from time import monotonic
from typing import Protocol

from aiortc import AudioStreamTrack
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack
from av import AudioFrame, AudioResampler
from whatsapp_api_client_python.tools.voip.audio import CallAudio

from .models import (
    AudioLoss,
    EventSink,
    InputAudioOptions,
    InputOverflowPolicy,
    MediaFailed,
    PcmChunk,
    PCM,
    PCM16_SAMPLE_BYTES,
)
from .recorder import CallRecorder, Track


logger = logging.getLogger(__name__)


class _InputAudioBuffer:
    """Keep queued PCM bounded; prefer recent caller speech after sustained overload."""
    def __init__(self, options: InputAudioOptions) -> None:
        self._options = options

        self._capacity_bytes = max(
            PCM.bytes_per_frame,
            int(options.buffer_seconds * PCM.rate * PCM16_SAMPLE_BYTES),
        )

        self._frames: deque[bytes] = deque()
        self._queued_bytes = 0
        self._finished = False
        self._dropping_oldest = False
        self._changed = asyncio.Condition()
        self.dropped_samples = 0
        self.peak_buffered_samples = 0

    async def put(self, data: bytes) -> None:
        if len(data) % PCM16_SAMPLE_BYTES:
            raise ValueError("Caller audio must contain whole PCM samples")

        deadline = monotonic() + self._options.enqueue_seconds

        for start in range(0, len(data), PCM.bytes_per_frame):
            await self._put_frame(data[start:start + PCM.bytes_per_frame], deadline)

    async def _put_frame(self, frame: bytes, deadline: float) -> None:
        async with self._changed:
            self._ensure_open()
            await self._wait_for_capacity(len(frame), deadline)
            self._ensure_open()

            if self._queued_bytes + len(frame) > self._capacity_bytes:
                self._make_room(len(frame))

            self._frames.append(frame)

            self._queued_bytes += len(frame)

            self.peak_buffered_samples = max(
                self.peak_buffered_samples,
                self._queued_bytes // PCM16_SAMPLE_BYTES,
            )

            self._changed.notify_all()

    async def _wait_for_capacity(self, frame_bytes: int, deadline: float) -> None:
        """Called while holding the condition; one input chunk shares a deadline."""
        if self._dropping_oldest:
            return

        try:
            async with asyncio.timeout(max(0, deadline - monotonic())):
                while self._queued_bytes + frame_bytes > self._capacity_bytes:
                    await self._changed.wait()
                    self._ensure_open()
        except TimeoutError:
            self._dropping_oldest = True

    def _ensure_open(self) -> None:
        if self._finished:
            raise RuntimeError("Caller audio buffer is closed")

    def _make_room(self, required_bytes: int) -> None:
        if self._options.overflow_policy == InputOverflowPolicy.FAIL:
            raise BufferError("Inbound audio buffer exceeded its time limit")

        while self._queued_bytes + required_bytes > self._capacity_bytes:
            removed = self._frames.popleft()
            self._queued_bytes -= len(removed)
            self.dropped_samples += len(removed) // PCM16_SAMPLE_BYTES

    async def get(self) -> bytes | None:
        async with self._changed:
            while not self._frames and not self._finished:
                await self._changed.wait()

            if not self._frames:
                return None

            frame = self._frames.popleft()
            self._queued_bytes -= len(frame)

            if self._queued_bytes <= self._capacity_bytes // 2:
                self._dropping_oldest = False

            self._changed.notify_all()

            return frame

    async def finish(self) -> None:
        async with self._changed:
            self._finished = True
            self._changed.notify_all()


class AudioSource(Protocol):
    def read_frame(self) -> bytes: ...
    def replace_track(self) -> None: ...


class AudioSink(Protocol):
    async def write_audio(self, data: bytes) -> None: ...


class RecordingTap:
    """Place both tracks on one clock without stretching adjacent frames.

    Local times use monotonic seconds. Caller media_time is relative to its
    source stream; recording positions count PCM samples from activation.
    """
    # Small callback delays stay contiguous; source timestamps cannot run far ahead of local time.
    _CALLER_JITTER_SAMPLES = 2 * PCM.samples_per_frame
    _MAX_CALLER_MEDIA_LEAD_SAMPLES = PCM.rate

    def __init__(self, recorder: CallRecorder) -> None:
        self._recorder = recorder
        self._epoch: float | None = None
        self._positions: dict[Track, int] = {}
        self._caller_media_origin: tuple[float, int] | None = None
        self._last_caller_media_time: float | None = None
        self.bot_inserted_silence_samples = 0
        self.caller_estimated_gap_samples = 0
        self.caller_timestamp_reanchors = 0

    def activate(self) -> None:
        if self._epoch is None:
            self._epoch = monotonic()

    def write(
        self, track: Track, data: bytes, media_time: float | None = None,
        *, frame_start_at: float | None = None, resumed_after_pause: bool = False,
    ) -> None:
        if self._epoch is None:
            return

        position = self._sample_position(track, media_time, frame_start_at, resumed_after_pause)

        try:
            self._recorder.write(track, PcmChunk(position, data))
        except Exception as error:
            self._recorder.disable(error)
            return

        self._positions[track] = position + len(data) // PCM16_SAMPLE_BYTES

    def _sample_position(
        self, track: Track, media_time: float | None,
        frame_start_at: float | None, resumed_after_pause: bool,
    ) -> int:
        """Choose a PCM position and count silence inserted into the bot track."""
        next_position = self._positions.get(track)

        if next_position is None:
            position = self._position_at(frame_start_at)
        elif track == Track.BOT and resumed_after_pause:
            position = max(next_position, self._position_at(frame_start_at))
            self.bot_inserted_silence_samples += position - next_position
        else:
            position = next_position

        if track == Track.CALLER:
            if media_time is not None and isfinite(media_time):
                position = self._caller_media_position(media_time, position, frame_start_at)
            else:
                position = self._caller_wall_position(position, frame_start_at)

        return position

    def _position_at(self, instant: float | None) -> int:
        if instant is None or not isfinite(instant):
            instant = monotonic()

        return max(0, round((instant - self._epoch) * PCM.rate))

    def _caller_media_position(
        self, media_time: float, next_position: int, frame_start_at: float | None,
    ) -> int:
        origin = self._caller_media_origin
        last_media_time = self._last_caller_media_time

        timestamp_restarted = (
            last_media_time is not None
            and media_time < last_media_time - PCM.frame_seconds
        )

        if origin is not None and not timestamp_restarted:
            origin_time, origin_position = origin
            media_elapsed_seconds = media_time - origin_time
            wall_position = self._position_at(frame_start_at)

            allowed_seconds = (
                max(next_position, wall_position) + self._MAX_CALLER_MEDIA_LEAD_SAMPLES
                - origin_position
            ) / PCM.rate

            if isfinite(media_elapsed_seconds) and -PCM.frame_seconds <= media_elapsed_seconds <= allowed_seconds:
                media_position = origin_position + round(media_elapsed_seconds * PCM.rate)
                self._last_caller_media_time = media_time
                return max(next_position, media_position)

        if origin is not None:
            self.caller_timestamp_reanchors += 1
            reason = "moved_backward" if timestamp_restarted else "outside_local_clock"
            logger.warning("Caller recording timestamp reanchored: reason=%s", reason)

        position = max(next_position, self._position_at(frame_start_at))

        self._caller_media_origin = (media_time, position)
        self._last_caller_media_time = media_time

        return position

    def _caller_wall_position(self, next_position: int, frame_start_at: float | None) -> int:
        self._caller_media_origin = None
        self._last_caller_media_time = None

        if frame_start_at is None:
            return next_position

        wall_position = self._position_at(frame_start_at)

        if wall_position > next_position + self._CALLER_JITTER_SAMPLES:
            self.caller_estimated_gap_samples += wall_position - next_position
            return wall_position

        return next_position

    def replace_tracks(self) -> None:
        self._positions.clear()
        self._caller_media_origin = None
        self._last_caller_media_time = None


class BotTrack(AudioStreamTrack):
    """Pace outgoing frames and preserve real send gaps in their PCM timestamps."""

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
        frame_start_at = monotonic()

        if len(data) != PCM.bytes_per_frame:
            raise ValueError("AudioSource must return exactly one PCM frame")

        resumed_after_pause = frame_start_at > due + PCM.frame_seconds

        if resumed_after_pause:
            # Advance the RTC timestamp as well as the recording clock after a pause.
            self._position = max(self._position, round((frame_start_at - self._epoch) * PCM.rate))

        self._tap.write(
            Track.BOT, data,
            frame_start_at=frame_start_at,
            resumed_after_pause=resumed_after_pause,
        )

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

    async def attach(self, track: MediaStreamTrack) -> None:
        if self._closing:
            raise RuntimeError("Caller audio sink is closing")

        task = asyncio.create_task(self._consume(track))
        self._tasks.add(task)
        task.add_done_callback(self._done)

    async def _consume(self, track: MediaStreamTrack) -> None:
        resampler = AudioResampler(format=PCM.av_format, layout=PCM.layout, rate=PCM.rate)
        buffer = _InputAudioBuffer(self._options)
        sender = asyncio.create_task(self._send(buffer), name="call:input-send")
        reader = asyncio.current_task()
        sender.add_done_callback(lambda done: self._sender_done(done, reader))
        stream_ended = False
        reported_dropped_samples = 0
        next_loss_report_at = 0.0

        try:
            while True:
                frames, frame_start_at = await self._read_resampled_frames(track, resampler)

                for frame in frames:
                    if sender.done():
                        return

                    await self._queue_frame(frame, buffer, frame_start_at)

                    frame_start_at += frame.samples / PCM.rate

                    if (
                        buffer.dropped_samples > reported_dropped_samples
                        and monotonic() >= next_loss_report_at
                    ):
                        lost_samples = buffer.dropped_samples - reported_dropped_samples

                        self._emit(AudioLoss("caller", lost_samples))

                        reported_dropped_samples = buffer.dropped_samples
                        next_loss_report_at = monotonic() + 1

                    await asyncio.sleep(0)
        except MediaStreamError:
            stream_ended = True
        finally:
            await buffer.finish()

            if buffer.dropped_samples > reported_dropped_samples:
                lost_samples = buffer.dropped_samples - reported_dropped_samples
                self._emit(AudioLoss("caller", lost_samples))

            if not stream_ended:
                sender.cancel()

            await asyncio.gather(sender, return_exceptions=True)

            if buffer.dropped_samples:
                logger.warning("Caller audio dropped: samples=%d peak_buffered_samples=%d",
                               buffer.dropped_samples, buffer.peak_buffered_samples)

    @staticmethod
    async def _read_resampled_frames(
        track: MediaStreamTrack,
        resampler: AudioResampler,
    ) -> tuple[list[AudioFrame], float]:
        """Return PCM frames and their estimated start in monotonic seconds."""
        incoming_frame = await track.recv()
        received_at = monotonic()
        frames = list(resampler.resample(incoming_frame))
        duration = sum(frame.samples for frame in frames) / PCM.rate

        return frames, received_at - duration

    async def _queue_frame(
        self, frame: AudioFrame, buffer: _InputAudioBuffer, frame_start_at: float,
    ) -> None:
        frame_bytes = frame.samples * PCM16_SAMPLE_BYTES
        data = bytes(frame.planes[0])[:frame_bytes]

        media_time = (
            float(frame.pts * frame.time_base)
            if frame.pts is not None and frame.time_base is not None else None
        )

        self._tap.write(Track.CALLER, data, media_time, frame_start_at=frame_start_at)
        await buffer.put(data)

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

    async def _send(self, buffer: _InputAudioBuffer) -> None:
        while (data := await buffer.get()) is not None:
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

    def log_recording_timing(self, session_id: str) -> None:
        if (
            self._tap.bot_inserted_silence_samples
            or self._tap.caller_estimated_gap_samples
            or self._tap.caller_timestamp_reanchors
        ):
            logger.info(
                "Call recording timing: session=%s bot_inserted_silence_samples=%d "
                "caller_estimated_gap_samples=%d caller_timestamp_reanchors=%d",
                session_id, self._tap.bot_inserted_silence_samples,
                self._tap.caller_estimated_gap_samples,
                self._tap.caller_timestamp_reanchors,
            )

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
