"""Allowed transitions for one call's outgoing audio playback."""

from enum import Enum


class PlaybackState(str, Enum):
    PLAYING = "playing"
    BACKLOG = "backlog"
    DRAINING = "draining"
    SUSPENDED = "suspended"


class PlaybackEvent(str, Enum):
    SOFT_LIMIT = "soft_limit"
    RECOVERED = "recovered"
    HARD_LIMIT = "hard_limit"
    DRAINED = "drained"
    STALLED = "stalled"
    RESUMED = "resumed"
    INTERRUPTED = "interrupted"
    TRACK_REPLACED = "track_replaced"


_TRANSITION_RULES: dict[tuple[PlaybackState, PlaybackEvent], PlaybackState] = {
    (PlaybackState.PLAYING, PlaybackEvent.SOFT_LIMIT): PlaybackState.BACKLOG,
    (PlaybackState.BACKLOG, PlaybackEvent.RECOVERED): PlaybackState.PLAYING,
    (PlaybackState.PLAYING, PlaybackEvent.HARD_LIMIT): PlaybackState.DRAINING,
    (PlaybackState.BACKLOG, PlaybackEvent.HARD_LIMIT): PlaybackState.DRAINING,
    (PlaybackState.DRAINING, PlaybackEvent.DRAINED): PlaybackState.PLAYING,
    (PlaybackState.SUSPENDED, PlaybackEvent.RESUMED): PlaybackState.PLAYING,
}

for state in (PlaybackState.PLAYING, PlaybackState.BACKLOG, PlaybackState.DRAINING):
    _TRANSITION_RULES[(state, PlaybackEvent.STALLED)] = PlaybackState.SUSPENDED
    _TRANSITION_RULES[(state, PlaybackEvent.INTERRUPTED)] = PlaybackState.PLAYING

for state in PlaybackState:
    _TRANSITION_RULES[(state, PlaybackEvent.TRACK_REPLACED)] = PlaybackState.PLAYING


class PlaybackStateMachine:
    """Ignore duplicate or late events, as the call state machine does."""

    @staticmethod
    def apply(state: PlaybackState, event: PlaybackEvent) -> PlaybackState:
        return _TRANSITION_RULES.get((state, event), state)
