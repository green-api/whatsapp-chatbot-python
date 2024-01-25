from whatsapp_chatbot_python import GreenAPIBot, Notification

bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345"
)


@bot.router.buttons()
def buttons_handler(notification: Notification) -> None:
    notification.answer_buttons("Choose a color", [
        {
            "buttonId": 1,
            "buttonText": "Red"
        },
        {
            "buttonId": 2,
            "buttonText": "Green"
        },
        {
            "buttonId": 3,
            "buttonText": "Blue"
        }
    ])


bot.run_forever()
