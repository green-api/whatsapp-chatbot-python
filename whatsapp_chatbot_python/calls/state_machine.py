from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass

from .models import (
    CallEndReason,
    CallEvent,
    CallSession,
    CallState,
    StateTransition,
    TERMINAL_STATES,
    TransitionOutcome,
    utc_now,
)


TransitionTarget = CallState | Callable[[CallSession], CallState]


@dataclass(frozen=True, slots=True)
class TransitionRule:
    target: TransitionTarget

    def resolve(self, session: CallSession) -> CallState:
        if callable(self.target):
            return self.target(session)

        return self.target


def _bridge_ready_while_ringing(session: CallSession) -> CallState:
    if session.remote_accepted:
        return CallState.IN_CALL

    return CallState.RINGING


def _remote_accepted_while_ringing(session: CallSession) -> CallState:
    if session.bridge_ready:
        return CallState.IN_CALL

    return CallState.CONNECTING


def _ending_target(session: CallSession) -> CallState:
    return {
        CallEndReason.RING_TIMEOUT: CallState.RING_TIMEOUT,
        CallEndReason.TALK_TIMEOUT: CallState.TALK_TIMEOUT,
    }.get(session.end_reason, CallState.FAILED)


_TRANSITION_RULES: dict[tuple[CallState, CallEvent], TransitionRule] = {
    (CallState.TRIGGERED, CallEvent.ENQUEUED): TransitionRule(CallState.QUEUED),
    (CallState.QUEUED, CallEvent.DEQUEUED): TransitionRule(CallState.DIALING),
    (CallState.QUEUED, CallEvent.CANCEL_REQUESTED): TransitionRule(CallState.CANCELLED),
    (CallState.DIALING, CallEvent.DIAL_ACCEPTED): TransitionRule(CallState.RINGING),
    (CallState.DIALING, CallEvent.REMOTE_IDLE): TransitionRule(CallState.REJECTED),

    (CallState.RINGING, CallEvent.BRIDGE_READY): TransitionRule(
        _bridge_ready_while_ringing
    ),

    (CallState.RINGING, CallEvent.REMOTE_ACCEPTED): TransitionRule(
        _remote_accepted_while_ringing
    ),

    (CallState.RINGING, CallEvent.REMOTE_IDLE): TransitionRule(CallState.REJECTED),

    (CallState.RINGING, CallEvent.RING_TIMER_EXPIRED): TransitionRule(
        CallState.ENDING
    ),

    (CallState.CONNECTING, CallEvent.BRIDGE_READY): TransitionRule(CallState.IN_CALL),

    # The remote party already answered; report a completed call even if the
    # bridge never became ready. No recording is created until IN_CALL.
    (CallState.CONNECTING, CallEvent.REMOTE_IDLE): TransitionRule(
        CallState.REMOTE_ENDED
    ),

    (CallState.CONNECTING, CallEvent.TALK_TIMER_EXPIRED): TransitionRule(
        CallState.ENDING
    ),

    (CallState.IN_CALL, CallEvent.REMOTE_IDLE): TransitionRule(CallState.REMOTE_ENDED),
    (CallState.IN_CALL, CallEvent.TALK_TIMER_EXPIRED): TransitionRule(CallState.ENDING),
    (CallState.ENDING, CallEvent.HANGUP_CONFIRMED): TransitionRule(_ending_target),
    (CallState.ENDING, CallEvent.REMOTE_IDLE): TransitionRule(_ending_target),
}


for nonterminal_state in CallState:
    if nonterminal_state in TERMINAL_STATES:
        continue

    for failure_event in (
        CallEvent.INTERNAL_ERROR,
        CallEvent.SHUTDOWN_REQUESTED,
    ):
        _TRANSITION_RULES[(nonterminal_state, failure_event)] = TransitionRule(
            CallState.FAILED
        )


class CallStateMachine:
    """
    Apply the formal call transition table and its associated session data.

    Duplicate, late and out-of-order events are intentionally ignored. An applied
    event may update session data without changing state, for example an early
    BRIDGE_READY event received while the remote side is still ringing.
    """

    def apply(
        self,
        session: CallSession,
        event: CallEvent,
        *,
        remote_reason: str | None = None,
        error_code: str | None = None,
    ) -> StateTransition:
        previous = session.state

        if previous in TERMINAL_STATES:
            return self._ignored(previous, event)

        rule = _TRANSITION_RULES.get((previous, event))

        if rule is None:
            return self._ignored(previous, event)

        if remote_reason is not None:
            session.remote_reason = remote_reason

        if error_code is not None:
            session.error_code = error_code

        self._apply_event_data(session, event)

        target = rule.resolve(session)

        session.state = target

        if target in TERMINAL_STATES:
            session.ended_at = utc_now()

        return StateTransition(
            previous=previous,
            current=target,
            event=event,
            outcome=TransitionOutcome.APPLIED,
        )

    @staticmethod
    def _ignored(previous: CallState, event: CallEvent) -> StateTransition:
        return StateTransition(
            previous=previous,
            current=previous,
            event=event,
            outcome=TransitionOutcome.IGNORED,
        )

    @staticmethod
    def _apply_event_data(session: CallSession, event: CallEvent) -> None:
        now = utc_now()

        if event == CallEvent.ENQUEUED:
            session.queued_at = now
        elif event == CallEvent.DEQUEUED:
            session.dialing_at = now
        elif event == CallEvent.CANCEL_REQUESTED:
            session.end_reason = CallEndReason.USER_CANCELLED
        elif event == CallEvent.REMOTE_ACCEPTED:
            session.remote_accepted = True
            session.answered_at = session.answered_at or now
        elif event == CallEvent.BRIDGE_READY:
            session.bridge_ready = True
        elif event == CallEvent.RING_TIMER_EXPIRED:
            session.end_reason = CallEndReason.RING_TIMEOUT
        elif event == CallEvent.TALK_TIMER_EXPIRED:
            session.end_reason = CallEndReason.TALK_TIMEOUT
        elif event == CallEvent.REMOTE_IDLE:
            CallStateMachine._apply_remote_idle(session)
        elif event == CallEvent.SHUTDOWN_REQUESTED:
            session.end_reason = CallEndReason.SHUTDOWN
        elif event == CallEvent.INTERNAL_ERROR:
            session.end_reason = CallEndReason.ERROR

    @staticmethod
    def _apply_remote_idle(session: CallSession) -> None:
        if session.state in {CallState.DIALING, CallState.RINGING}:
            session.end_reason = CallEndReason.REMOTE_REJECTED
        elif session.state in {CallState.CONNECTING, CallState.IN_CALL}:
            session.end_reason = CallEndReason.REMOTE_HANGUP
