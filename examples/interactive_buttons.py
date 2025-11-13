from whatsapp_chatbot_python import GreenAPIBot, Notification

bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345"
)


@bot.router.message()

def show_interactive_buttons_handler(notification: Notification) -> None:
    notification.answer_with_interactive_buttons(
        "This message contains interactive buttons", 
        [{
            "type": "call",
            "buttonId": "1",
            "buttonText": "Call me",
            "phoneNumber": "79123456789"
        },
        {
            "type": "url",
            "buttonId": "2",
            "buttonText": "Green-api",
            "url": "https://green-api.com"
        }],
        "Hello!",
        "Hope you like it!"
    )

    notification.answer_with_interactive_buttons_reply(
        "This message contains interactive reply buttons",
        [{
            "buttonId": "1",
            "buttonText": "First Button"
        },
        {
            "buttonId": "2",
            "buttonText": "Second Button"
        }],
        "Hello!",
        "Hope you like it!"
    )

bot.run_forever()