"""Application facade: serialized admission, execution and incoming control."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace

from .models import (
    CallEvent,
    CallDirection,
    CallExecutionResult,
    CallOptions,
    CallSession,
    CallServiceUnavailable,
    PlaybackOptions,
    ProviderConfig,
    RecordingOptions,
    TransitionCallback,
)


class WhatsAppCallService:
    """One service per instance. request_stop permanently closes admission."""
    def __init__(
        self,
        *,
        api_url: str,
        id_instance: str, api_token_instance: str,
        openai_api_key: str,
        realtime_model: str,
        realtime_voice: str,
        ring_timeout_seconds: float = 30,
        talk_timeout_seconds: float = 240,
        shutdown_timeout_seconds: float = 10,
        options: CallOptions | None = None,
        recording: RecordingOptions | None = None,
        playback: PlaybackOptions | None = None,
        instructions: str | None = None,
    ) -> None:
        if not all((id_instance, api_token_instance, openai_api_key)):
            raise ValueError("Instance ID and provider credentials are required")

        self._config = ProviderConfig(
            api_url,
            id_instance,
            api_token_instance,
            openai_api_key,
            realtime_model,
            realtime_voice,
            instructions,
        )

        base = options or CallOptions(
            ring_seconds=ring_timeout_seconds,
            talk_seconds=talk_timeout_seconds,
            shutdown_seconds=shutdown_timeout_seconds,
        )

        self._options = replace(
            base,
            recording=recording or base.recording,
            playback=playback or base.playback,
        )

        self._gate = asyncio.Lock()
        self._runner = None
        self._runner_with_incomplete_cleanup = None
        self._stopped = False
        self._healthy = True

    @property
    def healthy(self) -> bool:
        return self._healthy

    async def execute(
        self,
        session: CallSession,
        transition: TransitionCallback,
    ) -> CallExecutionResult:
        async with self._gate:
            already_finished = session.finished

            if not self._admit(session, transition):
                if (
                    self._stopped and session.direction == CallDirection.INCOMING
                    and session.call_id and not already_finished
                ):
                    with suppress(Exception):
                        await self.reject_incoming(session.call_id, session.sender_id)

                return CallExecutionResult()

            return await self._execute_admitted(session, transition)

    async def _execute_admitted(
        self,
        session: CallSession,
        transition: TransitionCallback,
    ) -> CallExecutionResult:
        from .call_runner import CallRunner

        try:
            self._runner = CallRunner(
                session,
                transition,
                self._config,
                self._options,
            )

            return await self._runner.run()
        except asyncio.CancelledError:
            transition(session, CallEvent.SHUTDOWN_REQUESTED)
            raise
        except Exception as error:
            transition(
                session,
                CallEvent.INTERNAL_ERROR,
                error_code=getattr(error, "code", type(error).__name__),
            )

            raise
        finally:
            if self._runner is not None and self._runner.cleanup_failed:
                self._healthy = False
                self._runner_with_incomplete_cleanup = self._runner

            self._runner = None

    def _admit(self, session: CallSession, transition: TransitionCallback) -> bool:
        if session.finished:
            return False

        if not self._healthy:
            transition(session, CallEvent.INTERNAL_ERROR, error_code="service_unhealthy")
            raise CallServiceUnavailable("Call service requires recovery after cleanup failure")

        if self._stopped:
            transition(session, CallEvent.SHUTDOWN_REQUESTED)
            return False

        return True

    def request_stop(self) -> None:
        self._stopped = True

        if self._runner is not None:
            self._runner.request_stop()

    def notify_incoming_ended(self, call_id: str) -> bool:
        if self._runner is None:
            return False

        return self._runner.notify_incoming_ended(call_id)

    async def reject_incoming(self, call_id: str, sender_id: str) -> bool:
        from .call_runner import finish_cleanup
        from .greenapi import GreenApiCall
        from .models import CallDirection

        session = CallSession(
            sender_id,
            sender_id,
            "",
            call_id=call_id,
            direction=CallDirection.INCOMING,
        )

        api = GreenApiCall(
            self._config,
            session,
            lambda event: None,
            self._options.command_seconds,
        )

        await api.__aenter__()

        try:
            return await asyncio.wait_for(api.reject_offer(), self._options.command_seconds)
        finally:
            await finish_cleanup(api.__aexit__(None, None, None))
