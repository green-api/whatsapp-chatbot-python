"""Cancellation and resource release across setup, signaling and teardown."""

import asyncio
import unittest
from time import monotonic
from unittest.mock import patch

from whatsapp_chatbot_python.calls.models import (
    CallEndReason, CallEvent, CallOptions, CallSession, CallState, RecordingOptions,
)
from whatsapp_chatbot_python.calls.service import WhatsAppCallService
from whatsapp_chatbot_python.calls.state_machine import CallStateMachine

from tests.calls.fakes import FakeApi, FakeRtc, FakeVoice, Scenario


class CleanupTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scenario = Scenario(remote_hangup=False)
        self.apis = []
        self.voices = []
        self.fsm = CallStateMachine()

        def api_factory(*args, **kwargs):
            api = FakeApi(self.scenario, *args, **kwargs)
            self.apis.append(api)
            return api

        def voice_factory(*args, **kwargs):
            voice = FakeVoice(self.scenario, *args, **kwargs)
            self.voices.append(voice)
            return voice

        for path, replacement in (
            ("whatsapp_api_client_python.API.GreenApi", api_factory),
            ("whatsapp_chatbot_python.calls.openai.RealtimeConversation", voice_factory),
        ):
            patcher = patch(path, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def service(self, *, setup=0.2):
        return WhatsAppCallService(
            api_url="https://example.invalid", id_instance="1",
            api_token_instance="token", openai_api_key="key",
            realtime_model="test", realtime_voice="marin",
            options=CallOptions(
                setup_seconds=setup, command_seconds=setup,
                ring_seconds=0.2, talk_seconds=0.2,
                shutdown_seconds=0.2,
                recording=RecordingOptions(enabled=False),
            ),
        )

    def session(self, incoming=False):
        session = (CallSession.incoming(
            sender_id="sender@c.us", call_id="call-1", language="ru",
            incoming_deadline=monotonic() + 1,
        ) if incoming else CallSession("sender@c.us", "sender@c.us", "ru"))
        self.fsm.apply(session, CallEvent.ENQUEUED)
        self.fsm.apply(session, CallEvent.DEQUEUED)
        return session

    async def wait_action(self, action):
        for _ in range(100):
            if action in self.scenario.actions:
                return
            await asyncio.sleep(0.001)
        self.fail(f"Missing action {action}: {self.scenario.actions}")

    async def test_cancellation_drains_cleanup_at_each_active_stage(self):
        for stage, incoming in (("voice_start", False), ("open", False),
                                ("dial", False), ("accept", True),
                                ("bridge", False)):
            with self.subTest(stage=stage):
                self.scenario = Scenario(state="inc-call" if incoming else "idle",
                                         remote_hangup=False)
                self.scenario.gates[stage] = asyncio.Event()
                self.apis.clear()
                self.voices.clear()
                session = self.session(incoming)
                task = asyncio.create_task(self.service().execute(session, self.fsm.apply))
                try:
                    await self.wait_action(stage if stage not in ("dial",) else
                                           ("dial", "sender@c.us"))
                    task.cancel("caller cancelled")
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 0.5)
                    self.assertEqual((session.state, session.end_reason),
                                     (CallState.CANCELLED, CallEndReason.SHUTDOWN))
                    self.assertTrue(self.apis[0].session_closed)
                    self.assertTrue(self.voices[0].closed)
                    if self.apis[0].rtc is not None:
                        self.assertTrue(self.apis[0].rtc.closed)
                finally:
                    self.scenario.gates[stage].set()
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

    async def test_repeated_cancellation_waits_for_blocked_close(self):
        self.scenario.gates["rtc_close"] = asyncio.Event()
        session = self.session()
        task = asyncio.create_task(self.service().execute(session, self.fsm.apply))
        try:
            await self.wait_action("bridge")
            task.cancel()
            await self.wait_action("rtc_close")
            task.cancel()
            self.assertFalse(task.done())
            self.scenario.gates["rtc_close"].set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 0.5)
            self.assertTrue(self.apis[0].session_closed)
            self.assertTrue(self.voices[0].closed)
        finally:
            self.scenario.gates["rtc_close"].set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_rtc_close_failure_still_closes_http_session_and_voice(self):
        self.scenario.remote_hangup = True
        session = self.session()

        async def broken_close(rtc):
            rtc.api.scenario.actions.append("rtc_close")
            raise RuntimeError("RTC close failed")

        with patch.object(FakeRtc, "closeAsync", broken_close):
            with self.assertRaisesRegex(RuntimeError, "RTC close failed"):
                await asyncio.wait_for(self.service().execute(session, self.fsm.apply), 0.5)

        self.assertTrue(self.apis[0].session_closed)
        self.assertTrue(self.voices[0].closed)
        # The remote outcome is already terminal; cleanup errors surface separately.
        self.assertEqual(session.state, CallState.REMOTE_ENDED)

    async def test_rtc_close_timeout_disables_service_and_continues_later(self):
        self.scenario.gates["rtc_close"] = asyncio.Event()
        self.scenario.remote_hangup = True
        session = self.session()
        service = self.service()

        try:
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(service.execute(session, self.fsm.apply), 1)

            self.assertFalse(service.healthy)
            self.assertTrue(self.apis[0].session_closed)
            self.assertTrue(self.voices[0].closed)
            self.assertFalse(self.apis[0].rtc.closed)
        finally:
            self.scenario.gates["rtc_close"].set()

        await asyncio.wait_for(service._runner_with_incomplete_cleanup._resources.api._rtc_close_task, 0.5)
        self.assertTrue(self.apis[0].rtc.closed)

    async def test_slow_operations_cleanup_does_not_skip_other_owners(self):
        from whatsapp_chatbot_python.calls.call_runner import Operations

        release = asyncio.Event()
        self.scenario.remote_hangup = True
        service = self.service()

        async def delayed_close(operations):
            await release.wait()

        try:
            with patch.object(Operations, "close", delayed_close):
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(
                        service.execute(self.session(), self.fsm.apply), 1,
                    )

            self.assertFalse(service.healthy)
            self.assertFalse(self.apis[0].session_closed)
            self.assertFalse(self.voices[0].closed)
        finally:
            release.set()

        cleanup = service._runner_with_incomplete_cleanup._resources._cleanup_still_running
        await asyncio.wait_for(cleanup, 0.5)
        self.assertTrue(self.apis[0].session_closed)
        self.assertTrue(self.apis[0].rtc.closed)
        self.assertTrue(self.voices[0].closed)

    async def test_late_prepare_cannot_open_rtc_after_cleanup_started(self):
        release = asyncio.Event()
        service = self.service(setup=2)

        async def late_voice_start(voice):
            voice.scenario.actions.append("voice_start")
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            voice.started = True

        with patch.object(FakeVoice, "start", late_voice_start):
            execution = asyncio.create_task(service.execute(self.session(), self.fsm.apply))
            try:
                await self.wait_action("voice_start")
                service.request_stop()
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(execution, 1)

                self.assertFalse(service.healthy)
                self.assertFalse(self.apis[0].session_closed)
                self.assertIsNone(self.apis[0].rtc)
            finally:
                release.set()

            cleanup = service._runner_with_incomplete_cleanup._resources._cleanup_still_running
            await asyncio.wait_for(cleanup, 0.5)

        self.assertTrue(self.apis[0].session_closed)
        self.assertTrue(self.voices[0].closed)
        self.assertIsNone(self.apis[0].rtc)
