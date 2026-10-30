"""Realtime transport tests keep OpenAI external and exercise actual playback."""

import asyncio
import base64
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from whatsapp_chatbot_python.calls.models import (
    AudioLoss, CallOptions, MediaFailed, PCM, PlaybackOptions, ProviderConfig, VadOptions,
)
from whatsapp_chatbot_python.calls.openai import RealtimeConversation
from whatsapp_chatbot_python.calls.playback_state_machine import PlaybackController


FRAME = bytes([1, 2]) * PCM.samples_per_frame


class FakeConnection:
    def __init__(self):
        self.events = asyncio.Queue()
        self.actions = []
        self.closed = False
        self.session = SimpleNamespace(update=self.update)
        self.response = SimpleNamespace(create=self.create, cancel=self.cancel)
        self.input_audio_buffer = SimpleNamespace(append=self.append)
        self.conversation = SimpleNamespace(
            item=SimpleNamespace(truncate=self.truncate, delete=self.delete),
        )

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

    async def delete(self, **kwargs):
        self.actions.append(("delete", kwargs))

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
        self.audio_losses = []

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
            self._record_event, "ru", 0.1,
        )
        await self.voice.__aenter__()
        await self.voice.start()

    def _record_event(self, event):
        if isinstance(event, AudioLoss):
            self.audio_losses.append(event)
        else:
            self.failures.append(event)

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

    async def test_explicit_prompt_is_sent_unchanged_only_once(self):
        self.assertEqual(self.client.api_key, "explicit-key")
        settings = self.client.connection.actions[0][1]["session"]
        self.assertEqual(settings["audio"]["output"]["voice"], "marin")
        self.assertEqual(settings["audio"]["input"]["format"]["rate"], PCM.rate)
        self.assertEqual(settings["audio"]["input"]["noise_reduction"],
                         {"type": "near_field"})
        self.assertEqual(settings["audio"]["input"]["turn_detection"], {
            "type": "server_vad",
            "threshold": 0.70,
            "prefix_padding_ms": 400,
            "silence_duration_ms": 800,
            "create_response": True,
            "interrupt_response": True,
        })
        await self.voice.greet_once("Introduce yourself and ask how you can help.")
        await self.voice.greet_once("This prompt must be ignored.")
        self.assertEqual([action for action, _ in self.client.connection.actions].count("create"), 1)
        self.assertEqual(
            self.client.connection.actions[-1][1]["response"]["instructions"],
            "Introduce yourself and ask how you can help.",
        )

    async def test_custom_vad_settings_reach_realtime(self):
        vad = VadOptions(
            threshold=0.85, prefix_padding_ms=250,
            silence_duration_ms=1200, noise_reduction="far_field",
        )
        config = ProviderConfig("https://example.invalid", "1", "token", "key", "model", "marin")
        async with RealtimeConversation(
            config, PlaybackOptions(), self._record_event, "ru", 0.1, vad=vad,
        ) as voice:
            await voice.start()
            audio_input = voice._client.connection.actions[0][1]["session"]["audio"]["input"]

        self.assertEqual(audio_input["noise_reduction"], {"type": "far_field"})
        self.assertEqual(audio_input["turn_detection"], {
            "type": "server_vad",
            "threshold": 0.85,
            "prefix_padding_ms": 250,
            "silence_duration_ms": 1200,
            "create_response": True,
            "interrupt_response": True,
        })

    async def test_none_disables_noise_reduction(self):
        self.voice._vad = VadOptions(noise_reduction=None)
        self.assertIsNone(self.voice._session_settings()["audio"]["input"]["noise_reduction"])

    async def test_default_greeting_instruction_uses_language_name(self):
        await self.voice.greet_once(None)
        self.assertEqual(
            self.client.connection.actions[-1][1]["response"]["instructions"],
            "Greet the user briefly in Russian.",
        )

    async def test_adaptive_language_uses_switching_fallbacks(self):
        self.voice._language = "adaptive"
        settings = self.voice._session_settings()
        required_phrases = ("another language", "switch to", "in English")
        session_instructions = settings["instructions"].lower()
        for phrase in required_phrases:
            with self.subTest(target="session", phrase=phrase):
                self.assertIn(phrase.lower(), session_instructions)

        await self.voice.greet_once(None)
        greeting_instructions = self.client.connection.actions[-1][1]["response"]["instructions"].lower()
        for phrase in required_phrases:
            with self.subTest(target="greeting", phrase=phrase):
                self.assertIn(phrase.lower(), greeting_instructions)

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

    async def test_provider_cancel_error_after_speech_started_is_ignored(self):
        await self.dispatch(dict(type="response.created", response=SimpleNamespace(id="response-1")))
        await self.dispatch(dict(type="input_audio_buffer.speech_started"))
        cancel = next(data for name, data in self.client.connection.actions if name == "cancel")
        await self.dispatch(dict(type="error", error=SimpleNamespace(
            event_id=cancel["event_id"], code="response_cancel_failed", message="Already cancelled",
        )))
        self.assertEqual(self.failures, [])


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

    async def test_drop_chunks_policy_keeps_the_same_response_active(self):
        self.voice._playback = PlaybackController(PlaybackOptions(
            soft_limit_seconds=PCM.frame_seconds,
            hard_limit_seconds=PCM.frame_seconds * 2,
            watchdog_seconds=0.01,
            overflow_policy="drop_chunks",
        ))
        frames = [bytes([index, 0]) * PCM.samples_per_frame for index in range(4)]

        await self.dispatch(dict(type="response.created", response=SimpleNamespace(id="response-1")))
        await self.audio(frames[0] + frames[1])
        await self.audio(frames[2])
        self.assertEqual(self.voice.read_frame(), frames[0])
        await self.audio(frames[3])
        await self.dispatch(dict(type="response.output_audio.done", item_id="item-1",
                                 content_index=0))
        await self.dispatch(dict(type="response.done", response=SimpleNamespace(
            id="response-1", status="completed")))

        self.assertEqual([self.voice.read_frame(), self.voice.read_frame()],
                         [frames[1], frames[3]])
        self.assertEqual(self.voice.read_frame(), bytes(PCM.bytes_per_frame))
        self.assertNotIn("cancel", [name for name, _ in self.client.connection.actions])
        self.assertEqual(self.failures, [])
        self.assertEqual(len(self.audio_losses), 1)
        self.assertEqual(self.audio_losses[0].dropped_samples, PCM.samples_per_frame)

        for _ in range(100):
            if ("delete", {"item_id": "item-1"}) in self.client.connection.actions:
                break
            await asyncio.sleep(0.001)
        self.assertIn(("delete", {"item_id": "item-1"}),
                      self.client.connection.actions)

    async def test_repeated_drops_are_counted_for_one_response(self):
        self.voice._playback = PlaybackController(PlaybackOptions(
            soft_limit_seconds=PCM.frame_seconds,
            hard_limit_seconds=PCM.frame_seconds * 2,
            overflow_policy="drop_chunks",
        ))
        await self.dispatch(dict(type="response.created", response=SimpleNamespace(id="response-1")))
        await self.audio(FRAME * 2)
        await self.audio(FRAME)
        await self.audio(FRAME)
        await self.dispatch(dict(type="response.done", response=SimpleNamespace(
            id="response-1", status="completed")))

        self.assertEqual([self.voice.read_frame(), self.voice.read_frame()], [FRAME, FRAME])
        self.assertEqual(self.failures, [])
        self.assertEqual(len(self.audio_losses), 1)
        self.assertEqual(self.audio_losses[0].dropped_samples, 2 * PCM.samples_per_frame)

    async def test_response_done_does_not_discard_audio_awaiting_playback(self):
        await self.dispatch(dict(type="response.created", response=SimpleNamespace(id="response-1")))
        await self.audio(FRAME * 2)
        await self.dispatch(dict(type="response.output_audio.done", item_id="item-1",
                                 content_index=0))
        await self.dispatch(dict(type="response.done", response=SimpleNamespace(
            id="response-1", status="completed")))

        self.assertEqual([self.voice.read_frame(), self.voice.read_frame()], [FRAME, FRAME])
        self.assertEqual(self.voice.read_frame(), bytes(PCM.bytes_per_frame))
        self.assertNotIn("cancel", [name for name, _ in self.client.connection.actions])
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


class VadOptionsTest(unittest.TestCase):
    def test_invalid_vad_settings_are_rejected_before_call_start(self):
        invalid_settings = (
            {"threshold": -0.1}, {"threshold": 1.1},
            {"threshold": float("nan")}, {"threshold": float("inf")},
            {"threshold": True}, {"threshold": "0.7"},
            {"prefix_padding_ms": -1}, {"prefix_padding_ms": True},
            {"prefix_padding_ms": 0.5}, {"silence_duration_ms": 0},
            {"silence_duration_ms": True}, {"noise_reduction": "unknown"},
        )
        for settings in invalid_settings:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                VadOptions(**settings)

        with self.assertRaisesRegex(ValueError, "VAD options must be VadOptions"):
            CallOptions(vad=None)
