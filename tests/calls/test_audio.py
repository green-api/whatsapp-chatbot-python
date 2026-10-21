"""RTC audio frames, replacement and narrow PCM contracts."""

import asyncio
import unittest
from fractions import Fraction

import av

from whatsapp_chatbot_python.calls.audio import AudioBridge, BotTrack, CallerSink, RecordingTap
from whatsapp_chatbot_python.calls.models import InputAudioOptions, MediaFailed, PCM
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
            InputAudioOptions(buffer_seconds=PCM.frame_seconds, send_seconds=0.1),
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
