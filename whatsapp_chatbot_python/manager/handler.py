from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING, Union

from whatsapp_api_client_python.response import Response

from ..filters import filters as event_filters

if TYPE_CHECKING:
    from .observer import Observer
    from .state import AbstractStateManager
    from ..bot import GreenAPI


class Notification:
    event: dict

    api: "GreenAPI"
    state_manager: "AbstractStateManager"

    def __init__(
            self,
            event: dict,
            api: "GreenAPI",
            state_manager: "AbstractStateManager"
    ):
        self.event = event

        self.api = api
        self.state_manager = state_manager

    @property
    def chat(self) -> Optional[str]:
        return self.get_chat()

    @property
    def sender(self) -> Optional[str]:
        return self.get_sender()

    @property
    def message_text(self) -> Optional[str]:
        return self.get_message_text()

    def get_chat(self) -> Optional[str]:
        type_webhook = self.event["typeWebhook"]
        if type_webhook != "outgoingMessageStatus":
            return self.event["senderData"]["chatId"]

    def get_sender(self) -> Optional[str]:
        type_webhook = self.event["typeWebhook"]
        if type_webhook != "outgoingMessageStatus":
            return self.event["senderData"]["sender"]

    def get_message_text(self) -> Optional[str]:
        message_data = self.event["messageData"]

        type_message = message_data["typeMessage"]
        if type_message == "textMessage":
            return message_data["textMessageData"]["textMessage"]
        elif type_message == "extendedTextMessage" or type_message == "quotedMessage":
            return message_data["extendedTextMessageData"]["text"]
        else:
            return "not supported"

    def answer(
            self,
            message: str,
            quoted_message_id: Optional[str] = None,
            archive_chat: Optional[bool] = None,
            link_preview: Optional[bool] = None
    ) -> Optional[Response]:
        chat = self.get_chat()
        if chat:
            return self.api.sending.sendMessage(
                chat, message, quoted_message_id, archive_chat, link_preview
            )

    def answer_buttons(
            self,
            message: str,
            buttons: List[Dict[str, Union[int, str]]],
            footer: Optional[str] = None,
            quoted_message_id: Optional[str] = None,
            archive_chat: Optional[bool] = None
    ) -> Optional[Response]:
        chat = self.get_chat()
        if chat:
            return self.api.sending.sendButtons(
                chat, message, buttons, footer, quoted_message_id, archive_chat
            )

    def answer_with_file(
            self,
            file: str,
            file_name: Optional[str] = None,
            caption: Optional[str] = None,
            quoted_message_id: Optional[str] = None
    ) -> Optional[Response]:
        chat = self.get_chat()
        if chat:
            return self.api.sending.sendFileByUpload(
                chat, file, file_name, caption, quoted_message_id
            )


HandlerType = Callable[[Notification], Any]


class AbstractHandler(ABC):
    handler: HandlerType
    filters: Dict[str, Any]

    @abstractmethod
    def check_event(self, notification: Notification) -> bool:
        pass

    @abstractmethod
    def execute_handler(self, observer: "Observer") -> bool:
        pass


class Handler(AbstractHandler):
    def __init__(self, handler: HandlerType, **filters: Any):
        self.handler = handler
        self.filters = filters

    def check_event(self, notification: Notification) -> bool:
        for filter_name in self.filters.keys():
            filter_ = event_filters.get(filter_name)
            if filter_:
                filter_data = self.filters[filter_name]
                response = filter_(filter_data).check_event(notification)
                if not response:
                    return False

        return True

    def execute_handler(self, observer: "Observer") -> bool:
        notification = Notification(
            observer.event, observer.router.api, observer.state_manager
        )

        response = self.check_event(notification)
        if response:
            self.handler(notification)

            return True
        return False


__all__ = ["AbstractHandler", "Handler", "HandlerType", "Notification"]
