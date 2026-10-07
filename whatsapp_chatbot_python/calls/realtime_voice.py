"""One OpenAI Realtime conversation per Green-API call."""

from __future__ import annotations
from collections import deque
from fractions import Fraction
from time import monotonic
from typing import Callable
from uuid import uuid4
from aiortc import AudioStreamTrack, MediaStreamTrack
from aiortc.mediastreams import MediaStreamError
from openai import AsyncOpenAI
from .playback_state_machine import PlaybackEvent, PlaybackState, PlaybackStateMachine
from .recording import CallRecorder
import asyncio
import base64
import logging
import av


# Constants

AUDIO_SAMPLE_RATE_HZ = 24 * 1000  # 24000 hz

FRAME_DURATION_MS = 20  # 0.02 seconds

SOFT_PLAYBACK_BUFFER_MS = 6 * 1000  # 6 seconds

HARD_PLAYBACK_BUFFER_MS = 24 * 1000  # 24 seconds

FRAME_SAMPLES = AUDIO_SAMPLE_RATE_HZ * FRAME_DURATION_MS // 1000  # 480 samples

RECOVER_PLAYBACK_BUFFER_MS = SOFT_PLAYBACK_BUFFER_MS // 2  # 3 second

FRAME_BYTES = 2 * FRAME_SAMPLES  # 960 bytes (Mono PCM16)

LANGUAGE_NAMES = {
    "ru": "Russian",
    "en": "English",
    "he": "Hebrew",
    "es": "Spanish",
    "kz": "Kazakh",
}


# Variables

logger = logging.getLogger(__name__)


class BotOutputTrack(AudioStreamTrack):
    def __init__(self, voice: VoiceBotSession, generation: int):
        super().__init__()
        self._voice = voice
        self._generation = generation
        self._pts = 0
        self._next_frame_at: float | None = None

    async def recv(self) -> av.AudioFrame:
        loop = asyncio.get_running_loop()

        if self._next_frame_at is None:
            self._next_frame_at = loop.time()

        await asyncio.sleep(max(0, self._next_frame_at - loop.time()))

        self._next_frame_at = max(self._next_frame_at + FRAME_DURATION_MS / 1000, loop.time())

        pcm = self._voice.take_frame(self._generation)

        if self._voice.recorder is not None:
            self._voice.recorder.add_bot(pcm, self._generation)

        frame = av.AudioFrame(format="s16", layout="mono", samples=FRAME_SAMPLES)

        frame.planes[0].update(pcm)

        frame.sample_rate = AUDIO_SAMPLE_RATE_HZ
        frame.time_base = Fraction(1, AUDIO_SAMPLE_RATE_HZ)
        frame.pts = self._pts
        self._pts += FRAME_SAMPLES

        return frame


class CallerAudioSink:
    def __init__(self, voice: VoiceBotSession, generation: int):
        self._voice = voice
        self._generation = generation
        self._task: asyncio.Task | None = None

    async def attach(self, track: MediaStreamTrack) -> None:
        if track.kind != "audio":
            raise ValueError("Expected an audio track")

        self._task = asyncio.create_task(self._consume(track))

    async def _consume(self, track: MediaStreamTrack) -> None:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=AUDIO_SAMPLE_RATE_HZ)

        try:
            while self._generation == self._voice.generation:
                frame = await track.recv()

                for converted in resampler.resample(frame):
                    if self._generation != self._voice.generation:
                        return

                    pcm = bytes(converted.planes[0])[:converted.samples * 2]

                    if self._voice.recorder is not None:
                        self._voice.recorder.add_caller(pcm, self._generation)

                    await self._voice.append_input(pcm)
        except MediaStreamError:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._voice.fail(error)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

            self._task = None


class VoiceBotSession:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        voice: str,
        language: str,
        on_error: Callable[[Exception], None],
        recorder: CallRecorder | None = None,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._voice = voice
        self._language = language
        self._on_error = on_error
        self.recorder = recorder
        self._manager = None
        self._connection = None
        self._reader: asyncio.Task | None = None
        self._watchdog: asyncio.Task | None = None
        self._drain_task: asyncio.Task | None = None
        self._closed = False
        self._greeted = False
        self.generation = 0
        self._frames: deque[tuple[str | None, bytes]] = deque()
        self._partial = bytearray()
        self._last_item: str | None = None
        self._played: dict[str, int] = {}
        self._truncated: set[str] = set()
        self._finished: set[str] = set()
        self._item_response: dict[str, str] = {}
        self._item_content_index: dict[str, int] = {}
        self._active_responses: set[str] = set()
        self._discarded_responses: set[str] = set()
        self._cancel_events: set[str] = set()
        self.playback_state = PlaybackState.PLAYING
        self._playback_fsm = PlaybackStateMachine()
        self._pending_truncation: set[str] = set()
        self._buffered_since: float | None = None
        self._last_pull_at: float | None = None

    async def start(self) -> None:
        if not self._client.api_key:
            raise ValueError("OpenAI API key is required for voice calls")

        self._manager = self._client.realtime.connect(model=self._model, max_retries=0)
        self._connection = await self._manager.__aenter__()

        try:
            await self._connection.session.update(session={
                "type": "realtime",
                "model": self._model,
                "output_modalities": ["audio"],
                "instructions": (
                    "You are a helpful voice assistant in a WhatsApp phone call. "
                    f"Speak in {LANGUAGE_NAMES.get(self._language, 'English')}. "
                    "Speak naturally and briefly. Do not use markdown."
                ),
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": AUDIO_SAMPLE_RATE_HZ},
                        "turn_detection": {"type": "semantic_vad"},
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": AUDIO_SAMPLE_RATE_HZ},
                        "voice": self._voice,
                    },
                },
            })

            while True:
                event = await asyncio.wait_for(self._connection.recv(), timeout=15)

                if event.type == "session.updated":
                    break

                if event.type == "error":
                    raise RuntimeError("OpenAI Realtime rejected session configuration")

            self._reader = asyncio.create_task(self._read_events())
            self._watchdog = asyncio.create_task(self._watch_playback())
        except BaseException:
            await self.close()
            raise

    async def greet_once(self) -> None:
        if self._greeted or self._closed:
            return

        self._greeted = True

        await self._connection.response.create(response={
            "instructions": (
                f"Greet the caller briefly in {LANGUAGE_NAMES.get(self._language, 'English')}."
            ),
        })

    async def new_output_track(self) -> BotOutputTrack:
        if self.generation:
            await self._interrupt(cancel=True, reason="track_replaced")

        self.generation += 1

        if self.recorder is not None:
            self.recorder.set_generation(self.generation)

        self._frames.clear()
        self._partial.clear()

        self._last_item = None
        self._buffered_since = None
        self._last_pull_at = None
        self._transition_playback(PlaybackEvent.TRACK_REPLACED)

        return BotOutputTrack(self, self.generation)

    def new_input_sink(self) -> CallerAudioSink:
        return CallerAudioSink(self, self.generation)

    async def append_input(self, pcm: bytes) -> None:
        if pcm and not self._closed:
            await self._connection.input_audio_buffer.append(
                audio=base64.b64encode(pcm).decode("ascii")
            )

    def _transition_playback(self, event: PlaybackEvent) -> None:
        previous = self.playback_state
        self.playback_state = self._playback_fsm.apply(previous, event)

        if self.playback_state != previous:
            logger.info(
                "OpenAI playback state: %s -> %s event=%s queued_ms=%d",
                previous.value, self.playback_state.value, event.value,
                len(self._frames) * FRAME_DURATION_MS,
            )

    def take_frame(self, generation: int) -> bytes:
        if generation != self.generation:
            return bytes(FRAME_BYTES)

        self._last_pull_at = monotonic()

        if self.playback_state == PlaybackState.SUSPENDED:
            self._transition_playback(PlaybackEvent.RESUMED)

        if not self._frames:
            return bytes(FRAME_BYTES)

        item, pcm = self._frames.popleft()

        if not self._frames and not self._partial:
            self._buffered_since = None

            if self.playback_state == PlaybackState.DRAINING and self._drain_task is None:
                self._drain_task = asyncio.create_task(self._finish_draining())

        if (
            self.playback_state == PlaybackState.BACKLOG and
            len(self._frames) * FRAME_DURATION_MS <= RECOVER_PLAYBACK_BUFFER_MS
        ):
            self._transition_playback(PlaybackEvent.RECOVERED)

        if item is not None:
            self._played[item] = self._played.get(item, 0) + FRAME_SAMPLES

        return pcm

    def _queue_audio(self, item_id: str, pcm: bytes) -> bool:
        if item_id in self._truncated:
            return True

        if self._last_item != item_id:
            self._partial.clear()

        self._last_item = item_id

        remaining_bytes = max(
            0,
            (HARD_PLAYBACK_BUFFER_MS // FRAME_DURATION_MS - len(self._frames))
            * FRAME_BYTES - len(self._partial),
        )

        overflow = len(pcm) > remaining_bytes
        pcm = pcm[:remaining_bytes]

        if not self._frames and not self._partial and pcm:
            self._buffered_since = monotonic()

        self._partial.extend(pcm)

        while len(self._partial) >= FRAME_BYTES:
            self._frames.append((item_id, bytes(self._partial[:FRAME_BYTES])))

            del self._partial[:FRAME_BYTES]

        if len(self._frames) * FRAME_DURATION_MS >= SOFT_PLAYBACK_BUFFER_MS:
            self._transition_playback(PlaybackEvent.SOFT_LIMIT)

        return not overflow

    def _finish_audio(self, item_id: str) -> bool:
        if item_id in self._truncated:
            return True

        self._finished.add(item_id)

        if self._partial and self._last_item == item_id:
            if (len(self._frames) + 1) * FRAME_DURATION_MS > HARD_PLAYBACK_BUFFER_MS:
                return False

            pcm = bytes(self._partial).ljust(FRAME_BYTES, b"\x00")

            self._frames.append((item_id, pcm))

            self._partial.clear()

        return True

    async def _stop_generation(self, responses: set[str]) -> None:
        new_responses = responses - self._discarded_responses

        self._discarded_responses.update(responses)

        for response_id in new_responses & self._active_responses:
            event_id = f"playback-cancel-{uuid4().hex}"
            self._cancel_events.add(event_id)
            await self._connection.response.cancel(response_id=response_id, event_id=event_id)

    async def _truncate_items(self, items: set[str]) -> None:
        pending = items - self._truncated

        self._truncated.update(pending)

        for item in pending:
            await self._connection.conversation.item.truncate(
                item_id=item,
                content_index=self._item_content_index.get(item, 0),
                audio_end_ms=self._played.get(item, 0) * 1000 // AUDIO_SAMPLE_RATE_HZ,
            )

    async def _finish_draining(self) -> None:
        try:
            items = set(self._pending_truncation)

            self._pending_truncation.clear()
            await self._truncate_items(items)
            self._transition_playback(PlaybackEvent.DRAINED)
        except Exception as error:
            self.fail(error)
        finally:
            self._drain_task = None

    async def _interrupt(self, *, cancel: bool = False, reason: str = "speech") -> None:
        items = {item for item, _ in self._frames if item is not None}
        items.update(self._pending_truncation)

        if self._last_item is not None and (
            self._partial or self._last_item not in self._finished
        ):
            items.add(self._last_item)

        responses = {self._item_response[item] for item in items if item in self._item_response}

        if cancel and (items or self._active_responses):
            logger.warning(
                "OpenAI playback interrupted: reason=%s queued_ms=%d pull_age_ms=%d",
                reason, len(self._frames) * FRAME_DURATION_MS,
                int((monotonic() - self._last_pull_at) * 1000) if self._last_pull_at else -1,
            )

        self._frames.clear()
        self._partial.clear()
        self._pending_truncation.clear()
        self._last_item = None
        self._buffered_since = None

        self._transition_playback(
            PlaybackEvent.STALLED if reason == "stalled" else PlaybackEvent.INTERRUPTED
        )

        if cancel:
            await self._stop_generation(responses | self._active_responses)
        else:
            self._discarded_responses.update(responses)

        await self._truncate_items(items)

    async def _hard_limit(self, response_id: str, item_id: str) -> None:
        self._partial.clear()
        self._pending_truncation.add(item_id)
        self._transition_playback(PlaybackEvent.HARD_LIMIT)
        await self._stop_generation({response_id})

    async def _watch_playback(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(0.5)

                buffered_ms = len(self._frames) * FRAME_DURATION_MS

                if buffered_ms >= RECOVER_PLAYBACK_BUFFER_MS and self._buffered_since is not None:
                    last_progress = max(self._buffered_since, self._last_pull_at or 0)

                    if (monotonic() - last_progress) * 1000 >= SOFT_PLAYBACK_BUFFER_MS:
                        await self._interrupt(cancel=True, reason="stalled")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.fail(error)

    async def _read_events(self) -> None:
        try:
            async for event in self._connection:
                if event.type == "response.created":
                    self._active_responses.add(event.response.id)
                elif event.type == "response.output_audio.delta":
                    if event.response_id in self._discarded_responses:
                        self._item_response[event.item_id] = event.response_id
                        self._item_content_index[event.item_id] = event.content_index

                        if event.item_id not in self._pending_truncation:
                            await self._truncate_items({event.item_id})

                        continue

                    self._item_response[event.item_id] = event.response_id
                    self._item_content_index[event.item_id] = event.content_index

                    if self.playback_state in {PlaybackState.DRAINING, PlaybackState.SUSPENDED}:
                        await self._stop_generation({event.response_id})
                        await self._truncate_items({event.item_id})
                        continue

                    if not self._queue_audio(event.item_id, base64.b64decode(event.delta)):
                        await self._hard_limit(event.response_id, event.item_id)
                elif event.type == "response.output_audio.done":
                    if self._item_response.get(event.item_id) in self._discarded_responses:
                        continue

                    if not self._finish_audio(event.item_id):
                        await self._hard_limit(self._item_response[event.item_id], event.item_id)
                elif event.type == "input_audio_buffer.speech_started":
                    await self._interrupt(cancel=True)
                elif event.type == "response.done":
                    response_id = getattr(event.response, "id", None)
                    self._active_responses.discard(response_id)

                    if event.response.status == "failed" and response_id not in self._discarded_responses:
                        raise RuntimeError("OpenAI Realtime response failed")
                elif event.type == "error":
                    cancel_id = getattr(event.error, "event_id", None)

                    if cancel_id in self._cancel_events:
                        self._cancel_events.discard(cancel_id)
                        continue

                    raise RuntimeError("OpenAI Realtime reported an error")

            if not self._closed:
                raise ConnectionError("OpenAI Realtime connection closed")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.fail(error)

    def fail(self, error: Exception) -> None:
        if not self._closed:
            self._on_error(error)

    async def close(self) -> None:
        if self._closed:
            return

        self._closed = True

        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)

        if self._watchdog is not None:
            self._watchdog.cancel()
            await asyncio.gather(self._watchdog, return_exceptions=True)

        if self._drain_task is not None:
            self._drain_task.cancel()
            await asyncio.gather(self._drain_task, return_exceptions=True)

        if self._manager is not None:
            await self._manager.__aexit__(None, None, None)

        await self._client.close()
