"""Single-call contracts and state machine (Python 3.11+).

Import the executor from ``.service`` after installing the ``voip`` extra.
The base chatbot package does not import this optional package.
"""

# Check the runtime before importing modules that use StrEnum.
# ruff: noqa: E402
import sys

if sys.version_info < (3, 11):
    raise ImportError("whatsapp_chatbot_python.calls requires Python 3.11 or newer")

from .contracts import CallExecutor, IncomingCallExecutor, TransitionCallback
from .models import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    WAITING_STATES,
    CallDirection,
    CallEndReason,
    CallEvent,
    CallExecutionResult,
    CallSession,
    CallState,
    StateTransition,
    TransitionOutcome,
)
from .state_machine import CallStateMachine

__all__ = [
    "CallExecutor", "IncomingCallExecutor", "TransitionCallback", "CallDirection", "CallEndReason", "CallEvent",
    "CallExecutionResult", "CallSession", "CallState", "StateTransition", "TransitionOutcome", "CallStateMachine",
    "ACTIVE_STATES", "TERMINAL_STATES", "WAITING_STATES",
]
