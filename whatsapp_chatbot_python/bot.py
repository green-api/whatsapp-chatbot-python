import logging
import time
from typing import NoReturn, Optional

from whatsapp_api_client_python.API import GreenAPI, GreenAPIError
import whatsapp_api_webhook_server_python.webhooksHandler as webhookServer

from .manager.router import Router


class Bot:
    def __init__(
            self,
            id_instance: str,
            api_token_instance: str,
            debug_mode: bool = False,
            raise_errors: bool = False,
            host: Optional[str] = None,
            media: Optional[str] = None,
            bot_debug_mode: bool = False,
            settings: Optional[dict] = None,
            delete_notifications_at_startup: bool = True,
            webhook_data: Optional[dict] = None
    ):
        self.id_instance = id_instance
        self.api_token_instance = api_token_instance
        self.debug_mode = debug_mode
        self.raise_errors = raise_errors
        self.webhook_data = webhook_data

        self.api = GreenAPI(
            id_instance,
            api_token_instance,
            debug_mode=debug_mode,
            raise_errors=raise_errors,
            host=host or "https://api.green-api.com",
            media=media or "https://media.green-api.com"
        )

        self.bot_debug_mode = bot_debug_mode

        self.logger = logging.getLogger("whatsapp-chatbot-python")
        self.__prepare_logger()

        if not settings:
            self._update_settings()
        else:
            self.logger.log(logging.DEBUG, "Updating instance settings.")

            self.api.account.setSettings(settings)

        if bot_debug_mode:
            if not delete_notifications_at_startup:
                delete_notifications_at_startup = True

                self.logger.log(
                    logging.DEBUG, "Enabled delete_notifications_at_startup."
                )

        if delete_notifications_at_startup:
            self._delete_notifications_at_startup()

        self.router = Router(self.api, self.logger)

    def run_forever(self) -> Optional[NoReturn]:
        self.api.session.headers["Connection"] = "keep-alive"
        self.logger.log(logging.INFO, "Started service.")

        def onEvent(webhookHandler: webhookServer.WebhooksHandler, typeWebhook: str, body):
            if typeWebhook in {'incomingMessageReceived', 'outgoingMessageReceived', 'outgoingAPIMessageReceived'}:
                self.router.route_event(body)

        if self.webhook_data:
            webhook_url = self.webhook_data.get("webhookUrl", "").strip()
            if webhook_url:
                self.logger.debug(f"Setting webhookUrl to {webhook_url}. It may take up to 5 minutes.")
                self.api.account.setSettings({"webhookUrl": webhook_url})
            port = self.webhook_data["port"]
            host = self.webhook_data["host"]
            webhookServer.startServer(host, port, onEvent)

            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                self.logger.log(logging.INFO, "Shutting down due to KeyboardInterrupt.")
            finally:
                self.logger.log(logging.INFO, "Stopped receiving incoming notifications.")
                return
        else:
            try:
                while True:
                    response = self.api.receiving.receiveNotification()
                    if not response.data:
                        continue
                    response = response.data

                    self.router.route_event(response["body"])
                    self.api.receiving.deleteNotification(response["receiptId"])

            except KeyboardInterrupt:
                self.logger.log(logging.INFO, "Stopped due to KeyboardInterrupt.")
            except Exception as error:
                if self.raise_errors:
                    raise GreenAPIBotError(error)
                self.logger.log(logging.ERROR, str(error))
                time.sleep(5.0)
            finally:
                self.api.session.headers["Connection"] = "close"
                self.logger.log(logging.INFO, "Stopped receiving incoming notifications.")

    def _update_settings(self) -> Optional[NoReturn]:
        self.logger.log(logging.DEBUG, "Checking current instance settings.")

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
            self.logger.log(
                logging.INFO, (
                    "All message notifications are disabled. "
                    "Enabling incoming and outgoing notifications. "
                    "Settings will be applied within 5 minutes."
                )
            )

            self.api.account.setSettings({
                "incomingWebhook": "yes",
                "outgoingMessageWebhook": "yes",
                "outgoingAPIMessageWebhook": "yes"
            })

    def _delete_notifications_at_startup(self) -> Optional[NoReturn]:
        self.api.session.headers["Connection"] = "keep-alive"

        self.logger.log(
            logging.DEBUG, "Started deleting old incoming notifications."
        )

        while True:
            response = self.api.receiving.receiveNotification()

            if not response.data:
                break

            self.api.receiving.deleteNotification(response.data["receiptId"])

        self.api.session.headers["Connection"] = "close"

        self.logger.log(
            logging.DEBUG, "Stopped deleting old incoming notifications."
        )

        self.logger.log(logging.INFO, "Deleted old incoming notifications.")

    def __prepare_logger(self) -> None:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            (
                "%(asctime)s:%(name)s:"
                "%(levelname)s:%(message)s"
            ), datefmt="%Y-%m-%d %H:%M:%S"
        ))

        self.logger.addHandler(handler)

        if not self.bot_debug_mode:
            self.logger.setLevel(logging.INFO)
        else:
            self.logger.setLevel(logging.DEBUG)


class GreenAPIBot(Bot):
    pass


class GreenAPIBotError(Exception):
    pass


__all__ = [
    "Bot",
    "GreenAPI",
    "GreenAPIBot",
    "GreenAPIError",
    "GreenAPIBotError"
]
