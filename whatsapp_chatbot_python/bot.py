import json
import logging
import time
from typing import NoReturn, Optional
from requests import Response

from whatsapp_api_client_python.API import GreenAPI, GreenAPIError

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
            try:
                response = self.api.account.setSettings(settings)
                self.__handle_response(response)
            except Exception as error:
                if self.raise_errors:
                    raise GreenAPIBotError(error)
                self.logger.log(logging.ERROR, error)
            
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

        self.logger.log(
            logging.INFO, "Started receiving incoming notifications."
        )

        while True:
            try:
                response = self.api.receiving.receiveNotification()
                self.__handle_response(response)
                if not response.data:
                    continue
                response = response.data

                self.router.route_event(response["body"])

                response = self.api.receiving.deleteNotification(response["receiptId"])
                self.__handle_response(response)
            except KeyboardInterrupt:
                break
            except Exception as error:
                if self.raise_errors:
                    raise GreenAPIBotError(error)
                self.logger.log(logging.ERROR, error)

                time.sleep(5.0)

                continue

        self.api.session.headers["Connection"] = "close"

        self.logger.log(
            logging.INFO, "Stopped receiving incoming notifications."
        )

    def _update_settings(self) -> Optional[NoReturn]:
        self.logger.log(logging.DEBUG, "Checking current instance settings.")
        try:
            response = self.api.account.getSettings()
            self.__handle_response(response)

            settings = response.data

            incoming_webhook = settings["incomingWebhook"]
            outgoing_message_webhook = settings["outgoingMessageWebhook"]
            outgoing_api_message_webhook = settings["outgoingAPIMessageWebhook"]
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

                response = self.api.account.setSettings({
                    "incomingWebhook": "yes",
                    "outgoingMessageWebhook": "yes",
                    "outgoingAPIMessageWebhook": "yes"
                })
                self.__handle_response(response)
        except Exception as error:
                if self.raise_errors:
                    raise GreenAPIBotError(error)
                self.logger.log(logging.ERROR, error)

    def _delete_notifications_at_startup(self) -> Optional[NoReturn]:
        self.api.session.headers["Connection"] = "keep-alive"

        self.logger.log(
            logging.DEBUG, "Started deleting old incoming notifications."
        )

        while True:
            response = self.api.receiving.receiveNotification()
            self.__handle_response(response)
            if not response.data:
                break

            response = self.api.receiving.deleteNotification(response.data["receiptId"])
            self.__handle_response(response)

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

    def __handle_response(self, response: Response) -> Optional[NoReturn]:
        status_code = response.status_code
        if status_code != 200 or self.debug_mode:
            data = json.dumps(
                json.loads(response.text), ensure_ascii=False, indent=4
            )

            if status_code != 200:
                error_message = (
                    f"Request was failed with status code: {status_code}."
                    f" Data: {data}"
                )

                if self.raise_errors:
                    raise GreenAPIError(error_message)
                self.logger.log(logging.ERROR, error_message)

                return None

            self.logger.log(
                logging.DEBUG, f"Request was successful with data: {data}"
            )

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
