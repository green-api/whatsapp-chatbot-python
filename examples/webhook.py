from whatsapp_chatbot_python import GreenAPIBot, Notification

bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345",
    webhook_data = {
        "port": 4567,
        "host": None,
        "webhook_url": "https://coyote-literate-porpoise.ngrok-free.app"
    },
    bot_debug_mode=True,
    debug_mode=True
)


@bot.router.message(text_message="message")
def message_handler(notification: Notification) -> None:
    notification.answer("Hello")


bot.run_forever()
