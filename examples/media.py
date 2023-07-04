from whatsapp_chatbot_python import GreenAPIBot, Notification

bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345"
)


@bot.router.message(type_message="contactMessage")
def contact_handler(notification: Notification) -> None:
    notification.answer("This is a contact message.")


@bot.router.message(type_message="locationMessage")
def location_handler(notification: Notification) -> None:
    notification.answer("This is a location message.")


@bot.router.message(type_message=[
    "imageMessage", "videoMessage", "documentMessage", "audioMessage"
])
def file_handler(notification: Notification) -> None:
    notification.answer("This is a message with a file.")


bot.run_forever()
