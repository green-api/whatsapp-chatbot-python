from whatsapp_chatbot_python import GreenAPIBot, Notification

bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345"
)

# Handle message, which sent from this instance without API
@bot.router.outgoing_message()
def message_handler_outgoing(notification: Notification) -> None:
    print("Outgoing Message: ", notification.get_id_message())
    print("Sender:", notification.get_sender())
    print("Chat:", notification.get_chat())
    print("Message Text:", notification.get_message_text())
    print("Message:", notification.get_message_data())

# Handle message, which sent from this instance with API
@bot.router.outgoing_api_message()
def message_handler_outgoing_api(notification: Notification) -> None:
    print("Outgoing Message from API: ", notification.get_id_message())
    print("Sender:", notification.get_sender())
    print("Chat:", notification.get_chat())
    print("Message Text:", notification.get_message_text())
    print("Message:", notification.get_message_data())

# Handle status of any sent message from this instance (sent, delivired, read)
@bot.router.outgoing_message_status()
def message_handler_outgoing_status(notification: Notification) -> None:
    print("Status of message:", notification.get_id_message(), "is", notification.get_status_message())

bot.run_forever()