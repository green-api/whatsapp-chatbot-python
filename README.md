# whatsapp-chatbot-python

![](https://img.shields.io/badge/license-CC%20BY--ND%204.0-green)
![](https://img.shields.io/pypi/status/whatsapp-chatbot-python)
![](https://img.shields.io/pypi/pyversions/whatsapp-chatbot-python)
![](https://img.shields.io/github/actions/workflow/status/green-api/whatsapp-chatbot-python/python-package.yml)
![](https://img.shields.io/pypi/dm/whatsapp-chatbot-python)

- [Документация на русском языке](https://github.com/green-api/whatsapp-chatbot-python/blob/master/docs/README.md).

whatsapp-chatbot-python is a library for integration with WhatsApp messenger using the API
service [green-api.com](https://green-api.com/en/). You should get a registration token and an account ID in
your [personal cabinet](https://console.green-api.com/) to use the library. There is a free developer account tariff.

## API

The documentation for the REST API can be found at the [link](https://green-api.com/en/docs/). The library is a wrapper
for the REST API, so the documentation at the link above also applies.

## Authorization

To send a message or perform other Green API methods, the WhatsApp account in the phone app must be authorized. To
authorize the account, go to your [cabinet](https://console.green-api.com/) and scan the QR code using the WhatsApp app.

## Installation

Installation:

```shell
python -m pip install whatsapp-chatbot-python
```

## Import

```
from whatsapp_chatbot_python import GreenAPIBot, Notification
```

## Examples

### How to initialize an object

```
bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345"
)
```

### How to set up an instance

To start receiving incoming notifications, you need to set up an instance. Open the personal cabinet page at
the [link](https://console.green-api.com/). Select an instance from the list and click on it. Click **Change**. In the *
*Notifications** category, enable all notifications that you want to receive.

### How to start receiving and answering messages

To start receiving messages, you must create a handler function with one parameter (`notification`). The `notification`
parameter is the class where the notification object (`event`) and the functions to answer the message are stored. To
send a text message in response to a notification, you need to call the `notification.answer` function and pass there
the text of the message. You don't need to pass the `chatId` parameter because it is automatically taken from the
notification.

Next, you need to add the handler function to the list of handlers. This can be done with the `bot.router.message`
decorator as in the example or with the `bot.router.message.add_handler` function. The decorator must be called with
brackets.

To start the bot, call the `bot.run_forever` function. You can stop the bot with the key combination Ctrl + C.

In this example, the bot will only answer the `message` message.

Link to example: [base.py](https://github.com/green-api/whatsapp-chatbot-python/blob/master/examples/base.py).

```
@bot.router.message(text_message="message")
def message_handler(notification: Notification) -> None:
    notification.answer("Hello")


bot.run_forever()
```

### How to receive other notifications and handle the notification body

You can receive not only incoming messages but also outgoing messages. You can also get the status of the sent message.

- To receive outgoing messages, you need to use the `bot.router.outgoing_message` object;
- To receive outgoing API messages, you need to use the `bot.router.outgoing_api_message` object;
- To receive the status of sent messages, you need to use the `bot.router.outgoing_message_status` object.

The body of the notification is in `notification.event`. In this example, we get the message type from the notification
body.

In this example, the bot receives all incoming messages.

Link to example: [event.py](https://github.com/green-api/whatsapp-chatbot-python/blob/master/examples/event.py).

```
@bot.router.message()
def message_handler(notification: Notification) -> None:
    print(notification.event)


bot.run_forever()
```

### How to filter incoming messages

Messages can be filtered by chat, sender, message type, and text. To filter chat, sender, and message type, you can use
a string (`str`) or a list of strings (`list[str]`). The message text can be filtered by text, command, and regular
expressions. Below is a table with filter names and possible values.

| Filter name    | Description                                                                               | Possible values                                                    |
|----------------|-------------------------------------------------------------------------------------------|--------------------------------------------------------------------|
| `from_chat`    | Chats or chats from which you want to receive messages                                    | `"11001234567@c.us"` or `["11001234567@c.us", "11002345678@c.us"]` |
| `from_sender`  | The sender or senders from whom you want to receive messages                              | `"11001234567@c.us"` or `["11001234567@c.us", "11002345678@c.us"]` |
| `type_message` | The type or types of message to be handled                                                | `"textMessage"` or `["textMessage", "extendedTextMessage"]`        |
| `text_message` | Your function will be executed if the text fully matches the text                         | `"Hello. I need help."` or `["Hello", "I need help"]`              |
| `regexp`       | Your function will be executed if the text matches the regular expression pattern         | `r"Hello. I need help."`                                           |
| `command`      | Your function will be executed if the prefix and the command match your values completely | `"help"` or `("help", "!/")`                                       |

#### How to add filters through the decorator

```
@bot.router.message(command="command")
```

#### How to add filters with the function

```
bot.router.message.add_handler(handler, command="command")
```

#### How to filter messages by chat, sender, or message type

To filter messages by chat, sender, or message type, you must add a string (`str`) or a list of strings (`list[str]`).

```
from_chat = "11001234567@c.us"
```

```
from_sender = "11001234567@c.us"
```

```
type_message = ["textMessage", "extendedTextMessage"]
```

#### How to filter messages by message text or regular expressions

You must add a string (`str`) to filter messages by text or regular expressions.

```
text_message = "Hello. I need help."
```

```
regexp = r"Hello. I need help."
```

#### How to filter messages by command

Add a string (`str`) or a tuple (`tuple`) to filter messages by command. You need to specify either a command name or a
command name and a prefix string. The default prefix is `/`.

```
command = "help"
```

```
command = ("help", "!/")
```

#### Example

In this example, the bot will send a photo in response to the `rates` command.

Link to example: [filters.py](https://github.com/green-api/whatsapp-chatbot-python/blob/master/examples/filters.py).

```
@bot.router.message(command="rates")
def message_handler(notification: Notification) -> None:
    notification.answer_with_file(file="data/rates.png")


bot.run_forever()
```

### How to handle buttons

To be notified when a button is pressed, you must use the `bot.router.buttons` object.

Link to example: [buttons.py](https://github.com/green-api/whatsapp-chatbot-python/blob/master/examples/buttons.py).

```
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
```

### How to manage user state

As an example, a bot was created for user registration.

To manage user states, we need to create states. Import the `BaseStates` class and inherit from it. To manage the state
we need to use `notification.state_manager`. The manager has methods for getting, setting, updating and deleting state.
You also have the option to save the user's data in his state.

| Manager's method    | Description                                                                           |
|---------------------|---------------------------------------------------------------------------------------|
| `get_state`         | Returns a state class with state name and user data                                   |
| `set_state`         | Sets the state for the user. If the state exists then the data will be deleted        |
| `update_state`      | If a state exists, it changes it. If not, it creates a new state                      |
| `delete_state`      | Deletes the user's state. Remember to get the data before deleting                    |
| `get_state_data`    | If the state exists, it returns the data in the form of a dictionary (dict)           |
| `set_state_data`    | If the state exists, it changes the data to the new data                              |
| `update_state_data` | If the state exists, it updates the data. If no data exists, the data will be created |
| `delete_state_data` | If the state exists, it deletes the data                                              |

The first argument is the sender ID. It can be found by calling `notification.sender`.

Link to example: [states.py](https://github.com/green-api/whatsapp-chatbot-python/blob/master/examples/states.py).

```python
from whatsapp_chatbot_python import BaseStates, GreenAPIBot, Notification

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


@bot.router.message(state=States.USERNAME.value)
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


@bot.router.message(state=States.PASSWORD.value)
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
```

### Example of a bot

As an example, a bot was created to support the GREEN API. Command list:

- start (the bot says hello and sends a list of commands)
- 1 or Report a problem (the bot will send a link to GitHub to create the bug)
- 2 or Show office address (the bot will send the office address as a map)
- 3 or Show available rates (the bot will send a picture of the rates)
- 4 or Call a support operator (the bot will send a text message)

To send a text message, you have to use the `notification.answer` method.
To send a location, you have to use the `sending.sendLocation` method from `notification.api`.
To send a message with a file, you have to use the `notification.answer_with_file` method.

In this example, the bot only responds to commands from the list above.

Link to example: [full.py](https://github.com/green-api/whatsapp-chatbot-python/blob/master/examples/full.py).

```python
from whatsapp_chatbot_python import GreenAPIBot, Notification

bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345"
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
            "4. Call a support operator\n\n"
            "Choose a number and send to me."
        )
    )


@bot.router.message(text_message=["1", "Report a problem"])
def report_problem_handler(notification: Notification) -> None:
    notification.answer(
        "https://github.com/green-api/issues/issues/new", link_preview=False
    )


@bot.router.message(text_message=["2", "Show office address"])
def show_office_address_handler(notification: Notification) -> None:
    chat = notification.chat

    notification.api.sending.sendLocation(
        chatId=chat, latitude=55.7522200, longitude=37.6155600
    )


@bot.router.message(text_message=["3", "Show available rates"])
def show_available_rates_handler(notification: Notification) -> None:
    notification.answer_with_file("data/rates.png")


@bot.router.message(text_message=["4", "Call a support operator"])
def call_support_operator_handler(notification: Notification) -> None:
    notification.answer("Good. A tech support operator will contact you soon.")


bot.run_forever()
```

## Service methods documentation

[Service methods documentation](https://green-api.com/en/docs/api/)

## License

Licensed under [
Creative Commons Attribution-NoDerivatives 4.0 International (CC BY-ND 4.0)
](https://creativecommons.org/licenses/by-nd/4.0/) terms. Please see file [LICENSE](
https://github.com/green-api/whatsapp-chatbot-python/blob/master/LICENSE
).
