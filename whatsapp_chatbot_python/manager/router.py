from typing import Dict, TYPE_CHECKING

from .observer import AbstractObserver, Observer, ButtonObserver

if TYPE_CHECKING:
    from ..bot import GreenAPI


class Router:
    def __init__(self, api: "GreenAPI"):
        self.api = api

        self.message: AbstractObserver = Observer(self)
        self.outgoing_message: AbstractObserver = Observer(self)
        self.outgoing_api_message: AbstractObserver = Observer(self)
        self.outgoing_message_status: AbstractObserver = Observer(self)

        self.buttons: AbstractObserver = ButtonObserver(self)

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
            observer.update_event(event)


__all__ = ["Router"]
