from whatsapp_api_client_python.API import GreenApi

from .manager.router import Router


class Bot:
    def __init__(self, id_instance: str, api_token_instance: str):
        self.api = GreenAPI(id_instance, api_token_instance)

        self.router = Router(self.api)

    def run_forever(self):
        while True:
            try:
                response = self.api.receiving.receiveNotification()
                if response.error:
                    raise GreenAPIError(response.error)

                if not response.data:
                    continue
                response = response.data

                self.router.route_event(response["body"])

                self.api.receiving.deleteNotification(response["receiptId"])
            except KeyboardInterrupt:
                break


class GreenAPI(GreenApi):
    pass


class GreenAPIError(Exception):
    pass


__all__ = ["Bot", "GreenAPI", "GreenAPIError"]
