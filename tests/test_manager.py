import unittest
from unittest.mock import patch

from whatsapp_chatbot_python import GreenAPIBot, Notification

event_example = {
    "typeWebhook": "incomingMessageReceived",
    "messageData": {
        "typeMessage": "textMessage",
        "textMessageData": {
            "textMessage": "Hello"
        }
    }
}


class ManagerTestCase(unittest.TestCase):
    def test_router(self):
        bot = self.create_bot()

        @bot.router.message()
        def message_handler(notification: Notification):
            self.assertEqual(notification.event, event_example)

        bot.router.route_event(event_example)

    def test_filters(self):
        bot = self.create_bot()

        @bot.router.message(command="help")
        def command_handler(_):
            raise Exception("filters do not filter messages")

        bot.router.route_event(event_example)

    def test_observers(self):
        bot = self.create_bot()

        @bot.router.message()
        def handler(_):
            pass

        bot.router.message.add_handler(handler)

        self.assertEqual(len(bot.router.message.handlers), 2)

    @patch("whatsapp_chatbot_python.bot.Bot._update_settings")
    def create_bot(self, mock__update_settings):
        mock__update_settings.return_value = None

        return GreenAPIBot("", "")


if __name__ == '__main__':
    unittest.main()
