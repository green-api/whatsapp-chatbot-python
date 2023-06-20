from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING, Union

from whatsapp_api_client_python.response import Response

from ..filters import filters as event_filters

if TYPE_CHECKING:
    from .observer import Observer
    from ..bot import GreenAPI


class Notification:
    event: dict

    api: "GreenAPI"

    def __init__(self, event: dict, api: "GreenAPI"):
        self.event = event

        self.api = api

    def get_chat(self) -> Optional[str]:
        type_webhook = self.event["typeWebhook"]
        if type_webhook != "outgoingMessageStatus":
            return self.event["senderData"]["chatId"]

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
    def check_event(self, event: dict) -> bool:
        pass

    @abstractmethod
    def execute_handler(self, observer: "Observer") -> bool:
        pass


class Handler(AbstractHandler):
    def __init__(self, handler: HandlerType, **filters: Any):
        self.handler = handler
        self.filters = filters

    def check_event(self, event: dict) -> bool:
        for filter_name in self.filters.keys():
            filter_ = event_filters.get(filter_name)
            if filter_:
                filter_data = self.filters[filter_name]
                response = filter_(filter_data).check_event(event)
                if not response:
                    return False

        return True

    def execute_handler(self, observer: "Observer") -> bool:
        response = self.check_event(observer.event)
        if response:
            self.handler(Notification(observer.event, observer.router.api))

            return True
        return False


__all__ = ["Notification", "HandlerType", "AbstractHandler", "Handler"]
