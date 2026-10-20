"""Playback policy and bounded PCM buffer have one owner."""
from collections import deque
from dataclasses import dataclass
from enum import Enum
from time import monotonic

from .models import PlaybackOptions, PCM, PCM16_SAMPLE_BYTES


class PlaybackState(Enum):
    PLAYING = "playing"
    BACKLOG = "backlog"
    DRAINING = "draining"
    SUSPENDED = "suspended"


class PlaybackEvent(Enum):
    SOFT_LIMIT = "soft_limit"
    RECOVERED = "recovered"
    HARD_LIMIT = "hard_limit"
    DRAINED = "drained"
    STALLED = "stalled"
    RESUMED = "resumed"
    INTERRUPTED = "interrupted"
    TRACK_REPLACED = "track_replaced"


class AppendResult(Enum):
    ACCEPTED = "accepted"
    OVERFLOW = "overflow"
    IGNORED = "ignored"


@dataclass(frozen=True)
class PlaybackCut:
    item_id: str
    content_index: int
    played_ms: int


_TRANSITIONS = {
    (PlaybackState.PLAYING, PlaybackEvent.SOFT_LIMIT): PlaybackState.BACKLOG,
    (PlaybackState.BACKLOG, PlaybackEvent.RECOVERED): PlaybackState.PLAYING,
    (PlaybackState.DRAINING, PlaybackEvent.DRAINED): PlaybackState.PLAYING,
    (PlaybackState.SUSPENDED, PlaybackEvent.RESUMED): PlaybackState.PLAYING,
}

for _state in PlaybackState:
    _TRANSITIONS[_state, PlaybackEvent.HARD_LIMIT] = PlaybackState.DRAINING
    _TRANSITIONS[_state, PlaybackEvent.STALLED] = PlaybackState.SUSPENDED
    _TRANSITIONS[_state, PlaybackEvent.INTERRUPTED] = PlaybackState.PLAYING
    _TRANSITIONS[_state, PlaybackEvent.TRACK_REPLACED] = PlaybackState.PLAYING


class PlaybackStateMachine:
    def __init__(self) -> None:
        self._state = PlaybackState.PLAYING

    @property
    def state(self) -> PlaybackState:
        return self._state

    def apply(self, event: PlaybackEvent) -> PlaybackState:
        self._state = _TRANSITIONS.get((self._state, event), self._state)
        return self._state


class PlaybackController:
    """Single event-loop owner. read_frame always returns one full PCM frame."""
    def __init__(self, options: PlaybackOptions) -> None:
        self._options = options
        self._fsm = PlaybackStateMachine()
        self._chunks: deque[tuple[tuple[str, int], bytes, int]] = deque()
        self._pending = bytearray()
        self._pending_item: tuple[str, int] | None = None
        self._received: dict[tuple[str, int], int] = {}
        self._played: dict[tuple[str, int], int] = {}
        self._completed: set[tuple[str, int]] = set()
        self._buffered = 0
        self._last_read = monotonic()
        self._drained_cuts: list[PlaybackCut] | None = None

    @property
    def state(self) -> PlaybackState:
        return self._fsm.state

    def append(self, item_id: str, content_index: int, data: bytes) -> AppendResult:
        key = (item_id, content_index)
        self._received[key] = self._received.get(key, 0) + len(data) // PCM16_SAMPLE_BYTES

        if self.state in (PlaybackState.DRAINING, PlaybackState.SUSPENDED):
            return AppendResult.IGNORED

        if self._seconds(self._buffered + len(data)) > self._options.hard_limit_seconds:
            self.finish_response()
            self._fsm.apply(PlaybackEvent.HARD_LIMIT)
            self._finish_drain_if_empty()

            return AppendResult.OVERFLOW

        if not self._buffered:
            self._last_read = monotonic()

        if self._pending_item is not None and self._pending_item != key:
            self.finish_response()

        self._pending_item = key
        self._buffered += len(data)
        self._pending.extend(data)
        self._split_frames()

        if self._seconds(self._buffered) >= self._options.soft_limit_seconds:
            self._fsm.apply(PlaybackEvent.SOFT_LIMIT)

        return AppendResult.ACCEPTED

    def _split_frames(self) -> None:
        while len(self._pending) >= PCM.bytes_per_frame:
            self._chunks.append((self._pending_item, bytes(self._pending[:PCM.bytes_per_frame]), PCM.samples_per_frame))
            del self._pending[:PCM.bytes_per_frame]

    def finish_response(self) -> None:
        if not self._pending:
            self._pending_item = None
            return

        samples = len(self._pending) // PCM16_SAMPLE_BYTES
        padding = PCM.bytes_per_frame - len(self._pending)
        data = bytes(self._pending) + bytes(padding)

        self._pending.clear()
        self._chunks.append((self._pending_item, data, samples))

        self._buffered += padding
        self._pending_item = None

    def complete_item(self, item_id: str, content_index: int) -> None:
        key = (item_id, content_index)

        if self._pending_item == key:
            self.finish_response()

        self._completed.add(key)
        self._prune_completed()

    def read_frame(self) -> bytes:
        self._last_read = monotonic()
        self._fsm.apply(PlaybackEvent.RESUMED)

        if not self._chunks:
            return bytes(PCM.bytes_per_frame)

        item_id, frame, samples = self._chunks.popleft()
        self._played[item_id] = self._played.get(item_id, 0) + samples
        self._buffered -= len(frame)

        self._after_read()

        return frame

    def _after_read(self) -> None:
        if not self._buffered:
            self._finish_drain_if_empty()

        if self.state != PlaybackState.DRAINING:
            self._prune_completed()

        if self._seconds(self._buffered) < self._options.soft_limit_seconds:
            self._fsm.apply(PlaybackEvent.RECOVERED)

    def _finish_drain_if_empty(self) -> None:
        if self.state != PlaybackState.DRAINING or self._buffered:
            return

        self._drained_cuts = self._cuts()

        self._clear()
        self._fsm.apply(PlaybackEvent.DRAINED)

    def _prune_completed(self) -> None:
        if self.state == PlaybackState.DRAINING:
            return

        buffered_keys = {key for key, _, _ in self._chunks}

        if self._pending_item is not None:
            buffered_keys.add(self._pending_item)

        for key in self._completed - buffered_keys:
            self._received.pop(key, None)
            self._played.pop(key, None)
            self._completed.remove(key)

    def take_drained_cuts(self) -> list[PlaybackCut] | None:
        cuts, self._drained_cuts = self._drained_cuts, None
        return cuts

    def interrupt(self) -> list[PlaybackCut]:
        cuts = self._cuts()

        self._clear()

        self._drained_cuts = None

        self._fsm.apply(PlaybackEvent.INTERRUPTED)

        return cuts

    def replace_track(self) -> list[PlaybackCut]:
        cuts = self._cuts()

        self._clear()

        self._drained_cuts = None

        self._fsm.apply(PlaybackEvent.TRACK_REPLACED)

        self._last_read = monotonic()

        return cuts

    def suspend_if_stalled(self) -> list[PlaybackCut] | None:
        if not self._buffered or monotonic() - self._last_read < self._options.stall_seconds:
            return None

        cuts = self._cuts()

        self._clear()

        self._drained_cuts = None

        self._fsm.apply(PlaybackEvent.STALLED)

        return cuts

    def _cuts(self) -> list[PlaybackCut]:
        return [
            PlaybackCut(key[0], key[1], played * 1000 // PCM.rate)
            for key, received in self._received.items()
            if received > (played := self._played.get(key, 0))
        ]

    def _clear(self) -> None:
        self._chunks.clear()
        self._pending.clear()

        self._pending_item = None

        self._received.clear()
        self._played.clear()
        self._completed.clear()

        self._buffered = 0

    @staticmethod
    def _seconds(size: int) -> float:
        return size / (PCM.rate * PCM16_SAMPLE_BYTES)
