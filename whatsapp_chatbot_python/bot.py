import logging
from typing import NoReturn, Optional

from whatsapp_api_client_python.API import GreenAPI, GreenAPIError

from .manager.router import Router


class Bot:
    def __init__(
            self,
            id_instance: str,
            api_token_instance: str,
            debug_mode: bool = False,
            raise_errors: bool = True,
            settings: Optional[dict] = None,
            delete_notifications_at_startup: bool = True
    ):
        self.id_instance = id_instance
        self.api_token_instance = api_token_instance
        self.debug_mode = debug_mode
        self.raise_errors = raise_errors

        self.api = GreenAPI(
            id_instance,
            api_token_instance,
            debug_mode=debug_mode,
            raise_errors=raise_errors
        )

        self.__prepare_logger()
        self.logger = logging.getLogger("whatsapp-chatbot-python")

        if not settings:
            self._update_settings()
        else:
            self.api.account.setSettings(settings)

        if delete_notifications_at_startup:
            self._delete_notifications_at_startup()

        self.router = Router(self.api)

    def run_forever(self) -> Optional[NoReturn]:
        while True:
            try:
                response = self.api.receiving.receiveNotification()

                if not response.data:
                    continue
                response = response.data

                self.router.route_event(response["body"])

                self.api.receiving.deleteNotification(response["receiptId"])
            except KeyboardInterrupt:
                break

    def _update_settings(self) -> Optional[NoReturn]:
        settings = self.api.account.getSettings()

        response = settings.data

        incoming_webhook = response["incomingWebhook"]
        outgoing_message_webhook = response["outgoingMessageWebhook"]
        outgoing_api_message_webhook = response["outgoingAPIMessageWebhook"]
        if (
                incoming_webhook == "no"
                and outgoing_message_webhook == "no"
                and outgoing_api_message_webhook == "no"
        ):
            self.api.account.setSettings({
                "incomingWebhook": "yes",
                "outgoingMessageWebhook": "yes",
                "outgoingAPIMessageWebhook": "yes"
            })

    def _delete_notifications_at_startup(self) -> Optional[NoReturn]:
        while True:
            response = self.api.receiving.receiveNotification()

            if not response.data:
                break

            self.api.receiving.deleteNotification(response.data["receiptId"])

    def __prepare_logger(self) -> None:
        if self.debug_mode:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)


class GreenAPIBot(Bot):
    pass


__all__ = ["Bot", "GreenAPI", "GreenAPIBot", "GreenAPIError"]
