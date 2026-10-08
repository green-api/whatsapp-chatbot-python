from __future__ import annotations
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from whatsapp_api_client_python.API import GreenAPI
from whatsapp_api_client_python.tools.voip import CallAudio, CallsConnection
from .contracts import TransitionCallback
from .realtime_voice import VoiceBotSession
from .recording import CallRecorder
from .models import CallEvent, CallExecutionResult, CallSession, CallState, TERMINAL_STATES
from .runtime import CallRuntime, RuntimeEvent, RuntimeEventType, RuntimeTimer
import asyncio
import logging

# Constants

BRIDGE_NEGOTIATION_TIMEOUT_SECONDS = 15

INITIAL_STATE_TIMEOUT_SECONDS = 10


@dataclass(slots=True)
class _CallExecution:
    """Resources and progress of one execution, including partially completed setup."""

    session: CallSession
    transition: TransitionCallback
    recorder: CallRecorder
    runtime: CallRuntime | None = None
    voice: VoiceBotSession | None = None
    api: GreenAPI | None = None
    calls: CallsConnection | None = None
    initial_state: asyncio.Future[str] | None = None
    bridge_task: asyncio.Task[None] | None = None
    dial_started: bool = False
    dialed: bool = False
    execution_completed: bool = False
    cancellation: asyncio.CancelledError | None = None
    recording_path: Path | None = None


class WhatsAppCallService:
    def __init__(
        self,
        *,
        api_url: str,
        id_instance: str,
        api_token_instance: str,
        openai_api_key: str,
        realtime_model: str,
        realtime_voice: str,
        ring_timeout_seconds: int,
        talk_timeout_seconds: int,
        shutdown_timeout_seconds: int,
        logger: logging.Logger,
    ) -> None:
        self._api_url = api_url
        self._id_instance = id_instance
        self._api_token_instance = api_token_instance
        self._openai_api_key = openai_api_key
        self._realtime_model = realtime_model
        self._realtime_voice = realtime_voice
        self._ring_timeout = ring_timeout_seconds
        self._talk_timeout = talk_timeout_seconds
        self._shutdown_timeout = shutdown_timeout_seconds
        self._logger = logger
        self._active_loop: asyncio.AbstractEventLoop | None = None
        self._active_runtime: CallRuntime | None = None
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True
        loop = self._active_loop
        runtime = self._active_runtime

        if loop is not None and runtime is not None and loop.is_running():
            loop.call_soon_threadsafe(runtime.events.put_nowait, RuntimeEvent.shutdown())

    async def execute(
        self,
        session: CallSession,
        transition: TransitionCallback,
    ) -> CallExecutionResult:
        if self._stop_requested:
            transition(session, CallEvent.SHUTDOWN_REQUESTED, error_code="shutdown")
            return CallExecutionResult()

        execution = _CallExecution(session, transition, CallRecorder(self._logger))

        try:
            await self._execute_call(execution)
        except asyncio.CancelledError as error:
            self._record_cancellation(execution, error)
            raise
        finally:
            await self._finalize_call(execution)

        return CallExecutionResult(execution.recording_path)

    async def _execute_call(self, execution: _CallExecution) -> None:
        # Setup failures propagate to the caller; call failures become FSM events.
        await self._prepare_call(execution)

        try:
            await self._run_call(execution)
        except Exception as error:
            await self._handle_call_failure(execution, error)

        execution.execution_completed = True

    async def _prepare_call(self, execution: _CallExecution) -> None:
        runtime = CallRuntime(
            ring_timeout_seconds=self._ring_timeout,
            talk_timeout_seconds=self._talk_timeout,
            bridge_timeout_seconds=BRIDGE_NEGOTIATION_TIMEOUT_SECONDS,
        )

        execution.runtime = runtime
        self._active_loop = asyncio.get_running_loop()
        self._active_runtime = runtime

        if self._stop_requested:
            runtime.events.put_nowait(RuntimeEvent.shutdown())

        execution.voice = VoiceBotSession(
            api_key=self._openai_api_key,
            model=self._realtime_model,
            voice=self._realtime_voice,
            language=execution.session.language,
            on_error=lambda error: runtime.events.put_nowait(RuntimeEvent.voice_failed(error)),
            recorder=execution.recorder,
        )

        await execution.voice.start()

        execution.api = GreenAPI(self._id_instance, self._api_token_instance, host=self._api_url)

        execution.calls = execution.api.voip.connect(
            audio_factory=partial(self._create_audio_session, execution.voice),
        )

        execution.initial_state = asyncio.get_running_loop().create_future()

        self._register_listeners(execution)

    async def _create_audio_session(self, voice: VoiceBotSession) -> CallAudio:
        track = await voice.new_output_track()

        try:
            sink = voice.new_input_sink()
        except BaseException:
            track.stop()
            raise

        return CallAudio(track, sink.attach, partial(self._close_audio_session, sink, track))

    @staticmethod
    async def _close_audio_session(sink, track) -> None:
        try:
            await sink.close()
        finally:
            track.stop()

    def _register_listeners(self, execution: _CallExecution) -> None:
        calls = execution.calls
        runtime = execution.runtime

        calls.on("state", partial(self._on_state, execution))

        calls.on("end_call", lambda detail: runtime.events.put_nowait(
            RuntimeEvent.end_call(detail.get("cause", None)),
        ))

        calls.on("disconnect", partial(self._on_disconnect, execution))

        calls.on("error", lambda detail: runtime.events.put_nowait(
            RuntimeEvent.bridge_failed(RuntimeError("callsRtc reported an error")),
        ))

    @staticmethod
    def _on_state(execution: _CallExecution, state) -> None:
        if not execution.initial_state.done():
            execution.initial_state.set_result(state.state)

        if execution.dial_started:
            execution.runtime.events.put_nowait(RuntimeEvent.server_state(state.state, state.reason))

    @staticmethod
    def _on_disconnect(execution: _CallExecution, detail) -> None:
        permanent = bool(detail.get("permanent"))

        if permanent and not execution.initial_state.done():
            execution.initial_state.set_exception(RuntimeError("callsRtc connection refused"))

        execution.runtime.events.put_nowait(RuntimeEvent.disconnect(permanent))

    @staticmethod
    def _on_bridge_done(execution: _CallExecution, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return

        error = task.exception()
        event = RuntimeEvent.bridge_failed(error) if error else RuntimeEvent.bridge_ready()

        execution.runtime.events.put_nowait(event)

    async def _run_call(self, execution: _CallExecution) -> None:
        await execution.calls.openAsync(timeout=INITIAL_STATE_TIMEOUT_SECONDS)

        state = await asyncio.wait_for(execution.initial_state, timeout=INITIAL_STATE_TIMEOUT_SECONDS)

        if state != "idle":
            raise RuntimeError(f"Instance already has a call: {state}")

        if self._stop_requested:
            execution.transition(execution.session, CallEvent.SHUTDOWN_REQUESTED, error_code="shutdown")
        else:
            execution.dial_started = True

            await execution.api.voip.dialAsync(execution.session.chat_id)

            execution.dialed = True

            execution.transition(execution.session, CallEvent.DIAL_ACCEPTED)
            execution.runtime.start_ringing()

            # Bridge completion confirms SDP, not ICE/DTLS or live audio.
            execution.bridge_task = asyncio.create_task(execution.calls.startAudioAsync())

            execution.bridge_task.add_done_callback(partial(self._on_bridge_done, execution))

        await self._process_events(execution)

    async def _process_events(self, execution: _CallExecution) -> None:
        while execution.session.state not in TERMINAL_STATES:
            event = await execution.runtime.next_event(execution.session.state)

            if event is None:
                await self._handle_timeout(
                    execution.runtime, execution.api, execution.session, execution.transition,
                )
            else:
                await self._handle_event(
                    event, execution.runtime, execution.api, execution.voice,
                    execution.recorder, execution.session, execution.transition,
                )

    async def _handle_call_failure(self, execution: _CallExecution, error: Exception) -> None:
        self._logger.error(
            "VoIP execution failed: session=%s error=%s",
            execution.session.session_id,
            type(error).__name__,
        )

        execution.transition(
            execution.session, CallEvent.INTERNAL_ERROR,
            error_code=type(error).__name__,
        )

        if execution.dialed:
            await self._safe_hang_up(execution.api)

    @staticmethod
    def _record_cancellation(execution: _CallExecution, error: asyncio.CancelledError) -> None:
        if execution.cancellation is None:
            execution.cancellation = error

            # A failing callback must not prevent resource cleanup.
            with suppress(Exception):
                execution.transition(
                    execution.session, CallEvent.SHUTDOWN_REQUESTED, error_code="cancelled",
                )

    async def _wait_for_cleanup(self, coroutine, execution: _CallExecution) -> None:
        # Own the cleanup task until completion, including repeated cancel().
        task = asyncio.create_task(coroutine)

        while True:
            try:
                await asyncio.shield(task)
                return
            except asyncio.CancelledError as error:
                self._record_cancellation(execution, error)

                if task.cancelled():
                    raise

    async def _finalize_call(self, execution: _CallExecution) -> None:
        try:
            await self._close_call(execution)
        finally:
            self._finish_recording(execution)

        if execution.cancellation is not None:
            raise execution.cancellation

    async def _close_call(self, execution: _CallExecution) -> None:
        try:
            await self._wait_for_cleanup(self._close_connections(execution), execution)
        finally:
            await self._hang_up_after_cancellation(execution)

    async def _close_connections(self, execution: _CallExecution) -> None:
        # Preserve shutdown order: RTC, voice session, then the bridge task.
        if execution.calls is not None:
            with suppress(Exception):
                await asyncio.wait_for(execution.calls.closeAsync(), timeout=self._shutdown_timeout)

        if execution.voice is not None:
            with suppress(Exception):
                await asyncio.wait_for(execution.voice.close(), timeout=self._shutdown_timeout)

        if execution.bridge_task is not None:
            if not execution.bridge_task.done():
                execution.bridge_task.cancel()

            with suppress(Exception):
                await asyncio.wait_for(
                    asyncio.gather(execution.bridge_task, return_exceptions=True),
                    timeout=self._shutdown_timeout,
                )

    async def _hang_up_after_cancellation(self, execution: _CallExecution) -> None:
        # The dial request may have reached the server before its response arrived.
        if execution.cancellation is not None and execution.dial_started:
            with suppress(Exception):
                await self._wait_for_cleanup(
                    asyncio.wait_for(self._safe_hang_up(execution.api), timeout=self._shutdown_timeout),
                    execution,
                )

    def _finish_recording(self, execution: _CallExecution) -> None:
        self._active_runtime = None
        self._active_loop = None
        path = execution.recorder.finish()

        keep_recording = (
            execution.execution_completed
            and execution.cancellation is None
            and execution.session.state in {CallState.REMOTE_ENDED, CallState.TALK_TIMEOUT}
        )

        if path is not None and not keep_recording:
            path.unlink(missing_ok=True)
            path = None

        execution.recording_path = path

    async def _handle_event(
        self,
        event: RuntimeEvent,
        runtime: CallRuntime,
        api: GreenAPI,
        voice: VoiceBotSession,
        recorder: CallRecorder,
        call_session: CallSession,
        transition: TransitionCallback,
    ) -> None:
        if event.type == RuntimeEventType.STATE:
            if event.state == "on-call":
                transition(call_session, CallEvent.REMOTE_ACCEPTED)
                runtime.remote_accepted(call_session.bridge_ready)

                if call_session.state == CallState.IN_CALL:
                    recorder.start()
                    await voice.greet_once()
            elif event.state == "idle":
                transition(call_session, CallEvent.REMOTE_IDLE, remote_reason=event.reason)
        elif event.type == RuntimeEventType.BRIDGE_READY:
            transition(call_session, CallEvent.BRIDGE_READY)
            runtime.bridge_negotiated()

            if call_session.state == CallState.IN_CALL:
                recorder.start()
                await voice.greet_once()
        elif event.type == RuntimeEventType.END_CALL:
            if event.reason is None:
                # The library reports connection-lost without a server cause.
                raise RuntimeError("callsRtc connection lost before audio bridge")

            transition(call_session, CallEvent.REMOTE_IDLE, remote_reason=event.reason)
        elif event.type == RuntimeEventType.BRIDGE_FAILED:
            raise event.error or RuntimeError("Audio bridge failed")
        elif event.type == RuntimeEventType.VOICE_FAILED:
            raise event.error or RuntimeError("OpenAI Realtime failed")
        elif event.type == RuntimeEventType.DISCONNECT:
            if event.permanent:
                raise RuntimeError("callsRtc connection permanently closed")
            # The library reconnects and rebuilds an active audio bridge.
        elif event.type == RuntimeEventType.SHUTDOWN:
            transition(call_session, CallEvent.SHUTDOWN_REQUESTED, error_code="shutdown")
            await self._safe_hang_up(api)

    async def _handle_timeout(
        self,
        runtime: CallRuntime,
        api: GreenAPI,
        call_session: CallSession,
        transition: TransitionCallback,
    ) -> None:
        timer = runtime.expired_timer(call_session.state)

        if timer == RuntimeTimer.RING:
            transition(call_session, CallEvent.RING_TIMER_EXPIRED)
            await self._hang_up(api, call_session, transition)
        elif timer == RuntimeTimer.TALK:
            transition(call_session, CallEvent.TALK_TIMER_EXPIRED)
            await self._hang_up(api, call_session, transition)
        elif timer == RuntimeTimer.BRIDGE:
            transition(
                call_session, CallEvent.INTERNAL_ERROR,
                error_code="bridge_negotiation_timeout",
            )

            await self._safe_hang_up(api)

    @staticmethod
    async def _hang_up(
        api: GreenAPI,
        call_session: CallSession,
        transition: TransitionCallback,
    ) -> None:
        try:
            await api.voip.hangUpAsync()
        except Exception as error:
            transition(
                call_session, CallEvent.INTERNAL_ERROR,
                error_code=f"hangup_{type(error).__name__}",
            )

            return

        transition(call_session, CallEvent.HANGUP_CONFIRMED)

    @staticmethod
    async def _safe_hang_up(api: GreenAPI) -> None:
        with suppress(Exception):
            await api.voip.hangUpAsync()
