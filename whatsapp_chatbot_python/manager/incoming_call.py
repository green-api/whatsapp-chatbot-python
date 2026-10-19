"""Typed access to an incoming-call webhook without VoIP dependencies."""

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Dict, Optional, TYPE_CHECKING

from .handler import Handler, Notification

if TYPE_CHECKING:
    from .state import AbstractStateManager
    from ..bot import GreenAPI


MIN_SECONDS = 946_684_800  # 2000-01-01 UTC

MAX_SECONDS = 4_102_444_800  # 2100-01-01 UTC, exclusive


def _optional_text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _unix_seconds(value: Any) -> Optional[float]:
    """Accept seconds, milliseconds or microseconds for dates in 2000–2100."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    try:
        value = float(value)
    except OverflowError:
        return None

    if not isfinite(value) or value <= 0:
        return None

    # Keep the supported units and plausible range explicit. A larger value is
    #  not automatically another time unit (for example, nanoseconds).
    if MIN_SECONDS <= value < MAX_SECONDS:
        return value

    if 1_000 * MIN_SECONDS <= value < 1_000 * MAX_SECONDS:
        return value / 1_000

    if 1_000_000 * MIN_SECONDS <= value < 1_000_000 * MAX_SECONDS:
        return value / 1_000_000

    return None


@dataclass(frozen=True)
class IncomingCall:
    sender: Optional[str]
    call_id: Optional[str]
    status: Optional[str]
    timestamp: Optional[float]
    raw_event: Optional[Dict[str, Any]] = field(default=None, compare=False, repr=False)

    @property
    def chat_id(self) -> Optional[str]:
        """The caller's address is the chat address for a direct call."""
        return self.sender

    @classmethod
    def from_event(cls, event: Dict[str, Any]) -> "IncomingCall":
        return cls(
            sender=_optional_text(event.get("from")),
            call_id=_optional_text(event.get("idMessage")),
            status=_optional_text(event.get("status")),
            timestamp=_unix_seconds(event.get("timestamp")),
            raw_event=event,
        )


class IncomingCallNotification(Notification):
    """Call fields plus the original webhook and the user's state manager."""

    call: IncomingCall

    def __init__(
            self, event: Dict[str, Any], api: "GreenAPI",
            state_manager: "AbstractStateManager",
    ) -> None:
        super().__init__(event, api, state_manager)
        self.call = IncomingCall.from_event(event)

    @property
    def raw_event(self) -> dict:
        return self.event

    def get_chat(self) -> Optional[str]:
        return self.call.chat_id

    def get_sender(self) -> Optional[str]:
        return self.call.sender

    def get_id_message(self) -> Optional[str]:
        return self.call.call_id

    def get_message_text(self) -> None:
        return None

    def get_message_data(self) -> None:
        return None


class IncomingCallHandler(Handler[IncomingCallNotification]):
    """Keep status filtering local to incoming calls."""

    def check_event(self, notification: IncomingCallNotification) -> bool:
        accepted_statuses = self.filters.get("status")

        if accepted_statuses is not None:
            if isinstance(accepted_statuses, str):
                accepted_statuses = [accepted_statuses]

            if notification.call.status not in accepted_statuses:
                return False

        return super().check_event(notification)


__all__ = ["IncomingCall", "IncomingCallNotification", "IncomingCallHandler"]
