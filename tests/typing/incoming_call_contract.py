"""Static contract for incoming-call handlers; checked by mypy in CI."""

from typing import Callable, Optional

from typing_extensions import assert_type

from whatsapp_chatbot_python import GreenAPIBot, IncomingCallNotification, Notification


def register_handlers(bot: GreenAPIBot) -> None:
    @bot.router.incoming_call(status="offer")
    def on_offer(notification: IncomingCallNotification) -> None:
        assert_type(notification.call.sender, Optional[str])

    assert_type(on_offer, Callable[[IncomingCallNotification], None])
    bot.router.incoming_call.add_handler(on_offer)

    def on_general(notification: Notification) -> None:
        pass

    bot.router.incoming_call.add_handler(on_general)

    @bot.router.incoming_call()  # type: ignore[arg-type]
    def wrong(value: int) -> None:
        pass

    def wrong_direct(value: int) -> None:
        pass

    bot.router.incoming_call.add_handler(wrong_direct)  # type: ignore[arg-type]
