from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import monotonic
from enum import StrEnum
from math import isfinite
from pathlib import Path
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CallState(StrEnum):
    TRIGGERED = "triggered"
    QUEUED = "queued"
    CANCELLED = "cancelled"
    DIALING = "dialing"
    RINGING = "ringing"
    CONNECTING = "connecting"
    IN_CALL = "in_call"
    ENDING = "ending"
    REJECTED = "rejected"
    RING_TIMEOUT = "ring_timeout"
    REMOTE_ENDED = "remote_ended"
    TALK_TIMEOUT = "talk_timeout"
    FAILED = "failed"


class CallDirection(StrEnum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"


class CallEvent(StrEnum):
    ENQUEUED = "enqueued"
    DEQUEUED = "dequeued"
    CANCEL_REQUESTED = "cancel_requested"
    DIAL_ACCEPTED = "dial_accepted"
    REMOTE_ACCEPTED = "remote_accepted"
    BRIDGE_READY = "bridge_ready"
    REMOTE_IDLE = "remote_idle"
    RING_TIMER_EXPIRED = "ring_timer_expired"
    TALK_TIMER_EXPIRED = "talk_timer_expired"
    HANGUP_CONFIRMED = "hangup_confirmed"
    SHUTDOWN_REQUESTED = "shutdown_requested"
    INTERNAL_ERROR = "internal_error"
    INCOMING_ACCEPTED = "incoming_accepted"
    INCOMING_EXPIRED = "incoming_expired"
    CALLER_LEFT = "caller_left"


class CallEndReason(StrEnum):
    REMOTE_HANGUP = "remote_hangup"
    REMOTE_REJECTED = "remote_rejected"
    RING_TIMEOUT = "ring_timeout"
    TALK_TIMEOUT = "talk_timeout"
    ERROR = "error"
    SHUTDOWN = "shutdown"
    USER_CANCELLED = "user_cancelled"
    INCOMING_EXPIRED = "incoming_expired"
    CALLER_LEFT = "caller_left"


class TransitionOutcome(StrEnum):
    APPLIED = "applied"
    IGNORED = "ignored"


TERMINAL_STATES = frozenset(
    {
        CallState.REJECTED,
        CallState.RING_TIMEOUT,
        CallState.REMOTE_ENDED,
        CallState.TALK_TIMEOUT,
        CallState.FAILED,
        CallState.CANCELLED,
    }
)

WAITING_STATES = frozenset(
    {
        CallState.TRIGGERED,
        CallState.QUEUED,
        CallState.DIALING,
        CallState.RINGING,
        CallState.CONNECTING,
    }
)

ACTIVE_STATES = frozenset({CallState.IN_CALL, CallState.ENDING})


@dataclass(slots=True)
class CallSession:
    sender_id: str
    chat_id: str
    language: str
    session_id: str = field(default_factory=lambda: str(uuid4()))
    state: CallState = CallState.TRIGGERED
    call_id: str | None = None
    remote_reason: str | None = None
    end_reason: CallEndReason | None = None
    error_code: str | None = None
    triggered_at: datetime = field(default_factory=utc_now)
    queued_at: datetime | None = None
    dialing_at: datetime | None = None
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    bridge_ready: bool = False
    remote_accepted: bool = False
    direction: CallDirection = CallDirection.OUTGOING
    incoming_deadline: float | None = None
    greeting_text: str | None = None
    incoming_accept_completed: bool = False

    @classmethod
    def incoming(
        cls, *, sender_id: str, call_id: str, language: str,
        incoming_deadline: float, chat_id: str | None = None,
        greeting_text: str | None = None,
    ) -> CallSession:
        if not sender_id or not call_id:
            raise ValueError("Incoming calls require sender_id and call_id")

        if not isfinite(incoming_deadline) or incoming_deadline <= monotonic():
            raise ValueError("Incoming call deadline has expired")

        return cls(
            sender_id=sender_id, chat_id=chat_id or sender_id, language=language,
            call_id=call_id, direction=CallDirection.INCOMING,
            incoming_deadline=incoming_deadline, greeting_text=greeting_text,
        )


@dataclass(frozen=True, slots=True)
class StateTransition:
    previous: CallState
    current: CallState
    event: CallEvent
    outcome: TransitionOutcome

    @property
    def applied(self) -> bool:
        return self.outcome == TransitionOutcome.APPLIED

    @property
    def changed(self) -> bool:
        return self.current != self.previous


@dataclass(frozen=True, slots=True)
class CallExecutionResult:
    recording_path: Path | None = None
