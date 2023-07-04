from abc import ABC, abstractmethod
from re import fullmatch
from typing import Dict, List, Optional, TYPE_CHECKING, Type, Union

if TYPE_CHECKING:
    from .manager.handler import Notification


class AbstractFilter(ABC):
    @abstractmethod
    def __init__(self, *args, **kwargs):
        pass

    @abstractmethod
    def check_event(self, notification: "Notification") -> bool:
        pass


class ChatIDFilter(AbstractFilter):
    def __init__(self, chat: Union[str, List[str]]):
        self.chat = chat
        if isinstance(chat, str):
            self.chat = [chat]

    def check_event(self, notification: "Notification") -> bool:
        chat = notification.chat

        if chat in self.chat:
            return True
        return False


class SenderFilter(AbstractFilter):
    def __init__(self, sender: Union[str, List[str]]):
        self.sender = sender
        if isinstance(sender, str):
            self.sender = [sender]

    def check_event(self, notification: "Notification") -> bool:
        sender = notification.sender

        if sender in self.sender:
            return True
        return False


class TypeMessageFilter(AbstractFilter):
    def __init__(self, type_message: Union[str, List[str]]):
        self.type_message = type_message
        if isinstance(type_message, str):
            self.type_message = [type_message]

    def check_event(self, notification: "Notification") -> bool:
        type_message = notification.event["messageData"]["typeMessage"]

        if type_message in self.type_message:
            return True
        return False


class TextMessageFilter(AbstractFilter):
    def __init__(self, text_message: Union[str, List[str]]):
        self.text_message = text_message
        if isinstance(text_message, str):
            self.text_message = [text_message]

    def check_event(self, notification: "Notification") -> bool:
        text_message = notification.message_text

        if text_message in self.text_message:
            return True
        return False


class RegExpFilter(AbstractFilter):
    def __init__(self, pattern: str):
        self.pattern = pattern

    def check_event(self, notification: "Notification") -> bool:
        text_message = notification.message_text

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

    def check_event(self, notification: "Notification") -> bool:
        text_message = notification.message_text

        for prefix in self.prefixes:
            if text_message.split()[0] != f"{prefix}{self.command}":
                continue
            return True
        return False


class StateFilter(AbstractFilter):
    def __init__(self, state_name: Optional[str]):
        self.state_name = state_name

    def check_event(self, notification: "Notification") -> bool:
        sender = notification.sender

        state = notification.state_manager.get_state(sender)
        if not state:
            if self.state_name is None:
                return True
            return False

        if state.name == self.state_name:
            return True
        return False


filters: Dict[str, Type[AbstractFilter]] = {
    "from_chat": ChatIDFilter,
    "from_sender": SenderFilter,
    "type_message": TypeMessageFilter,
    "text_message": TextMessageFilter,
    "regexp": RegExpFilter,
    "command": CommandFilter,
    "state": StateFilter
}

__all__ = [
    "AbstractFilter",
    "ChatIDFilter",
    "SenderFilter",
    "TypeMessageFilter",
    "TextMessageFilter",
    "RegExpFilter",
    "CommandFilter",
    "StateFilter",
    "filters"
]
