from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
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


class CallEndReason(StrEnum):
    REMOTE_HANGUP = "remote_hangup"
    REMOTE_REJECTED = "remote_rejected"
    RING_TIMEOUT = "ring_timeout"
    TALK_TIMEOUT = "talk_timeout"
    ERROR = "error"
    SHUTDOWN = "shutdown"
    USER_CANCELLED = "user_cancelled"


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
