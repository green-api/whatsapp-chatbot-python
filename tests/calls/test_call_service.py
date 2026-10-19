from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Thread
from unittest.mock import AsyncMock, Mock, patch
from time import monotonic
import asyncio
import logging
import unittest

from whatsapp_api_client_python.tools.voip import CallInfo
from whatsapp_chatbot_python.calls.models import CallEndReason, CallEvent, CallSession, CallState
from whatsapp_chatbot_python.calls.service import WhatsAppCallService
from whatsapp_chatbot_python.calls.state_machine import CallStateMachine


class FakeVoice:
    instances = []
    fail_start = False
    fail_greeting = False

    def __init__(self, **options):
        self.options = options
        self.greetings = 0
        self.closed = False
        self.instances.append(self)

    async def start(self):
        if self.fail_start:
            raise RuntimeError("OpenAI unavailable")

    async def close(self):
        self.closed = True

    async def greet_once(self):
        if self.greetings:
            return

        self.greetings += 1

        if self.fail_greeting:
            self.options["on_error"](RuntimeError("OpenAI failed"))

    async def new_output_track(self):
        self.tracks = getattr(self, "tracks", [])
        track = FakeTrack()

        self.tracks.append(track)

        return track

    def new_input_sink(self):
        self.sinks = getattr(self, "sinks", [])
        sink = FakeSink()

        self.sinks.append(sink)

        return sink


class FakeTrack:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeSink:
    def __init__(self):
        self.closed = False

    async def attach(self, track):
        pass

    async def close(self):
        self.closed = True


class FakeCalls:
    def __init__(self, client):
        self.client = client
        self.listeners = {}
        self.closed = False
        self.audio_factory = None
        self.devices = []
        self.state = None

    def on(self, name, callback):
        self.listeners.setdefault(name, []).append(callback)

    def emit(self, name, detail=None):
        if name == "state":
            self.state = detail
        for callback in self.listeners.get(name, []):
            callback(detail)

    async def openAsync(self, *, timeout):
        self.client.actions.append("open")

        if self.client.open_error:
            raise RuntimeError("callsRtc connection refused")

        asyncio.get_running_loop().call_soon(
            self.emit, "state", CallStateDetail(self.client.initial_state, info=self.client.initial_info)
        )

    async def startAudioAsync(self):
        self.client.actions.append("bridge")
        self.devices.append(await self.audio_factory())

        if self.client.bridge_error:
            raise RuntimeError("audio bridge failed")

        if self.client.answer and not self.client.answer_after_bridge:
            self.emit("state", CallStateDetail("on-call", info=self.client.initial_info))

        if self.client.answer and self.client.answer_after_bridge:
            asyncio.get_running_loop().call_later(
                0.001, self.emit, "state",
                CallStateDetail("on-call", info=self.client.initial_info),
            )

        if self.client.reconnect:
            self.emit("disconnect", {"permanent": False})
            await self.devices[-1].close()
            self.devices.append(await self.audio_factory())

        if self.client.remote_hangup:
            asyncio.get_running_loop().call_later(
                0.01, self.emit, "state",
                CallStateDetail("idle", "hangup"),
            )

    async def closeAsync(self):
        if self.client.close_hangs:
            await asyncio.Event().wait()

        for audio in self.devices:
            await audio.close()

        self.closed = True


class CallStateDetail:
    def __init__(self, state, reason=None, info=None):
        self.state = state
        self.reason = reason
        self.info = info


class FakeClient:
    initial_state = "idle"
    initial_info = None
    answer = True
    remote_hangup = True
    bridge_error = False
    open_error = False
    reconnect = False
    answer_after_bridge = False
    close_hangs = False
    instances = []

    def __init__(self, id_instance, api_token_instance, *, host):
        self.voip = self
        self.calls = FakeCalls(self)
        self.actions = []
        self.credentials = (id_instance, api_token_instance, host)
        self.session = self
        self.instances.append(self)

    def close(self):
        pass

    def connect(self, *, audio_factory):
        self.calls.audio_factory = audio_factory
        return self.calls

    async def dialAsync(self, target):
        self.actions.append(("dial", target))

    async def acceptAsync(self):
        self.actions.append("accept")
        self.calls.emit("state", CallStateDetail("on-call", info=self.initial_info))

    async def rejectAsync(self):
        self.actions.append("reject")

    async def getStateAsync(self):
        return self.calls.state

    async def hangUpAsync(self):
        self.actions.append("hangup")


class CallServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name, value in {
            "initial_state": "idle",
            "initial_info": None,
            "answer": True,
            "remote_hangup": True,
            "bridge_error": False,
            "open_error": False,
            "reconnect": False,
            "answer_after_bridge": False,
            "close_hangs": False,
        }.items():
            setattr(FakeClient, name, value)

        FakeClient.instances = []
        FakeVoice.instances = []
        FakeVoice.fail_start = False
        FakeVoice.fail_greeting = False

        for target, fake in (
            ("whatsapp_chatbot_python.calls.service.GreenAPI", FakeClient),
            ("whatsapp_chatbot_python.calls.service.VoiceBotSession", FakeVoice),
        ):
            patcher = patch(target, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_session(self):
        fsm = CallStateMachine()
        session = CallSession("sender@c.us", "sender@c.us", "ru")

        fsm.apply(session, CallEvent.ENQUEUED)
        fsm.apply(session, CallEvent.DEQUEUED)

        return session, fsm.apply

    def make_service(self, ring=1, talk=1, shutdown=10):
        logger = logging.getLogger("call-service-test")
        logger.disabled = True
        return WhatsAppCallService(
            api_url="https://example.invalid", id_instance="1",
            api_token_instance="token", openai_api_key="test-key",
            realtime_model="gpt-realtime-2.1", realtime_voice="marin",
            ring_timeout_seconds=ring, talk_timeout_seconds=talk,
            shutdown_timeout_seconds=shutdown, logger=logger,
        )

    async def test_dial_voice_bridge_and_remote_hangup(self):
        session, transition = self.make_session()

        await self.make_service().execute(session, transition)

        client = FakeClient.instances[0]
        voice = FakeVoice.instances[0]

        self.assertEqual(client.actions, ["open", ("dial", "sender@c.us"), "bridge"])
        self.assertEqual(session.state, CallState.REMOTE_ENDED)
        self.assertEqual(session.end_reason, CallEndReason.REMOTE_HANGUP)
        self.assertEqual(voice.greetings, 1)
        self.assertTrue(voice.closed)
        self.assertTrue(client.calls.closed)
        self.assertTrue(voice.tracks[0].stopped)
        self.assertTrue(voice.sinks[0].closed)

    async def test_reconnect_keeps_one_conversation_and_greeting(self):
        FakeClient.reconnect = True
        session, transition = self.make_session()

        await self.make_service().execute(session, transition)
        self.assertEqual(len(FakeVoice.instances), 1)
        self.assertEqual(FakeVoice.instances[0].greetings, 1)
        self.assertEqual(len(FakeClient.instances[0].calls.devices), 2)
        self.assertEqual(len(FakeVoice.instances[0].tracks), 2)
        self.assertTrue(all(track.stopped for track in FakeVoice.instances[0].tracks))
        self.assertTrue(all(sink.closed for sink in FakeVoice.instances[0].sinks))

    async def test_bridge_before_answer_greets_only_after_answer(self):
        FakeClient.answer_after_bridge = True
        session, transition = self.make_session()

        await self.make_service().execute(session, transition)
        self.assertEqual(session.state, CallState.REMOTE_ENDED)
        self.assertEqual(FakeVoice.instances[0].greetings, 1)

    async def test_voice_failure_hangs_up_once(self):
        FakeClient.remote_hangup = False
        FakeVoice.fail_greeting = True
        session, transition = self.make_session()

        await self.make_service().execute(session, transition)
        self.assertEqual(session.state, CallState.FAILED)
        self.assertEqual(session.end_reason, CallEndReason.ERROR)
        self.assertEqual(FakeClient.instances[0].actions.count("hangup"), 1)

    async def test_openai_start_failure_does_not_dial(self):
        FakeVoice.fail_start = True
        session, transition = self.make_session()

        with self.assertRaises(RuntimeError):
            await self.make_service().execute(session, transition)

        self.assertEqual(FakeClient.instances, [])
        self.assertTrue(FakeVoice.instances[0].closed)

    async def test_ring_and_talk_timeout_hang_up(self):
        for answer, expected in ((False, CallState.RING_TIMEOUT), (True, CallState.TALK_TIMEOUT)):
            with self.subTest(expected=expected):
                FakeClient.answer = answer
                FakeClient.remote_hangup = False
                session, transition = self.make_session()

                await self.make_service(ring=0.01, talk=0.01).execute(session, transition)
                self.assertEqual(session.state, expected)
                self.assertEqual(FakeClient.instances[-1].actions.count("hangup"), 1)

    async def test_busy_instance_is_not_dialed(self):
        FakeClient.initial_state = "on-call"
        session, transition = self.make_session()

        await self.make_service().execute(session, transition)
        self.assertEqual(session.state, CallState.FAILED)
        self.assertEqual(FakeClient.instances[0].actions, ["open"])

    async def test_socket_open_failure_does_not_dial(self):
        FakeClient.open_error = True
        session, transition = self.make_session()

        await self.make_service().execute(session, transition)

        self.assertEqual(session.state, CallState.FAILED)
        self.assertEqual(FakeClient.instances[0].actions, ["open"])
        self.assertTrue(FakeClient.instances[0].calls.closed)

    async def test_hung_connection_close_has_deadline(self):
        FakeClient.close_hangs = True
        session, transition = self.make_session()

        await asyncio.wait_for(self.make_service(shutdown=0.01).execute(session, transition), timeout=1)
        self.assertEqual(session.state, CallState.REMOTE_ENDED)

    async def test_bridge_failure_hangs_up(self):
        FakeClient.bridge_error = True
        session, transition = self.make_session()

        await self.make_service().execute(session, transition)
        self.assertEqual(session.state, CallState.FAILED)
        self.assertEqual(FakeClient.instances[0].actions.count("hangup"), 1)

    async def test_shutdown_hangs_up(self):
        FakeClient.answer = False
        FakeClient.remote_hangup = False
        session, transition = self.make_session()

        service = self.make_service()
        task = asyncio.create_task(service.execute(session, transition))

        while session.state != CallState.RINGING:
            await asyncio.sleep(0)

        service.request_stop()

        await task

        self.assertEqual(session.end_reason, CallEndReason.SHUTDOWN)
        self.assertEqual(FakeClient.instances[0].actions.count("hangup"), 1)

    async def test_shutdown_from_another_thread_hangs_up(self):
        FakeClient.answer = False
        FakeClient.remote_hangup = False
        session, transition = self.make_session()
        service = self.make_service()
        task = asyncio.create_task(service.execute(session, transition))

        async def wait_for_ringing():
            while session.state != CallState.RINGING:
                if task.done():
                    await task
                    self.fail(f"Call finished before ringing: {session.state}")
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_for_ringing(), timeout=1)

        thread = Thread(target=service.request_stop)
        thread.start()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        await asyncio.wait_for(task, timeout=1)

        self.assertEqual(session.end_reason, CallEndReason.SHUTDOWN)
        self.assertEqual(FakeClient.instances[0].actions.count("hangup"), 1)

    async def test_stopped_executor_does_not_start_resources(self):
        service = self.make_service()
        service.request_stop()
        for _ in range(2):
            session, transition = self.make_session()
            result = await service.execute(session, transition)
            self.assertEqual(session.end_reason, CallEndReason.SHUTDOWN)
            self.assertIsNone(result.recording_path)
        self.assertEqual(FakeClient.instances, [])
        self.assertEqual(FakeVoice.instances, [])

    async def test_executor_can_run_successive_calls(self):
        service = self.make_service()
        for _ in range(2):
            session, transition = self.make_session()
            await service.execute(session, transition)
            self.assertEqual(session.state, CallState.REMOTE_ENDED)
        self.assertEqual(len(FakeClient.instances), 2)
        self.assertEqual(len(FakeVoice.instances), 2)
        self.assertTrue(all(voice.closed for voice in FakeVoice.instances))
        self.assertIsNone(service._active_loop)
        self.assertIsNone(service._active_runtime)

    async def test_failed_conversation_discards_partial_recording(self):
        FakeClient.remote_hangup = False
        FakeVoice.fail_greeting = True
        session, transition = self.make_session()

        with NamedTemporaryFile(suffix=".mp3", delete=False) as file:
            path = Path(file.name)

        self.addCleanup(path.unlink, missing_ok=True)

        class FakeRecorder:
            def __init__(self, logger):
                pass

            def start(self):
                pass

            def finish(self):
                return path

        with patch("whatsapp_chatbot_python.calls.service.CallRecorder", FakeRecorder):
            result = await self.make_service().execute(session, transition)

        self.assertEqual(session.end_reason, CallEndReason.ERROR)
        self.assertIsNone(result.recording_path)
        self.assertFalse(path.exists())

    def make_incoming_session(self, *, seconds=60, call_id="call-1"):
        fsm = CallStateMachine()
        session = CallSession.incoming(
            sender_id="sender@c.us", call_id=call_id, language="ru",
            incoming_deadline=monotonic() + seconds,
            greeting_text="Здравствуйте! Что хотите обсудить?",
        )
        fsm.apply(session, CallEvent.ENQUEUED)
        fsm.apply(session, CallEvent.DEQUEUED)
        return session, fsm.apply

    async def test_incoming_accepts_matching_call_before_audio(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        session, transition = self.make_incoming_session()

        await self.make_service().execute(session, transition)

        self.assertEqual(FakeClient.instances[0].actions[:3], ["open", "accept", "bridge"])
        self.assertEqual(session.state, CallState.REMOTE_ENDED)
        self.assertEqual(session.end_reason, CallEndReason.REMOTE_HANGUP)
        self.assertTrue(session.incoming_accept_completed)
        self.assertIsNotNone(session.answered_at)
        self.assertEqual(FakeVoice.instances[0].options["greeting_text"], session.greeting_text)
        self.assertEqual(FakeVoice.instances[0].greetings, 1)

    async def test_voice_service_uses_explicit_credentials_despite_environment(self):
        import os

        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        session, transition = self.make_incoming_session()
        with patch.dict(os.environ, {
            "ID_INSTANCE": "ambient-instance",
            "API_TOKEN_INSTANCE": "ambient-token",
            "OPENAI_API_KEY": "ambient-openai-key",
        }):
            await self.make_service().execute(session, transition)

        self.assertEqual(
            FakeClient.instances[0].credentials,
            ("1", "token", "https://example.invalid"),
        )
        self.assertEqual(FakeVoice.instances[0].options["api_key"], "test-key")

    async def test_incoming_other_call_is_never_accepted_or_ended(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("new-call", "sender@c.us", None)
        session, transition = self.make_incoming_session()

        await self.make_service().execute(session, transition)

        self.assertEqual(session.state, CallState.REJECTED)
        self.assertEqual(session.end_reason, CallEndReason.CALLER_LEFT)
        self.assertEqual(FakeClient.instances[0].actions, ["open"])

    async def test_incoming_ended_before_execute_uses_no_resources(self):
        session, transition = self.make_incoming_session()
        transition(session, CallEvent.CALLER_LEFT)

        result = await self.make_service().execute(session, transition)

        self.assertIsNone(result.recording_path)
        self.assertEqual(session.state, CallState.REJECTED)
        self.assertEqual(FakeClient.instances, [])
        self.assertEqual(FakeVoice.instances, [])

    async def test_incoming_deadline_cancels_pending_accept(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        session, transition = self.make_incoming_session(seconds=0.03)

        async def slow_accept(client):
            client.actions.append("accept")
            await asyncio.Event().wait()

        with patch.object(FakeClient, "acceptAsync", slow_accept):
            await asyncio.wait_for(self.make_service().execute(session, transition), timeout=1)

        self.assertEqual(session.state, CallState.RING_TIMEOUT)
        self.assertEqual(session.end_reason, CallEndReason.INCOMING_EXPIRED)
        self.assertEqual(FakeClient.instances[0].actions, ["open", "accept", "reject"])

    async def test_matching_on_call_before_deadline_wins_over_pending_accept(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        session, transition = self.make_incoming_session(seconds=0.2)

        async def accepted_without_rest_response(client):
            client.actions.append("accept")
            client.calls.emit("state", CallStateDetail("on-call", info=client.initial_info))
            await asyncio.Event().wait()

        async def bridge_after_ring_deadline(calls):
            calls.client.actions.append("bridge")
            await asyncio.sleep(0.25)
            calls.emit("state", CallStateDetail("idle", "hangup"))

        with patch.object(FakeClient, "acceptAsync", accepted_without_rest_response), \
             patch.object(FakeCalls, "startAudioAsync", bridge_after_ring_deadline):
            await asyncio.wait_for(self.make_service().execute(session, transition), timeout=1)

        self.assertEqual(session.state, CallState.REMOTE_ENDED)
        self.assertEqual(session.end_reason, CallEndReason.REMOTE_HANGUP)
        self.assertIsNotNone(session.answered_at)
        self.assertFalse(session.incoming_accept_completed)
        self.assertEqual(FakeClient.instances[0].actions[:3], ["open", "accept", "bridge"])

    async def test_on_call_after_caller_left_does_not_reclassify_unanswered_offer(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        session, transition = self.make_incoming_session()
        service = self.make_service()
        accept_started = asyncio.Event()

        async def late_answer(client):
            client.actions.append("accept")
            accept_started.set()
            await asyncio.Event().wait()

        with patch.object(FakeClient, "acceptAsync", late_answer):
            task = asyncio.create_task(service.execute(session, transition))
            await asyncio.wait_for(accept_started.wait(), timeout=1)
            self.assertTrue(service.notify_incoming_ended("call-1"))
            await asyncio.sleep(0)
            FakeClient.instances[0].calls.emit(
                "state", CallStateDetail("on-call", info=FakeClient.initial_info),
            )
            await asyncio.wait_for(task, timeout=1)

        self.assertEqual(session.state, CallState.REJECTED)
        self.assertEqual(session.end_reason, CallEndReason.CALLER_LEFT)

    async def test_incoming_ended_during_execute_handoff_uses_no_resources(self):
        import whatsapp_chatbot_python.calls.service as call_service

        session, transition = self.make_incoming_session()
        original_execution = call_service._CallExecution
        service = self.make_service()

        def end_during_handoff(*args):
            execution = original_execution(*args)
            self.assertFalse(service.notify_incoming_ended("call-1"))
            transition(session, CallEvent.CALLER_LEFT)
            return execution

        with patch.object(call_service, "_CallExecution", side_effect=end_during_handoff):
            await service.execute(session, transition)

        self.assertEqual(session.state, CallState.REJECTED)
        self.assertEqual(FakeClient.instances, [])
        self.assertEqual(FakeVoice.instances, [])

    async def test_accept_error_after_caller_left_is_not_internal_failure(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        session, transition = self.make_incoming_session()

        async def failed_accept(client):
            client.actions.append("accept")
            client.calls.emit("state", CallStateDetail("idle"))
            raise RuntimeError("offer gone")

        with patch.object(FakeClient, "acceptAsync", failed_accept):
            await self.make_service().execute(session, transition)

        self.assertEqual(session.state, CallState.REJECTED)
        self.assertEqual(session.end_reason, CallEndReason.CALLER_LEFT)
        self.assertNotIn("bridge", FakeClient.instances[0].actions)

    async def test_accept_error_after_new_call_does_not_touch_new_call(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        session, transition = self.make_incoming_session()

        async def failed_accept(client):
            client.actions.append("accept")
            client.calls.state = CallStateDetail(
                "inc-call", info=CallInfo("call-2", "sender@c.us", None),
            )
            raise RuntimeError("offer replaced")

        with patch.object(FakeClient, "acceptAsync", failed_accept):
            await self.make_service().execute(session, transition)

        self.assertEqual(session.state, CallState.REJECTED)
        self.assertEqual(session.end_reason, CallEndReason.CALLER_LEFT)
        self.assertEqual(FakeClient.instances[0].actions, ["open", "accept"])

    async def test_accept_error_while_offer_is_live_remains_failure(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        session, transition = self.make_incoming_session()

        async def failed_accept(client):
            client.actions.append("accept")
            raise RuntimeError("accept unavailable")

        with patch.object(FakeClient, "acceptAsync", failed_accept):
            await self.make_service().execute(session, transition)

        self.assertEqual(session.state, CallState.FAILED)
        self.assertEqual(session.end_reason, CallEndReason.ERROR)
        self.assertEqual(FakeClient.instances[0].actions, ["open", "accept", "reject"])

    async def test_accept_error_with_unavailable_state_remains_failure(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        session, transition = self.make_incoming_session()

        async def failed_accept(client):
            client.actions.append("accept")
            raise RuntimeError("response lost")

        async def unavailable_state(client):
            raise RuntimeError("state unavailable")

        with patch.object(FakeClient, "acceptAsync", failed_accept), \
             patch.object(FakeClient, "getStateAsync", unavailable_state):
            await self.make_service().execute(session, transition)

        self.assertEqual(session.state, CallState.FAILED)
        self.assertEqual(session.end_reason, CallEndReason.ERROR)

    async def test_accept_error_with_matching_on_call_preserves_answer(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        session, transition = self.make_incoming_session()

        async def failed_after_answer(client):
            client.actions.append("accept")
            client.calls.emit("state", CallStateDetail("on-call", info=client.initial_info))
            raise RuntimeError("response lost")

        with patch.object(FakeClient, "acceptAsync", failed_after_answer):
            await self.make_service().execute(session, transition)

        self.assertEqual(session.state, CallState.REMOTE_ENDED)
        self.assertIsNotNone(session.answered_at)
        self.assertNotEqual(session.end_reason, CallEndReason.ERROR)

    async def test_accept_error_with_server_confirmed_on_call_preserves_answer(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        session, transition = self.make_incoming_session()

        async def failed_accept(client):
            client.actions.append("accept")
            raise RuntimeError("response lost")

        async def server_state(client):
            return CallStateDetail("on-call", info=client.initial_info)

        with patch.object(FakeClient, "acceptAsync", failed_accept), \
             patch.object(FakeClient, "getStateAsync", server_state):
            await self.make_service().execute(session, transition)

        self.assertEqual(session.state, CallState.REMOTE_ENDED)
        self.assertIsNotNone(session.answered_at)

    async def test_observed_answer_is_applied_before_external_caller_left(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        session, transition = self.make_incoming_session()

        async def answer_then_leave(client):
            client.actions.append("accept")
            client.calls.emit("state", CallStateDetail("on-call", info=client.initial_info))
            self.assertEqual(session.state, CallState.CONNECTING)
            transition(session, CallEvent.CALLER_LEFT)
            raise RuntimeError("response lost")

        with patch.object(FakeClient, "acceptAsync", answer_then_leave):
            await self.make_service().execute(session, transition)

        self.assertEqual(session.state, CallState.REMOTE_ENDED)
        self.assertEqual(session.end_reason, CallEndReason.REMOTE_HANGUP)

    async def test_incoming_deadline_cancels_voice_preparation(self):
        session, transition = self.make_incoming_session(seconds=0.03)
        service = self.make_service()

        async def slow_start(voice):
            await asyncio.Event().wait()

        with patch.object(FakeVoice, "start", slow_start), \
             patch.object(service, "reject_incoming", new_callable=AsyncMock) as reject:
            await asyncio.wait_for(service.execute(session, transition), timeout=1)

        self.assertEqual(session.state, CallState.RING_TIMEOUT)
        self.assertEqual(session.end_reason, CallEndReason.INCOMING_EXPIRED)
        reject.assert_awaited_once_with("call-1", "sender@c.us")
        self.assertTrue(FakeVoice.instances[0].closed)
        self.assertEqual(FakeClient.instances, [])

    async def test_incoming_caller_leaving_interrupts_voice_preparation(self):
        session, transition = self.make_incoming_session()
        service = self.make_service()
        preparing = asyncio.Event()

        async def slow_start(voice):
            preparing.set()
            await asyncio.Event().wait()

        with patch.object(FakeVoice, "start", slow_start):
            task = asyncio.create_task(service.execute(session, transition))
            try:
                await asyncio.wait_for(preparing.wait(), timeout=1)
                self.assertTrue(service.notify_incoming_ended("call-1"))
                await asyncio.wait_for(task, timeout=1)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(session.state, CallState.REJECTED)
        self.assertEqual(session.end_reason, CallEndReason.CALLER_LEFT)
        self.assertEqual(FakeClient.instances, [])

    async def test_incoming_deadline_cancels_calls_connection_open(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        session, transition = self.make_incoming_session(seconds=0.03)

        async def slow_open(calls, *, timeout):
            calls.client.actions.append("open")
            await asyncio.Event().wait()

        with patch.object(FakeCalls, "openAsync", slow_open):
            await asyncio.wait_for(self.make_service().execute(session, transition), timeout=1)

        self.assertEqual(session.state, CallState.RING_TIMEOUT)
        self.assertEqual(session.end_reason, CallEndReason.INCOMING_EXPIRED)
        self.assertEqual(FakeClient.instances[0].actions, ["open"])

    async def test_incoming_caller_left_signal_wakes_active_call(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        FakeClient.remote_hangup = False
        session, transition = self.make_incoming_session()
        service = self.make_service()
        task = asyncio.create_task(service.execute(session, transition))

        try:
            async with asyncio.timeout(1):
                while session.state != CallState.IN_CALL:
                    await asyncio.sleep(0)
            self.assertFalse(service.notify_incoming_ended("older-call"))
            self.assertTrue(service.notify_incoming_ended("call-1"))
            await asyncio.wait_for(task, timeout=1)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(session.state, CallState.REMOTE_ENDED)
        self.assertEqual(session.end_reason, CallEndReason.REMOTE_HANGUP)
        self.assertFalse(service.notify_incoming_ended("call-1"))

    async def test_incoming_bridge_failure_hangs_up_matching_call(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        FakeClient.bridge_error = True
        FakeClient.remote_hangup = False
        session, transition = self.make_incoming_session()

        await self.make_service().execute(session, transition)

        self.assertEqual(session.state, CallState.FAILED)
        self.assertEqual(FakeClient.instances[0].actions.count("hangup"), 1)

    async def test_incoming_caller_leaves_while_accept_is_pending(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        session, transition = self.make_incoming_session()
        service = self.make_service()
        accept_started = asyncio.Event()
        release_accept = asyncio.Event()

        async def pending_accept(client):
            client.actions.append("accept")
            accept_started.set()
            await release_accept.wait()
            client.calls.emit("state", CallStateDetail("on-call", info=client.initial_info))

        with patch.object(FakeClient, "acceptAsync", pending_accept):
            task = asyncio.create_task(service.execute(session, transition))
            try:
                await asyncio.wait_for(accept_started.wait(), timeout=1)
                self.assertTrue(service.notify_incoming_ended("call-1"))
                await asyncio.sleep(0)
                release_accept.set()
                await asyncio.wait_for(task, timeout=1)
            finally:
                release_accept.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(session.state, CallState.REJECTED)
        self.assertEqual(session.end_reason, CallEndReason.CALLER_LEFT)
        self.assertNotIn("bridge", FakeClient.instances[0].actions)
        self.assertEqual(FakeClient.instances[0].actions.count("hangup"), 1)

    async def test_new_call_after_accept_does_not_get_hung_up(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        session, transition = self.make_incoming_session()

        async def replaced_after_accept(client):
            client.actions.append("accept")
            client.calls.emit("state", CallStateDetail(
                "on-call", info=CallInfo("call-2", "sender@c.us", None),
            ))

        with patch.object(FakeClient, "acceptAsync", replaced_after_accept):
            await self.make_service().execute(session, transition)

        self.assertEqual(session.state, CallState.REJECTED)
        self.assertEqual(session.end_reason, CallEndReason.CALLER_LEFT)
        self.assertEqual(FakeClient.instances[0].actions, ["open", "accept"])

    async def test_shutdown_interrupts_pending_incoming_accept(self):
        FakeClient.initial_state = "inc-call"
        FakeClient.initial_info = CallInfo("call-1", "sender@c.us", None)
        session, transition = self.make_incoming_session()
        service = self.make_service()
        accept_started = asyncio.Event()

        async def pending_accept(client):
            client.actions.append("accept")
            accept_started.set()
            await asyncio.Event().wait()

        with patch.object(FakeClient, "acceptAsync", pending_accept):
            task = asyncio.create_task(service.execute(session, transition))
            try:
                await asyncio.wait_for(accept_started.wait(), timeout=1)
                service.request_stop()
                await asyncio.wait_for(task, timeout=1)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(session.state, CallState.FAILED)
        self.assertEqual(session.end_reason, CallEndReason.SHUTDOWN)
        self.assertEqual(FakeClient.instances[0].actions, ["open", "accept", "reject"])

    async def test_reject_incoming_checks_both_call_id_and_sender(self):
        api = Mock()
        api.voip.getStateAsync = AsyncMock(return_value=CallStateDetail(
            "inc-call", info=CallInfo("new-call", "sender@c.us", None),
        ))
        api.voip.rejectAsync = AsyncMock()
        with patch("whatsapp_chatbot_python.calls.service.GreenAPI", return_value=api):
            service = self.make_service()
            self.assertFalse(await service.reject_incoming("call-1", "sender@c.us"))
            api.voip.rejectAsync.assert_not_awaited()

            api.voip.getStateAsync.return_value = CallStateDetail(
                "inc-call", info=CallInfo("call-1", "other@c.us", None),
            )
            self.assertFalse(await service.reject_incoming("call-1", "sender@c.us"))
            api.voip.rejectAsync.assert_not_awaited()

            api.voip.getStateAsync.return_value = CallStateDetail(
                "inc-call", info=CallInfo("call-1", "sender@c.us", None),
            )
            self.assertTrue(await service.reject_incoming("call-1", "sender@c.us"))
            api.voip.rejectAsync.assert_awaited_once()
        self.assertEqual(api.session.close.call_count, 3)

    async def test_stop_before_execute_does_not_start_a_call(self):
        session, transition = self.make_session()

        service = self.make_service()

        service.request_stop()

        result = await service.execute(session, transition)

        self.assertEqual(session.end_reason, CallEndReason.SHUTDOWN)
        self.assertEqual(session.state, CallState.FAILED)
        self.assertIsNone(result.recording_path)
        self.assertEqual(FakeClient.instances, [])
        self.assertEqual(FakeVoice.instances, [])
