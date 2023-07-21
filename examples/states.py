from whatsapp_chatbot_python import BaseStates, GreenAPIBot, Notification
from whatsapp_chatbot_python.filters import TEXT_TYPES

bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345"
)


class States(BaseStates):
    USERNAME = "username"
    PASSWORD = "password"


@bot.router.message(state=None)
def message_handler(notification: Notification) -> None:
    sender = notification.sender

    notification.state_manager.set_state(sender, States.USERNAME.value)

    notification.answer("Hello. Tell me your username.")


@bot.router.message(command="cancel")
def cancel_handler(notification: Notification) -> None:
    sender = notification.sender

    state = notification.state_manager.get_state(sender)
    if not state:
        return None
    else:
        notification.state_manager.delete_state(sender)

        notification.answer("Bye")


@bot.router.message(state=States.USERNAME.value, type_message=TEXT_TYPES)
def username_handler(notification: Notification) -> None:
    sender = notification.sender
    username = notification.message_text

    if not 5 <= len(username) <= 20:
        notification.answer("Invalid username.")
    else:
        notification.state_manager.update_state(sender, States.PASSWORD.value)
        notification.state_manager.set_state_data(
            sender, {"username": username}
        )

        notification.answer("Tell me your password.")


@bot.router.message(state=States.PASSWORD.value, type_message=TEXT_TYPES)
def password_handler(notification: Notification) -> None:
    sender = notification.sender
    password = notification.message_text

    if not 8 <= len(password) <= 20:
        notification.answer("Invalid password.")
    else:
        data = notification.state_manager.get_state_data(sender)

        username = data["username"]

        notification.answer(
            (
                "Successful account creation.\n\n"
                f"Your username: {username}.\n"
                f"Your password: {password}."
            )
        )

        notification.state_manager.delete_state(sender)


bot.run_forever()
