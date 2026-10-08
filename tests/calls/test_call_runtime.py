from whatsapp_chatbot_python.calls.models import CallState
from whatsapp_chatbot_python.calls.runtime import CallRuntime, RuntimeEvent, RuntimeEventType
import asyncio
import unittest


class CallRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_queued_event_wins_over_expired_timer(self) -> None:
        runtime = CallRuntime(
            ring_timeout_seconds=0,
            talk_timeout_seconds=1,
            bridge_timeout_seconds=1,
        )
        runtime.start_ringing()
        runtime.events.put_nowait(RuntimeEvent.bridge_ready())

        event = await runtime.next_event(CallState.RINGING)

        self.assertIsNotNone(event)
        self.assertEqual(event.type, RuntimeEventType.BRIDGE_READY)


    async def test_empty_queue_returns_none_when_timer_expires(self) -> None:
        runtime = CallRuntime(0, 1, 1)
        runtime.start_ringing()
        self.assertIsNone(await runtime.next_event(CallState.RINGING))

    async def test_cancellation_is_not_swallowed_when_event_becomes_ready(self) -> None:
        waiting = asyncio.Event()

        class ObservedQueue(asyncio.Queue):
            async def get(self):
                waiting.set()
                return await super().get()

        runtime = CallRuntime(5, 1, 1, events=ObservedQueue())
        runtime.start_ringing()

        async def consume():
            event = await runtime.next_event(CallState.RINGING)
            # Allow cancellation to arrive even if the event was delivered first.
            await asyncio.sleep(0)
            return event

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(waiting.wait(), timeout=1)
            runtime.events.put_nowait(RuntimeEvent.bridge_ready())
            # Resume the queue reader, then cancel before wait_for's parent resumes.
            asyncio.get_running_loop().call_soon(task.cancel, "caller cancelled")
            with self.assertRaises(asyncio.CancelledError) as raised:
                await asyncio.wait_for(task, timeout=1)
            self.assertEqual(raised.exception.args, ("caller cancelled",))
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
