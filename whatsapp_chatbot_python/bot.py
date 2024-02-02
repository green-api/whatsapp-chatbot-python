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

        self.logger.log(
            logging.INFO, "Started receiving incoming notifications."
        )

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

        self.api.session.headers["Connection"] = "close"

        self.logger.log(
            logging.INFO, "Stopped receiving incoming notifications."
        )

    def _update_settings(self) -> Optional[NoReturn]:
        self.logger.log(logging.DEBUG, "Checking current instance settings.")

        settings = self.api.account.getSettings()

        current_settings = settings.data
        
        expected_settings = {
            "countryInstance": "",
	        "typeAccount": "",
	        "webhookUrl": "",
	        "webhookUrlToken": "",
	        "delaySendMessagesMilliseconds": 0,
	        "markIncomingMessagesReaded": "yes",
	        "markIncomingMessagesReadedOnReply": "no",
	        "sharedSession": "no",
	        "proxyInstance": "system proxy",
	        "outgoingWebhook": "yes",
	        "outgoingMessageWebhook": "yes",
	        "outgoingAPIMessageWebhook": "yes",
	        "incomingWebhook": "yes",
	        "deviceWebhook": "no",
	        "statusInstanceWebhook": "no",
	        "stateWebhook": "no",
	        "enableMessagesHistory": "no",
	        "keepOnlineStatus": "no",
	        "pollMessageWebhook": "yes",
	        "incomingBlockWebhook": "no",
	        "incomingCallWebhook": "no"
        }

        if not all(current_settings.get(key) == value for key, value in expected_settings.items()):
            update_settings_response = self.api.account.setSettings(expected_settings).data
            print("We will set settings to following expected_settings. It may take up to 5 minutes, please be patient.")
            print(expected_settings)
            print({f'Set settings result: {update_settings_response}'})
        else:
            print("Settings are already as expected.")

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


__all__ = ["Bot", "GreenAPI", "GreenAPIBot", "GreenAPIError"]
