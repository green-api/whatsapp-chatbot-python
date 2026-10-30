"""Dependency-free contracts. Times used for deadlines are monotonic seconds."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from math import isfinite
from pathlib import Path
from time import monotonic
from typing import Callable, Literal, Protocol
from uuid import uuid4


PCM16_SAMPLE_BITS = 16
PCM16_SAMPLE_BYTES = PCM16_SAMPLE_BITS // 8
PCM16_MIN_VALUE = -(1 << (PCM16_SAMPLE_BITS - 1))
PCM16_MAX_VALUE = (1 << (PCM16_SAMPLE_BITS - 1)) - 1


class CallDirection(str, Enum):
    INCOMING = "incoming"
    OUTGOING = "outgoing"


class InputOverflowPolicy(str, Enum):
    DROP_OLDEST = "drop_oldest"
    FAIL = "fail"


class PlaybackOverflowPolicy(str, Enum):
    DRAIN_AND_CANCEL = "drain_and_cancel"
    DROP_CHUNKS = "drop_chunks"


class CallState(str, Enum):
    TRIGGERED = "triggered"
    QUEUED = "queued"
    DIALING = "dialing"
    RINGING = "ringing"
    CONNECTING = "connecting"
    IN_CALL = "in_call"
    ENDING = "ending"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    RING_TIMEOUT = "ring_timeout"
    REMOTE_ENDED = "remote_ended"
    TALK_TIMEOUT = "talk_timeout"
    FAILED = "failed"


TERMINAL_STATES = frozenset({
    CallState.CANCELLED, CallState.REJECTED, CallState.RING_TIMEOUT,
    CallState.REMOTE_ENDED, CallState.TALK_TIMEOUT, CallState.FAILED,
})

WAITING_STATES = frozenset({CallState.TRIGGERED, CallState.QUEUED})

ACTIVE_STATES = frozenset(set(CallState) - TERMINAL_STATES - WAITING_STATES)


class CallEndReason(str, Enum):
    REMOTE_HANGUP = "remote_hangup"
    REMOTE_REJECTED = "remote_rejected"
    RING_TIMEOUT = "ring_timeout"
    TALK_TIMEOUT = "talk_timeout"
    ERROR = "error"
    SHUTDOWN = "shutdown"
    USER_CANCELLED = "user_cancelled"
    INCOMING_EXPIRED = "incoming_expired"
    CALLER_LEFT = "caller_left"


class CallEvent(str, Enum):
    ENQUEUED = "enqueued"
    DEQUEUED = "dequeued"
    CANCEL_REQUESTED = "cancel_requested"
    DIAL_ACCEPTED = "dial_accepted"
    INCOMING_ACCEPTED = "incoming_accepted"
    REMOTE_ACCEPTED = "remote_accepted"
    BRIDGE_READY = "bridge_ready"
    REMOTE_IDLE = "remote_idle"
    CALLER_LEFT = "caller_left"
    INCOMING_EXPIRED = "incoming_expired"
    RING_TIMER_EXPIRED = "ring_timer_expired"
    TALK_TIMER_EXPIRED = "talk_timer_expired"
    HANGUP_CONFIRMED = "hangup_confirmed"
    INTERNAL_ERROR = "internal_error"
    SHUTDOWN_REQUESTED = "shutdown_requested"


class DeadlineKind(Enum):
    SETUP = "setup"
    OFFER = "offer"
    RING = "ring"
    TALK = "talk"
    BRIDGE = "bridge"


class Command(Enum):
    PREPARE = "prepare"
    DIAL = "dial"
    ACCEPT = "accept"
    RECONCILE = "reconcile"
    BRIDGE = "bridge"
    GREET = "greet"


class AcceptResult(Enum):
    COMMAND_ACKNOWLEDGED = "command_acknowledged"
    ALREADY_ACTIVE = "already_active"


class FinishResult(Enum):
    FINISHED = "finished"
    ALREADY_ENDED = "already_ended"
    NOT_OWNED = "not_owned"
    UNKNOWN = "unknown"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class CallSession:
    sender_id: str
    chat_id: str
    language: str
    session_id: str = field(default_factory=lambda: str(uuid4()))
    direction: CallDirection = CallDirection.OUTGOING
    state: CallState = CallState.TRIGGERED
    call_id: str | None = None
    incoming_deadline: float | None = None
    prompt: str | None = None
    end_reason: CallEndReason | None = None
    remote_reason: str | None = None
    error_code: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    queued_at: datetime | None = None
    started_at: datetime | None = None
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    remote_accepted: bool = False
    bridge_ready: bool = False
    incoming_accept_completed: bool = False

    def __post_init__(self) -> None:
        if self.prompt is not None and not self.prompt.strip():
            raise ValueError("Call prompt must not be empty")

    @property
    def finished(self) -> bool:
        return self.state in TERMINAL_STATES

    @classmethod
    def incoming(
        cls,
        *,
        sender_id: str,
        call_id: str,
        language: str,
        incoming_deadline: float, prompt: str | None = None,
        chat_id: str | None = None,
    ) -> CallSession:
        if not sender_id or not call_id:
            raise ValueError("Incoming call requires sender_id and call_id")

        if not isfinite(incoming_deadline):
            raise ValueError("Incoming deadline must be finite")

        return cls(
            sender_id,
            chat_id or sender_id,
            language,
            direction=CallDirection.INCOMING,
            call_id=call_id,
            incoming_deadline=incoming_deadline,
            prompt=prompt,
        )


@dataclass(frozen=True)
class StateTransition:
    previous: CallState
    current: CallState
    event: CallEvent
    applied: bool

    @property
    def changed(self) -> bool:
        return self.previous != self.current

    @property
    def outcome(self) -> str:
        return "applied" if self.applied else "ignored"


@dataclass
class CallExecutionResult:
    """Own a returned recording until take_recording or close transfers/releases it."""
    recording_path: Path | None = None

    def take_recording(self) -> Path | None:
        path, self.recording_path = self.recording_path, None
        return path

    def close(self) -> None:
        if self.recording_path is not None:
            self.recording_path.unlink(missing_ok=True)
            self.recording_path = None

    def __enter__(self) -> CallExecutionResult:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            self.close()
        except OSError as error:
            logging.getLogger(__name__).warning(
                "Recording removal failed: error=%s", type(error).__name__,
            )

            if exc is None:
                raise

            exc.add_note("Recording removal also failed")


class TransitionCallback(Protocol):
    def __call__(
        self,
        session: CallSession,
        event: CallEvent,
        *,
        remote_reason: str | None = None,
        error_code: str | None = None,
    ) -> StateTransition: ...


class CallExecutor(Protocol):
    async def execute(
        self,
        session: CallSession,
        transition: TransitionCallback
    ) -> CallExecutionResult: ...

    def request_stop(self) -> None: ...


class IncomingCallExecutor(CallExecutor, Protocol):
    async def reject_incoming(self, call_id: str, sender_id: str) -> bool: ...

    def notify_incoming_ended(self, call_id: str) -> bool: ...


@dataclass(frozen=True)
class PcmFormat:
    """The supported wire format: signed little-endian PCM16, mono, 24 kHz."""
    rate: int = 24 * 1000
    frame_ms: int = 20
    channels: int = field(default=1, init=False)
    layout: str = field(default="mono", init=False)
    av_format: str = field(default="s16", init=False)

    def __post_init__(self) -> None:
        if self.rate != 24 * 1000 or self.frame_ms <= 0:
            raise ValueError("Only 24 kHz PCM with a positive frame duration is supported")

        if self.rate * self.frame_ms % 1000:
            raise ValueError("Frame duration must contain an integral number of samples")

    @property
    def samples_per_frame(self) -> int:
        return self.rate * self.frame_ms // 1000

    @property
    def bytes_per_frame(self) -> int:
        return self.samples_per_frame * PCM16_SAMPLE_BYTES

    @property
    def frame_seconds(self) -> float:
        return self.frame_ms / 1000


PCM = PcmFormat()


@dataclass(frozen=True)
class PcmChunk:
    """A track's samples positioned on the shared call timeline."""
    position: int
    data: bytes

    def __post_init__(self) -> None:
        if self.position < 0 or len(self.data) % PCM16_SAMPLE_BYTES:
            raise ValueError("PCM chunks require a nonnegative position and whole samples")


@dataclass(frozen=True)
class RecordingOptions:
    enabled: bool = True
    bitrate_bps: int = 96 * 1000
    max_pending_chunks: int = 256
    writer_poll_seconds: float = 0.1
    export_block_samples: int = 24 * 1000
    directory: Path | None = None

    def __post_init__(self) -> None:
        positive_values(
            self.bitrate_bps,
            self.max_pending_chunks,
            self.writer_poll_seconds,
            self.export_block_samples,
        )


def positive_values(*values: float) -> None:
    if any(not isfinite(value) or value <= 0 for value in values):
        raise ValueError("Options must be finite and positive")


@dataclass(frozen=True)
class PlaybackOptions:
    soft_limit_seconds: float = 30
    hard_limit_seconds: float = 180
    stall_seconds: float = 6
    watchdog_seconds: float = 1
    overflow_policy: PlaybackOverflowPolicy | str = PlaybackOverflowPolicy.DRAIN_AND_CANCEL

    def __post_init__(self) -> None:
        positive_values(
            self.soft_limit_seconds,
            self.hard_limit_seconds,
            self.stall_seconds,
            self.watchdog_seconds,
        )

        if self.soft_limit_seconds >= self.hard_limit_seconds:
            raise ValueError("Playback soft limit must be below its hard limit")

        try:
            policy = PlaybackOverflowPolicy(self.overflow_policy)
        except ValueError as error:
            raise ValueError("Unknown playback overflow policy") from error

        object.__setattr__(self, "overflow_policy", policy)


@dataclass(frozen=True)
class InputAudioOptions:
    buffer_seconds: float = 3
    send_seconds: float = 2
    enqueue_seconds: float = 0.25
    overflow_policy: InputOverflowPolicy | str = InputOverflowPolicy.DROP_OLDEST

    def __post_init__(self) -> None:
        positive_values(self.buffer_seconds, self.send_seconds, self.enqueue_seconds)

        try:
            policy = InputOverflowPolicy(self.overflow_policy)
        except ValueError as error:
            raise ValueError("Unknown input audio overflow policy") from error

        object.__setattr__(self, "overflow_policy", policy)


@dataclass(frozen=True)
class VadOptions:
    """Server VAD settings for caller audio in OpenAI Realtime."""
    threshold: float = 0.70
    prefix_padding_ms: int = 400
    silence_duration_ms: int = 800
    noise_reduction: Literal["near_field", "far_field"] | None = "near_field"

    def __post_init__(self) -> None:
        if (
            isinstance(self.threshold, bool)
            or not isinstance(self.threshold, (int, float))
            or not isfinite(self.threshold)
            or not 0 <= self.threshold <= 1
        ):
            raise ValueError("VAD threshold must be a finite number between 0 and 1")

        if type(self.prefix_padding_ms) is not int or self.prefix_padding_ms < 0:
            raise ValueError("VAD prefix padding must be non-negative milliseconds")

        if type(self.silence_duration_ms) is not int or self.silence_duration_ms <= 0:
            raise ValueError("VAD silence duration must be positive milliseconds")

        if self.noise_reduction not in ("near_field", "far_field", None):
            raise ValueError("Unknown VAD noise reduction setting")


@dataclass(frozen=True)
class AudioLoss:
    """Samples lost since the previous notice; the call can keep running."""
    direction: Literal["caller", "bot"]
    dropped_samples: int
    observed_at: float = field(default_factory=monotonic)

    @property
    def dropped_seconds(self) -> float:
        return self.dropped_samples / PCM.rate


@dataclass(frozen=True)
class CallOptions:
    """Call limits and policies. Audio-loss callbacks run on the call event loop."""
    ring_seconds: float = 30
    talk_seconds: float = 240
    setup_seconds: float = 30
    command_seconds: float = 10
    shutdown_seconds: float = 10
    recording: RecordingOptions = field(default_factory=RecordingOptions)
    playback: PlaybackOptions = field(default_factory=PlaybackOptions)
    input_audio: InputAudioOptions = field(default_factory=InputAudioOptions)
    # Return promptly; schedule slow application work in another task.
    on_audio_loss: Callable[[CallSession, AudioLoss], None] | None = None
    vad: VadOptions = field(default_factory=VadOptions)

    def __post_init__(self) -> None:
        positive_values(
            self.ring_seconds,
            self.talk_seconds,
            self.setup_seconds,
            self.command_seconds,
            self.shutdown_seconds,
        )

        if self.on_audio_loss is not None and not callable(self.on_audio_loss):
            raise ValueError("Audio loss callback must be callable")

        if not isinstance(self.vad, VadOptions):
            raise ValueError("VAD options must be VadOptions")


@dataclass(frozen=True)
class ProviderConfig:
    api_url: str
    id_instance: str
    api_token_instance: str = field(repr=False)
    openai_api_key: str = field(repr=False)
    realtime_model: str
    realtime_voice: str
    instructions: str | None = None


class ProviderError(RuntimeError):
    """Keep provider detail on the exception; log code without raw payloads."""
    def __init__(self, provider: str, code: str, detail: str) -> None:
        self.provider = provider
        self.code = code
        self.detail = detail
        super().__init__(f"{provider} [{code}]")


class CallCleanupError(RuntimeError):
    """A resource could not be confirmed closed; the instance needs recovery."""
    def __init__(self, stage: str, cause: Exception) -> None:
        self.stage = stage
        self.cause = cause
        self.code = f"cleanup_{stage}_{type(cause).__name__}"
        super().__init__(f"Call cleanup failed at {stage}: {type(cause).__name__}")


class CallServiceUnavailable(RuntimeError):
    """A previous call left resource ownership uncertain for this instance."""


@dataclass(frozen=True)
class DeadlineExpired:
    kind: DeadlineKind
    observed_at: float


class Deadlines:
    """One-shot deadlines; an observation exactly on the boundary is late."""
    def __init__(self) -> None:
        self._due: dict[DeadlineKind, float] = {}

    def arm(self, kind: DeadlineKind, at: float) -> None:
        self._due.setdefault(kind, at)

    def disarm(self, *kinds: DeadlineKind) -> None:
        for kind in kinds:
            self._due.pop(kind, None)

    def next_timeout(self) -> float | None:
        if not self._due:
            return None

        return max(0, min(self._due.values()) - monotonic())

    def expire_before(self, observed_at: float) -> DeadlineExpired | None:
        if not self._due:
            return None

        kind = min(self._due, key=self._due.__getitem__)
        at = self._due[kind]

        if at > observed_at:
            return None

        del self._due[kind]
        return DeadlineExpired(kind, at)


@dataclass(frozen=True)
class CommandCompleted:
    command: Command
    observed_at: float = field(default_factory=monotonic)
    result: AcceptResult | None = None


@dataclass(frozen=True)
class CommandFailed:
    command: Command
    error: Exception
    observed_at: float = field(default_factory=monotonic)


@dataclass(frozen=True)
class RemoteAnswered:
    call_id: str | None
    observed_at: float = field(default_factory=monotonic)


@dataclass(frozen=True)
class RemoteEnded:
    reason: str | None = None
    observed_at: float = field(default_factory=monotonic)


@dataclass(frozen=True)
class CallerLeft:
    call_id: str
    observed_at: float = field(default_factory=monotonic)


@dataclass(frozen=True)
class StopRequested:
    observed_at: float = field(default_factory=monotonic)


@dataclass(frozen=True)
class MediaFailed:
    error: Exception
    observed_at: float = field(default_factory=monotonic)


RuntimeEvent = (
    CommandCompleted
    | CommandFailed
    | RemoteAnswered
    | RemoteEnded
    | CallerLeft
    | StopRequested
    | MediaFailed
    | AudioLoss
    | DeadlineExpired
)

EventSink = Callable[[RuntimeEvent], None]
