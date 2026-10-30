"""Deterministic external boundaries for call integration specifications."""

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

from whatsapp_chatbot_python.calls.models import PCM


@dataclass
class Scenario:
    state: str = "idle"
    call_id: str = "call-1"
    wid: str = "sender@c.us"
    answer: bool = True
    remote_hangup: bool = True
    accept_error: bool = False
    open_error: bool = False
    bridge_error: bool = False
    voice_error: bool = False
    gates: dict[str, asyncio.Event] = field(default_factory=dict)
    actions: list = field(default_factory=list)

    def info(self):
        return SimpleNamespace(id=self.call_id, wid=self.wid)

    async def wait(self, name):
        if gate := self.gates.get(name):
            await gate.wait()


class FakeRtc:
    def __init__(self, api, audio_factory):
        self.api = api
        self.audio_factory = audio_factory
        self.listeners = {}
        self.audio = None
        self.closed = False

    def on(self, name, callback):
        self.listeners[name] = callback

    def emit(self, name, detail=None):
        if name == "state":
            self.api.state = detail.state
            self.api.reason = getattr(detail, "reason", None)
        callback = self.listeners.get(name)
        if callback:
            callback(detail)

    def set_state(self, state, reason=None, *, call_id=None):
        info = None if state == "idle" else SimpleNamespace(
            id=call_id or self.api.scenario.call_id, wid=self.api.scenario.wid)
        self.emit("state", SimpleNamespace(state=state, reason=reason, info=info))

    async def openAsync(self, *, timeout):
        self.api.scenario.actions.append("open")
        await self.api.scenario.wait("open")
        if self.api.scenario.open_error:
            raise RuntimeError("RTC unavailable")
        self.set_state(self.api.state)

    async def startAudioAsync(self):
        self.api.scenario.actions.append("bridge")
        await self.api.scenario.wait("bridge")
        self.audio = await self.audio_factory()
        if self.api.scenario.bridge_error:
            raise RuntimeError("bridge unavailable")
        if self.api.scenario.remote_hangup:
            asyncio.get_running_loop().call_later(
                0.01, self.set_state, "idle", "hangup")

    async def closeAsync(self):
        self.api.scenario.actions.append("rtc_close")
        await self.api.scenario.wait("rtc_close")
        if self.audio is not None:
            await self.audio.close()
        self.closed = True


class FakeApi:
    def __init__(self, scenario, id_instance, api_token_instance, *, host, raise_errors, host_timeout):
        self.scenario = scenario
        self.credentials = (id_instance, api_token_instance, host)
        self.state = scenario.state
        self.reason = None
        self.rtc = None
        self.session_closed = False
        self.session = SimpleNamespace(close=self.close_session)
        self.voip = self

    def close_session(self):
        self.scenario.actions.append("session_close")
        self.session_closed = True

    def connect(self, *, audio_factory):
        self.rtc = FakeRtc(self, audio_factory)
        return self.rtc

    async def getStateAsync(self):
        self.scenario.actions.append("get_state")
        info = None if self.state == "idle" else self.scenario.info()
        return SimpleNamespace(state=self.state, info=info, reason=self.reason)

    async def dialAsync(self, target):
        self.scenario.actions.append(("dial", target))
        await self.scenario.wait("dial")
        self.rtc.set_state("out-call")
        if self.scenario.answer:
            asyncio.get_running_loop().call_soon(self.rtc.set_state, "on-call")

    async def acceptAsync(self):
        self.scenario.actions.append("accept")
        await self.scenario.wait("accept")
        if self.scenario.accept_error:
            raise RuntimeError("accept unavailable")
        self.rtc.set_state("on-call")

    async def rejectAsync(self):
        self.scenario.actions.append("reject")
        self.state = "idle"

    async def hangUpAsync(self):
        self.scenario.actions.append("hangup")
        self.state = "idle"


class FakeVoice:
    def __init__(self, scenario, *args, vad=None):
        self.scenario = scenario
        self.vad = vad
        self.greetings = []
        self.closed = False
        self.started = False

    async def __aenter__(self):
        self.scenario.actions.append("voice_enter")
        return self

    async def __aexit__(self, *exc):
        self.scenario.actions.append("voice_close")
        self.closed = True

    async def start(self):
        self.scenario.actions.append("voice_start")
        await self.scenario.wait("voice_start")
        if self.scenario.voice_error:
            raise RuntimeError("voice unavailable")
        self.started = True

    async def greet_once(self, prompt):
        if not self.greetings:
            self.greetings.append(prompt)
            self.scenario.actions.append("greet")

    def read_frame(self):
        return bytes(PCM.bytes_per_frame)

    def replace_track(self):
        self.scenario.actions.append("replace_track")

    async def write_audio(self, data):
        self.scenario.actions.append(("caller_audio", len(data)))
