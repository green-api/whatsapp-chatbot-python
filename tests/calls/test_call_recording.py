"""MP3, PCM timing, and recording ownership guarantees."""

import asyncio
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

from whatsapp_chatbot_python.calls.audio import CallerSink, RecordingTap
from whatsapp_chatbot_python.calls.models import CallExecutionResult, PCM, PcmChunk, RecordingOptions
from whatsapp_chatbot_python.calls.recorder import CallRecorder, Mp3Encoder, Track


def pcm(value, samples=PCM.samples_per_frame):
    return array("h", [value] * samples).tobytes()


class RecordingTest(unittest.IsolatedAsyncioTestCase):
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
        class Capture:
            def __init__(self):
                self.chunks = []

            def write(self, track, chunk):
                self.chunks.append(chunk)

        clock = [0.0]
        capture = Capture()
        with patch("whatsapp_chatbot_python.calls.audio.monotonic", side_effect=lambda: clock[0]):
            tap = RecordingTap(capture)
            tap.activate()
            tap.write(Track.BOT, pcm(1000))
            clock[0] = 0.1  # Callback jitter is not a media gap.
            tap.write(Track.BOT, pcm(2000))
        self.assertEqual(capture.chunks[1].position - capture.chunks[0].position,
                         PCM.samples_per_frame)

    async def test_caller_media_timestamp_gap_is_kept_without_callback_jitter(self):
        """RED: a real media gap must be represented in the captured timeline."""
        class Capture:
            def __init__(self):
                self.chunks = []

            def write(self, track, chunk):
                self.chunks.append((track, chunk))

            def disable(self, error):
                raise AssertionError(error)

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

        capture = Capture()
        tap = RecordingTap(capture)
        tap.activate()
        await CallerSink(Sink(), tap, lambda event: None)._consume(InputTrack())
        positions = [chunk.position for track, chunk in capture.chunks if track == Track.CALLER]
        self.assertEqual(len(positions), 2)
        self.assertEqual(positions[1] - positions[0], PCM.rate // 10)
