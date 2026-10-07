from whatsapp_chatbot_python.calls.models import CallState
from whatsapp_chatbot_python.calls.runtime import CallRuntime, RuntimeEvent, RuntimeEventType
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


if __name__ == "__main__":
    unittest.main()
