"""Cancellation and resource release across setup, signaling and teardown."""

import asyncio
import unittest
from types import SimpleNamespace
from threading import Event
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

    def service(self, *, setup=0.2, talk=0.2):
        return WhatsAppCallService(
            api_url="https://example.invalid", id_instance="1",
            api_token_instance="token", openai_api_key="key",
            realtime_model="test", realtime_voice="marin",
            options=CallOptions(
                setup_seconds=setup,
                command_seconds=setup,
                ring_seconds=0.2,
                talk_seconds=talk,
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
            with self.assertLogs("whatsapp_chatbot_python.calls.call_runner", level="WARNING") as logs:
                await asyncio.wait_for(self.service().execute(session, self.fsm.apply), 0.5)

        self.assertTrue(self.apis[0].session_closed)
        self.assertTrue(self.voices[0].closed)
        # The remote outcome is already terminal; cleanup errors surface separately.
        self.assertEqual(session.state, CallState.REMOTE_ENDED)
        self.assertTrue(any("WARNING!!!" in message for message in logs.output))

    async def test_rtc_close_timeout_warns_and_allows_next_call(self):
        self.scenario.gates["rtc_close"] = asyncio.Event()
        self.scenario.remote_hangup = True
        session = self.session()
        service = self.service()

        with self.assertLogs("whatsapp_chatbot_python.calls.call_runner", level="WARNING") as logs:
            await asyncio.wait_for(service.execute(session, self.fsm.apply), 1)

        self.assertTrue(service.healthy)
        self.assertTrue(self.apis[0].session_closed)
        self.assertTrue(self.voices[0].closed)
        self.assertFalse(self.apis[0].rtc.closed)
        self.assertTrue(any("WARNING!!!" in message for message in logs.output))

        self.scenario.gates["rtc_close"].set()
        await asyncio.wait_for(service.execute(self.session(), self.fsm.apply), 1)
        self.assertEqual(len(self.apis), 2)
        self.assertTrue(self.apis[1].rtc.closed)

    async def test_remote_finish_failure_recovers_on_next_call(self):
        self.scenario.remote_hangup = False
        service = self.service(talk=0.02)
        original_get_state = FakeApi.getStateAsync

        async def fail_first_finish(api):
            if api is self.apis[0] and api.state != "idle":
                raise ConnectionError("Green API temporarily unavailable")

            return await original_get_state(api)

        with patch.object(FakeApi, "getStateAsync", fail_first_finish):
            with self.assertLogs("whatsapp_chatbot_python.calls.call_runner", level="WARNING") as logs:
                with self.assertRaisesRegex(RuntimeError, "greenapi"):
                    await asyncio.wait_for(service.execute(self.session(), self.fsm.apply), 1)

        self.assertTrue(service.healthy)
        self.assertTrue(self.apis[0].session_closed)
        self.assertTrue(self.apis[0].rtc.closed)
        self.assertTrue(any("is_remote_finish_unknown=True" in line for line in logs.output))

        self.scenario.remote_hangup = True
        await asyncio.wait_for(service.execute(self.session(), self.fsm.apply), 1)
        self.assertEqual(len(self.apis), 2)
        self.assertIn(("dial", "sender@c.us"), self.scenario.actions)
        self.assertTrue(self.apis[1].session_closed)

    async def test_remote_finish_failure_recovers_across_call_event_loops(self):
        self.scenario.remote_hangup = False
        service = self.service(talk=0.02)
        original_get_state = FakeApi.getStateAsync

        async def fail_first_finish(api):
            if api is self.apis[0] and api.state != "idle":
                raise ConnectionError("Green API temporarily unavailable")

            return await original_get_state(api)

        def execute_in_new_loop():
            return asyncio.run(service.execute(self.session(), self.fsm.apply))

        with patch.object(FakeApi, "getStateAsync", fail_first_finish):
            with self.assertRaisesRegex(RuntimeError, "greenapi"):
                await asyncio.to_thread(execute_in_new_loop)

        self.scenario.remote_hangup = True
        await asyncio.to_thread(execute_in_new_loop)
        self.assertEqual(len(self.apis), 2)
        self.assertTrue(service.healthy)

    async def test_unknown_remote_finish_marks_this_call_failed_but_keeps_service_ready(self):
        self.scenario.remote_hangup = False
        service = self.service(talk=0.02)
        session = self.session()
        original_get_state = FakeApi.getStateAsync

        async def unknown_at_finish(api):
            if api.state != "idle":
                return SimpleNamespace(state="on-call", info=None, reason=None)

            return await original_get_state(api)

        with patch.object(FakeApi, "getStateAsync", unknown_at_finish):
            with self.assertLogs("whatsapp_chatbot_python.calls.call_runner", level="ERROR") as logs:
                await asyncio.wait_for(service.execute(session, self.fsm.apply), 1)

        self.assertEqual(session.state, CallState.FAILED)
        self.assertEqual(session.error_code, "finish_unknown")
        self.assertTrue(service.healthy)
        self.assertTrue(any("is_remote_finish_unknown=True" in line for line in logs.output))

    async def test_next_call_starts_while_previous_recording_is_still_exporting(self):
        from whatsapp_chatbot_python.calls.recorder import CallRecorder

        started, release = Event(), Event()
        original_finish = CallRecorder._finish_blocking
        exporting_recorders = []
        service = self.service()

        def slow_first_export(recorder):
            exporting_recorders.append(recorder)
            started.set()
            release.wait(2)

            return original_finish(recorder)

        try:
            with patch.object(CallRecorder, "_finish_blocking", slow_first_export):
                with self.assertLogs("whatsapp_chatbot_python.calls.call_runner", level="ERROR") as logs:
                    first = await asyncio.wait_for(service.execute(self.session(), self.fsm.apply), 1)

            self.assertTrue(started.is_set())
            self.assertIsNone(first.recording_path)
            self.assertTrue(any("is_recording_export_running=True" in line for line in logs.output))

            second = await asyncio.wait_for(service.execute(self.session(), self.fsm.apply), 1)
            self.assertIsNone(second.recording_path)
            self.assertEqual(len(self.apis), 2)
            self.assertTrue(service.healthy)
        finally:
            release.set()

            for recorder in exporting_recorders:
                await asyncio.to_thread(recorder._export.result, 1)

    async def test_unfinished_local_close_warns_without_skipping_other_resources(self):
        from whatsapp_chatbot_python.calls.call_runner import Operations

        release = asyncio.Event()
        self.scenario.remote_hangup = True
        service = self.service()

        async def ignore_first_cancel(operations):
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()

        try:
            with patch.object(Operations, "close", ignore_first_cancel):
                with self.assertLogs("whatsapp_chatbot_python.calls.call_runner", level="WARNING") as logs:
                    await asyncio.wait_for(service.execute(self.session(), self.fsm.apply), 1)

            self.assertTrue(self.apis[0].session_closed)
            self.assertTrue(self.voices[0].closed)
            self.assertTrue(service.healthy)
            self.assertTrue(any("unfinished_tasks=1" in line for line in logs.output))
        finally:
            release.set()
            await asyncio.sleep(0)

    async def test_slow_operations_cleanup_closes_other_owners_before_returning(self):
        from whatsapp_chatbot_python.calls.call_runner import Operations

        release = asyncio.Event()
        self.scenario.remote_hangup = True
        service = self.service()

        async def delayed_close(operations):
            await release.wait()

        with patch.object(Operations, "close", delayed_close):
            await asyncio.wait_for(service.execute(self.session(), self.fsm.apply), 1)

        self.assertTrue(service.healthy)
        self.assertTrue(self.apis[0].session_closed)
        self.assertTrue(self.apis[0].rtc.closed)
        self.assertTrue(self.voices[0].closed)
        release.set()

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
                await asyncio.wait_for(execution, 1)

                self.assertFalse(service.healthy)  # request_stop() is permanent.
                self.assertTrue(self.apis[0].session_closed)
                self.assertIsNone(self.apis[0].rtc)
            finally:
                release.set()

        self.assertTrue(self.voices[0].closed)
        self.assertIsNone(self.apis[0].rtc)
