import logging
import time
from typing import NoReturn, Optional

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
        delete_notifications_at_startup: bool = True,
        webhook_mode: bool = False,
        webhook_host: str = "0.0.0.0",
        webhook_port: int = 8080,
        webhook_auth_header: Optional[str] = None,
    ):
        """
        Init args:

            - `id_instance: str` - (required) Instance ID

            - `api_token_instance: str` - (required) Api Token

            - `debug_mode: bool` - (default: `False`) Debug mode (extended logging)
            for API wrapper

            - `raise_errors: bool` - (default: `False`) Raise errors when it handled
            (for long polling mode only), otherwise - skip it

            - `host: str | None` - (default: `None`) API host url
            ("https://api.green-api.com" if `None` provided)

            - `media: str | None` - (default: `None`) API host url
            ("https://media.green-api.com" if `None` provided)

            -  `bot_debug_mode: bool` - (default: `False`)
            Debug mode (extended logging) for bot

            - `settings: dict | None` - (default: `None`)
            dict for updating instance settings if provided

            - `delete_notifications_at_startup: bool` - (default: `True`) Remove all
            notifications from notification queue on bot startup. If `bot_debug_mode`
            is `True` - this arg will be setted as `True` when bot object init

            - `webhook_mode: bool` - (default: `False`) Launch bot in webhook-server
            mode. All notifcations will recieving via webhooks.
            Otherwise - bot will running in long polling mode.

            - `webhook_host: str` - (default: `"0.0.0.0"`) Host for webhook server.

            - `webhook_port: int` - (default: `8080`) Port for webhook server.

            - `webhook_auth_header: str | None` - (default: `None`) Check that the
            authorization header matches the specified value.
            Will be ignored if set to `None`

        """

        self.id_instance = id_instance
        self.api_token_instance = api_token_instance
        self.api_debug_mode = debug_mode
        self.api_host = host
        self.api_media = media
        self.api_raise_errors = raise_errors
        self.instance_settings = settings
        self.bot_debug_mode = bot_debug_mode
        self.delete_notifications_at_startup = delete_notifications_at_startup
        self.webhook_mode = webhook_mode
        self.webhook_host = webhook_host
        self.webhook_port = webhook_port
        self.__webhook_auth_header = webhook_auth_header

        self.__init_logger()

        self.logger.info("Bot initialization...")

        self.__init_api_wrapper()
        self.__init_instance_settings()
        self.__delete_notifications_at_startup()
        self.__init_router()

        if self.webhook_mode:

            self.__init_webhook_handler()
            self.__init_webhook_server()

        self.logger.info("Bot initialization success")

    def __init_logger(self) -> None:

        logger = logging.getLogger("whatsapp-chatbot-python")
        if self.bot_debug_mode:
            logger.setLevel(logging.DEBUG)
        else:
            logger.setLevel(logging.INFO)

        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(handler)
        self.logger = logger
        self.logger.debug("Logger inited")

    def __init_api_wrapper(self) -> None:

        self.logger.debug("GreenAPI wrapper initialization...")

        self.api = GreenAPI(
            self.id_instance,
            self.api_token_instance,
            debug_mode=self.api_debug_mode,
            raise_errors=self.api_raise_errors,
            host=self.api_host or "https://api.green-api.com",
            media=self.api_media or "https://media.green-api.com",
        )

        self.logger.debug("GreenAPI wrapper OK")

    def __init_instance_settings(self) -> None:

        if self.instance_settings:
            self.logger.debug("Updating instance settings")
            self.api.account.setSettings(self.instance_settings)

        else:
            self.logger.debug("Getting instance settings...")

            account_settings_response = self.api.account.getSettings()
            account_settings_data = account_settings_response.data

            incoming_webhook = account_settings_data["incomingWebhook"]
            outgoing_message_webhook = account_settings_data["outgoingMessageWebhook"]
            outgoing_api_message_webhook = account_settings_data[
                "outgoingAPIMessageWebhook"
            ]

            self.logger.debug(
                f"Instance [{self.id_instance}] settings status (Incoming webhook, "
                "Outgoing message webhook, Outgoing API message webhook): "
                f"({incoming_webhook}, {outgoing_message_webhook}, "
                f"{outgoing_api_message_webhook})"
            )

            if all(
                webhook == "no"
                for webhook in [
                    incoming_webhook,
                    outgoing_message_webhook,
                    outgoing_api_message_webhook,
                ]
            ):
                self.logger.info(
                    "All message notifications are disabled. "
                    "Enabling incoming and outgoing notifications. "
                    "Settings will be applied within 5 minutes."
                )

                self.api.account.setSettings(
                    {
                        "incomingWebhook": "yes",
                        "outgoingMessageWebhook": "yes",
                        "outgoingAPIMessageWebhook": "yes",
                    }
                )

        self.logger.debug("Instance settings OK")

    def __delete_notifications_at_startup(self) -> None:

        if self.bot_debug_mode:
            self.delete_notifications_at_startup = True
            self.logger.debug("Enabled delete_notifications_at_startup")

        if self.delete_notifications_at_startup:

            self.api.session.headers["Connection"] = "keep-alive"
            self.logger.debug("Started deleting old incoming notifications")

            while True:
                response = self.api.receiving.receiveNotification()

                if not response.data:
                    break

                self.api.receiving.deleteNotification(response.data["receiptId"])

            self.api.session.headers["Connection"] = "close"
            self.logger.debug("Stopped deleting old incoming notifications")
            self.logger.debug("Old notifications was deleted successfull")

        else:
            self.logger.debug("Deleting notifications at startup is disbaled, skip")

    def __init_router(self) -> None:

        self.logger.debug("Router initialization...")
        self.router = Router(self.api, self.logger)
        self.logger.debug("Router OK")

    def __init_webhook_handler(self) -> None:

        self.logger.debug("Webhook handler initialization...")

        def webhook_handler(webhook_type: str, webhook_data: str):
            self.router.route_event(webhook_data)

        self._webhook_handler = webhook_handler
        self.logger.debug("Webhook handler OK")

    def __init_webhook_server(self) -> None:

        from whatsapp_api_webhook_server_python_v2 import GreenAPIWebhookServer

        self.logger.debug("GreenAPI webhook server initialization...")
        self._webhook_server = GreenAPIWebhookServer(
            event_handler=self._webhook_handler,
            host=self.webhook_host,
            port=self.webhook_port,
            webhook_auth_header=self.__webhook_auth_header,
            return_keys_by_alias=True,
        )
        self.logger.debug("GreenAPI webhook server OK")

    def run_forever(self) -> Optional[NoReturn]:

        if self.webhook_mode:
            self.logger.info(
                "Webhook mode: starting webhook server on "
                f"{self.webhook_host}:{self.webhook_port}"
            )
            self._webhook_server.start()

        else:
            self.logger.info(
                "Long polling mode: starting to poll incoming notifications"
            )

            self.api.session.headers["Connection"] = "keep-alive"
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
                except Exception as error:
                    if self.api_raise_errors:
                        raise GreenAPIBotError(error)
                    self.logger.error(error)

                    time.sleep(5.0)

                    continue

            self.api.session.headers["Connection"] = "close"
            self.logger.info("Stopped receiving incoming notifications")
            self.logger.info(
                "Long polling mode: stopping to poll incoming notifications"
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
    "GreenAPIBotError",
]
