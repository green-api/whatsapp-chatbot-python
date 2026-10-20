"""Realtime transport tests keep OpenAI external and exercise actual playback."""

import asyncio
import base64
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from whatsapp_chatbot_python.calls.models import (
    MediaFailed, PCM, PlaybackOptions, ProviderConfig,
)
from whatsapp_chatbot_python.calls.openai import RealtimeConversation


FRAME = bytes([1, 2]) * PCM.samples_per_frame


class FakeConnection:
    def __init__(self):
        self.events = asyncio.Queue()
        self.actions = []
        self.closed = False
        self.session = SimpleNamespace(update=self.update)
        self.response = SimpleNamespace(create=self.create, cancel=self.cancel)
        self.input_audio_buffer = SimpleNamespace(append=self.append)
        self.conversation = SimpleNamespace(item=SimpleNamespace(truncate=self.truncate))

    async def update(self, **kwargs):
        self.actions.append(("update", kwargs))
        await self.events.put(SimpleNamespace(type="session.updated"))

    async def create(self, **kwargs):
        self.actions.append(("create", kwargs))

    async def cancel(self, **kwargs):
        self.actions.append(("cancel", kwargs))

    async def append(self, **kwargs):
        self.actions.append(("append", kwargs))

    async def truncate(self, **kwargs):
        self.actions.append(("truncate", kwargs))

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.events.get()


class FakeOpenAI:
    def __init__(self, *, api_key):
        self.api_key = api_key
        self.connection = FakeConnection()
        self.closed = False
        self.realtime = SimpleNamespace(connect=lambda **kwargs: self)

    async def enter(self):
        return self.connection

    async def close(self):
        self.closed = True


class RealtimeConversationTest(unittest.IsolatedAsyncioTestCase):
    async def test_late_connection_is_closed_after_owner_stops(self):
        # A provider may finish enter() after the operation was cancelled.
        started, release = asyncio.Event(), asyncio.Event()
        second = RealtimeConversation(
            ProviderConfig("https://example.invalid", "1", "token", "key", "model", "marin"),
            PlaybackOptions(), self.failures.append, "ru", 0.1,
        )
        await second.__aenter__()

        async def delayed_enter():
            started.set()
            await release.wait()
            return second._client.connection

        with patch.object(second._client, "enter", delayed_enter):
            opening = asyncio.create_task(second.start())
            await started.wait()
            await second.__aexit__(None, None, None)
            release.set()

            with self.assertRaisesRegex(RuntimeError, "closing"):
                await opening

        self.assertTrue(second._client.connection.closed)
        self.assertFalse(second._tasks)

    async def asyncSetUp(self):
        self.client = None
        self.failures = []

        def create_client(**kwargs):
            self.client = FakeOpenAI(**kwargs)
            return self.client

        self.patcher = patch("openai.AsyncOpenAI", create_client)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.voice = RealtimeConversation(
            ProviderConfig("https://example.invalid", "1", "token", "explicit-key",
                           "realtime-test", "marin", None),
            PlaybackOptions(soft_limit_seconds=PCM.frame_seconds,
                            hard_limit_seconds=PCM.frame_seconds * 2,
                            watchdog_seconds=0.01),
            self.failures.append, "ru", 0.1,
        )
        await self.voice.__aenter__()
        await self.voice.start()

    async def asyncTearDown(self):
        await self.voice.__aexit__(None, None, None)

    async def dispatch(self, event):
        await self.client.connection.events.put(SimpleNamespace(**event))
        for _ in range(100):
            if self.client.connection.events.empty():
                await asyncio.sleep(0)
                return
            await asyncio.sleep(0.001)
        self.fail("Realtime reader did not consume event")

    async def audio(self, payload=FRAME, response="response-1", item="item-1"):
        await self.dispatch(dict(
            type="response.output_audio.delta", response_id=response,
            item_id=item, content_index=0,
            delta=base64.b64encode(payload).decode("ascii"),
        ))

    async def test_session_uses_explicit_key_and_greets_only_once(self):
        self.assertEqual(self.client.api_key, "explicit-key")
        settings = self.client.connection.actions[0][1]["session"]
        self.assertEqual(settings["audio"]["output"]["voice"], "marin")
        self.assertEqual(settings["audio"]["input"]["format"]["rate"], PCM.rate)
        await self.voice.greet_once("Привет")
        await self.voice.greet_once("Ещё раз")
        self.assertEqual([action for action, _ in self.client.connection.actions].count("create"), 1)
        self.assertEqual(self.client.connection.actions[-1][1]["response"]["instructions"], "Привет")

    async def test_incoming_pcm_is_base64_encoded(self):
        await self.voice.write_audio(FRAME)
        self.assertEqual(self.client.connection.actions[-1],
                         ("append", {"audio": base64.b64encode(FRAME).decode("ascii")}))

    async def test_barge_in_truncates_to_audio_actually_heard(self):
        await self.dispatch(dict(type="response.created", response=SimpleNamespace(id="response-1")))
        await self.audio(FRAME * 2)
        self.assertEqual(self.voice.read_frame(), FRAME)
        await self.dispatch(dict(type="input_audio_buffer.speech_started"))
        self.assertEqual(self.voice.read_frame(), bytes(PCM.bytes_per_frame))
        actions = self.client.connection.actions
        self.assertEqual([name for name, _ in actions[-2:]], ["cancel", "truncate"])
        self.assertEqual(actions[-1][1], dict(item_id="item-1", content_index=0,
                                              audio_end_ms=PCM.frame_ms))

    async def test_replaced_track_drops_old_audio_and_new_response_plays(self):
        await self.dispatch(dict(type="response.created", response=SimpleNamespace(id="response-1")))
        await self.audio(FRAME * 2)
        self.assertEqual(self.voice.read_frame(), FRAME)
        self.voice.replace_track()
        for _ in range(100):
            if any(name == "truncate" for name, _ in self.client.connection.actions):
                break
            await asyncio.sleep(0.001)
        self.assertEqual(self.voice.read_frame(), bytes(PCM.bytes_per_frame))
        self.assertEqual(self.client.connection.actions[-1][1]["audio_end_ms"], PCM.frame_ms)
        await self.dispatch(dict(type="response.created", response=SimpleNamespace(id="response-2")))
        await self.audio(FRAME, response="response-2", item="item-2")
        self.assertEqual(self.voice.read_frame(), FRAME)

    async def test_hard_buffer_limit_drains_accepted_audio(self):
        """RED: cancellation must not mute two already accepted PCM frames."""
        await self.dispatch(dict(type="response.created", response=SimpleNamespace(id="response-1")))
        await self.audio(FRAME)
        await self.audio(FRAME)
        await self.audio(FRAME)
        self.assertIn("cancel", [name for name, _ in self.client.connection.actions])
        self.assertEqual(self.voice.read_frame(), FRAME)
        self.assertEqual(self.voice.read_frame(), FRAME)
        self.assertEqual(self.failures, [])

    async def test_provider_response_failure_is_reported(self):
        await self.dispatch(dict(type="response.created", response=SimpleNamespace(id="response-1")))
        await self.dispatch(dict(type="response.done", response=SimpleNamespace(
            id="response-1", status="failed")))
        for _ in range(100):
            if self.failures:
                break
            await asyncio.sleep(0.001)
        self.assertEqual(len(self.failures), 1)
        self.assertIsInstance(self.failures[0], MediaFailed)
        self.assertEqual(self.failures[0].error.code, "response_failed")

    async def test_finished_cancelled_response_releases_tracking(self):
        await self.dispatch(dict(type="response.created", response=SimpleNamespace(id="response-1")))
        await self.voice._cancel_responses()
        self.assertIn("response-1", self.voice._cancelled)
        await self.dispatch(dict(type="response.done", response=SimpleNamespace(
            id="response-1", status="cancelled")))
        self.assertNotIn("response-1", self.voice._cancelled)
