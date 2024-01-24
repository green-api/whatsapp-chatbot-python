from whatsapp_chatbot_python import GreenAPIBot, Notification

bot = GreenAPIBot(
    "7103888795", "bf18f7bef8534503bc3693713b47634dec26594ce2f146c9b9"
)


@bot.router.message(text_message="message")
def message_handler(notification: Notification) -> None:
    notification.answer("Hello")


bot.run_forever()
