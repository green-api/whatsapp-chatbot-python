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
| `text_message` | Your function will be executed if the text fully matches the text                         | `"Hello. I need help."`                                            |
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

Link to example: [filters.py](https://github.com/green-api/whatsapp-chatbot-python/blob/master/examples/filters.py).

```
@bot.router.message(command="help")
def message_handler(notification: Notification) -> None:
    notification.answer_with_file(file="help.png")


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

## Service methods documentation

[Service methods documentation](https://green-api.com/en/docs/api/)

## License

Licensed under [
Creative Commons Attribution-NoDerivatives 4.0 International (CC BY-ND 4.0)
](https://creativecommons.org/licenses/by-nd/4.0/) terms.
Please see file [LICENSE](https://github.com/green-api/whatsapp-chatbot-python/blob/master/LICENSE).
