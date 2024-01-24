from whatsapp_chatbot_python import GreenAPIBot, Notification

bot = GreenAPIBot(
    "7103888795", "bf18f7bef8534503bc3693713b47634dec26594ce2f146c9b9"
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
