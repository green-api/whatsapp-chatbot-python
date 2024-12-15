from whatsapp_chatbot_python import GreenAPIBot, Notification

bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345"
)

@bot.router.message()
def message_handler(notification: Notification) -> None:
    if "@g.us" in notification.get_chat():
        print("Incoming message", notification.get_id_message(), "in group chat", notification.get_chat(), "from", notification.get_sender())
        notification.answer("Hello, group!")
        # Add your implementation
    if "@c.us" in notification.get_chat():
        print("Incoming message", notification.get_id_message(), "in contact chat", notification.get_chat())
        notification.answer("Hello, contact!")
         # Add your implementation
    
bot.run_forever()