"""Realtime provider adapter; playback policy is owned by PlaybackController."""
from __future__ import annotations

import asyncio
import base64
import logging
from time import monotonic
from uuid import uuid4
from contextlib import AsyncExitStack

from .models import (
    AudioLoss,
    EventSink,
    MediaFailed,
    PlaybackOptions,
    ProviderConfig,
    ProviderError,
    VadOptions,
    PCM,
)

from .playback_state_machine import (
    AppendResult,
    AudioItemKey,
    PlaybackController,
    PlaybackCut,
    PlaybackState,
)


_LANGUAGE_NAMES = {
    "ru": "Russian",
    "en": "English",
    "he": "Hebrew",
    "es": "Spanish",
    "kz": "Kazakh",
}


logger = logging.getLogger(__name__)


class RealtimeConversation:
    def __init__(
        self,
        config: ProviderConfig,
        playback: PlaybackOptions,
        emit: EventSink,
        language: str,
        shutdown_seconds: float,
        *,
        vad: VadOptions | None = None,
    ) -> None:
        self._config = config
        self._options = playback
        self._emit = emit
        self._language = language
        self._shutdown_seconds = shutdown_seconds
        self._vad = vad if vad is not None else VadOptions()
        self._stack = AsyncExitStack()
        self._connection = None
        self._client = None
        self._tasks: set[asyncio.Task] = set()
        self._responses: set[str] = set()
        self._response_items: dict[str, set[AudioItemKey]] = {}
        self._cancelled: set[str] = set()
        self._cancel_events: dict[str, float] = {}
        self._dropped_audio: dict[str, int] = {}
        self._playback = PlaybackController(playback)
        self._greeted = False
        self._closed = False

    async def __aenter__(self) -> RealtimeConversation:
        self._ensure_open()
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=self._config.openai_api_key)

        self._stack.push_async_callback(self._close_client)

        return self

    async def __aexit__(self, *exc) -> None:
        self._closed = True

        try:
            await self._stop_readers()
        finally:
            await self._stack.aclose()

    async def start(self) -> None:
        self._ensure_open()

        manager = self._client.realtime.connect(model=self._config.realtime_model)
        connection = await manager.enter()

        if self._closed:
            await asyncio.wait_for(connection.close(), self._shutdown_seconds)
            raise RuntimeError("Realtime conversation is closing")

        self._connection = connection

        self._stack.push_async_callback(self._close_connection)
        await self._connection.session.update(session=self._session_settings())
        self._ensure_open()
        await self._await_configuration()
        self._ensure_open()
        self._spawn(self._read())
        self._spawn(self._watch_playback())

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Realtime conversation is closing")

    async def _close_client(self) -> None:
        await asyncio.wait_for(self._client.close(), self._shutdown_seconds)

    async def _close_connection(self) -> None:
        await asyncio.wait_for(self._connection.close(), self._shutdown_seconds)

    def _session_settings(self) -> dict:
        instructions = self._config.instructions or self._default_conversation_instructions()

        return {
            "type": "realtime",
            "instructions": instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": PCM.rate},
                    "noise_reduction": (
                        {"type": self._vad.noise_reduction}
                        if self._vad.noise_reduction is not None else None
                    ),
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": self._vad.threshold,
                        "prefix_padding_ms": self._vad.prefix_padding_ms,
                        "silence_duration_ms": self._vad.silence_duration_ms,
                        "create_response": True,
                        "interrupt_response": True,
                    },
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": PCM.rate},
                    "voice": self._config.realtime_voice,
                },
            },
        }

    def _default_conversation_instructions(self) -> str:
        if self._language == "adaptive":
            # Workaround: English-only instructions can keep the model speaking English.
            return (
                "Greet the user briefly. "
                "If the user speaks another language, naturally switch to that language. "
                "The conversation is not only in English."
            )

        return f"Greet the caller briefly in {self._language}."

    async def _await_configuration(self) -> None:
        async for event in self._connection:
            match event.type:
                case "session.updated":
                    return
                case "error":
                    raise self._provider_error(event)

        raise ConnectionError("Realtime closed before session configuration")

    def _spawn(self, coroutine) -> None:
        if self._closed:
            coroutine.close()
            return

        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._reader_done)

    def _reader_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)

        if task.cancelled() or self._closed:
            return

        error = task.exception()

        if error is not None:
            self._emit(MediaFailed(error))

    async def _stop_readers(self) -> None:
        tasks = tuple(self._tasks)

        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)

    def read_frame(self) -> bytes:
        frame = self._playback.read_frame()
        cuts = self._playback.take_drained_cuts()
        items_to_delete = self._playback.take_items_to_delete()

        if cuts and self._connection is not None and not self._closed:
            self._spawn(self._truncate_cuts(cuts))

        if items_to_delete and self._connection is not None and not self._closed:
            self._spawn(self._delete_items(items_to_delete))

        return frame

    def replace_track(self) -> None:
        cuts = self._playback.replace_track()
        items_to_delete = self._playback.take_items_to_delete()

        if self._connection is not None and not self._closed and (cuts or self._responses):
            self._spawn(self._finish_interruption(cuts, reason="track_replaced"))

        if items_to_delete and self._connection is not None and not self._closed:
            self._spawn(self._delete_items(items_to_delete))

    async def write_audio(self, data: bytes) -> None:
        await self._connection.input_audio_buffer.append(
            audio=base64.b64encode(data).decode("ascii"))

    async def greet_once(self, prompt: str | None) -> None:
        if self._greeted:
            return

        self._greeted = True

        greeting_instruction = (
            prompt if prompt is not None else self._default_greeting_instruction()
        )

        await self._connection.response.create(response={
            "instructions": greeting_instruction,
        })

    def _default_greeting_instruction(self) -> str:
        if self._language == "adaptive":
            # Workaround for the model staying in English after an English greeting.
            return (
                "Greet the user briefly. If the user speaks another language, "
                "naturally switch to that language. The conversation is not only in English."
            )

        language_name = _LANGUAGE_NAMES.get(self._language, "English")

        return f"Greet the user briefly in {language_name}."

    async def _read(self) -> None:
        async for event in self._connection:
            await self._dispatch(event)
        if not self._closed:
            raise ConnectionError("Realtime stream ended")

    async def _dispatch(self, event) -> None:
        self._prune_cancel_events()

        match event.type:
            case "response.created":
                self._responses.add(event.response.id)

                if self._playback.state in (PlaybackState.DRAINING, PlaybackState.SUSPENDED):
                    reason = "playback_capacity_exceeded"

                    if self._playback.state == PlaybackState.SUSPENDED:
                        reason = "playback_stalled"

                    await self._cancel_responses(reason=reason)
            case "response.output_audio.delta":
                await self._receive_audio(event)
            case "response.output_audio.done":
                item_id = getattr(event, "item_id", None)

                if item_id is None:
                    self._playback.finish_response()
                else:
                    self._playback.complete_item(item_id, event.content_index)

                await self._delete_items(self._playback.take_items_to_delete())
            case "response.done":
                await self._response_finished(event.response)
            case "input_audio_buffer.speech_started":
                await self._finish_interruption(self._playback.interrupt())
            case "error":
                event_id = getattr(event.error, "event_id", None)

                if event_id in self._cancel_events:
                    self._cancel_events.pop(event_id)
                    return

                raise self._provider_error(event)

    async def _response_finished(self, response) -> None:
        self._responses.discard(response.id)
        response_item_keys = self._response_items.pop(response.id, set())

        for item_id, content_index in response_item_keys:
            self._playback.complete_item(item_id, content_index)

        self._playback.mark_finished_response(response_item_keys)

        await self._delete_items(self._playback.take_items_to_delete())

        dropped_samples = self._dropped_audio.pop(response.id, 0)

        if dropped_samples:
            self._emit(AudioLoss("bot", dropped_samples))

            logger.warning(
                "Realtime audio dropped: response=%s samples=%d",
               response.id, dropped_samples,
           )

        if response.status == "failed" and response.id not in self._cancelled:
            raise ProviderError("openai", "response_failed", "Realtime response failed")

        self._cancelled.discard(response.id)

        if response.status == "incomplete":
            details = getattr(response, "status_details", None)

            logger.warning(
                "Realtime response incomplete: response=%s reason=%s",
                response.id,
                getattr(details, "reason", None),
            )

    @staticmethod
    def _provider_error(event) -> ProviderError:
        return ProviderError(
            "openai",
            event.error.code or "realtime_error",
            event.error.message,
        )

    async def _receive_audio(self, event) -> None:
        if event.response_id in self._cancelled or event.response_id not in self._responses:
            return

        data = base64.b64decode(event.delta, validate=True)

        self._response_items.setdefault(event.response_id, set()).add(
            (event.item_id, event.content_index))

        previously_dropped = self._playback.dropped_samples
        result = self._playback.append(event.item_id, event.content_index, data)

        if result is AppendResult.OVERFLOW:
            await self._cancel_responses(reason="playback_capacity_exceeded")
        elif result is AppendResult.DROPPED:
            dropped_samples = self._playback.dropped_samples - previously_dropped

            self._dropped_audio[event.response_id] = (
                self._dropped_audio.get(event.response_id, 0) + dropped_samples
            )
        elif result is AppendResult.IGNORED and self._playback.state == PlaybackState.SUSPENDED:
            await self._finish_interruption(
                self._playback.interrupt(), reason="playback_stalled")

    async def _finish_interruption(
        self,
        cuts: list[PlaybackCut],
        reason: str = "caller_interrupted",
    ) -> None:
        await self._cancel_responses(reason=reason)
        await self._truncate_cuts(cuts)
        await self._delete_items(self._playback.take_items_to_delete())

    async def _truncate_cuts(self, cuts: list[PlaybackCut]) -> None:

        for cut in cuts:
            await self._connection.conversation.item.truncate(
                item_id=cut.item_id,
                content_index=cut.content_index,
                audio_end_ms=cut.played_ms,
            )

    async def _delete_items(self, item_ids: set[str]) -> None:
        for item_id in item_ids:
            await self._connection.conversation.item.delete(item_id=item_id)

    async def _cancel_responses(self, reason: str = "caller_interrupted") -> None:
        pending = self._responses - self._cancelled
        self._cancelled.update(pending)

        for response_id in pending:
            logger.info(
                "Realtime response cancelled: response=%s reason=%s",
                response_id,
                reason,
            )

            event_id = f"call-cancel-{uuid4().hex}"
            self._cancel_events[event_id] = monotonic() + 30
            await self._connection.response.cancel(response_id=response_id, event_id=event_id)

    def _prune_cancel_events(self) -> None:
        now = monotonic()

        for event_id, expires in tuple(self._cancel_events.items()):
            if expires <= now:
                del self._cancel_events[event_id]

    async def _watch_playback(self) -> None:
        while True:
            await asyncio.sleep(self._options.watchdog_seconds)

            cuts = self._playback.suspend_if_stalled()

            if cuts is not None:
                await self._finish_interruption(cuts, reason="playback_stalled")
