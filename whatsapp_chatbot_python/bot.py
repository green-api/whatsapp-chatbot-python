from typing import NoReturn, Optional

from whatsapp_api_client_python.API import GreenApi, Response

from .manager.router import Router


class Bot:
    def __init__(
            self,
            id_instance: str,
            api_token_instance: str,
            settings: Optional[dict] = None,
            delete_notifications_at_startup: bool = True
    ):
        self.api = GreenAPI(id_instance, api_token_instance)

        if not settings:
            self._update_settings()
        else:
            self.__validate_response(self.api.account.setSettings(settings))

        if delete_notifications_at_startup:
            self._delete_notifications_at_startup()

        self.router = Router(self.api)

    def run_forever(self) -> Optional[NoReturn]:
        while True:
            try:
                response = self.api.receiving.receiveNotification()

                self.__validate_response(response)

                if not response.data:
                    continue
                response = response.data

                self.router.route_event(response["body"])

                self.__validate_response(
                    self.api.receiving.deleteNotification(
                        response["receiptId"]
                    )
                )
            except KeyboardInterrupt:
                break

    def _update_settings(self) -> Optional[NoReturn]:
        settings = self.api.account.getSettings()

        self.__validate_response(settings)

        response = settings.data

        incoming_webhook = response["incomingWebhook"]
        outgoing_message_webhook = response["outgoingMessageWebhook"]
        outgoing_api_message_webhook = response["outgoingAPIMessageWebhook"]
        if (
                incoming_webhook == "no"
                and outgoing_message_webhook == "no"
                and outgoing_api_message_webhook == "no"
        ):
            self.__validate_response(
                self.api.account.setSettings({
                    "incomingWebhook": "yes",
                    "outgoingMessageWebhook": "yes",
                    "outgoingAPIMessageWebhook": "yes"
                })
            )

    def _delete_notifications_at_startup(self) -> Optional[NoReturn]:
        while True:
            response = self.api.receiving.receiveNotification()

            self.__validate_response(response)

            if not response.data:
                break

            self.__validate_response(
                self.api.receiving.deleteNotification(
                    response.data["receiptId"]
                )
            )

    @staticmethod
    def __validate_response(response: Response) -> Optional[NoReturn]:
        if response.code != 200:
            if response.error:
                raise GreenAPIError(response.error)
            raise GreenAPIError(
                f"GreenAPI error occurred with status code {response.code}"
            )


class GreenAPI(GreenApi):
    pass


class GreenAPIBot(Bot):
    pass


class GreenAPIError(Exception):
    pass


__all__ = ["Bot", "GreenAPI", "GreenAPIBot", "GreenAPIError"]
