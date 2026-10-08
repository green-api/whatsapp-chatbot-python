from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Thread
from unittest.mock import patch
import asyncio
import logging
import unittest

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

    def on(self, name, callback):
        self.listeners.setdefault(name, []).append(callback)

    def emit(self, name, detail=None):
        for callback in self.listeners.get(name, []):
            callback(detail)

    async def openAsync(self, *, timeout):
        self.client.actions.append("open")

        if self.client.open_error:
            raise RuntimeError("callsRtc connection refused")

        asyncio.get_running_loop().call_soon(
            self.emit, "state", CallStateDetail(self.client.initial_state)
        )

    async def startAudioAsync(self):
        self.client.actions.append("bridge")
        self.devices.append(await self.audio_factory())

        if self.client.bridge_error:
            raise RuntimeError("audio bridge failed")

        if self.client.answer and not self.client.answer_after_bridge:
            self.emit("state", CallStateDetail("on-call"))

        if self.client.answer and self.client.answer_after_bridge:
            asyncio.get_running_loop().call_later(
                0.001, self.emit, "state",
                CallStateDetail("on-call"),
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
    def __init__(self, state, reason=None):
        self.state = state
        self.reason = reason


class FakeClient:
    initial_state = "idle"
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
        self.instances.append(self)

    def connect(self, *, audio_factory):
        self.calls.audio_factory = audio_factory
        return self.calls

    async def dialAsync(self, target):
        self.actions.append(("dial", target))

    async def hangUpAsync(self):
        self.actions.append("hangup")


class CallServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name, value in {
            "initial_state": "idle",
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
