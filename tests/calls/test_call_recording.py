from array import array
from unittest.mock import patch
from queue import Full
from whatsapp_chatbot_python.calls.recording import CallRecorder, MP3_BIT_RATE, RATE
import logging
import unittest
import av


def pcm(value: int, samples: int) -> bytes:
    return array("h", [value] * samples).tobytes()


class CallRecordingTest(unittest.TestCase):
    def test_mixes_both_sides_and_keeps_pause_and_reconnect(self) -> None:
        clock = [0.0]
        recorder = CallRecorder(logging.getLogger("recording-test"))

        with patch("whatsapp_chatbot_python.calls.recording.monotonic", side_effect=lambda: clock[0]):
            recorder.set_generation(1)
            recorder.start()

            clock[0] = 0.1

            recorder.add_caller(pcm(4000, RATE // 10), 1)  # capture starts at zero

            clock[0] = 0.0

            recorder.add_bot(pcm(3000, RATE // 10), 1)
            recorder.set_generation(2)

            clock[0] = 0.2

            recorder.add_bot(pcm(10000, RATE // 10), 1)  # old bridge ignored
            recorder.add_bot(pcm(5000, RATE // 10), 2)

            path = recorder.finish()

        self.assertIsNotNone(path)

        try:
            with av.open(str(path)) as container:
                self.assertEqual(container.streams.audio[0].bit_rate, MP3_BIT_RATE)
                frames = [frame for packet in container.demux(audio=0) for frame in packet.decode()]

            converted = av.AudioResampler(format="s16", layout="mono", rate=RATE)
            samples = array("h")

            for frame in frames:
                for output in converted.resample(frame):
                    samples.frombytes(bytes(output.planes[0])[:output.samples * 2])

            # MP3 is lossy; check broad levels in each time segment.
            def level(start, end):
                section = samples[int(start * RATE):int(end * RATE)]
                return sum(section) / len(section)

            self.assertGreater(level(0.03, 0.07), 5500)
            self.assertLess(abs(level(0.13, 0.17)), 500)
            self.assertGreater(level(0.23, 0.27), 3500)
            self.assertLess(level(0.23, 0.27), 8000)
        finally:
            path.unlink(missing_ok=True)

    def test_frame_jitter_does_not_split_audio_but_media_gap_is_preserved(self) -> None:
        clock = [0.0]
        recorder = CallRecorder(logging.getLogger("recording-test"))
        tracks = {}

        def capture(_path):
            for side in ("caller", "bot"):
                stream = getattr(recorder, f"_{side}")
                stream.seek(0)
                tracks[side] = array("h")
                tracks[side].frombytes(stream.read())

        with patch("whatsapp_chatbot_python.calls.recording.monotonic", side_effect=lambda: clock[0]), \
             patch.object(recorder, "_encode", side_effect=capture):
            recorder.set_generation(1)
            recorder.start()
            recorder.add_bot(pcm(3000, 480), 1)
            clock[0] = 0.045  # A delayed callback must not insert 25 ms of silence.
            recorder.add_bot(pcm(4000, 480), 1)

            clock[0] = 0.02
            recorder.add_caller(pcm(1000, 480), 1, media_time=10.0)
            clock[0] = 0.065
            recorder.add_caller(pcm(2000, 480), 1, media_time=10.02)
            clock[0] = 0.075
            recorder.add_caller(pcm(3000, 480), 1, media_time=10.04)
            clock[0] = 0.15
            recorder.add_caller(pcm(4000, 480), 1, media_time=10.10)

            recorder.set_generation(2)
            clock[0] = 0.22
            recorder.add_caller(pcm(5000, 480), 2, media_time=0.0)
            path = recorder.finish()

        self.assertIsNotNone(path)
        try:
            self.assertEqual(tracks["bot"], array("h", [3000] * 480 + [4000] * 480))
            self.assertEqual(tracks["caller"][:1440], array("h", [1000] * 480 + [2000] * 480 + [3000] * 480))
            self.assertEqual(tracks["caller"][1440:2400], array("h", [0] * 960))
            self.assertEqual(tracks["caller"][2400:2880], array("h", [4000] * 480))
            self.assertEqual(tracks["caller"][4800:5280], array("h", [5000] * 480))
        finally:
            path.unlink(missing_ok=True)

    def test_no_audio_or_queue_overflow_disables_recording(self) -> None:
        recorder = CallRecorder(logging.getLogger("recording-test"))

        self.assertIsNone(recorder.finish())

        recorder = CallRecorder(logging.getLogger("recording-test"))

        recorder.set_generation(1)
        recorder.start()

        with patch.object(recorder._queue, "put_nowait", side_effect=Full):
            recorder.add_bot(pcm(1000, 480), 1)

        self.assertIsNone(recorder.finish())
