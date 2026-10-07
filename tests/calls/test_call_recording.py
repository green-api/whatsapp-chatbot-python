from array import array
from unittest.mock import patch
from queue import Full
from whatsapp_chatbot_python.calls.recording import CallRecorder, RATE
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

    def test_no_audio_or_queue_overflow_disables_recording(self) -> None:
        recorder = CallRecorder(logging.getLogger("recording-test"))

        self.assertIsNone(recorder.finish())

        recorder = CallRecorder(logging.getLogger("recording-test"))

        recorder.set_generation(1)
        recorder.start()

        with patch.object(recorder._queue, "put_nowait", side_effect=Full):
            recorder.add_bot(pcm(1000, 480), 1)

        self.assertIsNone(recorder.finish())
