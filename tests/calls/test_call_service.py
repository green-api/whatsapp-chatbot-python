"""Call-service scenarios exercise the real runner, FSM, RTC adapter and cleanup."""

import asyncio
import unittest
from contextlib import suppress
from time import monotonic
from unittest.mock import patch

from whatsapp_chatbot_python.calls.models import (
    CallEndReason, CallEvent, CallOptions, CallSession, CallState, ProviderError,
    RecordingOptions,
)
from whatsapp_chatbot_python.calls.service import WhatsAppCallService
from whatsapp_chatbot_python.calls.state_machine import CallStateMachine

from tests.calls.fakes import FakeApi, FakeVoice, Scenario


class CallServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scenario = Scenario()
        self.apis = []
        self.voices = []
        self.fsm = CallStateMachine()

        def create_api(*args, **kwargs):
            api = FakeApi(self.scenario, *args, **kwargs)
            self.apis.append(api)
            return api

        def create_voice(*args):
            voice = FakeVoice(self.scenario, *args)
            self.voices.append(voice)
            return voice

        for target, replacement in (
            ("whatsapp_api_client_python.API.GreenApi", create_api),
            ("whatsapp_chatbot_python.calls.openai.RealtimeConversation", create_voice),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def service(self, *, ring=0.2, talk=0.2):
        return WhatsAppCallService(
            api_url="https://example.invalid", id_instance="1",
            api_token_instance="token", openai_api_key="explicit-key",
            realtime_model="realtime-test", realtime_voice="marin",
            options=CallOptions(
                ring_seconds=ring, talk_seconds=talk, setup_seconds=0.1,
                command_seconds=0.1, shutdown_seconds=0.1,
                recording=RecordingOptions(enabled=False),
            ),
        )

    def outgoing(self):
        session = CallSession("sender@c.us", "sender@c.us", "ru")
        self.fsm.apply(session, CallEvent.ENQUEUED)
        self.fsm.apply(session, CallEvent.DEQUEUED)
        return session

    def incoming(self, *, call_id="call-1", deadline=None):
        session = CallSession.incoming(
            sender_id="sender@c.us", call_id=call_id, language="ru",
            incoming_deadline=deadline if deadline is not None else monotonic() + 1,
        )
        self.fsm.apply(session, CallEvent.ENQUEUED)
        self.fsm.apply(session, CallEvent.DEQUEUED)
        return session

    async def execute(self, service, session):
        return await asyncio.wait_for(service.execute(session, self.fsm.apply), 1)

    async def wait_action(self, action):
        for _ in range(100):
            if action in self.scenario.actions:
                return
            await asyncio.sleep(0.001)
        self.fail(f"Missing action {action}: {self.scenario.actions}")

    async def test_outgoing_answer_greets_once_and_preserves_remote_reason(self):
        session = self.outgoing()
        result = await self.execute(self.service(), session)
        self.assertEqual(session.state, CallState.REMOTE_ENDED)
        self.assertEqual(session.end_reason, CallEndReason.REMOTE_HANGUP)
        self.assertEqual(session.remote_reason, "hangup")
        self.assertTrue(session.remote_accepted and session.bridge_ready)
        self.assertEqual(self.voices[0].greetings, [None])
        self.assertIn(("dial", "sender@c.us"), self.scenario.actions)
        self.assertTrue(self.apis[0].session_closed)
        self.assertTrue(self.apis[0].rtc.closed)
        self.assertTrue(self.voices[0].closed)
        self.assertIsNone(result.recording_path)

    async def test_incoming_accepts_matching_offer_and_greets_after_answer(self):
        self.scenario.state = "inc-call"
        session = self.incoming()
        await self.execute(self.service(), session)
        self.assertEqual(session.state, CallState.REMOTE_ENDED)
        self.assertTrue(session.incoming_accept_completed)
        self.assertLess(self.scenario.actions.index("accept"), self.scenario.actions.index("greet"))
        self.assertTrue(self.apis[0].session_closed)

    async def test_incoming_call_id_takes_priority_over_pn_or_lid_address(self):
        self.scenario.state = "inc-call"
        self.scenario.wid = "sender@lid"
        session = self.incoming()
        await self.execute(self.service(), session)
        self.assertEqual(session.state, CallState.REMOTE_ENDED)
        self.assertIn("accept", self.scenario.actions)

    async def test_busy_instance_is_not_dialed(self):
        self.scenario.state = "on-call"
        session = self.outgoing()
        with self.assertRaisesRegex(RuntimeError, "already has a call"):
            await self.execute(self.service(), session)
        self.assertEqual(session.state, CallState.FAILED)
        self.assertFalse(any(isinstance(a, tuple) and a[0] == "dial" for a in self.scenario.actions))
        self.assertNotIn("hangup", self.scenario.actions)
        self.assertTrue(self.apis[0].session_closed)

    async def test_voice_setup_failure_never_dials(self):
        self.scenario.voice_error = True
        session = self.outgoing()
        with self.assertRaisesRegex(RuntimeError, "voice unavailable"):
            await self.execute(self.service(), session)
        self.assertEqual(session.state, CallState.FAILED)
        self.assertNotIn("open", self.scenario.actions)
        self.assertFalse(any(isinstance(a, tuple) and a[0] == "dial" for a in self.scenario.actions))
        self.assertTrue(self.apis[0].session_closed)

    async def test_rtc_open_failure_never_dials(self):
        self.scenario.open_error = True
        session = self.outgoing()
        with self.assertRaisesRegex(RuntimeError, "RTC unavailable"):
            await self.execute(self.service(), session)
        self.assertEqual(session.state, CallState.FAILED)
        self.assertFalse(any(isinstance(a, tuple) and a[0] == "dial" for a in self.scenario.actions))
        self.assertTrue(self.apis[0].session_closed)

    async def test_bridge_failure_fails_call_and_finishes_owned_remote(self):
        self.scenario.bridge_error = True
        self.scenario.remote_hangup = False
        session = self.outgoing()
        with self.assertRaisesRegex(RuntimeError, "bridge unavailable"):
            await self.execute(self.service(), session)
        self.assertEqual((session.state, session.end_reason),
                         (CallState.FAILED, CallEndReason.ERROR))
        self.assertIn("hangup", self.scenario.actions)
        self.assertTrue(self.apis[0].session_closed)

    async def test_ring_timeout_hangs_up_owned_outgoing_call(self):
        self.scenario.answer = False
        self.scenario.remote_hangup = False
        session = self.outgoing()
        await self.execute(self.service(ring=0.02), session)
        self.assertEqual((session.state, session.end_reason),
                         (CallState.RING_TIMEOUT, CallEndReason.RING_TIMEOUT))
        self.assertIn("hangup", self.scenario.actions)

    async def test_talk_timeout_hangs_up_owned_answered_call(self):
        self.scenario.remote_hangup = False
        session = self.outgoing()
        await self.execute(self.service(talk=0.02), session)
        self.assertEqual((session.state, session.end_reason),
                         (CallState.TALK_TIMEOUT, CallEndReason.TALK_TIMEOUT))
        self.assertIn("hangup", self.scenario.actions)
        self.assertEqual(self.voices[0].greetings, [None])

    async def test_outgoing_must_be_dequeued_before_any_resource_is_created(self):
        session = CallSession("sender@c.us", "sender@c.us", "ru")
        self.fsm.apply(session, CallEvent.ENQUEUED)
        with self.assertRaisesRegex(ValueError, "Dequeue"):
            await self.execute(self.service(), session)
        self.assertEqual(session.state, CallState.FAILED)
        self.assertEqual(self.apis, [])
        self.assertEqual(self.voices, [])

    async def test_expired_incoming_offer_does_not_start_voice_or_rtc(self):
        self.scenario.state = "inc-call"
        session = self.incoming(deadline=monotonic() - 1)
        await self.execute(self.service(), session)
        self.assertEqual((session.state, session.end_reason),
                         (CallState.REJECTED, CallEndReason.INCOMING_EXPIRED))
        self.assertEqual(self.voices, [])
        self.assertNotIn("open", self.scenario.actions)
        self.assertIn("reject", self.scenario.actions)
        self.assertTrue(self.apis[0].session_closed)

    async def test_already_finished_incoming_session_starts_no_resources(self):
        session = self.incoming()
        self.fsm.apply(session, CallEvent.CALLER_LEFT)
        result = await self.execute(self.service(), session)
        self.assertEqual(session.state, CallState.REJECTED)
        self.assertIsNone(result.recording_path)
        self.assertEqual(self.apis, [])
        self.assertEqual(self.voices, [])

    async def test_other_incoming_call_is_never_accepted_or_ended(self):
        self.scenario.state = "inc-call"
        self.scenario.call_id = "another-call"
        session = self.incoming(call_id="call-1")
        await self.execute(self.service(), session)
        self.assertEqual(session.state, CallState.REJECTED)
        self.assertNotIn("accept", self.scenario.actions)
        self.assertNotIn("reject", self.scenario.actions)
        self.assertNotIn("hangup", self.scenario.actions)

    async def test_incoming_deadline_interrupts_pending_accept(self):
        self.scenario.state = "inc-call"
        self.scenario.remote_hangup = False
        self.scenario.gates["accept"] = asyncio.Event()
        session = self.incoming(deadline=monotonic() + 0.03)
        await self.execute(self.service(), session)
        self.assertEqual((session.state, session.end_reason),
                         (CallState.REJECTED, CallEndReason.INCOMING_EXPIRED))
        self.assertIn("reject", self.scenario.actions)
        self.assertTrue(self.apis[0].session_closed)

    async def test_matching_answer_can_arrive_before_accept_command_returns(self):
        self.scenario.state = "inc-call"
        self.scenario.gates["accept"] = asyncio.Event()
        session = self.incoming()
        task = asyncio.create_task(self.execute(self.service(), session))
        try:
            await self.wait_action("accept")
            self.apis[0].rtc.set_state("on-call")
            await task
            self.assertEqual(session.state, CallState.REMOTE_ENDED)
            self.assertTrue(session.remote_accepted)
            self.assertEqual(self.voices[0].greetings, [None])
        finally:
            self.scenario.gates["accept"].set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_new_call_after_answer_is_never_hung_up_by_old_runner(self):
        self.scenario.state = "inc-call"
        self.scenario.remote_hangup = False
        session = self.incoming()
        task = asyncio.create_task(self.execute(self.service(talk=0.03), session))
        try:
            await self.wait_action("bridge")
            self.scenario.call_id = "next-call"
            await task
            self.assertEqual(session.state, CallState.FAILED)
            self.assertEqual(session.error_code, "finish_not_owned")
            self.assertNotIn("hangup", self.scenario.actions)
            self.assertNotIn("reject", self.scenario.actions)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_reject_incoming_only_rejects_matching_unanswered_offer(self):
        self.scenario.state = "inc-call"
        service = self.service()
        self.assertFalse(await service.reject_incoming("another-call", "sender@c.us"))
        self.assertTrue(await service.reject_incoming("call-1", "sender@c.us"))
        self.assertEqual(self.scenario.actions.count("reject"), 1)
        self.assertTrue(all(api.session_closed for api in self.apis))

    async def test_caller_left_during_pending_accept_wakes_runner(self):
        self.scenario.state = "inc-call"
        self.scenario.remote_hangup = False
        self.scenario.gates["accept"] = asyncio.Event()
        service = self.service()
        session = self.incoming()
        task = asyncio.create_task(self.execute(service, session))
        try:
            await self.wait_action("accept")
            self.assertFalse(service.notify_incoming_ended("older-call"))
            self.assertTrue(service.notify_incoming_ended("call-1"))
            await task
            self.assertEqual((session.state, session.end_reason),
                             (CallState.REJECTED, CallEndReason.CALLER_LEFT))
            self.assertFalse(service.notify_incoming_ended("call-1"))
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_stop_from_other_thread_interrupts_pending_accept(self):
        self.scenario.state = "inc-call"
        self.scenario.gates["accept"] = asyncio.Event()
        service = self.service()
        session = self.incoming()
        task = asyncio.create_task(self.execute(service, session))
        try:
            await self.wait_action("accept")
            await asyncio.to_thread(service.request_stop)
            await task
            self.assertEqual((session.state, session.end_reason),
                             (CallState.CANCELLED, CallEndReason.SHUTDOWN))
            self.assertTrue(self.apis[0].session_closed)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_stopped_service_does_not_start_new_call(self):
        service = self.service()
        service.request_stop()
        session = self.outgoing()
        await self.execute(service, session)
        self.assertEqual(session.state, CallState.CANCELLED)
        self.assertEqual(self.apis, [])
        self.assertEqual(self.voices, [])

    async def test_service_runs_successive_calls_with_fresh_resources(self):
        service = self.service()
        first, second = self.outgoing(), self.outgoing()
        await self.execute(service, first)
        await self.execute(service, second)
        self.assertEqual((first.state, second.state),
                         (CallState.REMOTE_ENDED, CallState.REMOTE_ENDED))
        self.assertEqual(len(self.apis), 2)
        self.assertTrue(all(api.session_closed for api in self.apis))

    async def test_service_serializes_two_concurrent_calls(self):
        self.scenario.gates["dial"] = asyncio.Event()
        service = self.service()
        first, second = self.outgoing(), self.outgoing()
        first_task = asyncio.create_task(self.execute(service, first))
        second_task = None
        try:
            await self.wait_action(("dial", "sender@c.us"))
            second_task = asyncio.create_task(self.execute(service, second))
            await asyncio.sleep(0.005)
            self.assertEqual(len(self.apis), 1)
            self.scenario.gates["dial"].set()
            await asyncio.gather(first_task, second_task)
            self.assertEqual((first.state, second.state),
                             (CallState.REMOTE_ENDED, CallState.REMOTE_ENDED))
            self.assertEqual(len(self.apis), 2)
        finally:
            self.scenario.gates["dial"].set()
            for task in (first_task, second_task):
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_caller_left_after_answer_is_remote_hangup(self):
        self.scenario.state = "inc-call"
        self.scenario.remote_hangup = False
        service = self.service()
        session = self.incoming()
        task = asyncio.create_task(self.execute(service, session))
        try:
            await self.wait_action("greet")
            self.assertTrue(service.notify_incoming_ended("call-1"))
            await task
            self.assertEqual((session.state, session.end_reason),
                             (CallState.REMOTE_ENDED, CallEndReason.REMOTE_HANGUP))
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_live_offer_accept_failure_is_not_misreported_as_caller_left(self):
        """RED: a failed accept against the same live offer is a provider failure."""
        self.scenario.state = "inc-call"
        self.scenario.accept_error = True
        session = self.incoming()
        with suppress(ProviderError):
            await self.execute(self.service(), session)
        self.assertEqual((session.state, session.end_reason),
                         (CallState.FAILED, CallEndReason.ERROR))
