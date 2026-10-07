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

Перед запуском бота необходимо удалить (при наличии) адрес отправки уведомлений (URL) из личного кабинета или выставив пустой webhookUrl с помощью [метода SetSettings](https://green-api.com/en/docs/api/account/SetSettings/). Следующие настройки будут включены ботом автоматически:

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

Ссылка на пример: [interactive_buttons.py](../examples/interactive_buttons.py).

```
@bot.router.message()

def show_interactive_buttons_handler(notification: Notification) -> None:
    notification.answer_with_interactive_buttons(
        "This message contains interactive buttons",
        [{
            "type": "call",
            "buttonId": "1",
            "buttonText": "Call me",
            "phoneNumber": "79123456789"
        },
        {
            "type": "url",
            "buttonId": "2",
            "buttonText": "Green-api",
            "url": "https://green-api.com"
        }],
        "Hello!",
        "Hope you like it!"
    )

    notification.answer_with_interactive_buttons_reply(
        "This message contains interactive reply buttons",
        [{
            "buttonId": "1",
            "buttonText": "First Button"
        },
        {
            "buttonId": "2",
            "buttonText": "Second Button"
        }],
        "Hello!",
        "Hope you like it!"
    )

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
- 5 или Show interactive buttons (бот отправит интерактивные кнопки)
- 6 или Show interactive reply buttons (бот отправит интерактивные кнопки с ответом)

Чтобы отправить текстовое сообщение, нужно использовать метод `notification.answer`.
Чтобы отправить место (локацию), нужно использовать метод `sending.sendLocation` из `notification.api`.
Чтобы отправить сообщение с файлом, нужно использовать метод `notification.answer_with_file`.
Чтобы отправить сообщение с интерактивными кнопками, нужно использовать метод `notification.answer_with_interactive_buttons`.
Чтобы отправить сообщение с интерактивными кнопками с ответом, нужно использовать метод `notification.answer_with_interactive_buttons_reply`.

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
            "4. Call a support operator\n"
            "5. Show interactive buttons\n"
            "6. Show interactive reply buttons\n\n"
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
    notification.answer_with_file("examples/data/rates.png")


@bot.router.message(text_message=["4", "Call a support operator"])
def call_support_operator_handler(notification: Notification) -> None:
    notification.answer("Good. A tech support operator will contact you soon.")

@bot.router.message(text_message=["5", "Show interactive buttons"])
def show_interactive_buttons_handler(notification: Notification) -> None:
    notification.answer_with_interactive_buttons(
        "This message contains interactive buttons",
        [{
            "type": "call",
            "buttonId": "1",
            "buttonText": "Call me",
            "phoneNumber": "79123456789"
        },
        {
            "type": "url",
            "buttonId": "2",
            "buttonText": "Green-api",
            "url": "https://green-api.com"
        }],
        "Hello!",
        "Hope you like it!"
    )

@bot.router.message(text_message=["6", "Show interactive reply buttons"])
def show_interactive_buttons_reply_handler(notification: Notification) -> None:
    notification.answer_with_interactive_buttons_reply(
        "This message contains interactive reply buttons",
        [{
            "buttonId": "1",
            "buttonText": "First Button"
        },
        {
            "buttonId": "2",
            "buttonText": "Second Button"
        }],
        "Hello!",
        "Hope you like it!"
    )

bot.run_forever()
```

## Голосовые звонки (необязательное дополнение VoIP)

Пакет `whatsapp_chatbot_python.calls` выполняет один исходящий WhatsApp-звонок:
подключение через GreenAPI SDK, голосовой разговор с OpenAI Realtime,
тайм-ауты, воспроизведение и запись MP3. Требуются **Python 3.11+**,
авторизованный инстанс с поддержкой VoIP и ключ OpenAI.

```shell
python -m pip install 'whatsapp-chatbot-python[voip]'
```

Для установки из исходников используйте `python -m pip install -e '.[voip]'`.
Дополнение фиксирует версии SDK, OpenAI и аудиобиблиотек из исходного демо.
Обычный импорт чат-бота не загружает OpenAI, aiortc или PyAV.

### Выполнить один звонок

Модель и голос задаёт приложение. При запуске с действительными реквизитами
этот пример совершит настоящий звонок:

```python
import asyncio
import logging
import os

from whatsapp_chatbot_python.calls import (
    CallEvent, CallSession, CallStateMachine,
)
from whatsapp_chatbot_python.calls.service import WhatsAppCallService

service = WhatsAppCallService(
    api_url="https://api.green-api.com",
    id_instance=os.environ["ID_INSTANCE"],
    api_token_instance=os.environ["API_TOKEN_INSTANCE"],
    openai_api_key=os.environ["OPENAI_API_KEY"],
    realtime_model=os.environ["REALTIME_MODEL"],
    realtime_voice=os.environ["REALTIME_VOICE"],
    ring_timeout_seconds=30,
    talk_timeout_seconds=120,
    shutdown_timeout_seconds=10,
    logger=logging.getLogger("calls"),
)

async def call_once():
    session = CallSession(
        sender_id="79123456789@c.us",
        chat_id="79123456789@c.us",
        language="ru",
    )
    fsm = CallStateMachine()
    fsm.apply(session, CallEvent.ENQUEUED)
    fsm.apply(session, CallEvent.DEQUEUED)
    result = await service.execute(session, fsm.apply)
    try:
        print(session.state, session.end_reason, session.error_code)
        if result.recording_path is not None:
            print("MP3:", result.recording_path)
            # Upload or copy the recording here, before removing it.
    finally:
        if result.recording_path is not None:
            result.recording_path.unlink(missing_ok=True)

asyncio.run(call_once())
```

Сохранён интерфейс `await service.execute(session, transition)`.
Перед выполнением сессия должна находиться в `DIALING`: выше её подготавливают
переходы `ENQUEUED` и `DEQUEUED`. Callback обязан синхронно применять FSM к
той же сессии. В координаторе это делается под его существующей блокировкой.
Callback, который только логирует событие, для этого контракта не подходит.

Итоговое состояние, причина завершения и код ошибки находятся в изменённой
`CallSession`. `CallExecutionResult` возвращает только путь к записи либо `None`.
На одном исполнителе звонки выполняются последовательно.
`request_stop()` можно вызвать из другого потока: он навсегда запрашивает
остановку данного исполнителя. Для возобновления создайте новый объект.
Ошибки начальной подготовки могут выбрасываться вызывающей стороне; координатор
должен их обрабатывать, как в исходном демо.

### Подключение к демо

Замените импорт сервиса и общих контрактов:

```python
from whatsapp_chatbot_python.calls.service import WhatsAppCallService
from whatsapp_chatbot_python.calls.contracts import CallExecutor, TransitionCallback
from whatsapp_chatbot_python.calls import CallSession, CallStateMachine
```

Аргументы конструктора сохраняются. В рабочем потоке координатора остаётся:

```python
result = asyncio.run(executor.execute(session, coordinator.transition))
```

Очередь, защита от повторных заявок, команды чата, меню, локализованные ответы
и загрузка записи остаются в приложении. Не ждите звонок внутри синхронного
обработчика сообщений: это задержит обработку остальных уведомлений.
Исполнитель создаёт собственный GreenAPI-клиент; передавать ему `Notification`
или API-клиент обработчика не требуется.

Старые импорты моделей и FSM можно сохранить явными реэкспортами.
`EnqueueResult` остаётся в демо. Пути подмен в тестах нужно изменить с
`internal.calls.service.*` на `whatsapp_chatbot_python.calls.service.*`,
аналогично для аудиомодулей: реэкспорт класса не меняет его глобальные зависимости.

### Аудио и запись

Сохранены PCM16 mono 24 кГц, кадры по 20 мс, прерывание ответа собеседником,
ограничение буфера и замена аудиотреков при переподключении без нового разговора
и повторного приветствия. Приветствие начинается после ответа и согласования
моста; согласование само по себе не подтверждает наличие живого аудио ICE/DTLS.
Таймер разговора запускается при ответе собеседника, включая ожидание моста.

Запись объединяет обе стороны и сохраняет паузы. Файл возвращается только при
`REMOTE_ENDED` и `TALK_TIMEOUT`; отсутствие аудио или ошибка записи могут дать
`None` и в этих случаях. Частичные записи неуспешных звонков удаляются.
Возвращённым временным MP3 владеет приложение: после отправки или копирования
оно обязано удалить файл, предпочтительно через `finally`.
Ошибка записи сама по себе не завершает разговор как неуспешный.

### Проверка

```shell
python -m pip install -e '.[voip]' pytest
python -m pytest tests
```

Тесты проверяют дозвон, порядок ответа и готовности моста, переподключения,
остановку, тайм-ауты, прерывания аудио и настоящее кодирование/декодирование MP3.
Сеть и провайдер моделируются. Без аудиозависимостей pytest собирает только
лёгкие тесты FSM и таймеров; на Python ниже 3.11 тесты звонков не собираются.
CI устанавливает дополнение и запускает полный набор на Python 3.11–3.13.

## Документация по методам сервиса

[Документация по методам сервиса](https://green-api.com/docs/api/)

## Лицензия

Лицензировано на условиях [
Creative Commons Attribution-NoDerivatives 4.0 International (CC BY-ND 4.0)
](https://creativecommons.org/licenses/by-nd/4.0/).
[LICENSE](../LICENSE).
