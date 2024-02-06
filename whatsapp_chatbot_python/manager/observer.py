import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, List, TYPE_CHECKING

from .handler import AbstractHandler, Handler, HandlerType
from .state import AbstractStateManager, StateManager

if TYPE_CHECKING:
    from .router import Router


class AbstractObserver(ABC):
    event: dict
    handlers: List[AbstractHandler]
    state_manager: AbstractStateManager

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
        self.state_manager = StateManager()

    def add_handler(self, handler: HandlerType, **filters: Any) -> None:
        self.handlers.append(Handler(handler, **filters))

    def propagate_event(self) -> None:
        if not self.handlers:
            self.router.logger.log(
                logging.DEBUG, (
                    "Skipping event because there are no subscribers."
                )
            )

            return None

        for handler in self.handlers:
            response = handler.execute_handler(self)
            if response:
                self.router.logger.log(
                    logging.DEBUG, "Event has been successfully handled."
                )

                return None

        self.router.logger.log(
            logging.DEBUG, (
                "Event has not been handled "
                "because all handlers do not match filters."
            )
        )

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


class PollObserver(Observer):
    def add_handler(self, handler: HandlerType, **filters: Any) -> None:
        filters["type_message"] = "pollMessage"

        self.router.message.add_handler(handler, **filters)


class PollUpdateObserver(Observer):
    def add_handler(self, handler: HandlerType, **filters: Any) -> None:
        filters["type_message"] = "pollUpdateMessage"

        self.router.message.add_handler(handler, **filters)

    def add_handler_with_stanza(
            self, handler: HandlerType, stanza: str, **filters: Any
    ) -> None:
        filters.update({
            "stanza": stanza,
            "type_message": "pollUpdateMessage"
        })

        self.router.message.add_handler(handler, **filters)


__all__ = [
    "AbstractObserver",
    "ButtonObserver",
    "Observer",
    "PollObserver",
    "PollUpdateObserver"
]
