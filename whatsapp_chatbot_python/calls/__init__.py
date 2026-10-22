"""Call contracts import without installing the optional VoIP dependencies."""
import sys

if sys.version_info < (3, 11):
    raise ImportError("whatsapp_chatbot_python.calls requires Python 3.11 or newer")

from .models import (
    AudioLoss,
    CallDirection,
    CallEndReason,
    CallEvent,
    CallExecutionResult,
    CallExecutor,
    CallOptions,
    CallSession,
    CallServiceUnavailable,
    CallState,
    IncomingCallExecutor,
    InputAudioOptions,
    InputOverflowPolicy,
    PlaybackOptions,
    PlaybackOverflowPolicy,
    ProviderError,
    RecordingOptions,
    StateTransition,
    TransitionCallback,
    ACTIVE_STATES,
    TERMINAL_STATES,
    WAITING_STATES,
)

from .service import WhatsAppCallService
from .state_machine import CallStateMachine

__all__ = [
    "ACTIVE_STATES", "TERMINAL_STATES", "WAITING_STATES", "AudioLoss", "CallDirection", "CallEndReason", "CallEvent",
    "CallExecutionResult", "CallExecutor", "CallOptions", "CallSession", "CallServiceUnavailable", "CallState",
    "CallStateMachine", "IncomingCallExecutor", "InputAudioOptions", "InputOverflowPolicy", "PlaybackOptions",
    "PlaybackOverflowPolicy", "ProviderError", "RecordingOptions", "StateTransition", "TransitionCallback",
    "WhatsAppCallService",
]
