from __future__ import annotations
from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic
from .models import CallState
import asyncio


class RuntimeEventType(StrEnum):
    STATE = "state"
    BRIDGE_READY = "bridge_ready"
    BRIDGE_FAILED = "bridge_failed"
    VOICE_FAILED = "voice_failed"
    END_CALL = "end_call"
    DISCONNECT = "disconnect"
    SHUTDOWN = "shutdown"


class RuntimeTimer(StrEnum):
    RING = "ring"
    TALK = "talk"
    BRIDGE = "bridge"


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    type: RuntimeEventType
    state: str | None = None
    reason: str | None = None
    error: Exception | None = None
    permanent: bool = False

    @classmethod
    def server_state(cls, state: str, reason: str | None = None) -> RuntimeEvent:
        return cls(RuntimeEventType.STATE, state=state, reason=reason)

    @classmethod
    def bridge_ready(cls) -> RuntimeEvent:
        return cls(RuntimeEventType.BRIDGE_READY)

    @classmethod
    def bridge_failed(cls, error: Exception) -> RuntimeEvent:
        return cls(RuntimeEventType.BRIDGE_FAILED, error=error)

    @classmethod
    def voice_failed(cls, error: Exception) -> RuntimeEvent:
        return cls(RuntimeEventType.VOICE_FAILED, error=error)

    @classmethod
    def end_call(cls, reason: str | None = None) -> RuntimeEvent:
        return cls(RuntimeEventType.END_CALL, reason=reason)

    @classmethod
    def disconnect(cls, permanent: bool) -> RuntimeEvent:
        return cls(RuntimeEventType.DISCONNECT, permanent=permanent)

    @classmethod
    def shutdown(cls) -> RuntimeEvent:
        return cls(RuntimeEventType.SHUTDOWN)


@dataclass(slots=True)
class CallRuntime:
    ring_timeout_seconds: float
    talk_timeout_seconds: float
    bridge_timeout_seconds: float
    events: asyncio.Queue[RuntimeEvent] = field(default_factory=asyncio.Queue)
    ring_deadline: float | None = None
    talk_deadline: float | None = None
    bridge_deadline: float | None = None

    def start_ringing(self) -> None:
        self.ring_deadline = monotonic() + self.ring_timeout_seconds

    def remote_accepted(self, bridge_ready: bool) -> None:
        if self.talk_deadline is None:
            self.talk_deadline = monotonic() + self.talk_timeout_seconds

        if not bridge_ready and self.bridge_deadline is None:
            self.bridge_deadline = monotonic() + self.bridge_timeout_seconds

    def bridge_negotiated(self) -> None:
        self.bridge_deadline = None

    async def next_event(self, state: CallState) -> RuntimeEvent | None:
        try:
            return self.events.get_nowait()
        except asyncio.QueueEmpty:
            pass

        timeout = self._next_timeout(state)

        try:
            # Python 3.11 wait_for() can swallow cancellation when an event becomes ready. Wait in the current task
            #   instead: https://github.com/python/cpython/issues/86296
            async with asyncio.timeout(timeout):
                return await self.events.get()
        except TimeoutError:
            return None

    def expired_timer(self, state: CallState) -> RuntimeTimer | None:
        now = monotonic()

        if state == CallState.RINGING and self._expired(self.ring_deadline, now):
            return RuntimeTimer.RING

        if state in {CallState.CONNECTING, CallState.IN_CALL}:
            if self._expired(self.talk_deadline, now):
                return RuntimeTimer.TALK

        if state == CallState.CONNECTING and self._expired(self.bridge_deadline, now):
            return RuntimeTimer.BRIDGE

        return None

    def _next_timeout(self, state: CallState) -> float | None:
        deadlines: list[float] = []

        if state == CallState.RINGING and self.ring_deadline is not None:
            deadlines.append(self.ring_deadline)

        if state in {CallState.CONNECTING, CallState.IN_CALL}:
            if self.talk_deadline is not None:
                deadlines.append(self.talk_deadline)

        if state == CallState.CONNECTING and self.bridge_deadline is not None:
            deadlines.append(self.bridge_deadline)

        if not deadlines:
            return None

        return max(0.0, min(deadlines) - monotonic())

    @staticmethod
    def _expired(deadline: float | None, now: float) -> bool:
        return deadline is not None and now >= deadline
