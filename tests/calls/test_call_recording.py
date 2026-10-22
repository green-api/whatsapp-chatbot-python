"""MP3, PCM timing, and recording ownership guarantees."""

import asyncio
import math
import tempfile
import unittest
from array import array
from fractions import Fraction
from io import BytesIO
from pathlib import Path
from threading import Event
from unittest.mock import patch

import av
from aiortc.mediastreams import MediaStreamError

from whatsapp_chatbot_python.calls.audio import BotTrack, CallerSink, RecordingTap
from whatsapp_chatbot_python.calls.models import CallExecutionResult, PCM, PcmChunk, RecordingOptions
from whatsapp_chatbot_python.calls.recorder import CallRecorder, Mp3Encoder, PcmCapture, Track


def pcm(value, samples=PCM.samples_per_frame):
    return array("h", [value] * samples).tobytes()


def tone(frequency, samples=PCM.rate // 10):
    return array("h", (
        round(6000 * math.sin(2 * math.pi * frequency * index / PCM.rate))
        for index in range(samples)
    )).tobytes()


def samples_at(seconds: float) -> int:
    return round(seconds * PCM.rate)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class FakeRecordingCapture:
    def __init__(self) -> None:
        self.chunks: list[tuple[Track, PcmChunk]] = []

    def write(self, track: Track, chunk: PcmChunk) -> None:
        self.chunks.append((track, chunk))

    @property
    def positions(self) -> list[int]:
        return [chunk.position for _, chunk in self.chunks]

    def disable(self, error: Exception) -> None:
        raise AssertionError(error)


class RecordingTest(unittest.IsolatedAsyncioTestCase):
    async def test_bot_recv_pause_keeps_caller_and_bot_on_one_timeline(self):
        class Source:
            def read_frame(self):
                return pcm(1000)

        clock = FakeClock()
        capture = FakeRecordingCapture()
        with patch("whatsapp_chatbot_python.calls.audio.monotonic", side_effect=clock.monotonic):
            tap = RecordingTap(capture)
            tap.activate()
            bot = BotTrack(Source(), tap, lambda event: None)
            try:
                await bot.recv()
                clock.now = 0.5
                await bot.recv()
                tap.write(Track.CALLER, pcm(2000), frame_start_at=0.6)
            finally:
                bot.stop()

        positions = [(track, chunk.position) for track, chunk in capture.chunks]
        self.assertEqual(positions, [
            (Track.BOT, 0), (Track.BOT, PCM.rate // 2),
            (Track.CALLER, PCM.rate * 6 // 10),
        ])

    async def test_continuous_bot_recv_records_silence_without_extra_gap(self):
        class Silence:
            def read_frame(self):
                return bytes(PCM.bytes_per_frame)

        clock = FakeClock()
        capture = FakeRecordingCapture()
        with patch("whatsapp_chatbot_python.calls.audio.monotonic", side_effect=clock.monotonic):
            tap = RecordingTap(capture)
            tap.activate()
            bot = BotTrack(Silence(), tap, lambda event: None)
            try:
                for frame_number in range(3):
                    clock.now = frame_number * PCM.frame_seconds
                    if frame_number == 1:
                        clock.now += 0.005  # Minor scheduling jitter should not create a gap.
                    await bot.recv()
            finally:
                bot.stop()

        self.assertEqual(capture.positions, [
            0, PCM.samples_per_frame, 2 * PCM.samples_per_frame,
        ])

    async def test_pcm_tracks_keep_the_gap_before_mixing(self):
        capture = PcmCapture(RecordingOptions())
        clock = FakeClock()
        with patch("whatsapp_chatbot_python.calls.audio.monotonic", side_effect=clock.monotonic):
            tap = RecordingTap(capture)
            tap.activate()
            tap.write(Track.BOT, pcm(1000), frame_start_at=0.0)
            clock.now = 0.5
            tap.write(
                Track.BOT, pcm(2000), frame_start_at=0.5, resumed_after_pause=True,
            )
            tap.write(Track.CALLER, pcm(3000), frame_start_at=0.6)

        try:
            tracks = capture.seal()
            bot = tracks[Track.BOT].read()
            caller = tracks[Track.CALLER].read()
            self.assertEqual(bot[:PCM.bytes_per_frame], pcm(1000))
            self.assertEqual(bot[PCM.bytes_per_frame:PCM.rate], bytes(PCM.rate - PCM.bytes_per_frame))
            self.assertEqual(bot[PCM.rate:PCM.rate + PCM.bytes_per_frame], pcm(2000))
            self.assertEqual(caller[PCM.rate * 6 // 10 * 2:], pcm(3000))
        finally:
            capture.close()

    async def test_caller_timestamp_restart_reanchors_without_overwriting_audio(self):
        capture = FakeRecordingCapture()
        clock = FakeClock()
        with patch("whatsapp_chatbot_python.calls.audio.monotonic", side_effect=clock.monotonic):
            tap = RecordingTap(capture)
            tap.activate()
            tap.write(Track.CALLER, pcm(1000), media_time=0.0, frame_start_at=0.0)
            tap.write(Track.CALLER, pcm(2000), media_time=0.1, frame_start_at=0.1)
            clock.now = 0.5
            tap.write(Track.CALLER, pcm(3000), media_time=0.0, frame_start_at=0.5)
            tap.write(Track.CALLER, pcm(4000), media_time=0.02, frame_start_at=0.52)

        self.assertEqual(capture.positions, [
            samples_at(0), samples_at(0.1), samples_at(0.5), samples_at(0.52),
        ])
        self.assertEqual(tap.caller_timestamp_reanchors, 1)

    async def test_replacing_tracks_keeps_the_shared_recording_clock(self):
        capture = FakeRecordingCapture()
        clock = FakeClock()
        with patch("whatsapp_chatbot_python.calls.audio.monotonic", side_effect=clock.monotonic):
            tap = RecordingTap(capture)
            tap.activate()
            tap.write(Track.BOT, pcm(1000))
            clock.now = 0.5
            tap.replace_tracks()
            tap.write(Track.BOT, pcm(2000))

        self.assertEqual(capture.positions, [0, PCM.rate // 2])

    async def test_result_context_releases_or_transfers_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "call.mp3"
            path.write_bytes(b"recording")

            with CallExecutionResult(path) as result:
                self.assertEqual(result.recording_path, path)

            self.assertFalse(path.exists())
            self.assertIsNone(result.recording_path)

            path.write_bytes(b"recording")
            with CallExecutionResult(path) as result:
                self.assertEqual(result.take_recording(), path)

            self.assertTrue(path.exists())
            path.unlink()

    async def test_real_mp3_contains_both_sides_and_caller_owns_output(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = CallRecorder(RecordingOptions(directory=Path(directory)))
            recorder.write(Track.CALLER, PcmChunk(0, pcm(2000, PCM.rate // 4)))
            recorder.write(Track.BOT, PcmChunk(0, pcm(3000, PCM.rate // 4)))
            exported = await recorder.finish()
            self.assertIsNotNone(exported)
            self.assertTrue(exported.exists())
            self.assertEqual(exported, recorder.take())
            await recorder.close()
            self.assertTrue(exported.exists(), "ownership transferred to caller")
            with av.open(str(exported)) as container:
                frames = list(container.decode(audio=0))
            self.assertTrue(frames)
            self.assertGreater(sum(frame.samples for frame in frames), PCM.rate // 5)
            exported.unlink()

    async def test_mixer_adds_tracks_and_clips_without_wraparound(self):
        encoder = Mp3Encoder(RecordingOptions(export_block_samples=4))
        left = BytesIO(pcm(20000, 4))
        right = BytesIO(pcm(20000, 4))
        mixed = array("h")
        mixed.frombytes(encoder._mix_block({Track.CALLER: left, Track.BOT: right}))
        self.assertEqual(list(mixed), [32767] * 4)

    async def test_no_audio_or_capture_failure_returns_no_recording(self):
        recorder = CallRecorder(RecordingOptions())
        self.assertIsNone(await recorder.finish())
        await recorder.close()

        recorder = CallRecorder(RecordingOptions())
        with patch.object(recorder._capture, "write", side_effect=BufferError("full")):
            recorder.write(Track.CALLER, PcmChunk(0, pcm(1000)))
        self.assertIsNone(await recorder.finish())
        await recorder.close()

    async def test_mp3_export_does_not_block_event_loop(self):
        started, release = Event(), Event()
        with tempfile.TemporaryDirectory() as directory:
            recorder = CallRecorder(RecordingOptions(directory=Path(directory)))
            recorder.write(Track.CALLER, PcmChunk(0, pcm(1000)))

            def slow_encode(tracks, path):
                started.set()
                release.wait(1)
                path.write_bytes(b"mp3 placeholder")

            with patch.object(recorder._encoder, "encode", side_effect=slow_encode):
                task = asyncio.create_task(recorder.finish())
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 0.5))
                    await asyncio.sleep(0.005)
                    self.assertFalse(task.done(), "export should still run on a worker thread")
                finally:
                    release.set()
                exported = await asyncio.wait_for(task, 0.5)
                self.assertEqual(exported, recorder.take())
                exported.unlink()
                await recorder.close()

    async def test_callback_delay_does_not_insert_false_silence(self):
        clock = FakeClock()
        capture = FakeRecordingCapture()
        with patch("whatsapp_chatbot_python.calls.audio.monotonic", side_effect=clock.monotonic):
            tap = RecordingTap(capture)
            tap.activate()
            tap.write(Track.BOT, pcm(1000))
            clock.now = 0.1  # Callback jitter is not a media gap.
            tap.write(Track.BOT, pcm(2000))
        self.assertEqual(capture.positions[1] - capture.positions[0], PCM.samples_per_frame)

    async def test_caller_media_timestamp_gap_is_kept_without_callback_jitter(self):
        class InputTrack:
            def __init__(self):
                self.index = 0

            async def recv(self):
                if self.index == 2:
                    raise MediaStreamError
                frame = av.AudioFrame(format="s16", layout="mono", samples=PCM.samples_per_frame)
                frame.planes[0].update(pcm(1000))
                frame.sample_rate = PCM.rate
                frame.pts = (0, PCM.rate // 10)[self.index]
                frame.time_base = Fraction(1, PCM.rate)
                self.index += 1
                return frame

        class Sink:
            async def write_audio(self, data):
                pass

        capture = FakeRecordingCapture()
        tap = RecordingTap(capture)
        tap.activate()
        await CallerSink(Sink(), tap, lambda event: None)._consume(InputTrack())
        positions = [chunk.position for track, chunk in capture.chunks if track == Track.CALLER]
        self.assertEqual(len(positions), 2)
        self.assertEqual(positions[1] - positions[0], PCM.rate // 10)

    async def test_first_caller_chunk_starts_before_it_is_received(self):
        clock = FakeClock()

        class InputTrack:
            def __init__(self):
                self.sent = False

            async def recv(self):
                if self.sent:
                    raise MediaStreamError
                self.sent = True
                clock.now = 0.6
                frame = av.AudioFrame(format="s16", layout="mono", samples=2 * PCM.samples_per_frame)
                frame.planes[0].update(pcm(1000, 2 * PCM.samples_per_frame))
                frame.sample_rate = PCM.rate
                frame.pts = 0
                frame.time_base = Fraction(1, PCM.rate)
                return frame

        class Sink:
            async def write_audio(self, data):
                pass

        capture = FakeRecordingCapture()
        with patch("whatsapp_chatbot_python.calls.audio.monotonic", side_effect=clock.monotonic):
            tap = RecordingTap(capture)
            tap.activate()
            await CallerSink(Sink(), tap, lambda event: None)._consume(InputTrack())

        self.assertEqual(capture.positions, [int(0.56 * PCM.rate)])

    async def test_48khz_resampler_handles_empty_and_partial_first_output(self):
        class InputTrack:
            def __init__(self):
                self.index = 0

            async def recv(self):
                sizes = (1, 959, 960)
                if self.index == len(sizes):
                    raise MediaStreamError

                samples = sizes[self.index]
                frame = av.AudioFrame(format="s16", layout="mono", samples=samples)
                frame.planes[0].update(pcm(1000, samples))
                frame.sample_rate = 48_000
                frame.pts = sum(sizes[:self.index])
                frame.time_base = Fraction(1, 48_000)
                clock.now = sum(sizes[:self.index + 1]) / 48_000
                self.index += 1
                return frame

        class Sink:
            async def write_audio(self, data):
                pass

        clock = FakeClock()
        capture = FakeRecordingCapture()
        with patch("whatsapp_chatbot_python.calls.audio.monotonic", side_effect=clock.monotonic):
            tap = RecordingTap(capture)
            tap.activate()
            await CallerSink(Sink(), tap, lambda event: None)._consume(InputTrack())

        self.assertEqual(len(capture.positions), 2)
        self.assertLessEqual(capture.positions[0], samples_at(0.001))
        self.assertEqual(capture.positions[1], samples_at(0.02))

    async def test_caller_without_timestamps_keeps_a_real_gap_but_ignores_small_jitter(self):
        capture = FakeRecordingCapture()
        with patch("whatsapp_chatbot_python.calls.audio.monotonic", return_value=0.0):
            tap = RecordingTap(capture)
            tap.activate()
            tap.write(Track.CALLER, pcm(1000), frame_start_at=0.0)
            tap.write(Track.CALLER, pcm(2000), frame_start_at=0.025)
            tap.write(Track.CALLER, pcm(3000), frame_start_at=0.5)

        self.assertEqual(capture.positions, [
            samples_at(0), samples_at(0.02), samples_at(0.5),
        ])
        self.assertEqual(tap.caller_estimated_gap_samples,
                         samples_at(0.5) - samples_at(0.04))

    async def test_caller_sink_preserves_a_gap_when_rtc_frames_have_no_pts(self):
        class InputTrack:
            def __init__(self):
                self.index = 0

            async def recv(self):
                if self.index == 2:
                    raise MediaStreamError
                clock.now = (0.02, 0.52)[self.index]
                self.index += 1
                frame = av.AudioFrame(format="s16", layout="mono", samples=PCM.samples_per_frame)
                frame.planes[0].update(pcm(1000))
                frame.sample_rate = PCM.rate
                return frame

        class Sink:
            async def write_audio(self, data):
                pass

        clock = FakeClock()
        capture = FakeRecordingCapture()
        with patch("whatsapp_chatbot_python.calls.audio.monotonic", side_effect=clock.monotonic):
            tap = RecordingTap(capture)
            tap.activate()
            await CallerSink(Sink(), tap, lambda event: None)._consume(InputTrack())

        self.assertEqual(capture.positions, [samples_at(0), samples_at(0.5)])

    async def test_caller_forward_timestamp_jump_does_not_create_an_hour_of_silence(self):
        capture = FakeRecordingCapture()
        with patch("whatsapp_chatbot_python.calls.audio.monotonic", return_value=0.0):
            tap = RecordingTap(capture)
            tap.activate()
            tap.write(Track.CALLER, pcm(1000), media_time=0.0, frame_start_at=0.0)
            tap.write(Track.CALLER, pcm(2000), media_time=3600.0, frame_start_at=0.5)
            tap.write(Track.CALLER, pcm(3000), media_time=3600.02, frame_start_at=0.52)

        self.assertEqual(capture.positions, [
            samples_at(0), samples_at(0.5), samples_at(0.52),
        ])
        self.assertEqual(tap.caller_timestamp_reanchors, 1)

    async def test_extreme_caller_timestamp_is_reanchored_before_sample_conversion(self):
        capture = FakeRecordingCapture()
        with patch("whatsapp_chatbot_python.calls.audio.monotonic", return_value=0.0):
            tap = RecordingTap(capture)
            tap.activate()
            tap.write(Track.CALLER, pcm(1000), media_time=0.0, frame_start_at=0.0)
            tap.write(Track.CALLER, pcm(2000), media_time=1e300, frame_start_at=0.5)

        self.assertEqual(capture.positions, [samples_at(0), samples_at(0.5)])
        self.assertEqual(tap.caller_timestamp_reanchors, 1)

    async def test_caller_forward_media_gap_matching_wall_time_is_preserved(self):
        capture = FakeRecordingCapture()
        clock = FakeClock()
        with patch("whatsapp_chatbot_python.calls.audio.monotonic", side_effect=clock.monotonic):
            tap = RecordingTap(capture)
            tap.activate()
            tap.write(Track.CALLER, pcm(1000), media_time=0.0, frame_start_at=0.0)
            clock.now = 2.0
            tap.write(Track.CALLER, pcm(2000), media_time=2.0, frame_start_at=2.0)

        self.assertEqual(capture.positions, [samples_at(0), samples_at(2)])
        self.assertEqual(tap.caller_timestamp_reanchors, 0)

    async def test_caller_keeps_wall_time_when_media_timestamps_disappear(self):
        capture = FakeRecordingCapture()
        with patch("whatsapp_chatbot_python.calls.audio.monotonic", return_value=0.0):
            tap = RecordingTap(capture)
            tap.activate()
            tap.write(Track.CALLER, pcm(1000), media_time=0.0, frame_start_at=0.0)
            tap.write(Track.CALLER, pcm(2000), frame_start_at=0.5)
            tap.write(Track.CALLER, pcm(3000), media_time=0.04, frame_start_at=0.52)
            tap.write(Track.CALLER, pcm(4000), media_time=0.14, frame_start_at=0.62)

        self.assertEqual(capture.positions, [
            samples_at(0), samples_at(0.5), samples_at(0.52), samples_at(0.62),
        ])

    async def test_bot_frame_pts_matches_recording_position_after_pause(self):
        class Source:
            def read_frame(self):
                return pcm(1000)

        clock = FakeClock()
        capture = FakeRecordingCapture()
        with patch("whatsapp_chatbot_python.calls.audio.monotonic", side_effect=clock.monotonic):
            tap = RecordingTap(capture)
            tap.activate()
            bot = BotTrack(Source(), tap, lambda event: None)
            try:
                first_frame = await bot.recv()
                clock.now = 0.5
                second_frame = await bot.recv()
            finally:
                bot.stop()

        self.assertEqual([first_frame.pts, second_frame.pts], capture.positions)

    async def test_bot_pts_and_recording_use_the_same_gap_with_different_origins(self):
        class Source:
            def read_frame(self):
                return pcm(1000)

        clock = FakeClock()
        capture = FakeRecordingCapture()
        with patch("whatsapp_chatbot_python.calls.audio.monotonic", side_effect=clock.monotonic):
            tap = RecordingTap(capture)
            tap.activate()
            clock.now = 0.1
            bot = BotTrack(Source(), tap, lambda event: None)
            try:
                first_frame = await bot.recv()
                clock.now = 0.5
                second_frame = await bot.recv()
            finally:
                bot.stop()

        self.assertEqual(capture.positions, [samples_at(0.1), samples_at(0.5)])
        self.assertEqual(second_frame.pts - first_frame.pts,
                         capture.positions[1] - capture.positions[0])

    async def test_mp3_keeps_bot_pause_and_caller_reply_in_their_recorded_order(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = PcmCapture(RecordingOptions(directory=Path(directory)))
            clock = FakeClock()
            with patch("whatsapp_chatbot_python.calls.audio.monotonic", side_effect=clock.monotonic):
                tap = RecordingTap(capture)
                tap.activate()
                tap.write(Track.BOT, tone(440), frame_start_at=0.0)
                tap.write(Track.CALLER, tone(660), frame_start_at=0.25)
                clock.now = 0.5
                tap.write(Track.BOT, tone(880), frame_start_at=0.5,
                          resumed_after_pause=True)

            try:
                output = Path(directory) / "ordered.mp3"
                Mp3Encoder(RecordingOptions()).encode(capture.seal(), output)
                with av.open(str(output)) as container:
                    decoded = array("h")
                    resampler = av.AudioResampler(format="s16", layout="mono", rate=PCM.rate)
                    for frame in container.decode(audio=0):
                        for mono in resampler.resample(frame):
                            decoded.frombytes(bytes(mono.planes[0])[:mono.samples * 2])
                    for mono in resampler.resample(None):
                        decoded.frombytes(bytes(mono.planes[0])[:mono.samples * 2])

                def frequency_strength(center, frequency):
                    start = round((center - 0.03) * PCM.rate)
                    samples = decoded[start:start + round(0.06 * PCM.rate)]
                    real = sum(value * math.cos(2 * math.pi * frequency * index / PCM.rate)
                               for index, value in enumerate(samples))
                    imaginary = sum(value * math.sin(2 * math.pi * frequency * index / PCM.rate)
                                    for index, value in enumerate(samples))
                    return math.hypot(real, imaginary)

                for center, expected_frequency in ((0.05, 440), (0.30, 660), (0.55, 880)):
                    other_frequencies = {440, 660, 880} - {expected_frequency}
                    self.assertGreater(
                        frequency_strength(center, expected_frequency),
                        3 * max(frequency_strength(center, other)
                                for other in other_frequencies),
                        f"wrong speaker or misplaced audio near {center} seconds",
                    )
            finally:
                capture.close()
