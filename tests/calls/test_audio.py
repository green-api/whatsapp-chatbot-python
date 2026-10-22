"""RTC audio frames, replacement and narrow PCM contracts."""

import asyncio
import unittest
from collections import deque
from fractions import Fraction

import av
from aiortc.mediastreams import MediaStreamError

from whatsapp_chatbot_python.calls.audio import (
    AudioBridge,
    BotTrack,
    CallerSink,
    RecordingTap,
    _InputAudioBuffer,
)

from whatsapp_chatbot_python.calls.models import (
    AudioLoss, InputAudioOptions, MediaFailed, PCM,
)
from whatsapp_chatbot_python.calls.recorder import Track


FRAME = bytes([3, 4]) * PCM.samples_per_frame


class Source:
    def __init__(self):
        self.replacements = 0
        self.frames = 0

    def read_frame(self):
        self.frames += 1
        return FRAME

    def replace_track(self):
        self.replacements += 1

    async def write_audio(self, data):
        pass


class Capture:
    def __init__(self):
        self.chunks = []

    def write(self, track, chunk):
        self.chunks.append((track, chunk))

    def disable(self, error):
        raise AssertionError(error)


class AudioBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_input_overflow_keeps_recent_audio_and_does_not_fail(self):
        buffer = _InputAudioBuffer(InputAudioOptions(
            buffer_seconds=PCM.frame_seconds,
            enqueue_seconds=0.001,
        ))

        await buffer.put(b"\x01\x00" * PCM.samples_per_frame)
        await buffer.put(b"\x02\x00" * PCM.samples_per_frame)
        self.assertEqual(buffer.dropped_samples, PCM.samples_per_frame)
        self.assertEqual(await buffer.get(), b"\x02\x00" * PCM.samples_per_frame)
        await buffer.finish()
        self.assertIsNone(await buffer.get())

    async def test_sustained_input_overflow_does_not_wait_for_every_frame(self):
        buffer = _InputAudioBuffer(InputAudioOptions(
            buffer_seconds=PCM.frame_seconds,
            enqueue_seconds=0.05,
        ))

        await buffer.put(FRAME)
        newer = b"\x05\x00" * PCM.samples_per_frame
        await asyncio.wait_for(
            buffer.put(newer * 100), timeout=0.5)
        self.assertEqual(await buffer.get(), newer)
        self.assertEqual(buffer.dropped_samples, 100 * PCM.samples_per_frame)

    async def test_caller_reports_audio_loss_and_continues_sending(self):
        class ReadyTrack:
            def __init__(self):
                self.frames_left = 10

            async def recv(self):
                if not self.frames_left:
                    raise MediaStreamError
                self.frames_left -= 1
                frame = av.AudioFrame(format="s16", layout="mono", samples=PCM.samples_per_frame)
                frame.planes[0].update(FRAME)
                frame.sample_rate = PCM.rate
                return frame

        class SlowSink:
            def __init__(self):
                self.sent = 0

            async def write_audio(self, data):
                await asyncio.sleep(0.005)
                self.sent += 1

        sink, events = SlowSink(), []
        caller = CallerSink(
            sink, RecordingTap(Capture()), events.append,
            InputAudioOptions(buffer_seconds=PCM.frame_seconds, enqueue_seconds=0.001),
        )
        await asyncio.wait_for(caller._consume(ReadyTrack()), 1)

        self.assertGreater(sink.sent, 0)
        self.assertTrue(events)
        self.assertTrue(all(isinstance(event, AudioLoss) for event in events))
        self.assertGreater(sum(event.dropped_samples for event in events), 0)

    async def test_finishing_input_buffer_wakes_waiting_sender(self):
        buffer = _InputAudioBuffer(InputAudioOptions())
        sender = asyncio.create_task(buffer.get())
        await asyncio.sleep(0)
        await buffer.finish()
        self.assertIsNone(await asyncio.wait_for(sender, 0.1))

    async def test_waiting_writer_cannot_reopen_finished_input_buffer(self):
        buffer = _InputAudioBuffer(InputAudioOptions(
            buffer_seconds=PCM.frame_seconds,
            enqueue_seconds=0.1,
        ))
        await buffer.put(FRAME)
        writer = asyncio.create_task(buffer.put(FRAME))
        await asyncio.sleep(0)
        await buffer.finish()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            await asyncio.wait_for(writer, 0.1)

    async def _assert_rtc_frames_sent(self, frame_count: int) -> None:
        class ReadyTrack:
            def __init__(self, frames):
                self.frames = deque(frames)

            async def recv(self):
                if not self.frames:
                    raise MediaStreamError
                return self.frames.popleft()

        class FastSink:
            def __init__(self):
                self.sent = []

            async def write_audio(self, data):
                self.sent.append(data)

        expected = []
        frames = []
        for index in range(frame_count):
            data = index.to_bytes(2, "little") * PCM.samples_per_frame
            frame = av.AudioFrame(format="s16", layout="mono", samples=PCM.samples_per_frame)
            frame.planes[0].update(data)
            frame.sample_rate = PCM.rate
            frames.append(frame)
            expected.append(data)

        sink = FastSink()
        failures = []
        caller = CallerSink(sink, RecordingTap(Capture()), failures.append)
        try:
            await asyncio.wait_for(caller._consume(ReadyTrack(frames)), timeout=1)
            self.assertEqual(sink.sent, expected)
            self.assertEqual(sum(len(data) for data in sink.sent),
                             frame_count * PCM.bytes_per_frame)
            self.assertEqual(failures, [])
        finally:
            await caller.close()

    async def test_26_ready_rtc_frames_do_not_overflow_with_a_fast_sender(self):
        await self._assert_rtc_frames_sent(26)

    async def test_100_ready_rtc_frames_are_sent_in_order(self):
        await self._assert_rtc_frames_sent(100)

    async def test_sender_failure_is_reported_without_another_rtc_frame(self):
        class OneFrameTrack:
            def __init__(self):
                self.received = 0

            async def recv(self):
                self.received += 1
                if self.received > 1:
                    await asyncio.Event().wait()

                frame = av.AudioFrame(format="s16", layout="mono", samples=PCM.samples_per_frame)
                frame.planes[0].update(FRAME)
                frame.sample_rate = PCM.rate
                return frame

        class BlockedSink:
            async def write_audio(self, data):
                await asyncio.Event().wait()

        failures = []
        track = OneFrameTrack()
        caller = CallerSink(
            BlockedSink(), RecordingTap(Capture()), failures.append,
            InputAudioOptions(buffer_seconds=0.5, send_seconds=0.02),
        )
        await caller.attach(track)

        for _ in range(100):
            if failures:
                break
            await asyncio.sleep(0.001)

        self.assertEqual(track.received, 2)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0].error, TimeoutError)
        await caller.close()

    async def test_slow_model_send_overloads_bounded_input_queue(self):
        class FastTrack:
            def __init__(self):
                self.received = 0

            async def recv(self):
                self.received += 1
                frame = av.AudioFrame(format="s16", layout="mono", samples=PCM.samples_per_frame)
                frame.planes[0].update(FRAME)
                frame.sample_rate = PCM.rate
                return frame

        class SlowSink:
            async def write_audio(self, data):
                await asyncio.Event().wait()

        track = FastTrack()
        caller = CallerSink(
            SlowSink(), RecordingTap(Capture()), lambda event: None,
            InputAudioOptions(buffer_seconds=PCM.frame_seconds, send_seconds=0.1,
                              enqueue_seconds=0.01, overflow_policy="fail"),
        )

        with self.assertRaisesRegex(BufferError, "Inbound audio buffer"):
            await asyncio.wait_for(caller._consume(track), 0.5)

        self.assertLessEqual(track.received, 3)

    async def test_bot_frames_have_20ms_pcm_timestamps_and_are_recorded(self):
        source, capture, events = Source(), Capture(), []
        tap = RecordingTap(capture)
        tap.activate()
        track = BotTrack(source, tap, events.append)
        try:
            first, second = await track.recv(), await track.recv()
            self.assertEqual((first.pts, second.pts), (0, PCM.samples_per_frame))
            self.assertEqual((first.sample_rate, first.time_base),
                             (PCM.rate, Fraction(1, PCM.rate)))
            self.assertEqual(bytes(first.planes[0])[:PCM.bytes_per_frame], FRAME)
            self.assertEqual([side for side, _ in capture.chunks], [Track.BOT, Track.BOT])
            self.assertEqual(capture.chunks[1][1].position - capture.chunks[0][1].position,
                             PCM.samples_per_frame)
            self.assertEqual(events, [])
        finally:
            track.stop()

    async def test_bot_track_resets_pacing_after_a_long_pause(self):
        source = Source()
        track = BotTrack(source, RecordingTap(Capture()), lambda event: None)
        try:
            track._epoch -= 10
            await track.recv()
            self.assertLess(abs(track._epoch + track._position / PCM.rate
                                - asyncio.get_running_loop().time()), 0.1)
        finally:
            track.stop()

    async def test_reconnect_replaces_track_and_stops_old_audio(self):
        source, capture = Source(), Capture()
        bridge = AudioBridge(source, source, capture, lambda event: None)
        first = await bridge.create()
        self.assertEqual(first.local_track.readyState, "live")
        second = await bridge.create()
        self.assertEqual(first.local_track.readyState, "ended")
        self.assertEqual(second.local_track.readyState, "live")
        self.assertEqual(source.replacements, 2)
        await bridge.close()
        self.assertEqual(second.local_track.readyState, "ended")

    async def test_bad_source_frame_surfaces_media_failure(self):
        class BadSource(Source):
            def read_frame(self):
                return b"too short"

        events = []
        track = BotTrack(BadSource(), RecordingTap(Capture()), events.append)
        try:
            with self.assertRaisesRegex(ValueError, "exactly one PCM frame"):
                await track.recv()
            self.assertEqual(len(events), 1)
            self.assertIsInstance(events[0], MediaFailed)
        finally:
            track.stop()
