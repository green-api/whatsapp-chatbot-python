from whatsapp_chatbot_python import GreenAPIBot, Notification

bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345"
)


@bot.router.incoming_call()
def message_handler(notification: Notification) -> None:
    print("Call from:", notification.event["from"], "with status", notification.event["status"])

bot.run_forever()
