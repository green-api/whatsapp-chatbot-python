import unittest

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
        bot = self.bot

        @bot.router.message()
        def message_handler(notification: Notification):
            self.assertEqual(notification.event, event_example)

        bot.router.route_event(event_example)

    def test_filters(self):
        bot = self.bot

        @bot.router.message(command="help")
        def command_handler(_):
            raise Exception("filters do not filter messages")

        bot.router.route_event(event_example)

    def test_observers(self):
        bot = self.bot

        @bot.router.message()
        def handler(_):
            pass

        bot.router.message.add_handler(handler)

        self.assertEqual(len(bot.router.message.handlers), 2)

    @property
    def bot(self):
        return GreenAPIBot("", "")


if __name__ == '__main__':
    unittest.main()
