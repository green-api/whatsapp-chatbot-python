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

## Настройки

Перед запуском бота необходимо включить входящие уведомления в настройках экземпляра с помощью [метода SetSettings](https://green-api.com/en/docs/api/account/SetSettings/).

```json
{
  "incomingWebhook": "yes",
  "outgoingMessageWebhook": "yes",
  "outgoingAPIMessageWebhook": "yes"
}
```

## Примеры

### Как инициализировать объект

```
bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345"
)
```

### Как включить режим отладки

```
bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345",
    bot_debug_mode=True
)
```

Также можно включить режим отладки API:

```
bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345",
    debug_mode=True, bot_debug_mode=True
)
```

### Как настроить инстанс

Чтобы начать получать входящие уведомления, нужно настроить инстанс. Открываем страницу личного кабинета
по [ссылке](https://console.green-api.com/). Выбираем инстанс из списка и кликаем на него. Нажимаем **Изменить**. В
категории **Уведомления** включаем все что необходимо получать.

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

В этом примере бот ответит только на сообщение `message`.

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

В этом примере бот получает все входящие сообщения.

Ссылка на пример: [event.py](../examples/event.py).

```
@bot.router.message()
def message_handler(notification: Notification) -> None:
    print(notification.event)


bot.run_forever()
```

### Получение уведомлений через HTTP API

Эта библиотека получает входящие веб-хуки (сообщения, статусы) через HTTP API-запросы в порядке, аналогичном реализации других методов Green API. Хронологический порядок веб-хуков гарантированно соответствует последовательности, в которой они были получены (FIFO). Все входящие веб-крючки хранятся в очереди и ожидаются к получению в течение 24 часов.

Для получения входящих веб-крючков эта библиотека последовательно вызывает два метода: [ReceiveNotification](https://green-api.com/en/docs/api/receiving/technology-http-api/ReceiveNotification/) и [DeleteNotification](https://green-api.com/en/docs/api/receiving/technology-http-api/DeleteNotofication/). Метод `ReceiveNotification` получает входящий вебхук, а метод `DeleteNotification` подтверждает успешное получение и обработку вебхука. Подробнее об этих методах читайте в соответствующих разделах [ReceiveNotification](https://green-api.com/en/docs/api/receiving/technology-http-api/ReceiveNotification/) и [DeleteNotification](https://green-api.com/en/docs/api/receiving/technology-http-api/DeleteNotofication/).

### Как фильтровать входящие сообщения

Сообщения можно фильтровать по чату, по отправителю, по типу и тексту сообщения. Для фильтров чата, отправителя и типа
сообщения можно использовать строку (`str`) или список из строк (`list[str]`). Текст сообщения можно фильтровать по
тексту, по команде и регулярным выражениям. Ниже таблица с названиями фильтров и возможными значениями.

| Название фильтра | Описание                                                                                      | Возможные значения                                                  |
| ---------------- | --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| `from_chat`      | Чат или чаты от которых нужно получать сообщения                                              | `"11001234567@c.us"` или `["11001234567@c.us", "11002345678@c.us"]` |
| `from_sender`    | Отправитель или отправители от которых нужно получать сообщения                               | `"11001234567@c.us"` или `["11001234567@c.us", "11002345678@c.us"]` |
| `type_message`   | Тип или типы сообщения, которые нужно обрабатывать                                            | `"textMessage"` или `["textMessage", "extendedTextMessage"]`        |
| `text_message`   | Ваша функция будет выполнена если текст полностью соответствует тексту                        | `"message"` или `["message", "MESSAGE"]`                            |
| `regexp`         | Ваша функция будет выполнена если текст полностью соответствует шаблону регулярного выражения | `r"message"` или `(r"message", re.IGNORECASE)`                      |
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

В этом примере бот отправит фотографию в ответ на команду `rates`.

Ссылка на пример: [filters.py](../examples/filters.py).

```
@bot.router.message(command="rates")
def message_handler(notification: Notification) -> None:
    notification.answer_with_file(file="data/rates.png")


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

### Как управлять состоянием пользователя

В качестве примера был создан бот для регистрации пользователя.

Чтобы управлять состоянием пользователя, нужно создать состояния. Импортируем класс `BaseStates` и наследуемся от него.
Для управления состоянием нужно использовать `notification.state_manager`. В менеджере есть методы получения, установки,
обновления и удаления состояния. Также у вас есть возможность сохранить данные пользователя в его состоянии.

| Метод менеджера     | Описание                                                                                    |
| ------------------- | ------------------------------------------------------------------------------------------- |
| `get_state`         | Возвращает класс состояния в котором есть имя состояния и данные пользователя               |
| `set_state`         | Устанавливает состояние для пользователя. Если состояние существует то данные будут удалены |
| `update_state`      | Если состояние существует то изменяет его. Если нет то создает новое состояние              |
| `delete_state`      | Удаляет состояние пользователя. Не забудьте получить данные перед удалением                 |
| `get_state_data`    | Если состояние существует то возвращает данные в виде словаря (dict)                        |
| `set_state_data`    | Если состояние существует то изменяет данные на новые                                       |
| `update_state_data` | Если состояние существует то обновляет данные. Если данных нет то данные будут созданы      |
| `delete_state_data` | Если состояние существует то удаляет данные                                                 |

Первым аргументом является ID отправителя. Его можно получить обратившись к `notification.sender`.

Ссылка на пример: [states.py](../examples/states.py).

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

### Часто задаваемые вопросы

- Как вызвать методы API?

```
bot.api.account.getSettings()
```

Или

```
notification.api.account.getSettings()
```

- Как отключить вызов ошибок?

```
bot = GreenAPIBot(
    "1101000001", "d75b3a66374942c5b3c019c698abc2067e151558acbd412345",
    raise_errors=False
)
```

- Как подписаться только на текстовые сообщения?

Нужно сначала импортировать нужные константы:

```
from whatsapp_chatbot_python.filters import TEXT_TYPES
```

Затем добавить этот фильтр: `type_message=TEXT_TYPES`.

- Как получить текст сообщения и ID отправителя?

Эти данные есть в объекте уведомления (`notification`):

```
@bot.router.message()
def message_handler(notification: Notification) -> None:
    print(notification.sender)
    print(notification.message_text)
```

### Пример бота

В качестве примера был создан бот для поддержки GREEN API. Список команд:

- start (бот поздоровается и отправит список команд)
- 1 или Report a problem (бот отправит ссылку на создание ошибки на GitHub)
- 2 или Show office address (бот отправит адрес офиса в виде карты)
- 3 или Show available rates (бот отправит картинку с тарифами)
- 4 или Call a support operator (бот отправит текстовое сообщение)

Чтобы отправить текстовое сообщение, нужно использовать метод `notification.answer`.
Чтобы отправить место (локацию), нужно использовать метод `sending.sendLocation` из `notification.api`.
Чтобы отправить сообщение с файлом, нужно использовать метод `notification.answer_with_file`.

В этом примере бот отвечает только на команды из списка выше.

Ссылка на пример: [full.py](../examples/full.py).

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

## Документация по методам сервиса

[Документация по методам сервиса](https://green-api.com/docs/api/)

## Лицензия

Лицензировано на условиях [
Creative Commons Attribution-NoDerivatives 4.0 International (CC BY-ND 4.0)
](https://creativecommons.org/licenses/by-nd/4.0/).
[LICENSE](../LICENSE).
