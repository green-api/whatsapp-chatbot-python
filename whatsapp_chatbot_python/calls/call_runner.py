"""One call: an event loop, owned operations, and a resource scope."""
from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from time import monotonic

from .models import (
    AcceptResult,
    CallerLeft,
    CallCleanupError,
    CallDirection,
    CallEndReason,
    CallEvent,
    CallExecutionResult,
    CallOptions,
    CallSession,
    CallState,
    Command,
    CommandCompleted,
    CommandFailed,
    DeadlineExpired,
    DeadlineKind,
    Deadlines,
    EventSink,
    FinishResult,
    MediaFailed,
    ProviderConfig,
    RemoteAnswered,
    RemoteEnded,
    RuntimeEvent,
    StopRequested,
    TransitionCallback,
)


logger = logging.getLogger(__name__)


class Events:
    """Thread-safe ingress; one consumer orders observations against deadlines."""
    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
        self._pending: RuntimeEvent | None = None
        self._closed = False

    def emit(self, event: RuntimeEvent) -> bool:
        if self._closed:
            logger.debug("[Events.emit] Unable to add the VoIP event: The loop is closed")
            return False

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is self._loop:
            self._queue.put_nowait(event)
            return True

        try:
            self._loop.call_soon_threadsafe(self._deliver, event)
            return True
        except RuntimeError as error:
            logger.warning("[Events.emit] Unable to add the VoIP event: %s", type(error).__name__)
            return False

    def _deliver(self, event: RuntimeEvent) -> None:
        if not self._closed:
            self._queue.put_nowait(event)

    async def receive(self, deadlines: Deadlines) -> RuntimeEvent:
        event = self._pending or await self._next(deadlines)

        if isinstance(event, DeadlineExpired):
            self._pending = None
            return event

        expired = deadlines.expire_before(event.observed_at)
        self._pending = event if expired is not None else None

        return expired or event

    async def _next(self, deadlines: Deadlines) -> RuntimeEvent:
        if not self._queue.empty():
            return self._queue.get_nowait()

        try:
            async with asyncio.timeout(deadlines.next_timeout()):
                return await self._queue.get()
        except TimeoutError:
            expired = deadlines.expire_before(monotonic())

            if expired is None:
                raise RuntimeError("Mailbox timed out without a deadline")

            return expired

    def close(self) -> None:
        self._closed = True


class Operations:
    """Each named operation runs once; its only output is a typed event."""
    def __init__(self, emit: EventSink) -> None:
        self._emit = emit
        self._tasks: dict[Command, asyncio.Task] = {}
        self._stopping = False

    def start(
        self,
        command: Command,
        factory: Callable[[], Awaitable[AcceptResult | None]],
        timeout: float,
    ) -> None:
        if self._stopping or command in self._tasks:
            logger.debug("[Operations.start] Unable to create VoIP task: The task already exists")
            return

        self._tasks[command] = asyncio.create_task(
            self._run(command, factory, timeout),
            name=f"call:{command.value}",
        )

    async def _run(
        self,
        command: Command,
        factory: Callable[[], Awaitable[AcceptResult | None]],
        timeout: float,
    ) -> None:
        try:
            result = await asyncio.wait_for(factory(), timeout)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not self._stopping:
                self._emit(CommandFailed(command, error))
        else:
            if not self._stopping:
                self._emit(CommandCompleted(command, result=result))

    async def close(self) -> None:
        self.begin_stop()
        tasks = tuple(self._tasks.values())

        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

    def begin_stop(self) -> None:
        self._stopping = True


async def finish_cleanup(work: Awaitable[None]) -> None:
    """Drain cleanup despite repeated cancellation, then propagate cancellation."""
    task = asyncio.create_task(work, name="call:cleanup")
    cancelled = False

    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break

    if cancelled:
        # Retrieve a cleanup exception as well; cancellation remains the caller's outcome.
        error = None if task.cancelled() else task.exception()
        raise asyncio.CancelledError from error

    task.result()


class CallResources:
    """Owns acquisition, task shutdown, remote finish and unclaimed recording."""
    def __init__(
        self,
        config: ProviderConfig,
        options: CallOptions,
        session: CallSession,
        emit: EventSink,
    ) -> None:
        from .audio import AudioBridge
        from .greenapi import GreenApiCall
        from .openai import RealtimeConversation
        from .recorder import CallRecorder

        self.options = options
        self._session = session
        self.operations = Operations(emit)
        self.recorder = CallRecorder(options.recording)

        self.voice = RealtimeConversation(
            config,
            options.playback,
            emit,
            session.language,
            options.shutdown_seconds,
        )

        self.audio = AudioBridge(
            self.voice,
            self.voice,
            self.recorder,
            emit,
            options.input_audio,
        )

        self.api = GreenApiCall(
            config,
            session,
            emit,
            options.command_seconds,
            options.shutdown_seconds,
        )

        self._stack = AsyncExitStack()
        self.finish_result: FinishResult | None = None
        self.cleanup_errors: list[CallCleanupError] = []
        self._pending_cleanup: set[asyncio.Task] = set()
        self._closing = False
        self._cleanup_still_running: asyncio.Task | None = None
        self._discard_recording = False

    async def __aenter__(self) -> CallResources:
        try:
            await self._enter_context("voice_close", self.voice)
            self._stack.push_async_callback(self._close_step, "audio_close", self.audio.close)
            await self._enter_context("greenapi_close", self.api)
            self._stack.push_async_callback(self._close_step, "remote_finish", self._finish_remote)
        except BaseException:
            await self.__aexit__(*sys.exc_info())
            raise

        return self

    async def _enter_context(self, stage: str, resource: AbstractAsyncContextManager) -> None:
        await resource.__aenter__()

        self._stack.push_async_callback(
            self._close_step,
            stage,
            resource.__aexit__,
            None,
            None,
            None,
        )

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        try:
            await finish_cleanup(self._close())
        except Exception:
            self.discard_recording()

            if exc is None:
                raise

            exc.add_note("Call resource cleanup also failed; see cleanup logs")
        except BaseException:
            self.discard_recording()
            raise
        finally:
            for error in self.cleanup_errors:
                logger.error(
                    "Call cleanup failed: session=%s stage=%s code=%s",
                    self._session.session_id,
                    error.stage,
                    error.code,
                )

        if exc_type is not None:
            self.discard_recording()

    async def prepare(self) -> None:
        await self.voice.start()
        self._ensure_running()
        await self.api.open(self.audio)

    def _ensure_running(self) -> None:
        if self._closing:
            raise RuntimeError("Call resources are closing")

    async def _finish_remote(self) -> None:
        self.finish_result = await asyncio.wait_for(
            self.api.finish(),
            self.options.shutdown_seconds,
        )

    async def _close(self) -> None:
        self._closing = True
        self.operations.begin_stop()
        operations = asyncio.create_task(self.operations.close(), name="call:stop-operations")

        try:
            await asyncio.wait_for(asyncio.shield(operations), self.options.shutdown_seconds)
        except Exception as error:
            self.cleanup_errors.append(CallCleanupError("operations_close", error))

            self._cleanup_still_running = asyncio.create_task(
                self._close_after_operations(operations), name="call:finish-late-cleanup",
            )

            self._cleanup_still_running.add_done_callback(self._observe_late_cleanup)

            raise

        await self._close_owned()
        self._raise_cleanup_errors()

    async def _close_after_operations(self, operations: asyncio.Task) -> None:
        previous_errors = len(self.cleanup_errors)
        try:
            try:
                await operations
            except Exception as error:
                self.cleanup_errors.append(CallCleanupError("operations_close", error))

            await self._close_owned()
        finally:
            for error in self.cleanup_errors[previous_errors:]:
                logger.error(
                    "Late call cleanup failed: session=%s stage=%s code=%s",
                    self._session.session_id, error.stage, error.code,
                )

            if self._discard_recording:
                self.recorder.discard()

    def _observe_late_cleanup(self, task: asyncio.Task) -> None:
        if task.cancelled():
            logger.error("Late call cleanup was cancelled: session=%s", self._session.session_id)
            return

        error = task.exception()

        if error is not None:
            logger.error(
                "Late call cleanup failed: session=%s type=%s",
                self._session.session_id, type(error).__name__,
            )

    async def _close_owned(self) -> None:
        try:
            await self._stack.aclose()
        finally:
            await self._close_step("recording_close", self._finish_recording)

    def _raise_cleanup_errors(self) -> None:
        if self.cleanup_errors:
            error = self.cleanup_errors[0]
            raise error.cause from error

    def discard_recording(self) -> None:
        if self._cleanup_still_running is not None and not self._cleanup_still_running.done():
            self._discard_recording = True
            return

        self.recorder.discard()

    async def _close_step(
        self, stage: str, operation: Callable[..., Awaitable[None]], *args,
    ) -> None:
        task = asyncio.create_task(operation(*args), name=f"call:close:{stage}")
        timeout = self.options.shutdown_seconds * (2 if stage == "greenapi_close" else 1)

        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except Exception as error:
            if not task.done():
                self._pending_cleanup.add(task)
                task.add_done_callback(lambda done: self._observe_late_close(stage, done))

            if stage == "greenapi_close" and self.api.close_errors:
                self.cleanup_errors.extend(self.api.close_errors)
                return

            if isinstance(error, CallCleanupError):
                self.cleanup_errors.append(error)
                return

            self.cleanup_errors.append(CallCleanupError(stage, error))

    def _observe_late_close(self, stage: str, task: asyncio.Task) -> None:
        self._pending_cleanup.discard(task)

        if task.cancelled():
            logger.error("Late call cleanup cancelled: stage=%s", stage)
            return

        error = task.exception()

        if error is not None:
            logger.error("Late call cleanup failed: stage=%s type=%s", stage, type(error).__name__)

    async def _finish_recording(self) -> None:
        keep = self._session.end_reason in (
            CallEndReason.REMOTE_HANGUP,
            CallEndReason.TALK_TIMEOUT,
        )

        if keep:
            await self.recorder.finish()
            return

        await self.recorder.close()

    def take_result(self, keep_recording: bool) -> CallExecutionResult:
        if not keep_recording:
            self.recorder.discard()
            return CallExecutionResult()

        return CallExecutionResult(self.recorder.take())


_DEADLINE_EVENTS = {
    DeadlineKind.OFFER: CallEvent.INCOMING_EXPIRED,
    DeadlineKind.RING: CallEvent.RING_TIMER_EXPIRED,
    DeadlineKind.TALK: CallEvent.TALK_TIMER_EXPIRED,
}

_START_STATES = {
    CallDirection.INCOMING: CallState.RINGING,
    CallDirection.OUTGOING: CallState.DIALING,
}

_KEEP_RECORDING = frozenset({CallState.REMOTE_ENDED, CallState.TALK_TIMEOUT})


class CallRunner:
    """Single-use scenario. Only the transition callback mutates session state."""
    def __init__(
        self,
        session: CallSession,
        transition: TransitionCallback,
        config: ProviderConfig,
        options: CallOptions,
    ) -> None:
        self.session = session
        self._transition = transition
        self._config = config
        self._options = options
        self._events = Events()
        self._deadlines = Deadlines()
        self._resources: CallResources | None = None

    def request_stop(self) -> None:
        self._events.emit(StopRequested())

    @property
    def cleanup_failed(self) -> bool:
        return self._resources is not None and bool(self._resources.cleanup_errors)

    def notify_incoming_ended(self, call_id: str) -> bool:
        if (
            self.session.direction != CallDirection.INCOMING
            or self.session.call_id != call_id
            or self.session.finished
        ):
            logger.debug(
                "[CallRunner.notify_incoming_ended] call_id=%s Unable create a VoIP incoming notification (direction=%s, call_id=%s, finished=%s)",
                call_id,
                self.session.direction,
                self.session.call_id,
                self.session.finished,
            )

            return False

        return self._events.emit(CallerLeft(call_id))

    async def run(self) -> CallExecutionResult:
        try:
            self._validate_start()

            if self._offer_expired():
                self._apply(CallEvent.INCOMING_EXPIRED)
                await self._reject_expired_offer()
                return CallExecutionResult()

            self._resources = CallResources(
                self._config,
                self._options,
                self.session,
                self._events.emit,
            )

            async with self._resources:
                self._begin_prepare()
                await self._consume_events()

            self._confirm_finish()

            return self._resources.take_result(self.session.state in _KEEP_RECORDING)
        except asyncio.CancelledError:
            self._apply(CallEvent.SHUTDOWN_REQUESTED)
            raise
        except Exception as error:
            code = self._error_code(error)

            self._apply(
                CallEvent.INTERNAL_ERROR,
                error_code=code,
            )

            logger.error(
                "Call execution failed: session=%s type=%s code=%s",
                self.session.session_id,
                type(error).__name__,
                code,
            )

            raise
        finally:
            self._events.close()

            if self._resources is not None:
                self._resources.discard_recording()

    def _confirm_finish(self) -> None:
        if self.session.state != CallState.ENDING:
            return

        result = self._resources.finish_result

        if result in (FinishResult.FINISHED, FinishResult.ALREADY_ENDED):
            self._apply(CallEvent.HANGUP_CONFIRMED)
            return

        code = f"finish_{result.value}" if result else "finish_missing_result"

        self._apply(CallEvent.INTERNAL_ERROR, error_code=code)

    def _error_code(self, error: Exception) -> str:
        if self._resources is not None and self._resources.cleanup_errors:
            cleanup = self._resources.cleanup_errors[0]

            if error is cleanup.cause:
                return cleanup.code

        return getattr(error, "code", type(error).__name__)

    def _validate_start(self) -> None:
        if self.session.state != _START_STATES[self.session.direction]:
            raise ValueError("Dequeue the session before executing a call")

        if self.session.direction == CallDirection.INCOMING:
            if not self.session.call_id or self.session.incoming_deadline is None:
                raise ValueError("Incoming call requires an ID and deadline")

    def _offer_expired(self) -> bool:
        return (
            self.session.direction == CallDirection.INCOMING
            and self.session.incoming_deadline <= monotonic()
        )

    async def _reject_expired_offer(self) -> None:
        from .greenapi import GreenApiCall

        try:
            api = GreenApiCall(
                self._config,
                self.session,
                self._events.emit,
                self._options.command_seconds,
            )

            async with api:
                await asyncio.wait_for(
                    api.reject_offer(), self._options.command_seconds)
        except Exception as error:
            logger.warning(
                "Could not reject expired offer: session=%s error=%s",
                self.session.session_id,
                type(error).__name__,
            )

    def _begin_prepare(self) -> None:
        deadline = self.session.incoming_deadline

        if deadline is not None and deadline <= monotonic():
            self._apply(CallEvent.INCOMING_EXPIRED)
            return

        self._deadlines.arm(DeadlineKind.SETUP, monotonic() + self._options.setup_seconds)

        if deadline is not None:
            self._deadlines.arm(DeadlineKind.OFFER, deadline)

        self._start(Command.PREPARE, self._resources.prepare, self._options.setup_seconds)

    async def _consume_events(self) -> None:
        while not self.session.finished and self.session.state != CallState.ENDING:
            event = await self._events.receive(self._deadlines)
            self._dispatch(event)

    def _dispatch(self, event: RuntimeEvent) -> None:
        match event:
            case CommandCompleted(command=command, result=result):
                self._command_completed(command, event.observed_at, result)
            case CommandFailed(command=command, error=error):
                self._command_failed(command, error)
            case RemoteAnswered(call_id=call_id):
                self._answered(call_id, event.observed_at)
            case RemoteEnded(reason=reason):
                self._apply(CallEvent.REMOTE_IDLE, remote_reason=reason)
            case CallerLeft(call_id=call_id):
                self._caller_left(call_id)
            case StopRequested():
                self._apply(CallEvent.SHUTDOWN_REQUESTED)
            case DeadlineExpired(kind=kind):
                self._deadline(kind)
            case MediaFailed(error=error):
                raise error

    def _command_completed(
        self, command: Command, at: float, result: AcceptResult | None,
    ) -> None:
        match command:
            case Command.PREPARE:
                self._deadlines.disarm(DeadlineKind.SETUP)
                self._begin_call()
            case Command.DIAL:
                self._apply(CallEvent.DIAL_ACCEPTED)

                if self.session.state == CallState.RINGING:
                    self._deadlines.arm(DeadlineKind.RING, at + self._options.ring_seconds)

                self._begin_bridge()
            case Command.ACCEPT:
                if result is AcceptResult.COMMAND_ACKNOWLEDGED:
                    self._apply(CallEvent.INCOMING_ACCEPTED)

                self._begin_bridge()
            case Command.BRIDGE:
                self._deadlines.disarm(DeadlineKind.BRIDGE)
                self._apply(CallEvent.BRIDGE_READY)
                self._activate_conversation(at)
            case Command.RECONCILE | Command.GREET:
                return

    def _begin_call(self) -> None:
        if self.session.direction == CallDirection.INCOMING:
            if self._offer_expired():
                self._apply(CallEvent.INCOMING_EXPIRED)
                return

            self._start(Command.ACCEPT, self._resources.api.accept)

            return

        self._start(Command.DIAL, self._resources.api.dial)

    def _command_failed(self, command: Command, error: Exception) -> None:
        if (
            command in (Command.ACCEPT, Command.RECONCILE)
            and getattr(error, "code", None) == "offer_gone"
        ):
            event = CallEvent.INCOMING_EXPIRED if self._offer_expired() else CallEvent.CALLER_LEFT
            self._apply(event)
            return

        if command not in (Command.ACCEPT, Command.RECONCILE):
            raise error

        if self.session.remote_accepted:
            return

        if command == Command.RECONCILE:
            raise error

        self._start(
            Command.RECONCILE,
            lambda: self._resources.api.reconcile_accept(error),
        )

    def _answered(self, call_id: str | None, at: float) -> None:
        if self.session.direction == CallDirection.INCOMING and call_id != self.session.call_id:
            return

        if self.session.remote_accepted:
            return

        self._apply(CallEvent.REMOTE_ACCEPTED)
        self._deadlines.disarm(DeadlineKind.OFFER, DeadlineKind.RING)
        self._begin_bridge()
        self._activate_conversation(at)

    def _begin_bridge(self) -> None:
        if self.session.bridge_ready:
            return

        self._deadlines.arm(DeadlineKind.BRIDGE, monotonic() + self._options.setup_seconds)
        self._start(Command.BRIDGE, self._resources.api.start_audio, self._options.setup_seconds)

    def _activate_conversation(self, at: float) -> None:
        if self.session.state != CallState.IN_CALL:
            return

        self._deadlines.disarm(DeadlineKind.BRIDGE)
        self._deadlines.arm(DeadlineKind.TALK, at + self._options.talk_seconds)
        self._resources.audio.activate_recording()

        self._start(
            Command.GREET,
            lambda: self._resources.voice.greet_once(self.session.greeting_text),
        )

    def _caller_left(self, call_id: str) -> None:
        if self.session.direction != CallDirection.INCOMING or call_id != self.session.call_id:
            return

        event = CallEvent.REMOTE_IDLE if self.session.remote_accepted else CallEvent.CALLER_LEFT

        self._apply(event)

    def _deadline(self, kind: DeadlineKind) -> None:
        event = _DEADLINE_EVENTS.get(kind)

        if event is None:
            raise TimeoutError(f"Call {kind.value} deadline expired")

        self._apply(event)

    def _start(
        self,
        command: Command,
        factory: Callable[[], Awaitable[AcceptResult | None]],
        timeout: float | None = None,
    ) -> None:
        self._resources.operations.start(
            command, factory, timeout or self._options.command_seconds)

    def _apply(self, event: CallEvent, **details):
        transition = self._transition(self.session, event, **details)

        if transition.changed:
            logger.debug(
                "Call transition: %s -> %s (%s)",
                transition.previous.value,
                transition.current.value,
                event.value,
            )

        return transition
