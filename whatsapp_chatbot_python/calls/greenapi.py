"""Green API signaling and RTC boundary. No call-state policy lives here."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Awaitable, TypeVar, TYPE_CHECKING

from .models import (
    AcceptResult,
    CallCleanupError,
    CallDirection,
    CallSession,
    EventSink,
    FinishResult,
    MediaFailed,
    ProviderConfig,
    ProviderError,
    RemoteAnswered,
    RemoteEnded,
)

if TYPE_CHECKING:
    from .audio import AudioBridge

T = TypeVar("T")


class GreenApiCall:
    """Owns HTTP session and RTC connection for exactly one call."""
    def __init__(
        self,
        config: ProviderConfig,
        session: CallSession,
        emit: EventSink,
        timeout: float,
        shutdown_timeout: float | None = None,
    ) -> None:
        self._config = config
        self._session = session
        self._emit = emit
        self._timeout = timeout
        self._shutdown_timeout = shutdown_timeout or timeout
        self._stack = AsyncExitStack()
        self._api = None
        self._rtc = None
        self._call_id = session.call_id
        self._command_started = False
        self._command_completed = False
        self._remote_ended = False
        self._closing = False
        self._connection_lost = False
        self.close_errors: list[CallCleanupError] = []
        self._rtc_close_task: asyncio.Task | None = None

    async def __aenter__(self) -> GreenApiCall:
        from whatsapp_api_client_python.API import GreenApi

        self._api = GreenApi(
            self._config.id_instance, self._config.api_token_instance,
            host=self._config.api_url, raise_errors=True,
            host_timeout=self._timeout,
        )

        self._stack.callback(self._close_session)

        return self

    async def __aexit__(self, *exc) -> None:
        self._closing = True
        await self._stack.aclose()

        if self.close_errors:
            raise self.close_errors[0]

    async def open(self, audio: AudioBridge) -> None:
        self._ensure_open()
        self._rtc = self._api.voip.connect(audio_factory=audio.create)

        self._stack.push_async_callback(self._close_rtc)
        self._rtc.on("state", self._on_state)
        self._rtc.on("error", self._on_error)
        self._rtc.on("disconnect", self._on_disconnect)
        self._rtc.on("end_call", self._on_end_call)
        await self._rtc.openAsync(timeout=self._timeout)
        self._ensure_open()

    def _ensure_open(self) -> None:
        if self._closing:
            raise RuntimeError("Green API call is closing")

    async def _close_rtc(self) -> None:
        if self._rtc_close_task is None:
            self._rtc_close_task = asyncio.create_task(
                self._rtc.closeAsync(),
                name="call:rtc-close",
            )

            self._rtc_close_task.add_done_callback(self._observe_rtc_close)

        try:
            await asyncio.wait_for(
                asyncio.shield(self._rtc_close_task), self._shutdown_timeout,
            )
        except Exception as error:
            self.close_errors.append(CallCleanupError("rtc_close", error))

    @staticmethod
    def _observe_rtc_close(task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()

    def _close_session(self) -> None:
        try:
            self._api.session.close()
        except Exception as error:
            self.close_errors.append(CallCleanupError("http_close", error))

    @staticmethod
    async def _request(name: str, operation: Awaitable[T]) -> T:
        try:
            return await operation
        except Exception as error:
            raise ProviderError(
                "greenapi",
                f"{name}_{type(error).__name__}",
                str(error),
            ) from error

    async def dial(self) -> None:
        self._ensure_open()
        state = await self._request("get_state", self._api.voip.getStateAsync())
        self._ensure_open()

        if state.state != "idle":
            raise RuntimeError("Cannot dial: the instance already has a call")

        self._command_started = True

        await self._request("dial", self._api.voip.dialAsync(self._session.chat_id))

        self._command_completed = True

    async def accept(self) -> AcceptResult:
        self._ensure_open()
        state = await self._request("get_state", self._api.voip.getStateAsync())
        self._ensure_open()

        if state.state == "on-call" and self._owns(state):
            self._command_started = True

            self._on_state(state)

            return AcceptResult.ALREADY_ACTIVE

        if state.state != "inc-call" or not self._owns(state):
            raise ProviderError("greenapi", "offer_gone", "Incoming offer is no longer current")

        self._command_started = True

        await self._request("accept", self._api.voip.acceptAsync())

        self._command_completed = True

        return AcceptResult.COMMAND_ACKNOWLEDGED

    async def reconcile_accept(self, error: Exception) -> None:
        state = await self._request("get_state", self._api.voip.getStateAsync())

        if self._owns(state):
            if state.state == "on-call":
                self._on_state(state)
                return

            if state.state == "inc-call":
                raise error

        if state.state == "idle" or state.info is not None:
            raise ProviderError("greenapi", "offer_gone", "Incoming offer is no longer current")

        raise error

    async def start_audio(self) -> None:
        self._ensure_open()
        await self._rtc.startAudioAsync()
        self._ensure_open()

    def _owns(self, state) -> bool:
        if state.info is None:
            return False

        if self._call_id is not None:
            return state.info.id == self._call_id

        target = self._session.chat_id

        if "@" not in target:
            target += "@c.us"

        return state.info.wid == target

    def _on_state(self, state) -> None:
        if self._closing or not self._command_started:
            return

        if state.state == "idle":
            if (
                self._session.direction == CallDirection.OUTGOING
                and self._call_id is None and not self._command_completed
            ):
                return

            self._remote_ended = True

            self._emit(RemoteEnded(state.reason))

            return

        if not self._owns(state):
            return

        self._call_id = state.info.id

        if state.state == "on-call":
            self._emit(RemoteAnswered(self._call_id))

    def _on_error(self, detail) -> None:
        if self._closing or self._connection_lost:
            return

        message = detail.get("message", "RTC failure") if isinstance(detail, dict) else str(detail)
        code = str(detail.get("code", "rtc_error")) if isinstance(detail, dict) else "rtc_error"

        self._emit(MediaFailed(ProviderError("greenapi", code, message)))

    def _on_disconnect(self, detail=None) -> None:
        if self._closing or self._remote_ended or self._connection_lost:
            return

        if isinstance(detail, dict) and detail.get("permanent"):
            self._connection_lost = True
            self._emit(MediaFailed(ConnectionError("Green API signaling permanently disconnected")))

    def _on_end_call(self, detail=None) -> None:
        if self._closing or self._remote_ended or not self._command_started:
            return

        if isinstance(detail, dict) and detail.get("reason") == "connection-lost":
            self._connection_lost = True
            self._emit(MediaFailed(ConnectionError("Green API call connection lost")))
            return

        reason = detail.get("cause") if isinstance(detail, dict) else None

        self._remote_ended = True
        self._emit(RemoteEnded(reason))

    async def finish(self) -> FinishResult:
        """Finish only our call. A stale session must never hang up another call."""
        if self._remote_ended:
            return FinishResult.ALREADY_ENDED

        if not self._command_started and self._session.direction == CallDirection.OUTGOING:
            return FinishResult.ALREADY_ENDED

        state = await self._request("get_state", self._api.voip.getStateAsync())

        if state.state == "idle":
            return FinishResult.ALREADY_ENDED

        if state.info is None:
            return FinishResult.UNKNOWN

        if not self._owns(state):
            return FinishResult.NOT_OWNED

        match state.state:
            case "inc-call":
                await self._request("reject", self._api.voip.rejectAsync())
                return FinishResult.FINISHED
            case "on-call" | "out-call":
                await self._request("hangup", self._api.voip.hangUpAsync())
                return FinishResult.FINISHED

        return FinishResult.UNKNOWN

    async def reject_offer(self) -> bool:
        """Reject only the matching unanswered offer; never hang up an active call."""
        state = await self._request("get_state", self._api.voip.getStateAsync())

        if state.state == "inc-call" and self._owns(state):
            await self._request("reject", self._api.voip.rejectAsync())
            return True

        return False
