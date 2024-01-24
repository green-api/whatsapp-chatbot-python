import json
import logging
from typing import Dict, TYPE_CHECKING

from .observer import AbstractObserver, ButtonObserver, PollObserver, Observer

if TYPE_CHECKING:
    from ..bot import GreenAPI


class Router:
    def __init__(self, api: "GreenAPI", logger: logging.Logger):
        self.api = api
        self.logger = logger

        self.message: AbstractObserver = Observer(self)
        self.outgoing_message: AbstractObserver = Observer(self)
        self.outgoing_api_message: AbstractObserver = Observer(self)
        self.outgoing_message_status: AbstractObserver = Observer(self)

        self.buttons: AbstractObserver = ButtonObserver(self)

        self.polls: AbstractObserver = PollObserver(self)

        self.observers: Dict[str, AbstractObserver] = {
            "incomingMessageReceived": self.message,
            "outgoingMessageReceived": self.outgoing_message,
            "outgoingAPIMessageReceived": self.outgoing_api_message,
            "outgoingMessageStatus": self.outgoing_message_status
        }

    def route_event(self, event: dict) -> None:
        type_webhook = event["typeWebhook"]

        observer = self.observers.get(type_webhook)
        if observer:
            data = json.dumps(event, ensure_ascii=False, indent=4)

            self.logger.log(
                logging.DEBUG, (
                    f"Routing {type_webhook} event with data: {data}"
                )
            )

            observer.update_event(event)

__all__ = ["Router"]
