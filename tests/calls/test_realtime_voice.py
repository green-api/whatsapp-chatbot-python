from fractions import Fraction
from types import SimpleNamespace
from unittest.mock import ANY, patch
from whatsapp_chatbot_python.calls.playback_state_machine import PlaybackState
import asyncio
import base64
import unittest
import av

from whatsapp_chatbot_python.calls.realtime_voice import (
    BotOutputTrack,
    CallerAudioSink,
    VoiceBotSession,
    FRAME_BYTES,
    FRAME_DURATION_MS,
    HARD_PLAYBACK_BUFFER_MS,
    SOFT_PLAYBACK_BUFFER_MS,
    RECOVER_PLAYBACK_BUFFER_MS,
)


class FakeConnection:
    def __init__(self):
        self.events = asyncio.Queue()
        self.actions = []
        self.session = SimpleNamespace(update=self.update)
        self.response = SimpleNamespace(create=self.create, cancel=self.cancel)
        self.input_audio_buffer = SimpleNamespace(append=self.append)
        self.conversation = SimpleNamespace(item=SimpleNamespace(truncate=self.truncate))

    async def update(self, **kwargs):
        self.actions.append(("update", kwargs))
        await self.events.put(SimpleNamespace(type="session.updated"))

    async def create(self, **kwargs):
        self.actions.append(("greet", kwargs))

    async def cancel(self, **kwargs):
        self.actions.append(("cancel", kwargs))

    async def append(self, **kwargs):
        self.actions.append(("append", kwargs))

    async def truncate(self, **kwargs):
        self.actions.append(("truncate", kwargs))

    async def recv(self):
        return await self.events.get()

    async def __aiter__(self):
        while True:
            yield await self.recv()


class FakeManager:
    def __init__(self, conn):
        self.conn = conn
        self.closed = False

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *args):
        self.closed = True


class FakeOpenAI:
    def __init__(self, api_key):
        self.api_key = api_key
        self.connection = FakeConnection()
        self.manager = FakeManager(self.connection)
        self.realtime = SimpleNamespace(connect=lambda **kwargs: self.manager)
        self.closed = False

    async def close(self):
        self.closed = True


class OneFrameTrack:
    kind = "audio"

    def __init__(self):
        self.sent = False

    async def recv(self):
        if self.sent:
            await asyncio.Future()

        self.sent = True
        frame = av.AudioFrame(format="s16", layout="mono", samples=480)
        frame.planes[0].update(bytes(FRAME_BYTES))
        frame.sample_rate = 24_000
        frame.pts = 0
        frame.time_base = Fraction(1, 24_000)

        return frame


class VoiceSessionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = None

        def make_client(**kwargs):
            self.client = FakeOpenAI(**kwargs)
            return self.client

        patcher = patch("whatsapp_chatbot_python.calls.realtime_voice.AsyncOpenAI", make_client)

        patcher.start()
        self.addCleanup(patcher.stop)
        self.failures = []

        self.voice = VoiceBotSession(
            api_key="test", model="gpt-realtime-2.1", voice="marin",
            language="ru", on_error=self.failures.append,
        )

    async def asyncTearDown(self):
        await self.voice.close()

    async def test_session_configuration_and_single_greeting(self):
        await self.voice.start()
        await self.voice.greet_once()
        await self.voice.greet_once()

        actions = self.client.connection.actions

        self.assertEqual([a for a, _ in actions], ["update", "greet"])
        self.assertEqual(actions[0][1]["session"]["audio"]["input"]["format"]["rate"], 24_000)

    async def test_supported_languages_configure_voice_and_greeting(self):
        for code, language in {
            "ru": "Russian", "en": "English", "he": "Hebrew",
            "es": "Spanish", "kz": "Kazakh",
        }.items():
            with self.subTest(code=code):
                voice = VoiceBotSession(
                    api_key="test", model="gpt-realtime-2.1", voice="marin",
                    language=code, on_error=self.failures.append,
                )

                try:
                    await voice.start()
                    await voice.greet_once()

                    actions = self.client.connection.actions

                    self.assertIn(language, actions[0][1]["session"]["instructions"])
                    self.assertIn(language, actions[1][1]["response"]["instructions"])
                finally:
                    await voice.close()

    async def test_audio_frames_are_paced_and_timestamped(self):
        await self.voice.start()

        track = await self.voice.new_output_track()

        self.voice._queue_audio("item-1", bytes([1, 2]) * 480)

        first = await track.recv()
        second = await track.recv()

        self.assertEqual(first.pts, 0)
        self.assertEqual(second.pts, 480)
        self.assertEqual(bytes(first.planes[0])[:FRAME_BYTES], bytes([1, 2]) * 480)
        self.assertEqual(bytes(second.planes[0])[:FRAME_BYTES], bytes(FRAME_BYTES))
        self.assertEqual(track._voice._played["item-1"], 480)

    async def test_barge_in_clears_audio_and_truncates_played_part(self):
        await self.voice.start()

        track = await self.voice.new_output_track()

        self.voice._active_responses.add("response-1")
        self.voice._item_response["item-1"] = "response-1"
        self.voice._queue_audio("item-1", bytes(FRAME_BYTES * 3))
        await track.recv()
        await self.voice._interrupt(cancel=True)
        self.assertEqual(len(self.voice._frames), 0)

        self.assertIn(("cancel", {"response_id": "response-1", "event_id": ANY}),
                      self.client.connection.actions)
        self.assertIn(("truncate", {
            "item_id": "item-1", "content_index": 0, "audio_end_ms": 20,
        }), self.client.connection.actions)

        self.voice._queue_audio("item-1", bytes(FRAME_BYTES))
        self.assertEqual(len(self.voice._frames), 0)

    async def test_backlog_recovers_without_cancelling_generation(self):
        await self.voice.start()
        track = await self.voice.new_output_track()
        self.voice._queue_audio("item-1", bytes(
            FRAME_BYTES * (SOFT_PLAYBACK_BUFFER_MS // FRAME_DURATION_MS)
        ))
        self.assertEqual(self.voice.playback_state, PlaybackState.BACKLOG)

        while len(self.voice._frames) * FRAME_DURATION_MS > RECOVER_PLAYBACK_BUFFER_MS:
            self.voice.take_frame(track._generation)

        self.assertEqual(self.voice.playback_state, PlaybackState.PLAYING)
        self.assertFalse(any(action == "cancel" for action, _ in self.client.connection.actions))

    async def test_full_buffer_drains_accepted_audio_before_truncating(self):
        await self.voice.start()

        old = await self.voice.new_output_track()
        await self.client.connection.events.put(SimpleNamespace(
            type="response.created", response=SimpleNamespace(id="response-1")
        ))
        await self.client.connection.events.put(SimpleNamespace(
            type="response.output_audio.delta", response_id="response-1",
            item_id="item-1", content_index=0,
            delta=base64.b64encode(
                bytes([1, 2]) * (FRAME_BYTES // 2)
                * (HARD_PLAYBACK_BUFFER_MS // FRAME_DURATION_MS + 1)
            ).decode(),
        ))

        for _ in range(20):
            if any(action == "cancel" for action, _ in self.client.connection.actions):
                break
            await asyncio.sleep(0.01)

        actions = self.client.connection.actions
        self.assertEqual(actions[-1][0], "cancel")
        self.assertEqual(actions[-1][1]["response_id"], "response-1")
        self.assertEqual(self.voice.playback_state, PlaybackState.DRAINING)
        self.assertEqual(len(self.voice._frames) * FRAME_DURATION_MS, HARD_PLAYBACK_BUFFER_MS)
        self.assertEqual(self.failures, [])

        await self.client.connection.events.put(SimpleNamespace(
            type="response.output_audio.delta", response_id="response-1",
            item_id="item-1", content_index=0,
            delta=base64.b64encode(bytes(FRAME_BYTES)).decode(),
        ))
        await asyncio.sleep(0)
        self.assertEqual(len(self.voice._frames) * FRAME_DURATION_MS, HARD_PLAYBACK_BUFFER_MS)

        for _ in range(HARD_PLAYBACK_BUFFER_MS // FRAME_DURATION_MS):
            self.assertEqual(self.voice.take_frame(old._generation), bytes([1, 2]) * 480)

        for _ in range(20):
            if any(action == "truncate" for action, _ in self.client.connection.actions):
                break
            await asyncio.sleep(0.01)

        self.assertEqual(self.voice.playback_state, PlaybackState.PLAYING)
        self.assertEqual(self.client.connection.actions[-1], ("truncate", {
            "item_id": "item-1", "content_index": 0,
            "audio_end_ms": HARD_PLAYBACK_BUFFER_MS,
        }))

        new = await self.voice.new_output_track()

        self.assertIsInstance(new, BotOutputTrack)
        self.assertEqual(self.voice.take_frame(old._generation), bytes(FRAME_BYTES))
        self.assertEqual(len(self.voice._frames), 0)

        await self.client.connection.events.put(SimpleNamespace(
            type="response.created", response=SimpleNamespace(id="response-2")
        ))
        await self.client.connection.events.put(SimpleNamespace(
            type="response.output_audio.delta", response_id="response-2",
            item_id="item-2", content_index=0,
            delta=base64.b64encode(bytes([1, 2]) * 480).decode(),
        ))
        for _ in range(20):
            if self.voice._frames:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(bytes((await new.recv()).planes[0])[:FRAME_BYTES], bytes([1, 2]) * 480)
        self.assertEqual(self.failures, [])

    async def test_barge_in_during_drain_discards_pending_audio(self):
        await self.voice.start()
        track = await self.voice.new_output_track()
        await self.client.connection.events.put(SimpleNamespace(
            type="response.created", response=SimpleNamespace(id="response-1")
        ))
        await self.client.connection.events.put(SimpleNamespace(
            type="response.output_audio.delta", response_id="response-1",
            item_id="item-1", content_index=0,
            delta=base64.b64encode(bytes(
                FRAME_BYTES * (HARD_PLAYBACK_BUFFER_MS // FRAME_DURATION_MS + 1)
            )).decode(),
        ))
        for _ in range(20):
            if self.voice.playback_state == PlaybackState.DRAINING:
                break
            await asyncio.sleep(0.01)

        self.voice.take_frame(track._generation)
        await self.client.connection.events.put(SimpleNamespace(
            type="input_audio_buffer.speech_started"
        ))
        for _ in range(20):
            if any(action == "truncate" for action, _ in self.client.connection.actions):
                break
            await asyncio.sleep(0.01)

        self.assertEqual(self.voice.playback_state, PlaybackState.PLAYING)
        self.assertEqual(len(self.voice._frames), 0)
        self.assertEqual(self.client.connection.actions[-1][1]["audio_end_ms"], FRAME_DURATION_MS)
        self.assertEqual(self.failures, [])

    async def test_track_replacement_truncates_old_playback(self):
        await self.voice.start()
        old = await self.voice.new_output_track()
        await self.client.connection.events.put(SimpleNamespace(
            type="response.created", response=SimpleNamespace(id="response-1")
        ))
        await self.client.connection.events.put(SimpleNamespace(
            type="response.output_audio.delta", response_id="response-1",
            item_id="item-1", content_index=0,
            delta=base64.b64encode(bytes(FRAME_BYTES * 3)).decode(),
        ))
        for _ in range(20):
            if self.voice._frames:
                break
            await asyncio.sleep(0.01)

        self.voice.take_frame(old._generation)
        new = await self.voice.new_output_track()

        self.assertEqual(self.voice.take_frame(old._generation), bytes(FRAME_BYTES))
        self.assertEqual(len(self.voice._frames), 0)
        self.assertEqual(self.voice.playback_state, PlaybackState.PLAYING)
        self.assertIn(("truncate", {
            "item_id": "item-1", "content_index": 0,
            "audio_end_ms": FRAME_DURATION_MS,
        }), self.client.connection.actions)
        self.assertIsInstance(new, BotOutputTrack)
        self.assertEqual(self.failures, [])

    async def test_stalled_consumer_is_recovered_without_failing_call(self):
        await self.voice.start()
        track = await self.voice.new_output_track()
        await self.client.connection.events.put(SimpleNamespace(
            type="response.created", response=SimpleNamespace(id="response-1")
        ))
        await self.client.connection.events.put(SimpleNamespace(
            type="response.output_audio.delta", response_id="response-1",
            item_id="item-1", content_index=0,
            delta=base64.b64encode(bytes(FRAME_BYTES * 200)).decode(),
        ))

        # Advance only the playback clock; keep the production watchdog interval intact.
        for _ in range(20):
            if self.voice._buffered_since is not None:
                break
            await asyncio.sleep(0.01)
        self.assertIsNotNone(self.voice._buffered_since)
        self.voice._buffered_since -= SOFT_PLAYBACK_BUFFER_MS / 1000 + 1
        await asyncio.sleep(0.55)

        self.assertEqual([action for action, _ in self.client.connection.actions[-2:]],
                         ["cancel", "truncate"])
        self.assertEqual(len(self.voice._frames), 0)
        self.assertEqual(self.voice.playback_state, PlaybackState.SUSPENDED)
        self.assertEqual(self.voice.take_frame(track._generation), bytes(FRAME_BYTES))
        self.assertEqual(self.voice.playback_state, PlaybackState.PLAYING)
        self.assertEqual(self.failures, [])

    async def test_inbound_audio_is_sent_as_base64_pcm(self):
        await self.voice.start()
        await self.voice.new_output_track()

        sink = CallerAudioSink(self.voice, self.voice.generation)

        await sink.attach(OneFrameTrack())

        for _ in range(20):
            if any(action == "append" for action, _ in self.client.connection.actions):
                break

            await asyncio.sleep(0.01)

        await sink.close()

        appended = [data for action, data in self.client.connection.actions if action == "append"]

        self.assertTrue(appended)
        self.assertEqual(len(base64.b64decode(appended[0]["audio"])), FRAME_BYTES)
        self.assertEqual(self.failures, [])

    async def test_recording_taps_tracks_and_ignores_old_bridge(self):
        class Recorder:
            def __init__(self):
                self.generation = 0
                self.caller = []
                self.bot = []

            def set_generation(self, generation):
                self.generation = generation

            def add_caller(self, pcm, generation):
                if generation == self.generation:
                    self.caller.append(pcm)

            def add_bot(self, pcm, generation):
                if generation == self.generation:
                    self.bot.append(pcm)

        recorder = Recorder()

        voice = VoiceBotSession(
            api_key="test",
            model="gpt-realtime-2.1",
            voice="marin",
            language="ru",
            on_error=self.failures.append,
            recorder=recorder,
        )

        try:
            await voice.start()

            old_track = await voice.new_output_track()
            sink = voice.new_input_sink()

            await sink.attach(OneFrameTrack())

            for _ in range(20):
                if recorder.caller:
                    break

                await asyncio.sleep(0.01)

            await sink.close()
            voice._queue_audio("item-1", bytes([1, 2]) * 480)
            await old_track.recv()
            self.assertEqual(recorder.caller, [bytes(FRAME_BYTES)])
            self.assertEqual(recorder.bot, [bytes([1, 2]) * 480])
            await voice.new_output_track()
            await old_track.recv()
            self.assertEqual(len(recorder.bot), 1)
        finally:
            await voice.close()

    async def test_failed_realtime_response_reports_call_error(self):
        await self.voice.start()

        await self.client.connection.events.put(SimpleNamespace(
            type="response.done", response=SimpleNamespace(status="failed")
        ))

        for _ in range(20):
            if self.failures:
                break

            await asyncio.sleep(0.01)

        self.assertEqual(len(self.failures), 1)
        self.assertIsInstance(self.failures[0], RuntimeError)
