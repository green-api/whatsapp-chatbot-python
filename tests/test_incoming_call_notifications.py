import logging
import unittest
from unittest.mock import Mock

from whatsapp_chatbot_python import IncomingCall, IncomingCallNotification, Notification
from whatsapp_chatbot_python.manager.router import Router


class IncomingCallNotificationTest(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.router = Router(self.api, logging.getLogger(__name__))

    @staticmethod
    def event(status="offer", timestamp=1_700_000_000):
        return {
            "typeWebhook": "incomingCall", "from": "caller@c.us",
            "idMessage": "call-1", "status": status, "timestamp": timestamp,
        }

    def test_typed_notification_keeps_original_webhook_and_message_methods_work(self):
        received = []
        self.router.incoming_call.add_handler(received.append)
        event = self.event(status="future-status")

        self.router.route_event(event)

        notification = received[0]
        self.assertIsInstance(notification, IncomingCallNotification)
        self.assertIsInstance(notification, Notification)
        self.assertIs(notification.event, event)
        self.assertIs(notification.raw_event, event)
        self.assertIs(notification.call.raw_event, event)
        self.assertEqual(notification.call, IncomingCall("caller@c.us", "call-1", "future-status", 1_700_000_000.0))
        self.assertEqual(notification.call.chat_id, "caller@c.us")
        self.assertEqual((notification.sender, notification.chat, notification.get_id_message()),
                         ("caller@c.us", "caller@c.us", "call-1"))
        self.assertIsNone(notification.message_text)
        self.assertIsNone(notification.get_message_data())

        notification.answer("Hello")
        self.api.sending.sendMessage.assert_called_once_with(
            "caller@c.us", "Hello", None, None, None, None,
        )

    def test_call_and_message_observers_share_state_only_with_each_other(self):
        state_manager = self.router.message.state_manager
        self.assertIs(self.router.incoming_call.state_manager, state_manager)
        self.assertIsNot(self.router.outgoing_message.state_manager, state_manager)
        state_manager.set_state("caller@c.us", "MENU")
        state_manager.set_state_data("caller@c.us", {"language_code": "ru"})
        received = []

        @self.router.incoming_call(status="offer", state="MENU", from_sender="caller@c.us")
        def on_offer(notification):
            received.append(notification.state_manager.get_state_data(notification.call.sender))

        self.router.route_event(self.event())
        self.assertEqual(received, [{"language_code": "ru"}])

        state_manager.update_state("caller@c.us", "DONE")
        self.router.route_event(self.event())
        self.assertEqual(len(received), 1)

    def test_status_filter_accepts_one_or_many_values_and_unknown_status_is_routed(self):
        offers, ended, other = [], [], []
        self.router.incoming_call.add_handler(offers.append, status="offer")
        self.router.incoming_call.add_handler(ended.append, status=["declined", "hungUp"])
        self.router.incoming_call.add_handler(other.append)

        for status in ("offer", "declined", "hungUp", "new-status"):
            self.router.route_event(self.event(status=status))

        self.assertEqual([item.call.status for item in offers], ["offer"])
        self.assertEqual([item.call.status for item in ended], ["declined", "hungUp"])
        self.assertEqual([item.call.status for item in other], ["new-status"])

    def test_invalid_call_filters_fail_at_registration(self):
        for filters in ({"status": None}, {"status": []}, {"status": 123},
                        {"status": ["offer", ""]}, {"command": "start"}):
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                self.router.incoming_call.add_handler(lambda notification: None, **filters)
        self.assertEqual(self.router.incoming_call.handlers, [])

    def test_timestamps_and_missing_fields_are_safe(self):
        for value, expected in ((1_700_000_000, 1_700_000_000.0),
                                (1_700_000_000_000, 1_700_000_000.0),
                                (1_700_000_000_000_000, 1_700_000_000.0),
                                (946_684_800, 946_684_800.0),
                                (4_102_444_800, None),
                                (1_700_000_000_000_000_000, None),
                                (12_345_678_901_234_567_890, None),
                                (True, None), (float("nan"), None), (10 ** 1000, None)):
            with self.subTest(timestamp=value):
                self.assertEqual(IncomingCall.from_event(self.event(timestamp=value)).timestamp, expected)
        self.assertEqual(IncomingCall.from_event({}), IncomingCall(None, None, None, None))

    def test_existing_message_notification_remains_unchanged(self):
        received = []
        self.router.message.add_handler(received.append)
        event = {
            "typeWebhook": "incomingMessageReceived",
            "senderData": {"sender": "caller@c.us", "chatId": "caller@c.us"},
            "messageData": {"typeMessage": "textMessage", "textMessageData": {"textMessage": "Hi"}},
        }
        self.router.route_event(event)
        self.assertIs(type(received[0]), Notification)
        self.assertEqual(received[0].message_text, "Hi")


if __name__ == "__main__":
    unittest.main()
