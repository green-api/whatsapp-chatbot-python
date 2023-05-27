from abc import ABC, abstractmethod
from re import fullmatch
from typing import Dict, List, Optional, Type, Union


class AbstractFilter(ABC):
    @abstractmethod
    def __init__(self, *args, **kwargs):
        pass

    @abstractmethod
    def check_event(self, event: dict) -> bool:
        pass


class ChatIDFilter(AbstractFilter):
    def __init__(self, chat: Union[str, List[str]]):
        self.chat = chat

    def check_event(self, event: dict) -> bool:
        chat = event["senderData"]["chatId"]

        if chat == self.chat or chat in self.chat:
            return True
        return False


class SenderFilter(AbstractFilter):
    def __init__(self, sender: Union[str, List[str]]):
        self.sender = sender

    def check_event(self, event: dict) -> bool:
        sender = event["senderData"]["sender"]

        if sender == self.sender or sender in self.sender:
            return True
        return False


class TypeMessageFilter(AbstractFilter):
    def __init__(self, type_message: Union[str, List[str]]):
        self.type_message = type_message

    def check_event(self, event: dict) -> bool:
        type_message = event["messageData"]["typeMessage"]

        if (
                type_message == self.type_message
                or type_message in self.type_message
        ):
            return True
        return False


class TextMessageFilter(AbstractFilter):
    def __init__(self, text_message: str):
        self.text_message = text_message

    def check_event(self, event: dict) -> bool:
        text_message = self.get_text_message(event)
        if text_message == self.text_message:
            return True
        return False

    @staticmethod
    def get_text_message(event: dict) -> Optional[str]:
        message_data = event["messageData"]

        type_message = message_data["typeMessage"]
        if type_message == "textMessage":
            return message_data["textMessageData"]["textMessage"]
        elif type_message == "extendedTextMessage":
            return message_data["extendedTextMessageData"]["text"]


class RegExpFilter(AbstractFilter):
    def __init__(self, pattern: str):
        self.pattern = pattern

    def check_event(self, event: dict) -> bool:
        text_message = TextMessageFilter.get_text_message(event)
        if fullmatch(self.pattern, text_message):
            return True
        return False


class CommandFilter(AbstractFilter):
    def __init__(self, command: str, prefixes: str = "/"):
        if isinstance(command, str):
            self.command = command
            self.prefixes = prefixes
        elif isinstance(command, tuple):
            if len(command) == 2:
                self.command, self.prefixes = command

    def check_event(self, event: dict) -> bool:
        text_message = TextMessageFilter.get_text_message(event)

        for prefix in self.prefixes:
            if text_message.split()[0] != f"{prefix}{self.command}":
                continue
            return True
        return False


filters: Dict[str, Type[AbstractFilter]] = {
    "from_chat": ChatIDFilter,
    "from_sender": SenderFilter,
    "type_message": TypeMessageFilter,
    "text_message": TextMessageFilter,
    "regexp": RegExpFilter,
    "command": CommandFilter
}

__all__ = [
    "AbstractFilter",
    "ChatIDFilter",
    "SenderFilter",
    "TypeMessageFilter",
    "TextMessageFilter",
    "RegExpFilter",
    "CommandFilter",
    "filters"
]
