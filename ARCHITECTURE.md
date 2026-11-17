# Architecture — whatsapp-chatbot-python

This document describes the implementation. [AGENTS.md](AGENTS.md) is the agent entry point; [CONTRIBUTING.md](CONTRIBUTING.md) defines mandatory standards for new and changed code. Existing implementation details below are not exemptions from those standards.

---

## 1) Purpose and package boundary

This is a Python SDK for developers building WhatsApp applications through Green API. It wraps notification polling, synchronous filtered callbacks, replies and process-local sender state. An optional async subsystem executes voice calls using Green API signaling/media and OpenAI Realtime. It is not an HTTP server, persistent chatbot service, CLI, application UI or multi-process call coordinator.

`setup.py` defines distribution `whatsapp-chatbot-python`, import package `whatsapp_chatbot_python`, and version 1.3.0. Base metadata declares Python >=3.7; CI exercises 3.9–3.12. Support below that CI range is not established by those checks. `calls/__init__.py` explicitly rejects Python below 3.11; calls CI runs 3.11–3.13. Calls use Python 3.11 runtime features such as `asyncio.timeout` and exception notes.

Base installation requires `whatsapp-api-client-python>=0.0.53`. The `voip` extra pins SDK 0.0.55, OpenAI 3.20.0, aiortc 1.15.0 and PyAV 17.1.0, with websockets >=16,<17 and Python >=3.11 markers. `requirements.txt` is a development/example dependency list, not identical to distribution requirements. Public calls contracts can import without the optional media stack on supported Python; execution loads that stack lazily. Moving adapter imports into base exports changes installation compatibility.

## 2) Components and ownership

Paths below are relative to `whatsapp_chatbot_python/` unless qualified.

| Location / symbol | Responsibility and caller | Consequence of local changes |
| --- | --- | --- |
| `bot.py`: `Bot`, `GreenAPIBot` | Application constructs client; bot owns settings, startup purge, polling and receipt deletion | Initialization can mutate an account; callback failures affect receipt retention |
| `manager/router.py`: `Router` | Bot passes webhook bodies; maps event types to observers | Mapping changes alter which business callback sees an event |
| `manager/observer.py`: `_TypedObserver` | Ordered registration and notification construction | First-match semantics and shared state affect consumer workflows |
| `manager/handler.py`: `Notification`, `Handler`; `filters.py` | Fields/replies and synchronous filter/callback execution | Payload keys, inferred chat and filter semantics are API contracts |
| `manager/state.py`: `StateManager` | Per-observer default in-memory sender state | Restart loses state; changing sharing affects message/call coordination |
| `manager/incoming_call.py`: `IncomingCall` | Parses call notifications independently of media dependencies | Parsing must not implicitly accept or execute calls |
| `calls/models.py` | Sessions, options, events, protocols, errors and recording result | Shared contracts couple runner, adapters and consumers |
| `calls/state_machine.py`: `CallStateMachine` | Plans and commits session transitions when invoked by consumer callback | Scattered mutations would bypass terminal/timestamp rules |
| `calls/service.py`: `WhatsAppCallService` | Application facade and serialized admission | Lock and stop semantics determine which calls can execute |
| `calls/call_runner.py`: `CallRunner`, `Events`, `Operations`, `CallResources` | Per-call dispatch/deadlines, named tasks and resource scope | Event timing and cleanup affect final state and leaked work |
| `calls/greenapi.py`: `GreenApiCall` | SDK HTTP/RTC lifecycle and owned-call signaling | Incorrect identity checks can finish a different call |
| `calls/openai.py`: `RealtimeConversation` | Provider session, response events, cancellation and history truncation | Playback and provider history must agree about heard audio |
| `calls/playback_state_machine.py`: `PlaybackController` | Bounded bot-audio buffering and interruptions | Capacity/drop changes alter latency and audible output |
| `calls/audio.py`: `AudioBridge`, `RecordingTap` | PCM tracks, pacing, caller queue and aligned capture | Timing changes can corrupt playback or recording alignment |
| `calls/recorder.py`: `CallRecorder`, `PcmCapture`, `Mp3Encoder` | Temporary capture and worker-thread MP3 export | File transfer and abandoned exports need explicit cleanup |
| `tests/calls/fakes.py` | Fake signaling, RTC and voice boundaries | Tests prove local contracts, not live interoperability |

## 3) Flow: polling, dispatch and acknowledgment

**Entry and sequence.** Application constructs `GreenAPIBot` (a `Bot` subclass), registers callbacks, then calls `run_forever()`:

```text
constructor -> settings API -> optional startup purge -> Router creation
run_forever -> receiveNotification -> route_event(body)
            -> observer -> filters -> callback -> deleteNotification(receiptId)
```

**Initialization side effects.** A falsy `settings` value triggers `getSettings()`; if all three message webhook settings are disabled, `_update_settings()` enables them through `setSettings()`. A truthy dictionary goes directly to `setSettings(settings)`. Neither path is an offline constructor. Pending notifications are purged by default before callbacks can be registered; `bot_debug_mode=True` forces purge even if the caller disabled it.

**Output and failures.** A nonempty received response is routed before deletion. Empty response data immediately continues polling. If parsing, routing, a filter or callback raises, deletion is skipped. Normally the bot logs the error, sleeps five seconds and continues; `raise_errors=True` instead raises `GreenAPIBotError`. A callback can already have sent a reply before raising, so partial side effects and repeated processing must be considered. Exact server redelivery is external and not verified. This code does not establish exactly-once processing.

**Ownership and shutdown.** Bot owns receipt handling; callbacks own business effects. Handlers are synchronous and run in the polling thread. KeyboardInterrupt breaks the loop and changes the connection header to `close`; the raised-error exit does not pass through that normal tail. There is no durable local receipt journal or general async bot lifecycle.

**Contracts and evidence.** `body`, `receiptId`, `typeWebhook` and message payload fields come from Green API. `Notification.answer()` delegates to SDK sending using the inferred chat. `tests/test_manager.py` covers routing, filters and observers; it does not directly test polling acknowledgment/retry. A loop change needs fake receiving/sending tests proving order, callback failure, malformed data, partial effects and exit behavior. Moving deletion earlier can acknowledge unfinished work; changing purge affects queued user messages.

## 4) Flow: observer matching and incoming-call extraction

**Entry and sequence.** `Router.route_event` reads `typeWebhook`, selecting incoming/outgoing message, outgoing API message, outgoing status or incoming call observers. Unknown types have no matching observer; a missing key raises. `_TypedObserver` builds a notification and tries handlers in registration order. The first matching handler executes and ends propagation regardless of its callback return value. Button and poll convenience registrations delegate to message handlers. In the generic `Handler.check_event`, unknown filter names are skipped when absent from the filter registry; incoming-call registration instead rejects unsupported names. This asymmetry is current behavior, not a recommended validation policy. Tightening generic validation would be an intentional compatibility change, since a previously ignored key would begin raising.

**State and side effects.** `StateManager` stores sender state/data only in memory. `router.incoming_call` shares `router.message.state_manager`; outgoing observers have separate defaults. A state change in a message callback therefore affects incoming-call filtering for the same sender, but is not persisted across restarts.

**Call payload contract.** `IncomingCall.from_event` extracts optional `from`, `idMessage`, `status`, and timestamp fields. Plausible numeric seconds, milliseconds and microseconds normalize to Unix seconds in [2000-01-01, 2100-01-01). Booleans, nonfinite, out-of-range and nonnumeric timestamps become `None`. Unknown status strings remain available. `raw_event` retains the input dictionary rather than copying it; the frozen dataclass does not make that dictionary immutable. `chat_id` is the caller address.

**Failures and boundaries.** Incoming handlers allow only `status`, `from_sender`, `from_chat`, and `state`. Unsupported filter names and invalid status-filter values fail registration. Runtime callback failures propagate to the poll loop and prevent receipt deletion. Notification parsing never creates a call session, queue, deadline or executor. Application code owns the handoff to call work and offer deduplication.

**Evidence and change risk.** `tests/test_incoming_call_notifications.py` covers missing fields, normalized timestamps, unknown statuses, filtering, replies and shared state. Changing timestamp bounds or copying/sharing behavior is visible to consumers; adding implicit execution would turn parsing into a provider action and cross the optional-dependency boundary.

## 5) Flow: serialized call execution and state transitions

**Entry.** Application creates a `CallSession`, applies `ENQUEUED` then `DEQUEUED` through a `CallStateMachine`, and passes a transition callback to `WhatsAppCallService.execute`. The callback must actually apply transitions to the session. Service does not supply a hidden FSM or application queue. Outgoing sessions must start execution in `DIALING`; incoming sessions in `RINGING`, with call ID and monotonic offer deadline.

```text
application queue + FSM -> execute admission lock -> one CallRunner
  -> prepare voice -> open Green API RTC -> dial / accept
  -> answer + bridge ready -> IN_CALL -> greeting and talk deadline
  -> remote end / timeout / failure / cancellation -> cleanup -> result
```

**Admission and ownership.** The service lock spans execution, including cleanup; serialization is per service object, not across processes or independent objects. Consumers should use one service per instance. `request_stop()` permanently closes admission and signals an active runner. Finished sessions are not re-executed. `healthy` means `not self._stopped`, not provider reachability or proof of successful cleanup. Service construction validates/configures; provider resource acquisition is deferred until execution.

**Transition model.** `CallStateMachine._find_rule` selects rules by `(state, event)` and direction; `_commit` owns state/reason/error metadata and first-occurrence timestamps. Unsupported events return an ignored `StateTransition` without mutation. Terminal states have no outgoing rules. Repeated events follow the actual table: some are ignored, while applicable same-state rules can still record metadata. `applied` and `changed` are distinct properties.

`TRIGGERED -> QUEUED -> DIALING` is the outgoing start; incoming dequeue goes to `RINGING`. Remote acceptance alone moves toward `CONNECTING`; both acceptance and bridge readiness are necessary for `IN_CALL`. Ring/talk expiry enters `ENDING`, then confirmed hangup selects the terminal reason. Internal errors fail nonterminal sessions; stop/cancel events terminate them. Adapter I/O is outside transition planning. Altering one event rule can change greeting timing, recording activation and timeout selection together.

**Ordering and failures.** `Events` compares local monotonic observation times to `Deadlines`; one-shot deadlines win when observation is exactly at the boundary. UTC session timestamps are metadata, not timeout clocks. `Operations` runs named asynchronous commands and emits completion/failure events. Setup, command, ring, talk and shutdown waits are bounded by options. No automatic retry surrounds ambiguous dial/accept effects. Failed incoming accept is reconciled with provider state before classifying failure. Media failure ends the call; reported audio loss alone does not.

**Cancellation/output.** Cancellation initiates resource cleanup and propagates `CancelledError`; service also requests the shutdown transition. The runner confirms remote finish as applicable and returns a `CallExecutionResult`, possibly owning a recording. An unconfirmed finish can fail execution without permanently stopping the service.

**Evidence.** `tests/calls/test_call_state_machine.py`, `test_call_service.py`, `test_call_runtime.py` and `test_call_cleanup.py` cover transitions, admission, deadlines, cancellation and race handling through fakes. Any runtime change must test both event orders, exact deadline equality, ignored late events and terminal non-revival.

## 6) Flow: incoming offer expiry, caller departure and identity

**Entry and sequence.** Application converts a webhook into `CallSession.incoming(...)`, computes a monotonic deadline, queues and dequeues it. `CallRunner._validate_start` enforces ID/deadline/start state. An expired offer takes `INCOMING_EXPIRED` to `REJECTED`, attempts `GreenApiCall.reject_offer`, and returns an empty result. Queue and preparation time both consume the offer deadline.

**Identity and effects.** `GreenApiCall` checks owned SDK call state before accept/finish/reject. Public `reject_incoming(call_id, sender_id)` creates a temporary adapter and closes it in `finally`. `reject_offer` returns true only after matching an unanswered `inc-call` offer and issuing rejection. An unrelated call must not be rejected or hung up by cleanup. Identity fallback logic belongs in `_owns`, not in application-wide blind hangup calls.

**Failure and order.** Expired-offer rejection errors are logged and suppressed; a local rejected session is not proof of remote rejection. An accept error triggers state reconciliation because the remote side may already have accepted. A missing offer is classified as expiry or caller departure according to deadline. `notify_incoming_ended(call_id)` forwards only a matching active incoming call: departure before acceptance rejects with `CALLER_LEFT`, while departure after acceptance is remote end.

**Ownership and evidence.** Application owns deduplication/admission; service owns executor dispatch; temporary adapters own their HTTP/RTC resources. `test_call_service.py` and `test_greenapi.py` cover matching/unmatching IDs, expired offers, caller-left races and accept reconciliation. Changing error classification or ID checks can turn a recoverable ambiguous accept into an incorrect rejection or affect someone else's call. Fake outcomes do not certify provider behavior.

## 7) Flow: audio, interruption and recording

**Entry and sequence.** Once RTC is connected, `AudioBridge` receives caller frames and supplies bot frames. `RealtimeConversation` transfers PCM to/from OpenAI; generated audio passes through `PlaybackController`. At `IN_CALL`, recording activates and a greeting is prompted. PCM is signed little-endian PCM16, mono, 24 kHz.

**Buffers and interruptions.** Caller input is bounded locally; the default overflow policy drops oldest audio and reports loss, while `FAIL` exposes failure semantics. Playback capacity and overflow policy determine whether accepted audio drains before response cancellation or chunks are dropped. Caller speech discards unheard bot audio, cancels model responses and truncates provider history to heard audio. A playback-only change can therefore desynchronize provider context unless response tracking and truncation stay consistent.

**Recording/output.** `RecordingTap` aligns caller and bot samples on a shared timeline. `PcmCapture` holds temporary PCM, and `Mp3Encoder` exports on a worker thread. Capture/export errors generally disable/omit optional recording instead of ending the call. `CallExecutionResult` owns its temporary output until `take_recording()` transfers the path or `close()` deletes it. After transfer, the consumer owns retention/removal. Context exit logs removal failure, raises if there was no primary exception, and otherwise adds an exception note.

**Shutdown limits.** Resource cleanup stops producers/readers and releases media/recording through their owners. Coroutine cancellation cannot forcibly stop an encoder thread; abandoned export cleanup can delete its file when the worker finishes. Local queue bounds do not prove upstream SDK/RTC queues are bounded. The separate local calls note reports an upstream receive-queue concern, but upstream internals were not independently verified here.

**Evidence and risk.** `test_audio.py`, `test_playback_state_machine.py`, `test_realtime_voice.py` and `test_call_recording.py` cover pacing, overflow, interruption, truncation, timeline gaps, export and ownership. Format/timing changes can corrupt both live audio and MP3s. Test sample counts, silence gaps, queue limits and transfer/delete behavior, not only method calls.

## 8) Resource shutdown ownership

`CallResources` registers acquired resources in an async exit stack and cleans partial acquisition on `BaseException`. It first stops command production/operations; registered cleanup unwinds remote finish, Green API, audio and voice, then recording finalization runs in a `finally`. Each registered step has bounded handling and records stage-specific cleanup errors. `finish_cleanup` shields and drains a named cleanup task through repeated cancellation before re-propagating cancellation; this is stronger than a bare `finally: await close()`.

Green API owns SDK RTC/HTTP closure; voice owns Realtime connection/tasks; audio owns media tracks and readers; recorder owns capture/export files; `Operations` owns its command tasks. Pending timed-out cleanup is tracked and late results observed. An RTC timeout can leave closure unconfirmed; logs and `CleanupResult` must not be interpreted as guaranteed release. Cleanup errors may affect unfinished execution results, while existing primary failures retain cleanup context. Moving cleanup ownership without preserving acquisition order can leak partial resources or close media while producers still use it.

**Documentation drift:** the English README's claim that cleanup timeout makes `healthy=False` and rejects later calls conflicts with `service.py:healthy` and `CallRunner.log_cleanup_error`. Current source allows later attempts unless explicitly stopped. The local `docs/calls-architecture.md` note agrees with source; it is separate working material and must not be overwritten as part of unrelated changes.

## 9) Contract register

| Contract / source | Consumers and compatibility impact |
| --- | --- |
| Base exports in `__init__.py`; optional exports in `calls/__init__.py` | Import paths and optional-dependency isolation are public API |
| Route before receipt delete; first-match callbacks | Business handlers may repeat partial effects after failure; neither all-match nor exactly-once is promised |
| Incoming optional fields, timestamp units, original event identity | Call integrators depend on safe extraction without initiating media |
| FSM callback mutation and ignored transition results | Applications own queue/FSM; logging-only callbacks cannot execute the lifecycle correctly |
| Per-service lock, permanent stop, health flag | Integrators cannot assume distributed exclusion or a reachability probe |
| Monotonic event/deadline ordering | Equality is late; wall-clock conversion changes race behavior |
| Provider identity and normalized errors | SDK adapters own protocol details; blind hangup breaks call isolation |
| `ProviderConfig` and `ProviderError` | Credentials are hidden in config repr; error string omits raw detail, but detail remains explicitly accessible and sensitive |
| PCM and bounded local buffers | Media consumers depend on exact format, latency and loss semantics |
| Recording result ownership | Consumers must take or close results; transfer does not imply permanent storage |

## 10) Integration, customization and verification limits

Configure host/media URLs, settings and debug/purge options for messaging; use observers/filters and consumer callbacks for business behavior. Call configuration exposes API URL, credentials, model, voice, instructions, timing and audio/recording options. `CallExecutor` and `IncomingCallExecutor` are consumer-facing protocols, but `WhatsAppCallService` internally constructs provider adapters in `CallResources`; it does not accept arbitrary provider injection. Replacing providers requires a deliberate new seam/source change or application-owned executor implementation.

A branded application can use this SDK as a component. Branding, durable state, queue policy, deduplication, user experience and application shutdown remain consumer work. There is no packaged White Label product. The SDK uses the [MIT License](LICENSE); redistribution must preserve its copyright and permission notice.

CI commands and task-specific verification are in [CONTRIBUTING.md](CONTRIBUTING.md). Manager tests use local events; call tests replace provider boundaries with fakes, with audio/recording tests doing local media work. Passing them establishes local behavior only. Live Green API receipt redelivery, WhatsApp/OpenAI interoperability, account setup and upstream buffering guarantees are **not verified**. The bot polling loop has no dedicated acknowledgment test in the present suite. No live call or bot construction is needed to validate these documentation examples.
