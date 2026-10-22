"""Playback policy and bounded PCM buffer have one owner."""
from collections import deque
from dataclasses import dataclass
from enum import Enum
from time import monotonic

from .models import PlaybackOptions, PlaybackOverflowPolicy, PCM, PCM16_SAMPLE_BYTES


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
    DROPPED = "dropped"
    IGNORED = "ignored"


@dataclass(frozen=True)
class PlaybackCut:
    item_id: str
    content_index: int
    played_ms: int


AudioItemKey = tuple[str, int]


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
        self._chunks: deque[tuple[AudioItemKey, bytes, int]] = deque()
        self._pending = bytearray()
        self._pending_item: AudioItemKey | None = None
        self._received_samples: dict[AudioItemKey, int] = {}
        self._played_samples: dict[AudioItemKey, int] = {}
        self._completed: set[AudioItemKey] = set()
        self._items_with_missing_audio: set[AudioItemKey] = set()
        self._pending_item_deletions: set[str] = set()
        self._item_deletions_queued: set[str] = set()
        self.dropped_samples = 0
        self._buffered_bytes = 0
        self.peak_buffered_samples = 0
        self._last_read = monotonic()
        self._drained_cuts: list[PlaybackCut] | None = None

    @property
    def state(self) -> PlaybackState:
        return self._fsm.state

    def append(self, item_id: str, content_index: int, data: bytes) -> AppendResult:
        if len(data) % PCM16_SAMPLE_BYTES:
            raise ValueError("Audio must contain whole PCM samples")

        key = (item_id, content_index)

        if self.state in (PlaybackState.DRAINING, PlaybackState.SUSPENDED):
            return AppendResult.IGNORED

        self._received_samples[key] = self._received_samples.get(key, 0) + len(data) // PCM16_SAMPLE_BYTES

        if self._pending_item is not None and self._pending_item != key:
            self._flush_pending_frame()

        available_bytes = max(0, self._max_buffer_bytes() - self._buffered_bytes)
        accepted_audio = data[:available_bytes]
        overflow = len(accepted_audio) != len(data)

        if not self._buffered_bytes and accepted_audio:
            self._last_read = monotonic()

        self._pending_item = key
        self._buffered_bytes += len(accepted_audio)

        self.peak_buffered_samples = max(
            self.peak_buffered_samples,
            self._buffered_bytes // PCM16_SAMPLE_BYTES,
        )

        self._pending.extend(accepted_audio)
        self._split_frames()

        if self._seconds_from_bytes(self._buffered_bytes) >= self._options.soft_limit_seconds:
            self._fsm.apply(PlaybackEvent.SOFT_LIMIT)

        if not overflow:
            return AppendResult.ACCEPTED

        self._flush_pending_frame()

        if self._options.overflow_policy == PlaybackOverflowPolicy.DROP_CHUNKS:
            self._items_with_missing_audio.add(key)
            self.dropped_samples += (len(data) - len(accepted_audio)) // PCM16_SAMPLE_BYTES
            return AppendResult.DROPPED

        self._fsm.apply(PlaybackEvent.HARD_LIMIT)
        self._finish_drain_if_empty()

        return AppendResult.OVERFLOW

    def _max_buffer_bytes(self) -> int:
        capacity_samples = int(self._options.hard_limit_seconds * PCM.rate)
        whole_frames = max(1, capacity_samples // PCM.samples_per_frame)
        return whole_frames * PCM.bytes_per_frame

    def _split_frames(self) -> None:
        complete_bytes = len(self._pending) // PCM.bytes_per_frame * PCM.bytes_per_frame

        for start in range(0, complete_bytes, PCM.bytes_per_frame):
            frame = bytes(self._pending[start:start + PCM.bytes_per_frame])
            self._chunks.append((self._pending_item, frame, PCM.samples_per_frame))

        del self._pending[:complete_bytes]

    def finish_response(self) -> None:
        """Finish the current audio content after the provider's done event."""
        self._flush_pending_frame()

    def _flush_pending_frame(self) -> None:
        if not self._pending:
            self._pending_item = None
            return

        samples = len(self._pending) // PCM16_SAMPLE_BYTES
        padding = PCM.bytes_per_frame - len(self._pending)
        data = bytes(self._pending) + bytes(padding)

        self._pending.clear()
        self._chunks.append((self._pending_item, data, samples))

        self._buffered_bytes += padding
        self._pending_item = None

    def complete_item(self, item_id: str, content_index: int) -> None:
        key = (item_id, content_index)

        if self._pending_item == key:
            self._flush_pending_frame()

        self._completed.add(key)
        self._prune_completed()

    def read_frame(self) -> bytes:
        self._last_read = monotonic()
        self._fsm.apply(PlaybackEvent.RESUMED)

        if not self._chunks:
            return bytes(PCM.bytes_per_frame)

        item_id, frame, samples = self._chunks.popleft()
        self._played_samples[item_id] = self._played_samples.get(item_id, 0) + samples
        self._buffered_bytes -= len(frame)

        self._after_read()

        return frame

    def _after_read(self) -> None:
        if not self._buffered_bytes:
            self._finish_drain_if_empty()

        if self.state != PlaybackState.DRAINING:
            self._prune_completed()

        if self._seconds_from_bytes(self._buffered_bytes) < self._options.soft_limit_seconds:
            self._fsm.apply(PlaybackEvent.RECOVERED)

    def _finish_drain_if_empty(self) -> None:
        if self.state != PlaybackState.DRAINING or self._buffered_bytes:
            return

        self._drained_cuts = self._prepare_interruption()

        self._clear()
        self._fsm.apply(PlaybackEvent.DRAINED)

    def _prune_completed(self) -> None:
        if self.state == PlaybackState.DRAINING:
            return

        buffered_keys = {key for key, _, _ in self._chunks}

        if self._pending_item is not None:
            buffered_keys.add(self._pending_item)

        for key in self._completed - buffered_keys:
            if key in self._items_with_missing_audio:
                self._mark_item_for_deletion(key[0])
                self._items_with_missing_audio.remove(key)

            self._received_samples.pop(key, None)
            self._played_samples.pop(key, None)
            self._completed.remove(key)

    def take_drained_cuts(self) -> list[PlaybackCut] | None:
        cuts, self._drained_cuts = self._drained_cuts, None
        return cuts

    def take_items_to_delete(self) -> set[str]:
        items, self._pending_item_deletions = self._pending_item_deletions, set()
        return items

    def mark_finished_response(self, keys: set[AudioItemKey]) -> None:
        for key in keys & self._items_with_missing_audio:
            self._mark_item_for_deletion(key[0])

    def _mark_item_for_deletion(self, item_id: str) -> None:
        if item_id not in self._item_deletions_queued:
            self._item_deletions_queued.add(item_id)
            self._pending_item_deletions.add(item_id)

    def interrupt(self) -> list[PlaybackCut]:
        cuts = self._prepare_interruption()

        self._clear()

        self._drained_cuts = None

        self._fsm.apply(PlaybackEvent.INTERRUPTED)

        return cuts

    def replace_track(self) -> list[PlaybackCut]:
        cuts = self._prepare_interruption()

        self._clear()

        self._drained_cuts = None

        self._fsm.apply(PlaybackEvent.TRACK_REPLACED)

        self._last_read = monotonic()

        return cuts

    def suspend_if_stalled(self) -> list[PlaybackCut] | None:
        if not self._buffered_bytes or monotonic() - self._last_read < self._options.stall_seconds:
            return None

        cuts = self._prepare_interruption()

        self._clear()

        self._drained_cuts = None

        self._fsm.apply(PlaybackEvent.STALLED)

        return cuts

    def _prepare_interruption(self) -> list[PlaybackCut]:
        self._mark_items_with_missing_audio_for_deletion()
        return self._get_cuts()

    def _mark_items_with_missing_audio_for_deletion(self) -> None:
        for item_id, _ in self._items_with_missing_audio:
            self._mark_item_for_deletion(item_id)

    def _get_cuts(self) -> list[PlaybackCut]:
        cuts = []

        for (item_id, content_index), received_samples in self._received_samples.items():
            key = (item_id, content_index)

            if key in self._items_with_missing_audio:
                continue

            played_samples = self._played_samples.get(key, 0)

            if received_samples <= played_samples:
                continue

            played_ms = played_samples * 1000 // PCM.rate

            cuts.append(PlaybackCut(item_id, content_index, played_ms))

        return cuts

    def _clear(self) -> None:
        self._chunks.clear()
        self._pending.clear()

        self._pending_item = None

        self._received_samples.clear()
        self._played_samples.clear()
        self._completed.clear()
        self._items_with_missing_audio.clear()

        self._buffered_bytes = 0

    @staticmethod
    def _seconds_from_bytes(size_bytes: int) -> float:
        return size_bytes / (PCM.rate * PCM16_SAMPLE_BYTES)
