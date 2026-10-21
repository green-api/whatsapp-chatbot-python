from whatsapp_chatbot_python import GreenAPIBot, IncomingCallNotification

bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345"
)


@bot.router.incoming_call()
def incoming_call_handler(notification: IncomingCallNotification) -> None:
    call = notification.call
    print("Call from:", call.sender, "with status", call.status)
    # notification.event (or notification.raw_event) still contains the original webhook.

bot.run_forever()
