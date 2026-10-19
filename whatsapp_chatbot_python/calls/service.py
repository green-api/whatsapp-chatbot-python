from __future__ import annotations
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from time import monotonic
from whatsapp_api_client_python.API import GreenAPI
from whatsapp_api_client_python.tools.voip import CallAudio, CallsConnection, CallState as ApiCallState
from .contracts import TransitionCallback
from .realtime_voice import VoiceBotSession
from .recording import CallRecorder
from .models import CallDirection, CallEvent, CallExecutionResult, CallSession, CallState, TERMINAL_STATES
from .runtime import CallRuntime, RuntimeEvent, RuntimeEventType, RuntimeTimer
import asyncio
import logging

# Constants

BRIDGE_NEGOTIATION_TIMEOUT_SECONDS = 15

INITIAL_STATE_TIMEOUT_SECONDS = 10

MATCHING_CALL_TIMEOUT_SECONDS = 5


class _IncomingCallerLeft(Exception):
    pass


class _IncomingDeadlineExceeded(TimeoutError):
    pass


class _IncomingShutdown(Exception):
    pass


_ANSWER_CONFIRMED = object()


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
    initial_state: asyncio.Future[ApiCallState] | None = None
    bridge_task: asyncio.Task[None] | None = None
    command_started: bool = False
    command_completed: bool = False
    incoming_ended: bool = False
    incoming_left_at: float | None = None
    incoming_end_event: asyncio.Event = field(default_factory=asyncio.Event)
    incoming_answered_at: float | None = None
    incoming_answer_event: asyncio.Event = field(default_factory=asyncio.Event)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
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
        for name, value in (
            ("id_instance", id_instance),
            ("api_token_instance", api_token_instance),
            ("openai_api_key", openai_api_key),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required for voice calls")

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
        self._active_execution: _CallExecution | None = None
        self._stop_requested = False

    def request_stop(self) -> None:
        self._stop_requested = True
        loop = self._active_loop
        runtime = self._active_runtime

        if loop is not None and loop.is_running():
            execution = self._active_execution

            def signal() -> None:
                if self._active_execution is execution and execution is not None:
                    execution.stop_event.set()

                    if runtime is not None:
                        runtime.events.put_nowait(RuntimeEvent.shutdown())

            loop.call_soon_threadsafe(signal)

    def notify_incoming_ended(self, call_id: str) -> bool:
        """Wake the active incoming call after an external caller-ended notification.

        The caller must also transition the matching session under its queue lock,
        even when this returns False during handoff. A stale call ID cannot stop
        the next execution for the same sender.
        """
        execution = self._active_execution
        loop = self._active_loop

        if (execution is None or loop is None or not loop.is_running()
                or execution.session.direction != CallDirection.INCOMING
                or execution.session.call_id != call_id
            ):

            return False

        def signal() -> None:
            if self._active_execution is not execution:
                return

            execution.incoming_ended = True
            execution.incoming_left_at = monotonic()

            execution.incoming_end_event.set()

            if execution.runtime is not None:
                execution.runtime.events.put_nowait(RuntimeEvent.caller_left())

        loop.call_soon_threadsafe(signal)

        return True

    async def reject_incoming(self, call_id: str, sender_id: str) -> bool:
        """Reject a waiting offer only while it still matches the live call."""

        api = GreenAPI(self._id_instance, self._api_token_instance, host=self._api_url)

        try:
            state = await asyncio.wait_for(api.voip.getStateAsync(), MATCHING_CALL_TIMEOUT_SECONDS)

            if not self._matches_incoming(state, call_id, sender_id, "inc-call"):
                return False

            await asyncio.wait_for(api.voip.rejectAsync(), MATCHING_CALL_TIMEOUT_SECONDS)

            return True
        finally:
            api.session.close()

    async def execute(
        self,
        session: CallSession,
        transition: TransitionCallback,
    ) -> CallExecutionResult:
        if self._stop_requested:
            transition(session, CallEvent.SHUTDOWN_REQUESTED, error_code="shutdown")
            if session.direction == CallDirection.INCOMING and session.call_id:
                with suppress(Exception):
                    await self.reject_incoming(session.call_id, session.sender_id)

            return CallExecutionResult()

        if session.direction == CallDirection.INCOMING:
            if not session.call_id or session.incoming_deadline is None:
                raise ValueError("Incoming calls require call_id and incoming_deadline")

            if session.state in TERMINAL_STATES:
                return CallExecutionResult()

            if monotonic() >= session.incoming_deadline:
                transition(session, CallEvent.INCOMING_EXPIRED)

                with suppress(Exception):
                    await self.reject_incoming(session.call_id, session.sender_id)

                return CallExecutionResult()

            if session.state != CallState.RINGING:
                raise ValueError("Incoming call must be dequeued before execution")

        execution = _CallExecution(session, transition, CallRecorder(self._logger))
        self._active_loop = asyncio.get_running_loop()
        self._active_execution = execution

        try:
            # The application can end the offer between the first state check
            # and this execution becoming visible to notify_incoming_ended().
            if session.direction == CallDirection.INCOMING and session.state in TERMINAL_STATES:
                return CallExecutionResult()

            await self._execute_call(execution)
        except asyncio.CancelledError as error:
            self._record_cancellation(execution, error)
            raise
        except Exception:
            if session.direction == CallDirection.INCOMING:
                transition(session, CallEvent.INTERNAL_ERROR, error_code="setup_failed")

                with suppress(Exception):
                    await self.reject_incoming(session.call_id, session.sender_id)

            raise
        finally:
            await self._finalize_call(execution)

        return CallExecutionResult(execution.recording_path)

    async def _execute_call(self, execution: _CallExecution) -> None:
        # Setup failures propagate to the caller; call failures become FSM events.
        if execution.session.direction == CallDirection.INCOMING:
            try:
                await self._await_incoming_operation(execution, self._prepare_call(execution))
            except _IncomingCallerLeft:
                execution.transition(execution.session, CallEvent.CALLER_LEFT)
                execution.execution_completed = True
                return
            except _IncomingDeadlineExceeded:
                execution.transition(execution.session, CallEvent.INCOMING_EXPIRED)

                with suppress(Exception):
                    await self.reject_incoming(execution.session.call_id, execution.session.sender_id)

                execution.execution_completed = True

                return
            except _IncomingShutdown:
                execution.transition(
                    execution.session, CallEvent.SHUTDOWN_REQUESTED, error_code="shutdown",
                )

                with suppress(Exception):
                    await self.reject_incoming(execution.session.call_id, execution.session.sender_id)

                execution.execution_completed = True

                return
            if execution.incoming_ended:
                execution.transition(execution.session, CallEvent.CALLER_LEFT)
                execution.execution_completed = True
                return
            if self._stop_requested:
                execution.transition(
                    execution.session, CallEvent.SHUTDOWN_REQUESTED, error_code="shutdown",
                )

                await self._safe_finish_matching_incoming(execution.api, execution.session)

                execution.execution_completed = True

                return
        else:
            await self._prepare_call(execution)

        try:
            await self._run_call(execution)
        except Exception as error:
            await self._handle_call_failure(execution, error)

        execution.execution_completed = True

    @staticmethod
    async def _await_incoming_operation(
        execution: _CallExecution, operation, *, answer_confirms: bool = False,
    ):
        """Stop setup or signalling when the caller leaves or the offer expires."""
        if execution.incoming_ended or execution.session.state in TERMINAL_STATES:
            operation.close()
            raise _IncomingCallerLeft

        if execution.stop_event.is_set():
            operation.close()
            raise _IncomingShutdown

        remaining = execution.session.incoming_deadline - monotonic()

        if remaining <= 0:
            operation.close()
            raise _IncomingDeadlineExceeded

        task = asyncio.create_task(operation)
        ended = asyncio.create_task(execution.incoming_end_event.wait())
        stopped = asyncio.create_task(execution.stop_event.wait())
        answered = asyncio.create_task(execution.incoming_answer_event.wait()) if answer_confirms else None

        try:
            pending = {task, ended, stopped}

            if answered is not None:
                pending.add(answered)

            done, _ = await asyncio.wait(
                pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED,
            )

            if task in done:
                try:
                    return await task
                except Exception:
                    if answer_confirms and WhatsAppCallService._answer_before_deadline(execution):
                        return _ANSWER_CONFIRMED

                    raise

            if ended in done:
                raise _IncomingCallerLeft

            if stopped in done:
                raise _IncomingShutdown

            if answer_confirms and WhatsAppCallService._answer_before_deadline(execution):
                return _ANSWER_CONFIRMED

            raise _IncomingDeadlineExceeded
        finally:
            ended.cancel()
            stopped.cancel()

            if answered is not None:
                answered.cancel()

            if not task.done():
                task.cancel()

            await asyncio.gather(
                task, ended, stopped, *([answered] if answered is not None else []),
                return_exceptions=True,
            )

    async def _prepare_call(self, execution: _CallExecution) -> None:
        runtime = CallRuntime(
            ring_timeout_seconds=self._ring_timeout,
            talk_timeout_seconds=self._talk_timeout,
            bridge_timeout_seconds=BRIDGE_NEGOTIATION_TIMEOUT_SECONDS,
        )

        execution.runtime = runtime
        self._active_runtime = runtime

        if self._stop_requested:
            runtime.events.put_nowait(RuntimeEvent.shutdown())

        execution.voice = VoiceBotSession(
            api_key=self._openai_api_key,
            model=self._realtime_model,
            voice=self._realtime_voice,
            language=execution.session.language,
            greeting_text=execution.session.greeting_text,
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
            execution.initial_state.set_result(state)

        if execution.session.direction == CallDirection.INCOMING:
            if state.state == "on-call" and WhatsAppCallService._matches_incoming(
                state, execution.session.call_id, execution.session.sender_id, "on-call",
            ):
                if execution.incoming_answered_at is None:
                    execution.incoming_answered_at = monotonic()

                    execution.incoming_answer_event.set()

                # Apply the answer through the same locked transition callback
                #  used by the application's caller-ended notification. Otherwise
                #  that notification could classify an already answered call as
                #  an unanswered offer while this event waits in the RTC queue.
                if execution.command_started:
                    WhatsAppCallService._apply_observed_incoming_answer(execution)

            if state.state == "idle" and not execution.command_completed:
                execution.incoming_ended = True
                execution.incoming_left_at = monotonic()
                execution.incoming_end_event.set()

            if state.state in {"inc-call", "on-call"} and not WhatsAppCallService._matches_incoming(
                state, execution.session.call_id, execution.session.sender_id, state.state,
            ):
                execution.incoming_ended = True
                execution.incoming_left_at = monotonic()

                execution.incoming_end_event.set()
                execution.runtime.events.put_nowait(RuntimeEvent.caller_left())

                return

        if execution.command_started:
            execution.runtime.events.put_nowait(RuntimeEvent.server_state(state.state, state.reason))

    @staticmethod
    def _matches_incoming(state: ApiCallState | None, call_id: str | None, sender_id: str, kind: str) -> bool:
        info = getattr(state, "info", None)

        return (
            getattr(state, "state", None) == kind and info is not None
            and info.id == call_id and info.wid == sender_id
        )

    @staticmethod
    def _answer_before_deadline(execution: _CallExecution) -> bool:
        observed_at = execution.incoming_answered_at

        return (
            observed_at is not None
            and observed_at < execution.session.incoming_deadline
            and (execution.incoming_left_at is None or observed_at < execution.incoming_left_at)
        )

    @staticmethod
    def _apply_observed_incoming_answer(execution: _CallExecution) -> bool:
        if not WhatsAppCallService._answer_before_deadline(execution):
            return False

        session = execution.session

        if session.state == CallState.RINGING:
            execution.transition(session, CallEvent.REMOTE_ACCEPTED)
            execution.runtime.remote_accepted(session.bridge_ready)

        return session.remote_accepted

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
        if execution.session.direction == CallDirection.INCOMING:
            execution.runtime.start_ringing(deadline=execution.session.incoming_deadline)

            try:
                await self._await_incoming_operation(
                    execution, execution.calls.openAsync(timeout=INITIAL_STATE_TIMEOUT_SECONDS),
                )

                await self._await_incoming_operation(
                    execution,
                    asyncio.wait_for(execution.initial_state, timeout=INITIAL_STATE_TIMEOUT_SECONDS),
                )
            except _IncomingCallerLeft:
                execution.transition(execution.session, CallEvent.CALLER_LEFT)
                return

            except _IncomingShutdown:
                execution.transition(
                    execution.session, CallEvent.SHUTDOWN_REQUESTED, error_code="shutdown",
                )

                await self._safe_finish_matching_incoming(execution.api, execution.session)

                return

            except _IncomingDeadlineExceeded:
                execution.transition(execution.session, CallEvent.INCOMING_EXPIRED)
                await self._safe_finish_matching_incoming(execution.api, execution.session)
                return

            await self._run_incoming(execution)

            return

        await execution.calls.openAsync(timeout=INITIAL_STATE_TIMEOUT_SECONDS)
        state = await asyncio.wait_for(execution.initial_state, timeout=INITIAL_STATE_TIMEOUT_SECONDS)

        if state.state != "idle":
            raise RuntimeError(f"Instance already has a call: {state.state}")

        if self._stop_requested:
            execution.transition(execution.session, CallEvent.SHUTDOWN_REQUESTED, error_code="shutdown")
        else:
            execution.command_started = True

            await execution.api.voip.dialAsync(execution.session.chat_id)

            execution.command_completed = True

            execution.transition(execution.session, CallEvent.DIAL_ACCEPTED)
            execution.runtime.start_ringing()

            # Bridge completion confirms SDP, not ICE/DTLS or live audio.
            execution.bridge_task = asyncio.create_task(execution.calls.startAudioAsync())

            execution.bridge_task.add_done_callback(partial(self._on_bridge_done, execution))

        await self._process_events(execution)

    async def _run_incoming(self, execution: _CallExecution) -> None:
        session = execution.session
        state = execution.calls.state

        if session.state in TERMINAL_STATES:
            await self._safe_finish_matching_incoming(execution.api, session)
            return

        if execution.incoming_ended:
            self._apply_observed_incoming_answer(execution)
            execution.transition(session, CallEvent.CALLER_LEFT)
            return

        if not self._matches_incoming(state, session.call_id, session.sender_id, "inc-call"):
            execution.transition(session, CallEvent.CALLER_LEFT)
            return

        if monotonic() >= session.incoming_deadline:
            execution.transition(session, CallEvent.INCOMING_EXPIRED)
            await self._safe_finish_matching_incoming(execution.api, session)
            return

        if self._stop_requested:
            execution.transition(session, CallEvent.SHUTDOWN_REQUESTED, error_code="shutdown")
            await self._safe_finish_matching_incoming(execution.api, session)
            return

        execution.command_started = True

        try:
            accept_result = await self._await_incoming_operation(
                execution, execution.api.voip.acceptAsync(), answer_confirms=True,
            )
        except _IncomingCallerLeft:
            self._apply_observed_incoming_answer(execution)
            execution.transition(session, CallEvent.CALLER_LEFT)
            await self._safe_finish_matching_incoming(execution.api, session)

            return
        except _IncomingShutdown:
            execution.transition(session, CallEvent.SHUTDOWN_REQUESTED, error_code="shutdown")
            await self._safe_finish_matching_incoming(execution.api, session)
            return
        except _IncomingDeadlineExceeded:
            if not self._apply_observed_incoming_answer(execution):
                execution.transition(session, CallEvent.INCOMING_EXPIRED)
                await self._safe_finish_matching_incoming(execution.api, session)
                return

            accept_result = _ANSWER_CONFIRMED
        except Exception as error:
            if not await self._reconcile_failed_incoming_accept(execution, error):
                return

            accept_result = _ANSWER_CONFIRMED

        if accept_result is not _ANSWER_CONFIRMED:
            execution.command_completed = True
            execution.transition(session, CallEvent.INCOMING_ACCEPTED)
        self._apply_observed_incoming_answer(execution)

        if self._stop_requested:
            execution.transition(session, CallEvent.SHUTDOWN_REQUESTED, error_code="shutdown")
            await self._safe_finish_matching_incoming(execution.api, session)
            return

        if execution.incoming_ended or session.state in TERMINAL_STATES:
            if execution.incoming_ended:
                execution.transition(session, CallEvent.CALLER_LEFT)

            await self._safe_finish_matching_incoming(execution.api, session)

            return

        if not session.remote_accepted and monotonic() >= session.incoming_deadline:
            execution.transition(session, CallEvent.INCOMING_EXPIRED)
            await self._safe_finish_matching_incoming(execution.api, session)
            return

        execution.bridge_task = asyncio.create_task(execution.calls.startAudioAsync())
        execution.bridge_task.add_done_callback(partial(self._on_bridge_done, execution))

        # The state notification may have arrived before acceptAsync returned.
        state = execution.calls.state
        if state is not None and state.state == "on-call":
            execution.runtime.events.put_nowait(RuntimeEvent.server_state("on-call", state.reason))

        await self._process_events(execution)

    async def _reconcile_failed_incoming_accept(
        self, execution: _CallExecution, accept_error: Exception,
    ) -> bool:
        """Classify an accept error using the current call, if it can be read."""
        self._apply_observed_incoming_answer(execution)

        session = execution.session

        if execution.incoming_ended or session.state in TERMINAL_STATES:
            execution.transition(session, CallEvent.CALLER_LEFT)
            return False

        try:
            state = await asyncio.wait_for(
                execution.api.voip.getStateAsync(), MATCHING_CALL_TIMEOUT_SECONDS,
            )
        except Exception:
            raise accept_error

        if self._matches_incoming(state, session.call_id, session.sender_id, "on-call"):
            # The REST response failed, but the server confirms this call was answered.
            execution.incoming_answered_at = monotonic()

            execution.transition(session, CallEvent.REMOTE_ACCEPTED)
            execution.runtime.remote_accepted(session.bridge_ready)

            return True
        if not self._matches_incoming(state, session.call_id, session.sender_id, "inc-call"):
            execution.transition(session, CallEvent.CALLER_LEFT)
            return False

        raise accept_error

    async def _process_events(self, execution: _CallExecution) -> None:
        while execution.session.state not in TERMINAL_STATES:
            if await self._finish_interrupted_incoming(execution):
                break

            event = await execution.runtime.next_event(execution.session.state)

            if await self._finish_interrupted_incoming(execution):
                break

            if event is None:
                await self._handle_timeout(
                    execution.runtime, execution.api, execution.session, execution.transition,
                )
            else:
                await self._handle_event(
                    event, execution.runtime, execution.api, execution.voice,
                    execution.recorder, execution.session, execution.transition,
                )

    async def _finish_interrupted_incoming(self, execution: _CallExecution) -> bool:
        session = execution.session

        if session.direction != CallDirection.INCOMING:
            return False

        self._apply_observed_incoming_answer(execution)

        if execution.incoming_ended:
            execution.transition(session, CallEvent.CALLER_LEFT)
            await self._safe_finish_matching_incoming(execution.api, session)
            return True

        if session.state == CallState.RINGING and monotonic() >= session.incoming_deadline:
            execution.transition(session, CallEvent.INCOMING_EXPIRED)
            await self._safe_finish_matching_incoming(execution.api, session)
            return True

        return False

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

        if execution.session.direction == CallDirection.INCOMING:
            await self._safe_finish_matching_incoming(execution.api, execution.session)
        elif execution.command_completed:
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
        # The command may have reached the server before its response arrived.
        if execution.cancellation is not None and (
            execution.command_started or execution.session.direction == CallDirection.INCOMING
        ):
            with suppress(Exception):
                cleanup = (
                    self._finish_matching_incoming(execution.api, execution.session)
                    if execution.session.direction == CallDirection.INCOMING
                    else self._safe_hang_up(execution.api)
                )

                await self._wait_for_cleanup(
                    asyncio.wait_for(cleanup, timeout=self._shutdown_timeout),
                    execution,
                )

    def _finish_recording(self, execution: _CallExecution) -> None:
        self._active_runtime = None
        self._active_loop = None
        self._active_execution = None
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
        elif event.type == RuntimeEventType.CALLER_LEFT:
            transition(call_session, CallEvent.CALLER_LEFT)
        elif event.type == RuntimeEventType.SHUTDOWN:
            transition(call_session, CallEvent.SHUTDOWN_REQUESTED, error_code="shutdown")

            if call_session.direction == CallDirection.INCOMING:
                await self._safe_finish_matching_incoming(api, call_session)
            else:
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
            if call_session.direction == CallDirection.INCOMING:
                transition(call_session, CallEvent.INCOMING_EXPIRED)
                await self._safe_finish_matching_incoming(api, call_session)
            else:
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

            if call_session.direction == CallDirection.INCOMING:
                await self._safe_finish_matching_incoming(api, call_session)
            else:
                await self._safe_hang_up(api)

    @staticmethod
    async def _hang_up(
        api: GreenAPI,
        call_session: CallSession,
        transition: TransitionCallback,
    ) -> None:
        try:
            if call_session.direction == CallDirection.INCOMING:
                await WhatsAppCallService._finish_matching_incoming(api, call_session)
            else:
                await api.voip.hangUpAsync()
        except Exception as error:
            transition(
                call_session, CallEvent.INTERNAL_ERROR,
                error_code=f"hangup_{type(error).__name__}",
            )

            return

        transition(call_session, CallEvent.HANGUP_CONFIRMED)

    @staticmethod
    async def _finish_matching_incoming(api: GreenAPI | None, session: CallSession) -> None:
        if api is None:
            return

        state = await asyncio.wait_for(api.voip.getStateAsync(), MATCHING_CALL_TIMEOUT_SECONDS)

        if WhatsAppCallService._matches_incoming(
            state, session.call_id, session.sender_id, "inc-call",
        ):
            await asyncio.wait_for(api.voip.rejectAsync(), MATCHING_CALL_TIMEOUT_SECONDS)
        elif WhatsAppCallService._matches_incoming(
            state, session.call_id, session.sender_id, "on-call",
        ):
            await asyncio.wait_for(api.voip.hangUpAsync(), MATCHING_CALL_TIMEOUT_SECONDS)

    async def _safe_finish_matching_incoming(
        self, api: GreenAPI | None, session: CallSession,
    ) -> None:
        try:
            await self._finish_matching_incoming(api, session)
        except Exception as error:
            self._logger.warning(
                "Unable to finish incoming call: session=%s error=%s",
                session.session_id, type(error).__name__,
            )

    @staticmethod
    async def _safe_hang_up(api: GreenAPI) -> None:
        with suppress(Exception):
            await api.voip.hangUpAsync()
