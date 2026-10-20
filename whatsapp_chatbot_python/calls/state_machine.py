"""Pure transition planning followed by a single, explicit commit."""
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .models import (
    utc_now,
    CallDirection,
    CallEndReason as Reason,
    CallEvent as Event,
    CallSession,
    CallState as State,
    StateTransition,
    TERMINAL_STATES,
)


_FINAL_STATE = {
    Reason.RING_TIMEOUT: State.RING_TIMEOUT,
    Reason.TALK_TIMEOUT: State.TALK_TIMEOUT,
    Reason.SHUTDOWN: State.CANCELLED,
    Reason.USER_CANCELLED: State.CANCELLED,
}


@dataclass(frozen=True)
class Change:
    target: State
    reason: Reason | None = None
    queued: bool = False
    started: bool = False
    answered: bool = False
    bridge: bool = False
    accepted: bool = False


Resolver = Callable[[CallSession], Change]


@dataclass(frozen=True)
class TransitionRule:
    change: Change | Resolver
    direction: CallDirection | None = None

    def resolve(self, session: CallSession) -> Change:
        return self.change(session) if callable(self.change) else self.change


def _dequeue(session: CallSession) -> Change:
    target = {
        CallDirection.INCOMING: State.RINGING,
        CallDirection.OUTGOING: State.DIALING,
    }[session.direction]

    return Change(target, started=True)


def _answer(session: CallSession) -> Change:
    return Change(
        State.IN_CALL if session.bridge_ready else State.CONNECTING,
        answered=True,
    )


def _bridge(session: CallSession) -> Change:
    target = State.IN_CALL if session.remote_accepted else session.state
    return Change(target, bridge=True)


def _idle(session: CallSession) -> Change:
    if session.remote_accepted:
        return Change(
            State.REMOTE_ENDED,
            Reason.REMOTE_HANGUP,
        )

    return Change(State.REJECTED, Reason.REMOTE_REJECTED)


def _hangup(session: CallSession) -> Change:
    return Change(_FINAL_STATE.get(session.end_reason, State.FAILED))


def _rules() -> dict[tuple[State, Event], TransitionRule]:
    rules = {
        (State.TRIGGERED, Event.ENQUEUED): TransitionRule(Change(State.QUEUED, queued=True)),
        (State.QUEUED, Event.DEQUEUED): TransitionRule(_dequeue),
        (State.DIALING, Event.DIAL_ACCEPTED): TransitionRule(Change(State.RINGING)),
        (State.ENDING, Event.HANGUP_CONFIRMED): TransitionRule(_hangup),
        (State.IN_CALL, Event.TALK_TIMER_EXPIRED): TransitionRule(Change(State.ENDING, Reason.TALK_TIMEOUT)),
    }

    active = (State.DIALING, State.RINGING, State.CONNECTING, State.IN_CALL)

    for state in active:
        rules[state, Event.BRIDGE_READY] = TransitionRule(_bridge)
        rules[state, Event.REMOTE_IDLE] = TransitionRule(_idle)
        rules[state, Event.INCOMING_ACCEPTED] = TransitionRule(Change(state, accepted=True), CallDirection.INCOMING)

    for state in (State.DIALING, State.RINGING):
        rules[state, Event.REMOTE_ACCEPTED] = TransitionRule(_answer)
        rules[state, Event.RING_TIMER_EXPIRED] = TransitionRule(Change(State.ENDING, Reason.RING_TIMEOUT))

    for state in set(State) - TERMINAL_STATES:
        rules[state, Event.INTERNAL_ERROR] = TransitionRule(Change(State.FAILED, Reason.ERROR))
        rules[state, Event.SHUTDOWN_REQUESTED] = TransitionRule(Change(State.CANCELLED, Reason.SHUTDOWN))
        rules[state, Event.CANCEL_REQUESTED] = TransitionRule(Change(State.CANCELLED, Reason.USER_CANCELLED))

    for state in (State.TRIGGERED, State.QUEUED, State.RINGING):
        rules[state, Event.INCOMING_EXPIRED] = TransitionRule(
            Change(State.REJECTED, Reason.INCOMING_EXPIRED),
            CallDirection.INCOMING,
        )

        rules[state, Event.CALLER_LEFT] = TransitionRule(
            Change(State.REJECTED, Reason.CALLER_LEFT),
            CallDirection.INCOMING,
        )

    return rules


_RULES = _rules()


class CallStateMachine:
    def apply(
        self,
        session: CallSession,
        event: Event,
        *,
        remote_reason: str | None = None,
        error_code: str | None = None,
    ) -> StateTransition:
        rule = self._find_rule(session, event)

        if rule is None:
            return StateTransition(session.state, session.state, event, False)

        change = rule.resolve(session)

        return self._commit(session, event, change, utc_now(), remote_reason, error_code)

    @staticmethod
    def _find_rule(session: CallSession, event: Event) -> TransitionRule | None:
        rule = _RULES.get((session.state, event))

        if rule is None or rule.direction not in (None, session.direction):
            return None

        return rule

    @staticmethod
    def _commit(
        session: CallSession,
        event: Event,
        change: Change,
        now: datetime, remote_reason: str | None,
        error_code: str | None,
    ) -> StateTransition:
        previous = session.state
        session.state = change.target
        session.end_reason = change.reason or session.end_reason
        session.remote_reason = remote_reason if remote_reason is not None else session.remote_reason
        session.error_code = error_code if error_code is not None else session.error_code

        if change.queued:
            session.queued_at = session.queued_at or now

        if change.started:
            session.started_at = session.started_at or now

        if change.answered:
            session.remote_accepted = True
            session.answered_at = session.answered_at or now

        session.bridge_ready |= change.bridge
        session.incoming_accept_completed |= change.accepted

        if session.finished:
            session.ended_at = session.ended_at or now

        return StateTransition(previous, session.state, event, True)
