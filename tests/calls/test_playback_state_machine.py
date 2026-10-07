import unittest

from whatsapp_chatbot_python.calls.playback_state_machine import (
    PlaybackEvent,
    PlaybackState,
    PlaybackStateMachine,
)


class PlaybackStateMachineTest(unittest.TestCase):
    def test_allowed_transitions(self):
        fsm = PlaybackStateMachine()

        transitions = [
            (PlaybackState.PLAYING, PlaybackEvent.SOFT_LIMIT, PlaybackState.BACKLOG),
            (PlaybackState.BACKLOG, PlaybackEvent.RECOVERED, PlaybackState.PLAYING),
            (PlaybackState.PLAYING, PlaybackEvent.HARD_LIMIT, PlaybackState.DRAINING),
            (PlaybackState.BACKLOG, PlaybackEvent.HARD_LIMIT, PlaybackState.DRAINING),
            (PlaybackState.DRAINING, PlaybackEvent.DRAINED, PlaybackState.PLAYING),
            (PlaybackState.SUSPENDED, PlaybackEvent.RESUMED, PlaybackState.PLAYING),
        ]

        for state in (PlaybackState.PLAYING, PlaybackState.BACKLOG, PlaybackState.DRAINING):
            transitions.extend([
                (state, PlaybackEvent.STALLED, PlaybackState.SUSPENDED),
                (state, PlaybackEvent.INTERRUPTED, PlaybackState.PLAYING),
            ])

        for state in PlaybackState:
            transitions.append((state, PlaybackEvent.TRACK_REPLACED, PlaybackState.PLAYING))

        for previous, event, expected in transitions:
            with self.subTest(previous=previous, event=event):
                self.assertEqual(fsm.apply(previous, event), expected)

    def test_duplicate_and_out_of_order_events_are_ignored(self):
        fsm = PlaybackStateMachine()

        self.assertEqual(fsm.apply(PlaybackState.DRAINING, PlaybackEvent.SOFT_LIMIT),
                         PlaybackState.DRAINING)

        self.assertEqual(fsm.apply(PlaybackState.SUSPENDED, PlaybackEvent.DRAINED),
                         PlaybackState.SUSPENDED)
