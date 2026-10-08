import asyncio
import unittest
from unittest.mock import patch

from whatsapp_chatbot_python.calls.models import CallEndReason, CallEvent, CallState
from whatsapp_chatbot_python.calls.recording import CallRecorder

from . import test_call_service as fixtures


class CallCleanupTest(unittest.IsolatedAsyncioTestCase):
    make_session = fixtures.CallServiceTest.make_session
    make_service = fixtures.CallServiceTest.make_service

    def setUp(self):
        fixtures.CallServiceTest.setUp(self)
        self.recorders = []
        recorders = self.recorders

        class ObservedRecorder(CallRecorder):
            def __init__(self, logger):
                super().__init__(logger)
                self.finish_calls = 0
                self.finished_path = None
                recorders.append(self)

            def finish(self):
                self.finish_calls += 1
                self.finished_path = super().finish()
                return self.finished_path

        patcher = patch("whatsapp_chatbot_python.calls.service.CallRecorder", ObservedRecorder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_recorder_closed(self, service):
        recorder = self.recorders[-1]
        self.assertEqual(recorder.finish_calls, 1)
        self.assertTrue(recorder._caller.closed)
        self.assertTrue(recorder._bot.closed)
        if recorder._thread is not None:
            self.assertFalse(recorder._thread.is_alive())
        self.assertIsNone(service._active_loop)
        self.assertIsNone(service._active_runtime)

    async def test_execute_accepts_contract_keyword_arguments(self):
        session, transition = self.make_session()
        service = self.make_service()
        await service.execute(session=session, transition=transition)
        self.assertEqual(session.state, CallState.REMOTE_ENDED)
        self.assert_recorder_closed(service)

    async def test_startup_errors_finish_recorder_and_still_propagate(self):
        for stage in ("constructor", "start", "connect", "listeners"):
            with self.subTest(stage=stage):
                session, transition = self.make_session()
                service = self.make_service()
                if stage == "constructor":
                    target = "whatsapp_chatbot_python.calls.service.VoiceBotSession"
                elif stage == "start":
                    target = "tests.calls.test_call_service.FakeVoice.start"
                elif stage == "connect":
                    target = "tests.calls.test_call_service.FakeClient.connect"
                else:
                    target = "tests.calls.test_call_service.FakeCalls.on"

                with patch(target, side_effect=RuntimeError("startup failed")):
                    with self.assertRaisesRegex(RuntimeError, "startup failed"):
                        await service.execute(session, transition)
                self.assert_recorder_closed(service)

    async def cancel_at_stage(self, stage, *, repeat=False, hangup_fails=False):
        fixtures.FakeClient.answer = stage != "ring"
        fixtures.FakeClient.remote_hangup = stage == "cleanup"
        session, apply = self.make_session()
        service = self.make_service(ring=5, talk=5)
        reached = asyncio.Event()
        close_entered = asyncio.Event()
        release_close = asyncio.Event()
        cancel_seen = asyncio.Event()
        if stage != "cleanup" and not repeat:
            release_close.set()

        start = fixtures.FakeVoice.start
        open_connection = fixtures.FakeCalls.openAsync
        dial = fixtures.FakeClient.dialAsync
        bridge = fixtures.FakeCalls.startAudioAsync
        greet = fixtures.FakeVoice.greet_once
        close = fixtures.FakeCalls.closeAsync
        hangup = fixtures.FakeClient.hangUpAsync

        async def pause(name):
            if stage == name:
                reached.set()
                await asyncio.Event().wait()

        async def voice_start(voice):
            await start(voice)
            await pause("start")

        async def calls_open(calls, **options):
            await open_connection(calls, **options)
            await pause("open")

        async def calls_dial(client, target):
            await dial(client, target)
            await pause("dial")

        async def calls_bridge(calls):
            await pause("bridge")
            await bridge(calls)

        async def voice_greet(voice):
            await greet(voice)
            # Exercise the real recording thread and MP3 encoding on cancellation.
            recorder = voice.options["recorder"]
            recorder.set_generation(1)
            recorder.add_bot(b"\x01\x00" * 480, 1)
            await pause("talk")

        async def calls_close(calls):
            close_entered.set()
            if stage == "cleanup":
                reached.set()
            await release_close.wait()
            await close(calls)

        async def calls_hangup(client):
            await hangup(client)
            if hangup_fails:
                raise RuntimeError("hangup failed")

        def transition(call_session, event, **options):
            result = apply(call_session, event, **options)
            if stage == "ring" and event == CallEvent.DIAL_ACCEPTED:
                reached.set()
            if event == CallEvent.SHUTDOWN_REQUESTED:
                cancel_seen.set()
            return result

        with (
            patch.object(fixtures.FakeVoice, "start", voice_start),
            patch.object(fixtures.FakeCalls, "openAsync", calls_open),
            patch.object(fixtures.FakeClient, "dialAsync", calls_dial),
            patch.object(fixtures.FakeCalls, "startAudioAsync", calls_bridge),
            patch.object(fixtures.FakeVoice, "greet_once", voice_greet),
            patch.object(fixtures.FakeCalls, "closeAsync", calls_close),
            patch.object(fixtures.FakeClient, "hangUpAsync", calls_hangup),
        ):
            task = asyncio.create_task(service.execute(session, transition))
            try:
                await asyncio.wait_for(reached.wait(), timeout=1)
                task.cancel("caller cancelled")
                await asyncio.wait_for(cancel_seen.wait(), timeout=1)
                if repeat:
                    await asyncio.wait_for(close_entered.wait(), timeout=1)
                    task.cancel("cancelled again")
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                release_close.set()
                with self.assertRaises(asyncio.CancelledError) as raised:
                    await asyncio.wait_for(task, timeout=1)
                self.assertEqual(raised.exception.args, ("caller cancelled",))
            finally:
                release_close.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        self.assert_recorder_closed(service)
        recorder = self.recorders[-1]
        if stage in {"talk", "cleanup"}:
            self.assertIsNotNone(recorder.finished_path)
        if recorder.finished_path is not None:
            self.assertFalse(recorder.finished_path.exists())
        if stage == "cleanup":
            self.assertEqual(session.state, CallState.REMOTE_ENDED)
        else:
            self.assertEqual(session.end_reason, CallEndReason.SHUTDOWN)
            self.assertEqual(session.error_code, "cancelled")
        self.assertTrue(fixtures.FakeVoice.instances[-1].closed)
        if stage != "start":
            client = fixtures.FakeClient.instances[-1]
            self.assertTrue(client.calls.closed)
            self.assertEqual(client.actions.count("hangup"), int(stage != "open"))
            voice = fixtures.FakeVoice.instances[-1]
            self.assertTrue(all(track.stopped for track in getattr(voice, "tracks", [])))
            self.assertTrue(all(sink.closed for sink in getattr(voice, "sinks", [])))

    async def test_cancellation_finishes_recording_at_every_stage(self):
        for stage in ("start", "open", "dial", "ring", "bridge", "talk", "cleanup"):
            with self.subTest(stage=stage):
                await self.cancel_at_stage(stage)

    async def test_repeated_cancel_waits_for_cleanup(self):
        await self.cancel_at_stage("talk", repeat=True)

    async def test_hangup_error_does_not_replace_cancellation(self):
        await self.cancel_at_stage("talk", hangup_fails=True)

    async def test_normal_success_still_returns_recording(self):
        greet = fixtures.FakeVoice.greet_once

        async def record_audio(voice):
            await greet(voice)
            recorder = voice.options["recorder"]
            recorder.set_generation(1)
            recorder.add_bot(b"\x01\x00" * 480, 1)

        for remote_hangup in (True, False):
            with self.subTest(remote_hangup=remote_hangup):
                fixtures.FakeClient.remote_hangup = remote_hangup
                session, transition = self.make_session()
                service = self.make_service(talk=0.02)
                with patch.object(fixtures.FakeVoice, "greet_once", record_audio):
                    result = await service.execute(session, transition)
                self.assert_recorder_closed(service)
                self.assertEqual(session.state, CallState.REMOTE_ENDED if remote_hangup else CallState.TALK_TIMEOUT)
                self.assertIsNotNone(result.recording_path)
                try:
                    self.assertTrue(result.recording_path.exists())
                finally:
                    result.recording_path.unlink(missing_ok=True)

    async def test_close_failures_preserve_cleanup_order(self):
        for failing_resource in ("calls", "voice"):
            with self.subTest(failing_resource=failing_resource):
                session, transition = self.make_session()
                service = self.make_service()
                actions = []
                calls_close = fixtures.FakeCalls.closeAsync
                voice_close = fixtures.FakeVoice.close

                async def close_calls(calls):
                    actions.append("calls")
                    await calls_close(calls)
                    if failing_resource == "calls":
                        raise RuntimeError("RTC close failed")

                async def close_voice(voice):
                    actions.append("voice")
                    await voice_close(voice)
                    if failing_resource == "voice":
                        raise RuntimeError("voice close failed")

                finish = CallRecorder.finish

                def finish_recording(recorder):
                    actions.append("recording")
                    return finish(recorder)

                with (
                    patch.object(fixtures.FakeCalls, "closeAsync", close_calls),
                    patch.object(fixtures.FakeVoice, "close", close_voice),
                    patch.object(CallRecorder, "finish", finish_recording),
                ):
                    await service.execute(session, transition)
                self.assertEqual(actions, ["calls", "voice", "recording"])
                self.assertEqual(session.state, CallState.REMOTE_ENDED)
                self.assert_recorder_closed(service)

    async def test_failed_error_transition_still_finishes_recorder(self):
        fixtures.FakeClient.open_error = True
        session, apply = self.make_session()
        service = self.make_service()

        def transition(call_session, event, **options):
            if event == CallEvent.INTERNAL_ERROR:
                raise ValueError("error callback failed")
            return apply(call_session, event, **options)

        with self.assertRaisesRegex(ValueError, "error callback failed"):
            await service.execute(session, transition)
        self.assert_recorder_closed(service)
        self.assertTrue(fixtures.FakeClient.instances[-1].calls.closed)
        self.assertTrue(fixtures.FakeVoice.instances[-1].closed)

    async def test_failed_cancellation_transition_preserves_original_cancellation(self):
        session, apply = self.make_session()
        service = self.make_service()
        cancellation = asyncio.CancelledError("original cancellation")

        def transition(call_session, event, **options):
            if event == CallEvent.SHUTDOWN_REQUESTED:
                raise ValueError("shutdown callback failed")
            return apply(call_session, event, **options)

        with patch.object(fixtures.FakeVoice, "start", side_effect=cancellation):
            with self.assertRaises(asyncio.CancelledError) as raised:
                await service.execute(session, transition)
        self.assertIs(raised.exception, cancellation)
        self.assert_recorder_closed(service)
        self.assertTrue(fixtures.FakeVoice.instances[-1].closed)

    async def test_sink_creation_failure_stops_output_track(self):
        session, transition = self.make_session()
        service = self.make_service()
        with patch.object(fixtures.FakeVoice, "new_input_sink", side_effect=RuntimeError("sink failed")):
            await service.execute(session, transition)
        voice = fixtures.FakeVoice.instances[-1]
        self.assertTrue(voice.tracks[0].stopped)
        self.assertEqual(session.state, CallState.FAILED)
        self.assertEqual(session.error_code, "RuntimeError")
        self.assert_recorder_closed(service)

    async def test_sink_close_failure_stops_track_and_closes_voice(self):
        session, transition = self.make_session()
        service = self.make_service()
        with patch.object(fixtures.FakeSink, "close", side_effect=RuntimeError("sink close failed")):
            await service.execute(session, transition)
        voice = fixtures.FakeVoice.instances[-1]
        self.assertTrue(voice.tracks[0].stopped)
        self.assertTrue(voice.closed)
        self.assertEqual(session.state, CallState.REMOTE_ENDED)
        self.assert_recorder_closed(service)
