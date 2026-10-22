"""Mailbox and deadline semantics used by every call direction."""

import asyncio
import unittest
from time import monotonic

from whatsapp_chatbot_python.calls.call_runner import CallRunner, Events, Operations
from whatsapp_chatbot_python.calls.models import (
    AudioLoss, CallOptions, CallSession, Command, CommandCompleted, CommandFailed,
    DeadlineExpired, DeadlineKind, Deadlines, ProviderConfig, StopRequested,
)


class RuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_audio_loss_callback_receives_session_without_stopping_call(self):
        notices = []
        session = CallSession("caller", "chat", "en")
        runner = CallRunner(
            session, lambda *args, **kwargs: None,
            ProviderConfig("https://example.invalid", "instance", "token", "key", "model", "voice"),
            CallOptions(on_audio_loss=lambda call, loss: notices.append((call, loss))),
        )
        loss = AudioLoss("caller", 480)

        runner._dispatch(loss)

        self.assertEqual(notices, [(session, loss)])
        self.assertFalse(session.finished)

    async def test_audio_loss_callback_error_does_not_end_call(self):
        def broken_callback(_session, _loss):
            raise RuntimeError("callback failed")

        session = CallSession("caller", "chat", "en")
        runner = CallRunner(
            session, lambda *args, **kwargs: None,
            ProviderConfig("https://example.invalid", "instance", "token", "key", "model", "voice"),
            CallOptions(on_audio_loss=broken_callback),
        )

        with self.assertLogs("whatsapp_chatbot_python.calls.call_runner", level="WARNING"):
            runner._dispatch(AudioLoss("caller", 480))
        self.assertFalse(session.finished)

    async def test_observation_before_deadline_wins(self):
        events = Events()
        deadlines = Deadlines()
        event = StopRequested(observed_at=monotonic() - 1)
        deadlines.arm(DeadlineKind.OFFER, monotonic() - 0.5)
        events.emit(event)
        self.assertIs(await events.receive(deadlines), event)

    async def test_observation_on_or_after_deadline_is_late(self):
        for offset in (0, 0.01):
            with self.subTest(offset=offset):
                events = Events()
                deadlines = Deadlines()
                due = monotonic() - 1
                deadlines.arm(DeadlineKind.OFFER, due)
                event = StopRequested(observed_at=due + offset)
                events.emit(event)
                expired = await events.receive(deadlines)
                self.assertIsInstance(expired, DeadlineExpired)
                self.assertEqual(expired.kind, DeadlineKind.OFFER)
                self.assertIs(await events.receive(deadlines), event)

    async def test_empty_mailbox_returns_deadline(self):
        events = Events()
        deadlines = Deadlines()
        deadlines.arm(DeadlineKind.RING, monotonic() + 0.001)
        result = await asyncio.wait_for(events.receive(deadlines), 0.2)
        self.assertEqual(result.kind, DeadlineKind.RING)

    async def test_waiter_cancellation_is_not_swallowed(self):
        events = Events()
        deadlines = Deadlines()
        task = asyncio.create_task(events.receive(deadlines))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        events.close()

    async def test_event_from_another_thread_wakes_the_consumer(self):
        events = Events()
        event = StopRequested()
        waiter = asyncio.create_task(events.receive(Deadlines()))
        self.assertTrue(await asyncio.to_thread(events.emit, event))
        self.assertIs(await asyncio.wait_for(waiter, 0.2), event)
        events.close()

    async def test_named_operation_runs_once_and_reports_completion(self):
        reported = []
        operations = Operations(reported.append)
        calls = 0

        async def work():
            nonlocal calls
            calls += 1

        operations.start(Command.GREET, work, 1)
        operations.start(Command.GREET, work, 1)
        await asyncio.gather(*operations._tasks.values())
        await operations.close()
        self.assertEqual(calls, 1)
        self.assertEqual([type(event) for event in reported], [CommandCompleted])

    async def test_hung_operation_reports_timeout_and_is_cancelled(self):
        reported = []
        operations = Operations(reported.append)
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def hung():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        operations.start(Command.DIAL, hung, 0.005)
        await asyncio.wait_for(entered.wait(), 0.2)
        await asyncio.wait_for(cancelled.wait(), 0.2)
        await operations.close()
        self.assertEqual(len(reported), 1)
        self.assertIsInstance(reported[0], CommandFailed)
        self.assertIsInstance(reported[0].error, TimeoutError)
