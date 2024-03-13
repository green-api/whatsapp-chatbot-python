from whatsapp_chatbot_python import GreenAPIBot, Notification

bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345"
)


@bot.router.message(command="start")
def message_handler(notification: Notification) -> None:
    sender_data = notification.event["senderData"]
    sender_name = sender_data["senderName"]

    notification.answer_with_poll(
        f"Hello, {sender_name}. Here's what I can do:\n\n",
        [
            {"optionName": "1. Report a problem"},
            {"optionName": "2. Show office address"},
            {"optionName": "3. Show available rates"},
            {"optionName": "4. Call a support operator"}
        ]
    )


@bot.router.poll_update_message()
def start_poll_handler(notification: Notification) -> None:
    votes = notification.event["messageData"]["pollMessageData"]["votes"]
    for vote_data in votes:
        voters = vote_data["optionVoters"]
        if voters:
            option_name = vote_data["optionName"]
            if option_name == "1. Report a problem":
                link = "https://github.com/green-api/issues/issues/new"

                notification.answer(link, link_preview=False)
            elif option_name == "2. Show office address":
                notification.api.sending.sendLocation(
                    notification.chat, 55.7522200, 37.6155600
                )
            elif option_name == "3. Show available rates":
                notification.answer_with_file("data/rates.png")
            elif option_name == "4. Call a support operator":
                notification.answer(
                    "Good. A tech support operator will contact you soon."
                )


bot.run_forever()
