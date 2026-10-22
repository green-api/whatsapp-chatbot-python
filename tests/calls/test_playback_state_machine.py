"""Playback policy at the PCM boundary, including deliberate red guarantees."""

from unittest import TestCase

from whatsapp_chatbot_python.calls.models import PCM, PlaybackOptions
from whatsapp_chatbot_python.calls.playback_state_machine import (
    AppendResult, PlaybackController, PlaybackEvent, PlaybackState, PlaybackStateMachine,
)


FRAME = bytes([1, 2]) * PCM.samples_per_frame


class PlaybackStateMachineTest(TestCase):
    def test_transitions_are_stateful_and_duplicate_events_are_safe(self):
        machine = PlaybackStateMachine()
        self.assertEqual(machine.apply(PlaybackEvent.SOFT_LIMIT), PlaybackState.BACKLOG)
        self.assertEqual(machine.apply(PlaybackEvent.SOFT_LIMIT), PlaybackState.BACKLOG)
        self.assertEqual(machine.apply(PlaybackEvent.RECOVERED), PlaybackState.PLAYING)
        self.assertEqual(machine.apply(PlaybackEvent.HARD_LIMIT), PlaybackState.DRAINING)
        self.assertEqual(machine.apply(PlaybackEvent.DRAINED), PlaybackState.PLAYING)
        self.assertEqual(machine.apply(PlaybackEvent.STALLED), PlaybackState.SUSPENDED)
        self.assertEqual(machine.apply(PlaybackEvent.RESUMED), PlaybackState.PLAYING)


class PlaybackControllerTest(TestCase):
    def make_controller(self):
        return PlaybackController(PlaybackOptions(
            soft_limit_seconds=PCM.frame_seconds,
            hard_limit_seconds=PCM.frame_seconds * 2,
        ))

    def test_soft_backlog_recovers_after_consumption(self):
        playback = self.make_controller()
        self.assertEqual(playback.append("item", 0, FRAME), AppendResult.ACCEPTED)
        self.assertEqual(playback.state, PlaybackState.BACKLOG)
        self.assertEqual(playback.read_frame(), FRAME)
        self.assertEqual(playback.state, PlaybackState.PLAYING)

    def test_fast_generation_keeps_a_30_second_response_complete(self):
        playback = PlaybackController(PlaybackOptions())
        expected = []

        frames_per_batch = 10
        batch_count = 150
        # Ten 20 ms frames arrive for each frame consumed; no wall-clock wait.
        for batch in range(batch_count):
            for offset in range(frames_per_batch):
                index = batch * frames_per_batch + offset
                data = index.to_bytes(2, "little") * PCM.samples_per_frame
                expected.append(data)
                self.assertEqual(playback.append("item", 0, data), AppendResult.ACCEPTED,
                                 f"audio stopped at frame {index}")

            self.assertEqual(playback.read_frame(), expected[batch])

        playback.complete_item("item", 0)
        remaining = [playback.read_frame() for _ in range(len(expected) - batch_count)]
        self.assertEqual(remaining, expected[batch_count:])
        self.assertEqual(playback.read_frame(), bytes(PCM.bytes_per_frame))
        self.assertEqual(playback.state, PlaybackState.PLAYING)

    def test_three_minute_response_fits_even_if_all_audio_arrives_first(self):
        playback = PlaybackController(PlaybackOptions())
        frame_count = 180 * 1000 // PCM.frame_ms

        for index in range(frame_count):
            data = index.to_bytes(2, "little") * PCM.samples_per_frame
            self.assertEqual(playback.append("item", 0, data), AppendResult.ACCEPTED,
                             f"audio stopped at frame {index}")

        playback.complete_item("item", 0)
        for index in range(frame_count):
            expected = index.to_bytes(2, "little") * PCM.samples_per_frame
            self.assertEqual(playback.read_frame(), expected, f"wrong frame {index}")

        self.assertEqual(playback.read_frame(), bytes(PCM.bytes_per_frame))
        self.assertEqual(playback.state, PlaybackState.PLAYING)

    def test_large_provider_chunk_is_split_without_losing_frames(self):
        playback = PlaybackController(PlaybackOptions())
        payload = FRAME * (30 * 1000 // PCM.frame_ms)
        self.assertEqual(playback.append("item", 0, payload), AppendResult.ACCEPTED)
        playback.complete_item("item", 0)
        for _ in range(30 * 1000 // PCM.frame_ms):
            self.assertEqual(playback.read_frame(), FRAME)
        self.assertEqual(playback.read_frame(), bytes(PCM.bytes_per_frame))

    def test_barge_in_discards_unheard_audio_and_reports_played_duration(self):
        playback = self.make_controller()
        self.assertEqual(playback.append("item", 0, FRAME * 2), AppendResult.ACCEPTED)
        self.assertEqual(playback.read_frame(), FRAME)
        self.assertEqual([(c.item_id, c.content_index, c.played_ms)
                          for c in playback.interrupt()], [("item", 0, PCM.frame_ms)])
        self.assertEqual(playback.read_frame(), bytes(PCM.bytes_per_frame))

    def test_partial_final_frame_is_padded_but_played_duration_is_exact(self):
        playback = self.make_controller()
        partial = FRAME[:100]
        self.assertEqual(playback.append("item", 0, partial), AppendResult.ACCEPTED)
        playback.finish_response()
        self.assertEqual(playback.read_frame(), partial + bytes(PCM.bytes_per_frame - 100))
        self.assertEqual(playback.interrupt(), [])

    def test_track_replacement_discards_old_buffer_and_reports_cut(self):
        playback = self.make_controller()
        playback.append("old-item", 2, FRAME * 2)
        self.assertEqual(playback.read_frame(), FRAME)
        cut = playback.replace_track()
        self.assertEqual([(c.item_id, c.content_index, c.played_ms) for c in cut],
                         [("old-item", 2, PCM.frame_ms)])
        self.assertEqual(playback.read_frame(), bytes(PCM.bytes_per_frame))
        self.assertEqual(playback.append("new-item", 0, FRAME), AppendResult.ACCEPTED)
        self.assertEqual(playback.read_frame(), FRAME)

    def test_hard_limit_drains_already_accepted_frames_before_truncation(self):
        """A slow caller must still hear audio that the bot already accepted."""
        playback = self.make_controller()
        self.assertEqual(playback.append("item", 0, FRAME), AppendResult.ACCEPTED)
        self.assertEqual(playback.append("item", 0, FRAME), AppendResult.ACCEPTED)
        self.assertEqual(playback.append("item", 0, FRAME), AppendResult.OVERFLOW)
        self.assertEqual(playback.state, PlaybackState.DRAINING)
        self.assertEqual(playback.read_frame(), FRAME)
        self.assertEqual(playback.read_frame(), FRAME)
        self.assertEqual(playback.state, PlaybackState.PLAYING)
        self.assertEqual(playback.read_frame(), bytes(PCM.bytes_per_frame))

    def test_overflow_keeps_the_prefix_that_still_fits(self):
        playback = self.make_controller()
        first = b"\x01\x00" * PCM.samples_per_frame
        second = b"\x02\x00" * PCM.samples_per_frame
        third = b"\x03\x00" * PCM.samples_per_frame

        self.assertEqual(playback.append("item", 0, first), AppendResult.ACCEPTED)
        self.assertEqual(playback.append("item", 0, second + third), AppendResult.OVERFLOW)
        self.assertEqual(playback.state, PlaybackState.DRAINING)
        self.assertEqual(playback.read_frame(), first)
        self.assertEqual(playback.read_frame(), second)
        self.assertEqual(playback.read_frame(), bytes(PCM.bytes_per_frame))
        self.assertEqual(playback.state, PlaybackState.PLAYING)
        self.assertEqual(playback.take_drained_cuts()[0].played_ms, 2 * PCM.frame_ms)

    def test_drop_chunks_policy_accepts_new_audio_after_space_is_freed(self):
        playback = PlaybackController(PlaybackOptions(
            soft_limit_seconds=PCM.frame_seconds,
            hard_limit_seconds=PCM.frame_seconds * 2,
            overflow_policy="drop_chunks",
        ))
        frames = [bytes([index, 0]) * PCM.samples_per_frame for index in range(4)]

        self.assertEqual(playback.append("item", 0, frames[0] + frames[1]),
                         AppendResult.ACCEPTED)
        self.assertEqual(playback.append("item", 0, frames[2]), AppendResult.DROPPED)
        self.assertEqual(playback.read_frame(), frames[0])
        self.assertEqual(playback.append("item", 0, frames[3]), AppendResult.ACCEPTED)
        playback.complete_item("item", 0)
        self.assertEqual([playback.read_frame(), playback.read_frame()],
                         [frames[1], frames[3]])
        self.assertEqual(playback.read_frame(), bytes(PCM.bytes_per_frame))

    def test_drop_chunks_keeps_capacity_and_marks_history_for_deletion(self):
        playback = PlaybackController(PlaybackOptions(
            soft_limit_seconds=PCM.frame_seconds,
            hard_limit_seconds=PCM.frame_seconds * 2,
            overflow_policy="drop_chunks",
        ))
        half_frame = FRAME[:PCM.bytes_per_frame // 2]
        self.assertEqual(playback.append("first", 0, half_frame), AppendResult.ACCEPTED)
        self.assertEqual(playback.append("second", 0, FRAME * 2), AppendResult.DROPPED)
        self.assertLessEqual(playback._buffered_bytes, PCM.bytes_per_frame * 2)
        self.assertEqual(playback.read_frame(), half_frame + bytes(len(half_frame)))
        self.assertEqual(playback.read_frame(), FRAME)
        playback.mark_finished_response({("second", 0)})
        self.assertEqual(playback.take_items_to_delete(), {"second"})
        self.assertEqual(playback.interrupt(), [])
        self.assertEqual(playback.take_items_to_delete(), set())

    def test_completed_item_counters_are_released_after_playback(self):
        playback = self.make_controller()
        playback.append("item", 0, FRAME)
        playback.complete_item("item", 0)
        self.assertIn(("item", 0), playback._received_samples)
        self.assertEqual(playback.read_frame(), FRAME)
        self.assertNotIn(("item", 0), playback._received_samples)
        self.assertNotIn(("item", 0), playback._played_samples)
