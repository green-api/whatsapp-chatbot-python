"""Contracts shared by the executor and an application's call coordinator."""

from typing import Protocol

from .models import CallEvent, CallExecutionResult, CallSession, StateTransition


class TransitionCallback(Protocol):
    """Synchronously apply the event to the supplied session, under its owner's lock."""

    def __call__(
        self,
        session: CallSession,
        event: CallEvent,
        *,
        remote_reason: str | None = None,
        error_code: str | None = None,
    ) -> StateTransition: ...


class CallExecutor(Protocol):
    """Execute calls serially; request_stop permanently stops this executor."""

    async def execute(
        self, session: CallSession, transition: TransitionCallback
    ) -> CallExecutionResult: ...

    def request_stop(self) -> None: ...
