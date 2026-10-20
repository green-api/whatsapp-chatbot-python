"""Realtime provider adapter; playback policy is owned by PlaybackController."""
from __future__ import annotations

import asyncio
import base64
from time import monotonic
from uuid import uuid4
from contextlib import AsyncExitStack

from .models import (
    EventSink,
    MediaFailed,
    PlaybackOptions,
    ProviderConfig,
    ProviderError,
    PCM,
)

from .playback_state_machine import AppendResult, PlaybackController, PlaybackCut, PlaybackState


class RealtimeConversation:
    def __init__(
        self,
        config: ProviderConfig,
        playback: PlaybackOptions,
        emit: EventSink,
        language: str,
        shutdown_seconds: float,
    ) -> None:
        self._config = config
        self._options = playback
        self._emit = emit
        self._language = language
        self._shutdown_seconds = shutdown_seconds
        self._stack = AsyncExitStack()
        self._connection = None
        self._client = None
        self._tasks: set[asyncio.Task] = set()
        self._responses: set[str] = set()
        self._cancelled: set[str] = set()
        self._cancel_events: dict[str, float] = {}
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
        instructions = self._config.instructions or (
            f"Have a concise, helpful phone conversation in {self._language}."
        )

        return {
            "type": "realtime",
            "instructions": instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": PCM.rate},
                    "turn_detection": {"type": "semantic_vad"},
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": PCM.rate},
                    "voice": self._config.realtime_voice,
                },
            },
        }

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

        if cuts and self._connection is not None and not self._closed:
            self._spawn(self._truncate_cuts(cuts))

        return frame

    def replace_track(self) -> None:
        cuts = self._playback.replace_track()
        if self._connection is not None and not self._closed and (cuts or self._responses):
            self._spawn(self._finish_interruption(cuts))

    async def write_audio(self, data: bytes) -> None:
        await self._connection.input_audio_buffer.append(
            audio=base64.b64encode(data).decode("ascii"))

    async def greet_once(self, text: str | None) -> None:
        if self._greeted:
            return

        self._greeted = True

        await self._connection.response.create(response={
            "instructions": text or f"Greet the caller briefly in {self._language}.",
        })

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
                    await self._cancel_responses()
            case "response.output_audio.delta":
                await self._receive_audio(event)
            case "response.output_audio.done":
                item_id = getattr(event, "item_id", None)

                if item_id is None:
                    self._playback.finish_response()
                else:
                    self._playback.complete_item(item_id, event.content_index)
            case "response.done":
                self._responses.discard(event.response.id)

                if event.response.status == "failed" and event.response.id not in self._cancelled:
                    raise ProviderError("openai", "response_failed", "Realtime response failed")

                self._cancelled.discard(event.response.id)
            case "input_audio_buffer.speech_started":
                await self._finish_interruption(self._playback.interrupt())
            case "error":
                event_id = getattr(event.error, "event_id", None)

                if event_id in self._cancel_events:
                    self._cancel_events.pop(event_id)
                    return

                raise self._provider_error(event)

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

        result = self._playback.append(event.item_id, event.content_index, data)

        if result is AppendResult.OVERFLOW:
            await self._cancel_responses()
        elif result is AppendResult.IGNORED and self._playback.state == PlaybackState.SUSPENDED:
            await self._finish_interruption(self._playback.interrupt())

    async def _finish_interruption(self, cuts: list[PlaybackCut]) -> None:
        await self._cancel_responses()
        await self._truncate_cuts(cuts)

    async def _truncate_cuts(self, cuts: list[PlaybackCut]) -> None:

        for cut in cuts:
            await self._connection.conversation.item.truncate(
                item_id=cut.item_id,
                content_index=cut.content_index,
                audio_end_ms=cut.played_ms,
            )

    async def _cancel_responses(self) -> None:
        pending = self._responses - self._cancelled
        self._cancelled.update(pending)

        for response_id in pending:
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
                await self._finish_interruption(cuts)
