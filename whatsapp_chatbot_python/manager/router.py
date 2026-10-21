import json
import logging
from typing import Any, Dict, TYPE_CHECKING

from .observer import (
    AbstractObserver,
    ButtonObserver,
    IncomingCallObserver,
    Observer,
    PollObserver,
    PollUpdateObserver
)

if TYPE_CHECKING:
    from ..bot import GreenAPI


class Router:
    def __init__(self, api: "GreenAPI", logger: logging.Logger):
        self.api = api
        self.logger = logger

        self.message: Observer = Observer(self)
        self.outgoing_message: Observer = Observer(self)
        self.outgoing_api_message: Observer = Observer(self)
        self.outgoing_message_status: Observer = Observer(self)

        self.incoming_call: IncomingCallObserver = IncomingCallObserver(
            self, state_manager=self.message.state_manager,
        )

        self.buttons: ButtonObserver = ButtonObserver(self)

        self.poll_message: PollObserver = PollObserver(self)
        self.poll_update_message: PollUpdateObserver = PollUpdateObserver(self)

        self.observers: Dict[str, AbstractObserver[Any]] = {
            "incomingMessageReceived": self.message,
            "outgoingMessageReceived": self.outgoing_message,
            "outgoingAPIMessageReceived": self.outgoing_api_message,
            "outgoingMessageStatus": self.outgoing_message_status,
            "incomingCall": self.incoming_call
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
