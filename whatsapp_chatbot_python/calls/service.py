from __future__ import annotations
from contextlib import suppress
from whatsapp_api_client_python.API import GreenAPI
from whatsapp_api_client_python.tools.voip import CallAudio
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
        call_session: CallSession,
        transition: TransitionCallback,
    ) -> CallExecutionResult:
        if self._stop_requested:
            transition(call_session, CallEvent.SHUTDOWN_REQUESTED, error_code="shutdown")
            return CallExecutionResult()

        recorder = CallRecorder(self._logger)

        runtime = CallRuntime(
            ring_timeout_seconds=self._ring_timeout,
            talk_timeout_seconds=self._talk_timeout,
            bridge_timeout_seconds=BRIDGE_NEGOTIATION_TIMEOUT_SECONDS,
        )

        self._active_loop = asyncio.get_running_loop()
        self._active_runtime = runtime

        if self._stop_requested:
            runtime.events.put_nowait(RuntimeEvent.shutdown())

        voice = VoiceBotSession(
            api_key=self._openai_api_key,
            model=self._realtime_model,
            voice=self._realtime_voice,
            language=call_session.language,
            on_error=lambda error: runtime.events.put_nowait(RuntimeEvent.voice_failed(error)),
            recorder=recorder,
        )

        async def make_audio_session() -> CallAudio:
            track = await voice.new_output_track()

            try:
                sink = voice.new_input_sink()
            except BaseException:
                track.stop()
                raise

            async def close_audio() -> None:
                try:
                    await sink.close()
                finally:
                    track.stop()

            return CallAudio(track, sink.attach, close_audio)

        try:
            await voice.start()
            api = GreenAPI(self._id_instance, self._api_token_instance, host=self._api_url)
            calls = api.voip.connect(audio_factory=make_audio_session)
        except Exception:
            await voice.close()
            recorder.finish()
            self._active_runtime = None
            self._active_loop = None

            raise

        bridge_task: asyncio.Task[None] | None = None
        dialed = False
        initial_state: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        dial_started = False

        def on_state(state) -> None:
            if not initial_state.done():
                initial_state.set_result(state.state)

            if dial_started:
                runtime.events.put_nowait(RuntimeEvent.server_state(state.state, state.reason))

        def on_end_call(detail) -> None:
            # NOTE: If the server returned the reason, it is a handled case.
            #   Otherwise it falls into the error handler.
            runtime.events.put_nowait(RuntimeEvent.end_call(detail.get("cause", None)))

        def on_disconnect(detail) -> None:
            permanent = bool(detail.get("permanent"))

            if permanent and not initial_state.done():
                initial_state.set_exception(RuntimeError("callsRtc connection refused"))

            runtime.events.put_nowait(RuntimeEvent.disconnect(permanent))

        def on_bridge_done(task: asyncio.Task[None]) -> None:
            if task.cancelled():
                return

            error = task.exception()
            result = RuntimeEvent.bridge_failed(error) if error else RuntimeEvent.bridge_ready()

            runtime.events.put_nowait(result)

        calls.on("state", on_state)
        calls.on("end_call", on_end_call)
        calls.on("disconnect", on_disconnect)

        calls.on(
            "error",
            lambda detail: runtime.events.put_nowait(
                RuntimeEvent.bridge_failed(RuntimeError("callsRtc reported an error"))
            ),
        )

        try:
            await calls.openAsync(timeout=INITIAL_STATE_TIMEOUT_SECONDS)

            state = await asyncio.wait_for(initial_state, timeout=INITIAL_STATE_TIMEOUT_SECONDS)

            if state != "idle":
                raise RuntimeError(f"Instance already has a call: {state}")

            if self._stop_requested:
                transition(call_session, CallEvent.SHUTDOWN_REQUESTED, error_code="shutdown")
            else:
                dial_started = True

                await api.voip.dialAsync(call_session.chat_id)

                dialed = True

                transition(call_session, CallEvent.DIAL_ACCEPTED)
                runtime.start_ringing()

                # The library owns signaling and WebRTC; the voice session supplies audio.
                # Bridge completion confirms the SDP answer, not ICE/DTLS or live audio.
                bridge_task = asyncio.create_task(calls.startAudioAsync())
                bridge_task.add_done_callback(on_bridge_done)

            while call_session.state not in TERMINAL_STATES:
                event = await runtime.next_event(call_session.state)

                if event is None:
                    await self._handle_timeout(runtime, api, call_session, transition)
                else:
                    await self._handle_event(event, runtime, api, voice, recorder, call_session, transition)
        except Exception as error:
            self._logger.error(
                "VoIP execution failed: session=%s error=%s",
                call_session.session_id,
                type(error).__name__,
            )

            transition(
                call_session, CallEvent.INTERNAL_ERROR,
                error_code=type(error).__name__,
            )

            if dialed:
                await self._safe_hang_up(api)
        finally:
            with suppress(Exception):
                await asyncio.wait_for(calls.closeAsync(), timeout=self._shutdown_timeout)

            with suppress(Exception):
                await asyncio.wait_for(voice.close(), timeout=self._shutdown_timeout)

            if bridge_task is not None:
                if not bridge_task.done():
                    bridge_task.cancel()

                with suppress(Exception):
                    await asyncio.wait_for(
                        asyncio.gather(bridge_task, return_exceptions=True),
                        timeout=self._shutdown_timeout,
                    )

            self._active_runtime = None
            self._active_loop = None

        # Encoding runs after the RTC and Realtime connections have closed.
        path = recorder.finish()

        if call_session.state not in {CallState.REMOTE_ENDED, CallState.TALK_TIMEOUT}:
            # Preserve the established policy: discard partial failed calls.
            if path is not None:
                path.unlink(missing_ok=True)

            path = None

        return CallExecutionResult(path)

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
