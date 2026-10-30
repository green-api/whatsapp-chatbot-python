from whatsapp_chatbot_python.calls.state_machine import CallStateMachine
import unittest

from whatsapp_chatbot_python.calls.models import (
    CallDirection,
    CallEndReason,
    CallEvent,
    CallSession,
    CallState,
    TERMINAL_STATES,
)
from time import monotonic


class CallStateMachineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fsm = CallStateMachine()
        self.session = CallSession("sender@c.us", "sender@c.us", "ru")

    def test_incoming_call_keeps_its_prompt(self) -> None:
        session = CallSession.incoming(
            sender_id="sender@c.us", call_id="call-1", language="adaptive",
            incoming_deadline=monotonic() + 1,
            prompt="Introduce yourself briefly.",
        )
        self.assertEqual(session.prompt, "Introduce yourself briefly.")

    def test_empty_prompt_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Call prompt must not be empty"):
            CallSession("sender@c.us", "sender@c.us", "adaptive", prompt="  ")

    def apply(self, event: CallEvent) -> CallState:
        self.fsm.apply(self.session, event)
        return self.session.state

    def test_successful_call_requires_remote_and_bridge(self) -> None:
        self.assertEqual(self.apply(CallEvent.ENQUEUED), CallState.QUEUED)
        self.assertEqual(self.apply(CallEvent.DEQUEUED), CallState.DIALING)
        self.assertEqual(self.apply(CallEvent.DIAL_ACCEPTED), CallState.RINGING)
        result = self.fsm.apply(self.session, CallEvent.BRIDGE_READY)
        self.assertTrue(result.applied)
        self.assertFalse(result.changed)
        self.assertEqual(self.session.state, CallState.RINGING)
        self.assertTrue(self.session.bridge_ready)
        self.assertEqual(self.apply(CallEvent.REMOTE_ACCEPTED), CallState.IN_CALL)
        self.assertEqual(self.apply(CallEvent.REMOTE_IDLE), CallState.REMOTE_ENDED)
        self.assertEqual(self.session.end_reason, CallEndReason.REMOTE_HANGUP)

    def test_remote_answer_before_bridge_uses_connecting_state(self) -> None:
        for event in (
            CallEvent.ENQUEUED,
            CallEvent.DEQUEUED,
            CallEvent.DIAL_ACCEPTED,
            CallEvent.REMOTE_ACCEPTED,
        ):
            self.apply(event)
        self.assertEqual(self.session.state, CallState.CONNECTING)
        self.assertEqual(self.apply(CallEvent.BRIDGE_READY), CallState.IN_CALL)

    def test_ring_timeout_has_separate_terminal_state(self) -> None:
        for event in (
            CallEvent.ENQUEUED,
            CallEvent.DEQUEUED,
            CallEvent.DIAL_ACCEPTED,
            CallEvent.RING_TIMER_EXPIRED,
        ):
            self.apply(event)
        self.assertEqual(self.session.state, CallState.ENDING)
        self.assertEqual(self.apply(CallEvent.HANGUP_CONFIRMED), CallState.RING_TIMEOUT)

    def test_talk_timeout_has_separate_terminal_state(self) -> None:
        for event in (
            CallEvent.ENQUEUED,
            CallEvent.DEQUEUED,
            CallEvent.DIAL_ACCEPTED,
            CallEvent.REMOTE_ACCEPTED,
            CallEvent.BRIDGE_READY,
            CallEvent.TALK_TIMER_EXPIRED,
        ):
            self.apply(event)
        self.assertEqual(self.session.state, CallState.ENDING)
        self.assertEqual(self.apply(CallEvent.HANGUP_CONFIRMED), CallState.TALK_TIMEOUT)

    def test_remote_hangup_after_answer(self) -> None:
        for event in (
            CallEvent.ENQUEUED,
            CallEvent.DEQUEUED,
            CallEvent.DIAL_ACCEPTED,
            CallEvent.REMOTE_ACCEPTED,
            CallEvent.BRIDGE_READY,
            CallEvent.REMOTE_IDLE,
        ):
            self.apply(event)
        self.assertEqual(self.session.state, CallState.REMOTE_ENDED)

    def test_remote_idle_is_rejected_before_answer(self) -> None:
        for event in (
            CallEvent.ENQUEUED,
            CallEvent.DEQUEUED,
            CallEvent.DIAL_ACCEPTED,
            CallEvent.REMOTE_IDLE,
        ):
            self.apply(event)
        self.assertEqual(self.session.state, CallState.REJECTED)

    def test_remote_idle_is_rejected_while_dialing(self) -> None:
        self.apply(CallEvent.ENQUEUED)
        self.apply(CallEvent.DEQUEUED)

        self.assertEqual(self.apply(CallEvent.REMOTE_IDLE), CallState.REJECTED)
        self.assertEqual(self.session.end_reason, CallEndReason.REMOTE_REJECTED)

    def test_remote_hangup_while_connecting(self) -> None:
        for event in (
            CallEvent.ENQUEUED,
            CallEvent.DEQUEUED,
            CallEvent.DIAL_ACCEPTED,
            CallEvent.REMOTE_ACCEPTED,
        ):
            self.apply(event)

        self.assertEqual(self.apply(CallEvent.REMOTE_IDLE), CallState.REMOTE_ENDED)
        self.assertEqual(self.session.end_reason, CallEndReason.REMOTE_HANGUP)

    def test_remote_idle_confirms_pending_timeout(self) -> None:
        for event in (
            CallEvent.ENQUEUED,
            CallEvent.DEQUEUED,
            CallEvent.DIAL_ACCEPTED,
            CallEvent.RING_TIMER_EXPIRED,
        ):
            self.apply(event)

        # The runner confirms the timeout after its remote finish operation.
        self.assertEqual(self.apply(CallEvent.REMOTE_IDLE), CallState.ENDING)
        self.assertEqual(self.apply(CallEvent.HANGUP_CONFIRMED), CallState.RING_TIMEOUT)

    def test_late_event_cannot_change_terminal_state(self) -> None:
        self.test_remote_idle_is_rejected_before_answer()
        result = self.fsm.apply(self.session, CallEvent.REMOTE_ACCEPTED)
        self.assertFalse(result.applied)
        self.assertFalse(result.changed)
        self.assertEqual(self.session.state, CallState.REJECTED)

    def test_unsupported_event_is_ignored(self) -> None:
        self.apply(CallEvent.ENQUEUED)
        result = self.fsm.apply(self.session, CallEvent.BRIDGE_READY)
        self.assertFalse(result.applied)
        self.assertFalse(result.changed)
        self.assertEqual(self.session.state, CallState.QUEUED)

    def test_internal_error_is_defined_for_every_nonterminal_state(self) -> None:
        for state in CallState:
            if state in TERMINAL_STATES:
                continue
            with self.subTest(state=state):
                session = CallSession("sender@c.us", "sender@c.us", "ru", state=state)
                result = self.fsm.apply(
                    session,
                    CallEvent.INTERNAL_ERROR,
                    error_code="test",
                )
                self.assertTrue(result.applied)
                self.assertEqual(session.state, CallState.FAILED)
                self.assertEqual(session.end_reason, CallEndReason.ERROR)

    def test_shutdown_has_explicit_end_reason(self) -> None:
        self.apply(CallEvent.ENQUEUED)
        result = self.fsm.apply(
            self.session,
            CallEvent.SHUTDOWN_REQUESTED,
            error_code="shutdown",
        )
        self.assertTrue(result.applied)
        self.assertEqual(self.session.state, CallState.CANCELLED)
        self.assertEqual(self.session.end_reason, CallEndReason.SHUTDOWN)

    def test_all_terminal_states_ignore_late_events(self) -> None:
        for state in TERMINAL_STATES:
            for event in CallEvent:
                with self.subTest(state=state, event=event):
                    session = CallSession(
                        "sender@c.us",
                        "sender@c.us",
                        "ru",
                        state=state,
                    )
                    result = self.fsm.apply(session, event)
                    self.assertFalse(result.applied)
                    self.assertEqual(session.state, state)

    def test_incoming_uses_existing_queue_and_answer_transitions(self) -> None:
        session = CallSession.incoming(
            sender_id="sender@c.us", call_id="call-1", language="ru",
            incoming_deadline=monotonic() + 60,
        )
        self.assertEqual(session.direction, CallDirection.INCOMING)
        self.fsm.apply(session, CallEvent.ENQUEUED)
        self.fsm.apply(session, CallEvent.DEQUEUED)
        self.assertEqual(session.state, CallState.RINGING)
        self.assertIsNotNone(session.started_at)
        self.assertIsNone(session.answered_at)

        self.fsm.apply(session, CallEvent.INCOMING_ACCEPTED)
        self.assertEqual(session.state, CallState.RINGING)
        self.assertTrue(session.incoming_accept_completed)
        self.assertIsNone(session.answered_at)

        self.fsm.apply(session, CallEvent.REMOTE_ACCEPTED)
        self.assertEqual(session.state, CallState.CONNECTING)
        self.fsm.apply(session, CallEvent.BRIDGE_READY)
        self.assertEqual(session.state, CallState.IN_CALL)
        self.assertIsNotNone(session.answered_at)

    def test_incoming_expiry_and_caller_left_from_queue(self) -> None:
        for event, state, reason in (
            (CallEvent.INCOMING_EXPIRED, CallState.REJECTED, CallEndReason.INCOMING_EXPIRED),
            (CallEvent.CALLER_LEFT, CallState.REJECTED, CallEndReason.CALLER_LEFT),
        ):
            with self.subTest(event=event):
                session = CallSession.incoming(
                    sender_id="sender@c.us", call_id="call-1", language="en",
                    incoming_deadline=monotonic() + 60,
                )
                self.fsm.apply(session, CallEvent.ENQUEUED)
                self.fsm.apply(session, event)
                self.assertEqual((session.state, session.end_reason), (state, reason))
                self.assertFalse(self.fsm.apply(session, CallEvent.DEQUEUED).applied)

    def test_incoming_only_events_do_not_change_outgoing_calls(self) -> None:
        self.apply(CallEvent.ENQUEUED)
        for event in (CallEvent.INCOMING_EXPIRED, CallEvent.CALLER_LEFT, CallEvent.INCOMING_ACCEPTED):
            self.assertFalse(self.fsm.apply(self.session, event).applied)
        self.assertEqual(self.session.state, CallState.QUEUED)


if __name__ == "__main__":
    unittest.main()
