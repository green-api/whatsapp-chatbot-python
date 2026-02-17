from whatsapp_chatbot_python import GreenAPIBot, Notification

bot = GreenAPIBot(
    "1101111111", "c880070976f64f8797484d14819276a457f481a327d64f9da4"
)


@bot.router.message(command="start")
def message_handler(notification: Notification) -> None:
    sender_data = notification.event["senderData"]
    sender_name = sender_data["senderName"]

    notification.answer(
        (
            f"Hello, {sender_name}. Here's what I can do:\n\n"
            "1. Report a problem\n"
            "2. Show office address\n"
            "3. Show available rates\n"
            "4. Call a support operator\n"
            "5. Show interactive buttons\n"
            "6. Show interactive reply buttons\n\n"
            "Choose a number and send to me."
        )
    )


@bot.router.message(text_message=["1", "Report a problem"])
def report_problem_handler(notification: Notification) -> None:
    notification.answer(
        "https://github.com/green-api/issues/issues/new", link_preview=False, typing_time=2000
    )


@bot.router.message(text_message=["2", "Show office address"])
def show_office_address_handler(notification: Notification) -> None:
    chat = notification.chat

    notification.api.sending.sendLocation(
        chatId=chat, latitude=55.7522200, longitude=37.6155600
    )


@bot.router.message(text_message=["3", "Show available rates"])
def show_available_rates_handler(notification: Notification) -> None:
    notification.answer_with_file("examples/data/rates.png")


@bot.router.message(text_message=["4", "Call a support operator"])
def call_support_operator_handler(notification: Notification) -> None:
    notification.answer(
        "Good. A tech support operator will contact you soon.", typing_time=2000
    )

@bot.router.message(text_message=["5", "Show interactive buttons"])
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

@bot.router.message(text_message=["6", "Show interactive reply buttons"])
def show_interactive_buttons_reply_handler(notification: Notification) -> None:
    notification.answer_with_interactive_buttons_reply(
        "This message contains interactive reply buttons",
        [{
            "buttonId": "1",
            "buttonText": "Report a problem"
        },
        {
            "buttonId": "2",
            "buttonText": "Show office address"
        },
        {
            "buttonId": "3",
            "buttonText": "Show available rates"
        },
        {
            "buttonId": "4",
            "buttonText": "Call a support operator"
        },
        {
            "buttonId": "5",
            "buttonText": "Show interactive buttons"
        }],
        "Hello!",
        "Hope you like it!"
    )

bot.run_forever()
