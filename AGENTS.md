# whatsapp-chatbot-python

Instructions for AI agents working with the repository.

---

## 0) Quick facts about this repository

This Python library helps application developers receive and route WhatsApp notifications and reply through Green API. Its optional async calls package executes voice calls using Green API and OpenAI Realtime. It is an SDK, not a deployed chatbot product, HTTP service, persistent queue or customer UI.

- Distribution: `whatsapp-chatbot-python`; import: `whatsapp_chatbot_python`; manifest: `setup.py`.
- Base metadata declares Python >=3.7, but base CI tests 3.9–3.12. Do not claim CI proves 3.7 support. Calls require Python >=3.11; calls CI tests 3.11–3.13.
- `Bot`/`GreenAPIBot` in `bot.py` own synchronous polling. `Router` owns observer selection. `WhatsAppCallService` owns serialized call admission; the application supplies the queue and FSM callback.
- Green API provides account/API/RTC behavior. OpenAI provides Realtime conversation; media dependencies belong to the optional `voip` extra. Base imports must remain independent of that stack.
- Source, manifests and tests determine current behavior; README claims can lag. `.github/workflows/python-package.yml` and `ruff.toml` define actual checks.
- **Open [ARCHITECTURE.md](ARCHITECTURE.md)** before changing routing, call state, timing, adapters, media or resource ownership, or answering integration/lifecycle questions that depend on them.
- **Open [CONTRIBUTING.md](CONTRIBUTING.md)** before any code change or review, and for test setup, examples of corporate rules or compatibility exceptions. Links do not imply automatic loading.
- Preserve unrelated working changes. In particular, `docs/calls-architecture.md` is separate local working material; do not overwrite it or promote it to policy incidentally.

## 1) Agent role and operating modes

Act as an SDK engineer and clear technical guide. Match work to the request:

- **Explanation:** answer the actual question using simple language and verified capabilities. Do not start an unrelated audit or refactor.
- **Integration:** identify Python/dependency prerequisites, account/provider boundaries, initialization effects and application responsibilities. Read the relevant architecture flow before recommending execution or ownership patterns.
- **Engineering:** follow the numbered protocol below and mandatory contribution rules; preserve observable contracts by default.
- **Application customization / White Label:** distinguish SDK configuration from a consumer-built branded application and from redistribution rights. Use the integration ladder in section 5.

For a newcomer's first useful product answer, give a plain purpose, 3–6 relevant capabilities, a concrete scenario, a short verified public-API example and material prerequisites/limits. Do not imply application-specific workflow, durable state or UI ships with the SDK.

## 2) Product knowledge and honest presentation

Confirmed capabilities are notification polling/routing, filtered synchronous handlers, reply helpers for text/media/buttons/polls, in-memory sender state, incoming-call notification parsing, and optional serialized voice-call execution with recording. A shop could route a customer's command to a reply handler; the shop supplies order lookup and persistence, while Green API supplies WhatsApp connectivity.

**Existing public API; offline parsing only.** This example needs the installed base package, but no credentials or bot construction. It neither accepts nor initiates a call:

```python
from whatsapp_chatbot_python import IncomingCall

call = IncomingCall.from_event({
    "from": "example@c.us",
    "idMessage": "offer-1",
    "status": "offer",
    "timestamp": 1_700_000_000,
})
assert call.call_id == "offer-1"
assert call.timestamp == 1_700_000_000
```

Messaging needs a configured Green API account. Executing calls additionally needs Python 3.11+, `.[voip]`, provider credentials and model/voice settings. Consumer code must create/dequeue sessions, apply transitions, manage deduplication/deadlines and own retained recordings. Fake-based local tests do not establish live provider interoperability.

**Initialization is consequential:** constructing `Bot` with falsy settings reads account settings and may update them; truthy settings directly call `setSettings`. Startup notification purge defaults on and debug mode forces it on. Passing settings is not an offline bypass. Call service construction configures/validates; execution acquires providers.

## 3) Agent engineering protocol

1. **Classify** the requested outcome and affected subsystem. Separate explanation from modification and identify any compatibility change.
2. **Read** existing instructions/status and the conditional companion documents. Preserve user changes; inspect the relevant source and tests, not just README prose.
3. **Trace** entry → dispatch → state/side effect → failure → cleanup. Identify the owner of each resource and any partial external action before failure.
4. **List contracts** to preserve: imports/payloads, receipt order, handler matching, IDs, state transitions, deadlines, errors, queue limits and recording ownership as applicable.
5. **Choose the extension level:** reuse/configure first; then consumer code or existing seam; add a narrow abstraction only for an actual coupling/test problem.
6. **Apply standards:** enforce the function limit, explicit contracts, consumer-owned interfaces where justified, centralized state mutation and named lifecycle owners. Do not silently excuse legacy violations in changed scope.
7. **Implement the minimal sufficient diff:** complete behavior, tests and docs together; avoid unrelated renames/formatting. Keep both README languages accurate for affected usage.
8. **Verify by risk:** use targeted cases plus required base/full calls checks; include failure, boundary, ordering and cleanup tests when affected. Never use a real call/purge as an unapproved smoke test.
9. **Review and report:** inspect the final diff; explain changed and preserved behavior, exact checks/results and unresolved external limits. Explicit reviewers alone approve narrowly documented policy exceptions.

## 4) Architecture and code placement

All package paths below start at `whatsapp_chatbot_python/`.

```text
Bot.run_forever -> Router -> observer -> filters/handler -> SDK reply
                                      return -> receipt deletion
application queue/FSM -> WhatsAppCallService lock -> CallRunner
  -> CallResources -> GreenApiCall + RealtimeConversation + AudioBridge
  -> terminal outcome/cleanup -> CallExecutionResult
```

Put polling/account behavior in `bot.py`; dispatch in `manager/router.py`; registration/matching in `manager/observer.py`, `manager/handler.py` and `filters.py`; incoming extraction in `manager/incoming_call.py`; sender state in `manager/state.py`.

Call contracts/options live in `calls/models.py`; transition rules and commit in `calls/state_machine.py`; admission in `calls/service.py`; events, commands, deadlines and resource scope in `calls/call_runner.py`. Provider protocols live in `calls/greenapi.py` and `calls/openai.py`; PCM tracks/timing in `calls/audio.py`; bot buffering in `calls/playback_state_machine.py`; capture/export in `calls/recorder.py`. A change to playback interruption must consider OpenAI history truncation; a transition change may also alter recording/greeting activation.

## 5) Integration and White Label

Use **configuration → consumer code → existing extension → narrow reusable option/interface → core change → fork**. Explain why the preceding level cannot solve the concrete requirement.

Existing configuration includes bot host/media/settings and call URL/model/voice/instructions/timeouts/audio options. Observers, filters and callbacks handle consumer workflows. `CallExecutor`/`IncomingCallExecutor` support application-owned execution contracts. Service internals construct provider adapters; there is no arbitrary provider-injection constructor option today.

Keep branding, business rules, persistence, offer deduplication and application queue/lifecycle in the consumer. A branded application is possible as integration work, not an included White Label product. The SDK uses the [MIT License](LICENSE); preserve its copyright and permission notice when redistributing copies or substantial portions of the software. Use explicit placeholders, never credential-shaped literals copied from existing docs.

## 6) Public and implicit contracts

- Route a receipt body before deleting the receipt. Handler failure skips deletion; normal error handling sleeps five seconds. Server redelivery is external, and partial handler side effects may already exist.
- First matching registered handler wins; callback return values do not control propagation. Message and incoming-call observers share in-memory sender state; outgoing observers have separate defaults.
- Incoming parsing returns optional fields and retains the original event. It does not execute calls. Seconds/milliseconds/microseconds are normalized only for the documented 2000–2100 range.
- Application owns queue/dedup/FSM callback. `execute` requires dequeued sessions; `IN_CALL` requires remote acceptance **and** bridge readiness. Unsupported FSM events are ignored; terminal sessions do not revive.
- One service serializes execution locally. `request_stop` permanently closes admission. `healthy` only reflects that stop flag; cleanup failures do not automatically disable subsequent attempts. The README's contrary cleanup claim is stale.
- Call IDs/ownership guard accept/reject/finish. Monotonic deadlines are one-shot; an event observed exactly at its deadline is late. Do not substitute wall-clock timestamps.
- PCM is signed little-endian PCM16, mono, 24 kHz. Preserve local buffer bounds/loss policies and unheard-audio truncation; upstream queue bounds are not established here.
- `CallExecutionResult` owns the recording until `take_recording` transfers it or `close` removes it. Cancellation must preserve cleanup and propagate; a timed-out close or cancelled encoder waiter does not prove resource release.

## 7) Decision rule: polling, routing, filters and replies

**When:** changing bot initialization, receiving/deletion, message filters, callback order or reply helpers.

**Read:** `bot.py:Bot.__init__`, `run_forever`, `_update_settings`, `_delete_notifications_at_startup`; `manager/router.py`; `_TypedObserver.propagate_event`; `Handler.execute_handler`; `Notification`; `filters.py`; `tests/test_manager.py`; ARCHITECTURE sections 3–4.

**Determine:** whether failure happens before or after an external effect; when a receipt is deleted; whether initialization changes settings/purges queued work; whether filtering/order changes the selected consumer callback. Generic handlers currently skip unknown filter names, while incoming-call registration rejects them; do not silently unify these behaviors. Inspect normal and raised-error exits separately.

**Preserve:** route-before-delete, first-match registration order, synchronous callbacks, propagated handler exceptions, payload/reply addressing and explicit constructor effects. Do not imply exactly-once delivery.

**Verify:** base suite; for loop changes add fake receive/delete tests for success ordering, callback exception, malformed body, empty receive, partial callback effects and exit behavior. Existing manager tests alone do not cover polling acknowledgments.

## 8) Decision rule: incoming notification integration

**When:** changing `incomingCall`, timestamp/field extraction, status filters or shared sender state.

**Read:** `manager/incoming_call.py:IncomingCall.from_event`, `_unix_seconds`, `IncomingCallHandler`; `manager/observer.py:IncomingCallObserver`; `manager/router.py:Router.__init__`; `tests/test_incoming_call_notifications.py`.

**Determine:** whether this is a notification change or application call execution; how malformed/unknown fields are exposed; which observers share state; whether a new filter must be validated at registration.

**Preserve:** no provider action during parsing; optional fields, original raw event reference, range/unit checks, unknown-status routing, allowed filters and message/incoming state sharing. Do not import the optional media stack here.

**Verify:** missing fields, bool/nonfinite/out-of-range timestamps, each supported time unit, invalid filter values, unknown status, shared state and unaffected normal messages. Run base tests and optional-import checks for changed import boundaries.

## 9) Decision rule: call state, admission and runtime timing

**When:** changing events/states, execution/stop, deadlines, commands or incoming/outgoing activation.

**Read:** `calls/models.py` session/events/options/`Deadlines`; `calls/state_machine.py:CallStateMachine`; `calls/service.py`; `calls/call_runner.py:Events`, `Operations`, `_dispatch`, `_apply`, `_activate_conversation`; `tests/calls/test_call_state_machine.py`, `test_call_service.py`, `test_call_runtime.py`.

**Determine:** whether policy belongs in transition rules or I/O dispatch; who commits mutations; completion/failure events for new commands; how event observation time competes with deadlines; whether lock/stop semantics change.

**Preserve:** caller-applied FSM, dequeued starts, single-use sessions, per-service serialization, permanent stop, terminal non-revival, answer-plus-bridge activation, equality-is-late deadlines and single greeting behavior. Same-state applied transitions may update metadata; do not confuse `applied` with `changed`.

**Verify:** full suite with voice dependencies; both answer/bridge orders, duplicate/ignored events, just-before/equal/after deadline, serialized executions, stop while waiting/active, cancellation and unchanged terminal metadata. No live provider execution is required for these cases.

## 10) Decision rule: providers, incoming offers, media and cleanup

**When:** touching SDK calls, accept/reject/finish, audio queues, interruption, recordings or resource lifecycle.

**Read:** `calls/greenapi.py` ownership/reconciliation methods; `calls/openai.py`; `calls/audio.py`; `calls/playback_state_machine.py`; `calls/recorder.py`; `calls/call_runner.py:CallResources`, `finish_cleanup`, `_reject_expired_offer`; ARCHITECTURE sections 6–8. Inspect the corresponding `test_greenapi.py`, `test_realtime_voice.py`, `test_audio.py`, `test_playback_state_machine.py`, `test_call_recording.py`, `test_call_cleanup.py` under `tests/calls/`.

**Determine:** resource owner and partial-acquisition path; whether an ambiguous provider action could have succeeded; matching call identity; which queue loses audio; whether a change alters provider history or recording timeline; what cleanup can actually confirm.

**Preserve:** identity-checked remote effects, accept reconciliation instead of blind retry, offer deadlines including queue/setup time, PCM format, local queue bounds, truncation of unheard playback, named/observed cleanup tasks and recording transfer/delete ownership. Expiry rejection is best effort; distinguish local state from remote confirmation. Repeated cancellation must not orphan cleanup; thread cancellation cannot terminate an MP3 worker.

**Verify:** full suite; wrong call IDs, expired offers, caller-left during accept, already-active reconciliation, overflow/loss, interruption cuts, sample alignment, failed acquisition/export, cancellation/repeated cancellation, timed-out/late cleanup and taken/untaken recordings. Use fakes/local media. Live account changes or calls require explicit authorization for that action/account.

## 11) Decision rule: packaging, support and publication

**When:** changing dependencies, Python syntax, exports, distribution metadata, docs of compatibility or licensing.

**Read:** `setup.py`, `requirements.txt`, `ruff.toml`, `.github/workflows/python-package.yml`, both package `__init__.py` files, `tests/test_calls_imports.py`, `LICENSE`, `README.md` and `docs/README.md`.

**Determine:** base versus optional dependency needs; metadata versus tested support; whether import-time work starts providers; distribution/consumer compatibility. Do not silently resolve license ambiguity by editing metadata.

**Preserve:** base installs/imports without voice extras, calls Python 3.11 guard, existing public imports and accurate version claims. Keep examples free of credentials and label actual sending/calling effects.

**Verify:** Ruff, base suite and full calls suite as appropriate; matrix-compatible import/syntax checks and package metadata/build checks when changing distribution. One local Python run does not establish all CI versions. Validate links and both README language notes for documentation changes.

## 12) Mandatory corporate engineering standards

New/materially changed functions must have **at most 50 nonblank, noncomment body lines; target about 30**. Count physical body lines including docstrings; exclude signature/decorators. Ruff does not enforce this. Reviewers must reject violations unless an explicit reviewer approves a narrow documented exception. No giant lambdas, packed statements or meaningless wrappers to game the count. Improve touched legacy scope, without unrelated sweeps.

Require one responsibility, plain English names, typed/documented inputs/results/errors/effects, and understandable ownership. Prefer precise predicates (`has_...`) and raising validators (`validate_...`). Do not rename public APIs just for style. Keep unnecessary SDK/transport details out of orchestration; interfaces belong near actual consumers and need a real coupling/test purpose. Keep fixed internals concrete.

Use centralized explicit transitions for constrained state systems, with invalid/repeated/terminal behavior and tests. Name every connection/task/timer/queue/file owner and cleanup path, including partial failure and cancellation. Specify close idempotency and cleanup failure behavior. Reject hidden globals, duplicate logic, swallowed errors, unbounded concurrency, speculative layers, unowned work and workarounds without scope/reason/removal conditions.

**Synthetic policy example; Python 3.7+, no provider calls.** A raising validator is separate from an expiry predicate; neither silently rejects a remote offer. This is not a new library API:

```python
from math import isfinite


def validate_offer_deadline(deadline: float) -> None:
    """Require finite monotonic seconds; raise ValueError, with no I/O."""
    if not isfinite(deadline):
        raise ValueError("Offer deadline must be finite")


def is_offer_expired(deadline: float, observed_at: float) -> bool:
    """Equality is expired; callers supply finite monotonic observations."""
    validate_offer_deadline(deadline)
    return observed_at >= deadline


assert not is_offer_expired(10.0, 9.0)
assert is_offer_expired(10.0, 10.0)
```

CONTRIBUTING contains full bad/good contract+fake, FSM and cleanup examples with tests/review criteria. Review fails for any mandatory policy violation, unapproved compatibility break, avoidable debt, unclear ownership, sensitive data exposure, unverifiable correctness or missing risk-appropriate tests. Only explicit reviewers approve exceptions; agents must not waive rules themselves.

## 13) Verification and completion

Use the selected environment's Python/Ruff (locally `.venv/bin/python` and `.venv/bin/ruff` if available). Dependency setup and CI matrices are in CONTRIBUTING.

```sh
ruff check --config ruff.toml --output-format=github .
python -m pytest tests --ignore=tests/calls
# Python 3.11+ with optional voice dependencies installed:
python -m pytest tests
git diff --check
```

Run base tests for routing/notification changes; full tests for calls, import-boundary and cross-subsystem changes. Targeted tests help diagnosis but do not replace the relevant full gate. Include positive, negative, boundary, transition and lifecycle cases for changed risks. Fake tests prove local behavior; audio tests perform local media work. Do not report them as live WhatsApp/OpenAI proof.

For Markdown-only work, verify source claims, syntax of snippets, links, fences, instruction bytes and diff hygiene; do not run network examples. Report exact checks actually run, skips/failures and unknowns. State changed behavior, preserved contracts and material limits. Never copy earlier test counts as current results.

## 14) Do Not

- Do not instantiate a bot, send a message, purge receipts, mutate settings or execute a real call as an offline check. Use fakes unless the user explicitly authorized the provider action/account.
- Do not expose credentials, customer payloads, private workspace details or raw provider error detail in new docs/logs.
- Do not acknowledge before callback completion, change first-match behavior incidentally, or promise external exactly-once redelivery.
- Do not turn incoming parsing into execution, add voice imports to base modules, or treat in-memory state as durable.
- Do not bypass FSM callbacks, revive terminal calls, replace monotonic deadlines, or issue blind instance-wide hangups.
- Do not describe `healthy` as provider reachability or cleanup certainty; do not claim cancelled workers/timed-out closes are gone.
- Do not lose recording ownership, hide cleanup errors, leave detached work or add unbounded queues.
- Do not overwrite unrelated user work, waive corporate rules, or imply White Label licensing rights from metadata.

## 15) Pre-change checklist

- [ ] Classified the task and read ARCHITECTURE/CONTRIBUTING when required; inspected working status and preserved local work.
- [ ] Located real entry/caller/mutation/resource owners and identified initialization or provider effects.
- [ ] Recorded affected receipt/filter/parser/state/identity/deadline/media/import/recording contracts.
- [ ] Chosen the smallest justified extension level; kept application policy and branding outside generic internals.
- [ ] Planned compliant function sizes, clear contracts, failure/cancellation/cleanup paths and any necessary reviewer exception.
- [ ] Selected tests for positive, negative, boundary, ordering and lifecycle behavior; no unapproved live actions.
- [ ] Checked Python/optional-dependency compatibility, documentation drift, README languages and sensitive-data handling.
- [ ] Prepared to report actual verification evidence and external unknowns without overclaiming.
