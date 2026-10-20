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

    def test_completed_item_counters_are_released_after_playback(self):
        playback = self.make_controller()
        playback.append("item", 0, FRAME)
        playback.complete_item("item", 0)
        self.assertIn(("item", 0), playback._received)
        self.assertEqual(playback.read_frame(), FRAME)
        self.assertNotIn(("item", 0), playback._received)
        self.assertNotIn(("item", 0), playback._played)
