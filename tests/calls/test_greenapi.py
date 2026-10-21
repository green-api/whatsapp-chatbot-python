"""A call adapter may finish only the call that it owns."""

import asyncio
import unittest
from time import monotonic
from unittest.mock import patch

from whatsapp_chatbot_python.calls.greenapi import GreenApiCall
from whatsapp_chatbot_python.calls.models import (
    CallCleanupError, CallSession, FinishResult, MediaFailed, ProviderConfig, ProviderError,
    RemoteAnswered,
)

from tests.calls.fakes import FakeApi, Scenario
from tests.calls.fakes import FakeRtc


class GreenApiCallTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scenario = Scenario(remote_hangup=False)
        self.apis = []
        self.events = []

        def create_api(*args, **kwargs):
            api = FakeApi(self.scenario, *args, **kwargs)
            self.apis.append(api)
            return api

        patcher = patch("whatsapp_api_client_python.API.GreenApi", create_api)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.config = ProviderConfig("https://example.invalid", "1", "token", "key",
                                     "model", "voice")

    async def start_outgoing(self):
        call = GreenApiCall(self.config,
                            CallSession("sender@c.us", "sender@c.us", "ru"),
                            self.events.append, 0.1)
        await call.__aenter__()
        await call.open(type("Audio", (), {"create": lambda self: None})())
        await call.dial()
        await asyncio.sleep(0)
        return call

    async def test_owned_outgoing_call_is_hung_up_and_http_session_closed(self):
        call = await self.start_outgoing()
        try:
            self.assertEqual(await call.finish(), FinishResult.FINISHED)
            self.assertIn("hangup", self.scenario.actions)
            self.assertTrue(any(isinstance(event, RemoteAnswered) for event in self.events))
        finally:
            await call.__aexit__(None, None, None)
        self.assertTrue(self.apis[0].session_closed)
        self.assertTrue(self.apis[0].rtc.closed)

    async def test_other_call_is_not_hung_up(self):
        call = await self.start_outgoing()
        try:
            self.scenario.call_id = "new-call"
            self.assertEqual(await call.finish(), FinishResult.NOT_OWNED)
            self.assertNotIn("hangup", self.scenario.actions)
            self.assertNotIn("reject", self.scenario.actions)
        finally:
            await call.__aexit__(None, None, None)

    async def test_remote_ended_call_is_not_hung_up_twice(self):
        call = await self.start_outgoing()
        try:
            self.apis[0].rtc.set_state("idle", "hangup")
            self.assertEqual(await call.finish(), FinishResult.ALREADY_ENDED)
            self.assertNotIn("hangup", self.scenario.actions)
        finally:
            await call.__aexit__(None, None, None)

    async def test_temporary_disconnect_is_recoverable_but_permanent_is_reported(self):
        call = await self.start_outgoing()
        try:
            self.apis[0].rtc.emit("disconnect", {"permanent": False})
            self.assertFalse(any(isinstance(event, MediaFailed) for event in self.events))
            self.apis[0].rtc.emit("disconnect", {"permanent": True})
            self.assertEqual(sum(isinstance(event, MediaFailed) for event in self.events), 1)
        finally:
            await call.__aexit__(None, None, None)

    async def test_reject_offer_requires_matching_unanswered_call(self):
        self.scenario.state = "inc-call"
        session = CallSession.incoming(sender_id="sender@c.us", call_id="call-1",
                                       language="ru", incoming_deadline=monotonic() + 1)
        call = GreenApiCall(self.config, session, self.events.append, 0.1)
        async with call:
            self.assertTrue(await call.reject_offer())
            self.assertIn("reject", self.scenario.actions)
        self.assertTrue(self.apis[0].session_closed)

        self.scenario.actions.clear()
        self.scenario.state = "on-call"
        async with GreenApiCall(self.config, session, self.events.append, 0.1) as second:
            self.assertFalse(await second.reject_offer())
        self.assertNotIn("reject", self.scenario.actions)

    async def test_provider_error_retains_details_for_caller_without_exposing_token(self):
        self.scenario.state = "inc-call"
        self.scenario.accept_error = True
        session = CallSession.incoming(sender_id="sender@c.us", call_id="call-1",
                                       language="ru", incoming_deadline=monotonic() + 1)
        async with GreenApiCall(self.config, session, self.events.append, 0.1) as call:
            with self.assertRaises(ProviderError) as raised:
                await call.accept()
        self.assertEqual(raised.exception.code, "accept_RuntimeError")
        self.assertIn("accept unavailable", raised.exception.detail)
        self.assertNotIn("token", str(raised.exception))

    async def test_rtc_timeout_does_not_cancel_sdk_close(self):
        call = await self.start_outgoing()
        release = asyncio.Event()

        async def delayed_close(rtc):
            await release.wait()
            rtc.closed = True

        try:
            with patch.object(FakeRtc, "closeAsync", delayed_close):
                with self.assertRaises(CallCleanupError) as raised:
                    await call.__aexit__(None, None, None)

            self.assertEqual(raised.exception.stage, "rtc_close")
            self.assertTrue(self.apis[0].session_closed)
            self.assertFalse(self.apis[0].rtc.closed)
        finally:
            release.set()
            await asyncio.wait_for(call._rtc_close_task, 0.5)

        self.assertTrue(self.apis[0].rtc.closed)

    async def test_late_rtc_open_cannot_restart_closed_adapter(self):
        started, release = asyncio.Event(), asyncio.Event()
        call = GreenApiCall(
            self.config, CallSession("sender@c.us", "sender@c.us", "ru"),
            self.events.append, 0.1,
        )
        await call.__aenter__()

        async def delayed_open(rtc, *, timeout):
            started.set()
            await release.wait()

        with patch.object(FakeRtc, "openAsync", delayed_open):
            opening = asyncio.create_task(call.open(type("Audio", (), {"create": lambda self: None})()))
            await started.wait()
            await call.__aexit__(None, None, None)
            release.set()

            with self.assertRaisesRegex(RuntimeError, "closing"):
                await opening

        self.assertTrue(self.apis[0].rtc.closed)
        self.assertTrue(self.apis[0].session_closed)
