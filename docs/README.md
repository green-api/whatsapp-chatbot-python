# whatsapp-chatbot-python

![](https://img.shields.io/badge/license-CC%20BY--ND%204.0-green)
![](https://img.shields.io/pypi/status/whatsapp-chatbot-python)
![](https://img.shields.io/pypi/pyversions/whatsapp-chatbot-python)
![](https://img.shields.io/github/actions/workflow/status/green-api/whatsapp-chatbot-python/python-package.yml)
![](https://img.shields.io/pypi/dm/whatsapp-chatbot-python)

whatsapp-chatbot-python - библиотека для интеграции с мессенджером WhatsApp через API
сервиса [green-api.com](https://green-api.com/). Чтобы воспользоваться библиотекой, нужно получить регистрационный токен
и ID аккаунта в [личном кабинете](https://console.green-api.com/). Есть бесплатный тариф аккаунта разработчика.

## API

Документация к REST API находится по [ссылке](https://green-api.com/docs/api/). Библиотека является обёрткой к REST API,
поэтому документация по ссылке выше применима и к самой библиотеке.

## Авторизация

Чтобы отправить сообщение или выполнить другие методы GREEN API, аккаунт WhatsApp в приложении телефона должен быть в
авторизованном состоянии. Для авторизации аккаунта перейдите в [личный кабинет](https://console.green-api.com/) и
сканируйте QR-код с использованием приложения WhatsApp.

## Установка

Установка:

```shell
python -m pip install whatsapp-chatbot-python
```

## Импорт

```
from whatsapp_chatbot_python import GreenAPIBot, Notification
```

## Примеры

### Как инициализировать объект

```
bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345"
)
```

### Как начать получать сообщения и отвечать на них

Чтобы начать получать сообщения, вам нужно создать функцию-обработчик с одним параметром (`notification`).
Параметр `notification` это класс в котором хранится объект уведомления (`event`) и функции для ответа на сообщение.
Чтобы отправить текстовое сообщение в ответ на уведомление, вам нужно вызвать функцию `notification.answer` и передать
туда текст сообщения. Параметр `chatId` указывать не нужно, так как он автоматически подставляется из уведомления.

Далее нужно добавить функцию-обработчик в список обработчиков. Сделать это можно с помощью
декоратора `bot.router.message` как в примере или с помощью функции `bot.router.message.add_handler`. Декоратор
обязательно нужно вызвать с помощью скобок.

Чтобы запустить бота, нужно вызвать функцию `bot.run_forever`.
Остановить бота можно с помощью сочетания клавиш Ctrl + C.

Ссылка на пример: [base.py](../examples/base.py).

```
@bot.router.message(text_message="message")
def message_handler(notification: Notification) -> None:
    notification.answer("Hello")


bot.run_forever()
```

### Как получать другие уведомления и обрабатывать тело уведомления

Получать можно не только входящие сообщения, но и исходящие. Также можно получать статус отправленного сообщения.

- Чтобы получать исходящие сообщения, нужно использовать объект `bot.router.outgoing_message`;
- Чтобы получать исходящие API сообщения, нужно использовать объект `bot.router.outgoing_api_message`;
- Чтобы получать статус отправленного сообщения, нужно использовать объект `bot.router.outgoing_message_status`.

Тело уведомления находится в `notification.event`. В этом примере мы отправляем в консоль тело нового уведомления.

Ссылка на пример: [event.py](../examples/event.py).

```
@bot.router.message()
def message_handler(notification: Notification) -> None:
    print(notification.event)


bot.run_forever()
```

### Как фильтровать входящие сообщения

Сообщения можно фильтровать по чату, по отправителю, по типу и тексту сообщения. Для фильтров чата, отправителя и типа
сообщения можно использовать строку (`str`) или список из строк (`list[str]`). Текст сообщения можно фильтровать по
тексту, по команде и регулярным выражениям. Ниже таблица с названиями фильтров и возможными значениями.

| Название фильтра | Описание                                                                                      | Возможные значения                                                  |
|------------------|-----------------------------------------------------------------------------------------------|---------------------------------------------------------------------|
| `from_chat`      | Чат или чаты от которых нужно получать сообщения                                              | `"11001234567@c.us"` или `["11001234567@c.us", "11002345678@c.us"]` |
| `from_sender`    | Отправитель или отправители от которых нужно получать сообщения                               | `"11001234567@c.us"` или `["11001234567@c.us", "11002345678@c.us"]` |
| `type_message`   | Тип или типы сообщения, которые нужно обрабатывать                                            | `"textMessage"` или `["textMessage", "extendedTextMessage"]`        |
| `text_message`   | Ваша функция будет выполнена если текст полностью соответствует тексту                        | `"Привет. Мне нужна помощь"` или `["Привет", "Мне нужна помощь"]`   |
| `regexp`         | Ваша функция будет выполнена если текст полностью соответствует шаблону регулярного выражения | `r"Привет. Мне нужна помощь"`                                       |
| `command`        | Ваша функция будет выполнена если префикс и команда полностью соответствуем вашим значениям   | `"help"` или `("help", "!/")`                                       |

#### Как добавить фильтры через декоратор

```
@bot.router.message(command="command")
```

#### Как добавить фильтры с помощью функции

```
bot.router.message.add_handler(handler, command="command")
```

#### Как фильтровать сообщения по чату, отправителю или типу сообщения

Чтобы фильтровать сообщения по чату, отправителю или типу сообщения, нужно добавить строку (`str`) или список из
строк (`list[str]`).

```
from_chat = "11001234567@c.us"
```

```
from_sender = "11001234567@c.us"
```

```
type_message = ["textMessage", "extendedTextMessage"]
```

#### Как фильтровать сообщения по тексту сообщения или регулярным выражениям

Чтобы фильтровать сообщения по тексту сообщения, регулярным выражениям или команде, нужно добавить строку (`str`).

```
text_message = "Привет. Мне нужна помощь"
```

```
regexp = r"Привет. Мне нужна помощь"
```

#### Как фильтровать сообщения по команде

Чтобы фильтровать сообщения по команде, нужно добавить строку (`str`) или кортеж (`tuple`). Вам нужно указать либо
название команды, либо название команды и строку префиксов. Префикс по умолчанию: `/`.

```
command = "help"
```

```
command = ("help", "!/")
```

#### Пример

Ссылка на пример: [filters.py](../examples/filters.py).

```
@bot.router.message(command="help")
def message_handler(notification: Notification) -> None:
    notification.answer_with_file(file="help.png")


bot.run_forever()
```

### Как обрабатывать кнопки

Чтобы получать уведомления о нажатиях на кнопку, нужно использовать объект `bot.router.buttons`.

Ссылка на пример: [buttons.py](../examples/buttons.py).

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

### Пример бота

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
    chat = notification.get_chat()

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

## Документация по методам сервиса

[Документация по методам сервиса](https://green-api.com/docs/api/)

## Лицензия

Лицензировано на условиях [
Creative Commons Attribution-NoDerivatives 4.0 International (CC BY-ND 4.0)
](https://creativecommons.org/licenses/by-nd/4.0/).
[LICENSE](../LICENSE).
