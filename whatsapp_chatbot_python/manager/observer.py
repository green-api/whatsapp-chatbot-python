from abc import ABC, abstractmethod
from typing import Any, Callable, List, TYPE_CHECKING

from .handler import HandlerType, AbstractHandler, Handler

if TYPE_CHECKING:
    from .router import Router


class AbstractObserver(ABC):
    event: dict
    handlers: List[AbstractHandler]

    def update_event(self, event: dict) -> None:
        self.event = event

        self.propagate_event()

    @abstractmethod
    def add_handler(self, handler: HandlerType, **filters: Any) -> None:
        pass

    @abstractmethod
    def propagate_event(self) -> None:
        pass


class Observer(AbstractObserver):
    def __init__(self, router: "Router"):
        self.router = router

        self.event = {}
        self.handlers = []

    def add_handler(self, handler: HandlerType, **filters: Any) -> None:
        self.handlers.append(Handler(handler, **filters))

    def propagate_event(self) -> None:
        for handler in self.handlers:
            response = handler.execute_handler(self)
            if response:
                break

    def __call__(self, **filters: Any) -> Callable[[HandlerType], HandlerType]:
        def wrapper(handler: HandlerType) -> HandlerType:
            self.add_handler(handler, **filters)

            return handler

        return wrapper


class ButtonObserver(Observer):
    def add_handler(self, handler: HandlerType, **filters: Any) -> None:
        message_types = [
            "buttonsResponseMessage",
            "templateButtonsReplyMessage",
            "listResponseMessage"
        ]

        message_type = filters.get("type_message")
        if message_type not in message_types:
            filters["type_message"] = message_types

        self.router.message.add_handler(handler, **filters)


__all__ = ["AbstractObserver", "Observer", "ButtonObserver"]
