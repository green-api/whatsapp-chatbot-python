import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Generic, List, Optional, TYPE_CHECKING, Type, TypeVar

from .handler import AbstractHandler, Handler, Notification, NotificationT
from .incoming_call import IncomingCallHandler, IncomingCallNotification
from .state import AbstractStateManager, StateManager

if TYPE_CHECKING:
    from .router import Router

ReturnT = TypeVar("ReturnT")


class AbstractObserver(ABC, Generic[NotificationT]):
    router: "Router"
    event: dict
    handlers: List[AbstractHandler[NotificationT]]
    state_manager: AbstractStateManager

    @abstractmethod
    def make_notification(self) -> NotificationT:
        pass

    def update_event(self, event: dict) -> None:
        self.event = event

        self.propagate_event()

    @abstractmethod
    def add_handler(self, handler: Callable[[NotificationT], Any], **filters: Any) -> None:
        pass

    @abstractmethod
    def propagate_event(self) -> None:
        pass


class _TypedObserver(AbstractObserver[NotificationT], Generic[NotificationT]):
    def __init__(
            self, router: "Router", notification_type: Type[NotificationT],
            state_manager: Optional[AbstractStateManager] = None,
    ):
        self.router = router
        self.event = {}
        self.handlers: List[AbstractHandler[NotificationT]] = []
        self.state_manager = state_manager if state_manager is not None else StateManager()
        self._notification_type = notification_type

    def make_notification(self) -> NotificationT:
        return self._notification_type(self.event, self.router.api, self.state_manager)

    def add_handler(self, handler: Callable[[NotificationT], Any], **filters: Any) -> None:
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

    def __call__(
            self, **filters: Any,
    ) -> Callable[[Callable[[NotificationT], ReturnT]], Callable[[NotificationT], ReturnT]]:
        def wrapper(
                handler: Callable[[NotificationT], ReturnT],
        ) -> Callable[[NotificationT], ReturnT]:
            self.add_handler(handler, **filters)
            return handler

        return wrapper


class Observer(_TypedObserver[Notification]):
    def __init__(
            self, router: "Router", state_manager: Optional[AbstractStateManager] = None,
    ):
        super().__init__(router, Notification, state_manager)


class ButtonObserver(Observer):
    def add_handler(self, handler: Callable[[Notification], Any], **filters: Any) -> None:
        message_types = [
            "buttonsResponseMessage",
            "templateButtonsReplyMessage",
            "listResponseMessage"
        ]

        message_type = filters.get("type_message")
        if message_type not in message_types:
            filters["type_message"] = message_types

        self.router.message.add_handler(handler, **filters)


class IncomingCallObserver(_TypedObserver[IncomingCallNotification]):
    """Incoming-call notifications share message state for the same sender."""

    _allowed_filters = frozenset({"status", "from_sender", "from_chat", "state"})

    def __init__(
            self, router: "Router", state_manager: Optional[AbstractStateManager] = None,
    ):
        super().__init__(
            router, IncomingCallNotification, state_manager,
        )

    def add_handler(
            self, handler: Callable[[IncomingCallNotification], Any], **filters: Any,
    ) -> None:
        unsupported = set(filters) - self._allowed_filters
        if unsupported:
            raise ValueError(
                "Unsupported incoming-call filters: " + ", ".join(sorted(unsupported))
            )

        if "status" in filters:
            status = filters["status"]

            if isinstance(status, str):
                statuses = [status]
            elif isinstance(status, (list, tuple)):
                statuses = list(status)
            else:
                statuses = []

            if not statuses or not all(isinstance(value, str) and value for value in statuses):
                raise ValueError(
                    "Incoming-call status must be a string or a nonempty list of strings"
                )

        self.handlers.append(IncomingCallHandler(handler, **filters))


class PollObserver(Observer):
    def add_handler(self, handler: Callable[[Notification], Any], **filters: Any) -> None:
        filters["type_message"] = "pollMessage"

        self.router.message.add_handler(handler, **filters)


class PollUpdateObserver(Observer):
    def add_handler(self, handler: Callable[[Notification], Any], **filters: Any) -> None:
        filters["type_message"] = "pollUpdateMessage"

        self.router.message.add_handler(handler, **filters)

    def add_handler_with_stanza(
            self, handler: Callable[[Notification], Any], stanza: str, **filters: Any
    ) -> None:
        filters.update({
            "stanza": stanza,
            "type_message": "pollUpdateMessage"
        })

        self.router.message.add_handler(handler, **filters)


__all__ = [
    "AbstractObserver",
    "ButtonObserver",
    "IncomingCallObserver",
    "Observer",
    "PollObserver",
    "PollUpdateObserver"
]
