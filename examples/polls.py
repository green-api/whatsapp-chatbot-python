from whatsapp_chatbot_python import GreenAPIBot, Notification

bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345"
)

@bot.router.message(text_message="message")
def send_poll(notification: Notification) -> None:
    notification.api.sending.sendPoll(
        chatId=notification.chat,
        message=f'A or B?',
        options=[
            {"optionName": "A"},
            {"optionName": "B"},
        ],
        multipleAnswers=False
    )

@bot.router.polls()
def polls_handler(notification: Notification) -> None:
    chatId = notification.chat
    vote_data = notification.event["messageData"]["pollMessageData"]["votes"]
    sender_vote = None

    for vote in vote_data:
        if notification.event["senderData"]["sender"] in vote["optionVoters"]:
            sender_vote = vote["optionName"]
            break
    if sender_vote == "A":
        notification.api.sending.sendMessage(chatId, "You selected A")
    if sender_vote == "B":
        notification.api.sending.sendMessage(chatId, "You selected B")


bot.run_forever()