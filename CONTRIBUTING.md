# Contributing — whatsapp-chatbot-python

These rules apply to human and AI contributions. Read [AGENTS.md](AGENTS.md) for agent workflow and [ARCHITECTURE.md](ARCHITECTURE.md) for current implementation, ownership and contracts. This document sets mandatory requirements for new and materially changed code; it does not claim all legacy code already complies.

---

## 1) Engineering goal and scope

Deliver the smallest complete, safe, maintainable change, including necessary tests and documentation. Preserve consumer behavior unless a compatibility change is explicitly approved with a migration plan. Reuse current public seams, configuration, standard library and existing dependencies before adding machinery. Business logic and branding belong in consumer applications.

New code must comply immediately. Improve materially touched legacy functions within the task's scope; do not perform unrelated formatting, renaming or architecture sweeps. An exception requires **explicit reviewer approval**, naming the exact rule, scope, reason, risk and follow-up/removal condition. An agent cannot grant its own exception. Existing violations are not precedent for new ones.

## 2) Required task workflow

1. Classify the request: explanation, integration, bug fix, behavior change, compatibility change or packaging. State the intended observable outcome.
2. Inspect status and existing instructions. Preserve unrelated edits, including the local `docs/calls-architecture.md` note. Never copy credentials from examples into new material.
3. Trace entry, caller, mutation owner, output and cleanup in the actual code. Use the relevant flow in ARCHITECTURE; source resolves documentation disagreements.
4. Write down affected contracts: payloads/imports, first-match/acknowledgment order, state transitions, IDs, deadline units, errors, concurrency, media and file ownership as applicable.
5. Choose the narrowest real extension: configuration, consumer callback, existing protocol, then a justified reusable seam, then core modification. Record why a new abstraction is necessary.
6. Implement cohesive changes under the standards below. Keep transition rules separate from I/O and provider-specific details at adapters where practical.
7. Verify positive, failure and boundary cases appropriate to risk. Test invalid/repeated transitions, cancellation, partial acquisition and cleanup when affected. Run the corresponding repository checks.
8. Review the diff for compatibility, sensitive data, duplicated logic and misleading docs. Update both README languages when changing documented behavior.
9. Report what changed, why, contracts preserved or intentionally migrated, exact checks/results and remaining limits. Do not equate fake tests with live provider validation.

## 3) Function size, naming and explicit contracts

**Review must reject new or materially changed functions with more than 50 nonblank, noncomment body lines; target about 30.** Count physical body lines, excluding blank lines and comment-only lines; signature/decorators are outside the body. Docstring lines count under this conservative rule. Nested function definitions do not hide work from the enclosing body's count. Do not game the limit using giant lambdas, semicolon-packed statements, deep nesting or meaningless forwarding wrappers. Ruff currently selects E4/E7/E9/F and does not enforce this rule; reviewers must check it.

Each function has one concrete reason to exist. Within roughly five minutes a newcomer should be able to identify its input, result/effect, expected exceptions, state mutation, side effects and resource owner. Decompose by responsibility, not by arbitrary line chunks.

Use plain English and domain terms with Python `snake_case` functions/variables and `PascalCase` types. Predicates such as `has_required_call_fields` return a boolean; `validate_call_fields` may raise. Avoid vague names such as `process`, `ensure` or `handle_data` when a concrete action is available. Do not rename a public API solely for style. Types/signatures and docstrings must expose significant inputs, results, errors, I/O, state changes and cancellation semantics. Comments explain why a contract exists.

### Example 1: naming and decomposition (synthetic, Python 3.7+)

**Bad:** validation, time policy and external rejection are coupled behind a vague predicate-like name. Missing or malformed fields fail incidentally, and validation unexpectedly invokes I/O.

```python
# Synthetic anti-pattern; api is an unspecified external dependency.
def ensure_offer(data, now, api):
    if data["deadline"] <= now:
        api.reject(data["id"])
        return False
    return data["id"]
```

**Good:** these standalone pure functions separate shape validation from the expiry decision. Application orchestration owns any later rejection action. This illustrates consumer input validation, not a replacement for `IncomingCall.from_event`, whose permissive parsing contract must remain intact.

```python
from math import isfinite
from typing import Any, Mapping


def validate_offer_id(offer: Mapping[str, Any]) -> str:
    """Return a nonempty ID or raise ValueError; perform no I/O."""
    call_id = offer.get("id")
    if not isinstance(call_id, str) or not call_id.strip():
        raise ValueError("Offer ID must be nonempty text")
    return call_id


def is_offer_expired(deadline: float, observed_at: float) -> bool:
    """Compare finite monotonic seconds; equality is expired."""
    if not isfinite(deadline) or not isfinite(observed_at):
        raise ValueError("Offer times must be finite")
    return observed_at >= deadline


assert validate_offer_id({"id": "offer-1"}) == "offer-1"
assert not is_offer_expired(10.0, 9.0)
assert is_offer_expired(10.0, 10.0)
```

**Why:** callers can reason about validation without account effects; names distinguish a raising validator from a predicate. Each function owns one decision and is well below the hard limit.

**Tests:** valid ID, absent/empty/whitespace/non-string ID; before/equal/after deadline; nonfinite values; no provider calls. **Review:** reject combined validation-and-rejection helpers, implicit wall-clock conversion, or changes that silently make the real permissive parser strict.

## 4) Dependency direction and real interfaces

High-level orchestration must not know unnecessary SDK transport, storage or provider payload details. At a demonstrated integration/test seam, place a small contract near its consumer, inject a concrete adapter, and verify policy with a fake. Keep fixed local implementation details concrete. Interfaces for every class, factories for hypothetical providers and speculative layers fail review.

Current reality: call executor protocols exist, but service internals construct Green API/OpenAI adapters. Do not advertise constructor injection that is absent. A future seam needs a concrete consumer/testing problem and compatibility analysis. Base-package changes must not gain a Python-3.11-only `Protocol` dependency from the standard library; the following example targets the calls/application Python 3.11 boundary.

### Example 2: consumer contract, adapter and fake (synthetic, Python 3.11+)

**Bad:** a welcome policy builds provider payloads and selects transport methods directly, making a policy test depend on SDK shape.

```python
# Synthetic anti-pattern; transport is a hypothetical HTTP client.
def send_welcome(transport, chat_id):
    return transport.post("/sendMessage", json={
        "chatId": chat_id, "message": "Welcome!",
    })
```

**Good:** the consumer requires only sending text. The callable adapter is a synthetic seam around an existing integration's send function; it does not claim the SDK accepts this exact signature.

```python
from collections.abc import Callable
from typing import Protocol


class TextSender(Protocol):
    def send_text(self, chat_id: str, text: str) -> None:
        """Send once; propagate failure. No implicit retry."""
        ...


class WelcomeService:
    def __init__(self, sender: TextSender) -> None:
        self._sender = sender

    def welcome(self, chat_id: str) -> None:
        """Validate recipient and send once; propagate sender exceptions."""
        if not chat_id:
            raise ValueError("Chat ID is required")
        self._sender.send_text(chat_id, "Welcome!")


class FunctionTextSender:
    def __init__(self, send: Callable[[str, str], None]) -> None:
        self._send = send

    def send_text(self, chat_id: str, text: str) -> None:
        self._send(chat_id, text)


class FakeTextSender:
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[tuple[str, str]] = []
        self.error = error

    def send_text(self, chat_id: str, text: str) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append((chat_id, text))


fake = FakeTextSender()
WelcomeService(fake).welcome("example@c.us")
assert fake.sent == [("example@c.us", "Welcome!")]
```

**Why:** policy owns the capability contract; a lower-level adapter handles actual integration details. The adapter is justified only when it joins a real consumer and existing sender. This synchronous example makes no cancellation guarantee and must not block an async runner; an async consumer needs an async contract with documented cancellation.

**Tests:** successful send exactly once, empty recipient never calls sender, provider exception propagates without retry, adapter forwards arguments once, fake retains no successful send on its configured failure. Adapter integration tests separately verify the actual SDK payload and response errors. **Review:** reject transport payload construction in the high-level policy or an interface introduced without a real consumer.

## 5) Explicit state transition policy

When correctness depends on allowed states/events, require a centralized FSM or equivalent explicit transition model with one mutation owner. Define invalid, repeated and terminal-event semantics and test them. Separate pure transition planning from I/O where practical. Do not impose FSMs on trivial flags or change existing ignored-event semantics incidentally.

In this repository, update `calls/state_machine.py` for state rules and `calls/call_runner.py` for commands/runtime dispatch. A new command needs completion/failure behavior, ownership, cancellation and deadline analysis. The application callback must apply the FSM; direct scattered assignment to `session.state` bypasses reason/timestamp handling.

### Example 3: explicit transition owner (synthetic, Python 3.7+)

**Bad:** arbitrary mutations permit finishing a never-started operation and reviving completed work.

```python
# Synthetic anti-pattern.
def update_session(session, event):
    if event == "finish":
        session.state = "done"
    elif event == "start":
        session.state = "running"
```

**Good:** this standalone model explicitly ignores invalid/repeated events and makes terminal state absorbing. It illustrates the approach; it is not the repository's complete call FSM.

```python
from enum import Enum


class State(Enum):
    NEW = "new"
    RUNNING = "running"
    DONE = "done"


class Event(Enum):
    START = "start"
    FINISH = "finish"


TRANSITIONS = {
    (State.NEW, Event.START): State.RUNNING,
    (State.RUNNING, Event.FINISH): State.DONE,
}


class SessionState:
    def __init__(self) -> None:
        self.state = State.NEW

    def apply(self, event: Event) -> bool:
        """Commit one allowed transition; return False without mutation otherwise."""
        target = TRANSITIONS.get((self.state, event))
        if target is None:
            return False
        self.state = target
        return True


session = SessionState()
assert not session.apply(Event.FINISH)
assert session.apply(Event.START)
assert not session.apply(Event.START)
assert session.apply(Event.FINISH)
assert not session.apply(Event.START)
assert not session.apply(Event.FINISH)
assert session.state is State.DONE
```

**Why:** one owner commits only table-supported transitions. A reader can enumerate all state/event pairs and see that callbacks cannot revive a terminal session through this API.

**Tests:** all state/event pairs, repeated START, finish-before-start, all terminal events and no mutation on ignored events. Real call tests must additionally assert timestamps/reasons, incoming direction restrictions, both answer/bridge orders, and late deadline behavior. **Review:** reject scattered mutations, an undocumented invalid-event policy, or conflating `applied` with `changed` in real transitions.

## 6) Resource ownership, cleanup and cancellation

Every connection, task, timer, queue, stream and file must have a named owner and end-of-life path. That owner handles partial acquisition, cancellation, cleanup and error reporting. Higher levels invoke a simple lifecycle method/context rather than closing subsets of internals. Specify repeated-close behavior, timeout handling and what happens after failed cleanup. Do not claim idempotency after failure unless implemented and tested. Unowned background tasks and hidden infinite queues fail review.

For calls, read `CallResources`, `Operations`, `finish_cleanup`, `GreenApiCall` and `CallExecutionResult`. Reuse the existing cancellation/late-cleanup mechanisms instead of creating detached cleanup tasks. A bare async `finally` is insufficient against repeated cancellation; `asyncio.shield` without retaining and draining the task is also insufficient. Cancellation must propagate after owned cleanup, with cleanup errors observed. An encoder worker thread cannot be forcibly stopped by cancelling its waiter.

### Example 4: one file owner across asynchronous cancellation (synthetic, Python 3.11+)

**Bad:** work failure or cancellation skips closure, and callers cannot tell who owns the file.

```python
# Synthetic anti-pattern.
async def capture(path, receive):
    stream = open(path, "wb")
    stream.write(await receive())
    stream.close()
```

**Good:** a small owner wraps a synchronously closed file; the runner acquires it before awaiting input and always invokes close. The injected opener is a real acquisition seam for partial-failure and cleanup tests.

```python
from collections.abc import Awaitable, Callable
from typing import BinaryIO


class CaptureFile:
    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed:
            raise ValueError("Capture is closed")
        self._stream.write(data)

    def close(self) -> None:
        """Repeat after success is a no-op; failure propagates and may be retried."""
        if not self._closed:
            self._stream.close()
            self._closed = True


async def capture_once(
    open_stream: Callable[[], BinaryIO],
    receive: Callable[[], Awaitable[bytes]],
) -> None:
    """Own one file; preserve receive errors/cancellation with cleanup context."""
    owner = CaptureFile(open_stream())
    primary = None
    try:
        owner.write(await receive())
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            owner.close()
        except Exception as cleanup_error:
            if primary is None:
                raise
            primary.add_note(
                "Capture close failed: " + type(cleanup_error).__name__
            )
```

**Why:** acquisition failure transfers no resource; the opener must release anything it acquired before raising. Once returned, the stream belongs to `CaptureFile`. Cleanup succeeds before cancellation escapes, because this close has no await point. A close failure alone propagates; a simultaneous primary error/cancellation remains primary with a safe error-type note. No guarantee is made that a failed close released the file. A successfully closed owner rejects writes and tolerates repeated close.

This example is suitable only for small local file work with bounded synchronous latency; it is not a network or MP3-worker cleanup design. Async or potentially blocking closure needs owned tasks, deadlines, retained outcomes and the repository's repeated-cancellation discipline. Do not generalize this snippet into a promise that all file systems or provider cleanup are bounded.

**Tests:** normal write/close, receive exception, cancellation while awaiting receive, write failure, acquisition failure, close-only failure, combined primary/close failure preserving primary, successful repeated close, failed-close retry and write-after-close. Real resource changes also need partial multi-resource acquisition, repeated cancellation, timed-out close, late cleanup failure and abandoned recording tests. **Review:** reject lost primary exceptions, silently discarded cleanup errors, detached tasks or an unqualified “always cleaned up” claim.

## 7) Errors, retries, concurrency and debt prevention

Propagate or explicitly classify errors at the owning boundary. Suppression is acceptable only for a documented best-effort action with observability and tests, such as expired-offer rejection; do not extend that to arbitrary provider errors. Preserve safe error codes and primary exceptions. Never log credentials, raw provider detail or full customer payloads in new diagnostics. Existing router debug logging includes payloads; do not infer that similar new disclosure is acceptable.

Retry only when operation semantics support it. Dial/accept may succeed remotely before local failure; preserve reconciliation instead of blindly retrying. Callback failure can occur after sending a reply but before receipt deletion; business idempotency is a consumer concern. Timeouts and local cancellation do not prove remote actions were undone.

Bound queues, task creation and concurrency at the appropriate owner. Per-service serialization is not a global lock. Define loss/backpressure policies, observation timestamps and how shutdown drains or abandons work. Avoid blocking provider/file calls in async orchestration without an explicit execution strategy.

Review rejects duplicate logic, hidden globals, vague methods, speculative abstraction, unbounded work, SDK leakage into policy, swallowed failures and quick workarounds lacking scope/reason/removal conditions. A small diff that leaves ownership or tests incomplete is not a minimal sufficient change.

## 8) Compatibility gates by change category

| Change | Required preserved behavior / evidence |
| --- | --- |
| Polling, handlers, filters, replies | Route before delete; first match wins regardless of callback return; exceptions prevent ack; account settings/purge remain explicit. Test fake receive/delete order and partial callback effects when touching the loop. |
| Incoming notification integration | Optional parser fields/units and original event; supported filter validation; only message/incoming observers share state. Test malformed timestamps, unknown statuses, shared-state and unmatched handlers. Generic handlers currently skip unknown filter names while incoming registration rejects them; stricter generic validation requires explicit compatibility review. |
| Call states/runtime | Consumer queue/FSM ownership; dequeued start state; answer plus bridge; terminal non-revival; monotonic equality is late; one execution per service; permanent stop. Test event races, errors and cancellation. |
| Providers/media/resources | Owned-call finish/reject; accept reconciliation; PCM16 LE mono 24 kHz; bounded local buffers; truncation of unheard audio; cleanup and result transfer/delete. Test wrong IDs, overflow, partial acquisition and late cleanup. |
| Packaging/support/publication | Base imports remain independent of voice extras; calls require 3.11+. Check manifests, CI matrices and exports. Do not silently increase base Python requirements or claim untested 3.7 compatibility. |

Intentional changes to imports, payloads, states, error shapes, retry/order semantics, timeouts, observable logs or ownership require explicit compatibility analysis and reviewer-approved migration instructions. Preserve both public and implicit contracts. Update English/Russian guidance when relevant; do not reproduce known README health drift as fact.

## 9) Integration and White Label changes

Use this ladder: configuration → consumer code → existing extension → narrow reusable option/interface → core change → fork. Document the limitation that requires moving up a level. Model/voice/instructions, URLs and timing options already cover many integrations. Observers and callbacks cover application behavior; executor protocols support application-owned execution. Service provider adapters are internally constructed, so replacing them is not currently just configuration.

Keep brand names, tenant business policy, durable storage and customer UI outside generic library internals. Check constructor requirements: bot construction contacts settings APIs and may purge receipts, whereas call service construction defers provider acquisition. Use placeholders for credentials and label examples that send messages/place calls. The SDK uses the [MIT License](LICENSE); preserve its copyright and permission notice when redistributing copies or substantial portions of the software.

## 10) Verification and safe commands

Use a disposable virtual environment or the existing `.venv` when available. Commands below use `python`/`ruff` from the selected environment; locally these may be `.venv/bin/python` and `.venv/bin/ruff`. Installation downloads dependencies and is distinct from offline test execution.

Base CI environment (Python 3.9–3.12):

```sh
python -m pip install ruff pytest
python -m pip install -r requirements.txt
ruff check --config ruff.toml --output-format=github .
python -m pytest tests --ignore=tests/calls
```

Calls CI environment (Python 3.11–3.13):

```sh
python -m pip install '.[voip]' pytest
python -m pytest tests
```

Run Ruff for code changes and the relevant base tests; use the full suite for call, optional-import, packaging or cross-subsystem changes. Targeted diagnosis can use `python -m pytest tests/test_incoming_call_notifications.py` or the affected `tests/calls/test_*.py`, but does not replace the full calls gate for call changes. Match CI matrices when changing supported Python syntax/dependencies; one local interpreter cannot certify the matrix. Package builds are relevant to distribution changes, not required for a documentation-only edit.

`tests/test_calls_imports.py` protects optional imports; manager/incoming-call tests protect routing; `tests/calls/fakes.py` supplies simulated provider boundaries. Media tests may create temporary recordings, but no live accounts are needed. Local tests do not prove live signaling, upstream queues or exact receipt redelivery.

For Markdown-only work, inspect source-backed claims, parse standalone Python examples without invoking providers, validate relative links/fences and run `git diff --check`. Run illustrative examples only with local data/fakes. Do not instantiate `Bot` as an offline smoke test; even explicit settings call a provider. Real messaging/calls, purge/account changes and paid provider sessions require explicit authorization for that action and test account. Existing public examples are not authorization.

Report commands, interpreter/environment, pass/fail/skip results and unrun checks honestly. Do not carry historical passing counts forward as a current result. Failed checks must be resolved or explicitly reported with their scope and reason.

## 11) Mandatory review acceptance and rejection

A change is acceptable only when a reviewer can locate the owner, follow its failure path, understand contracts and reproduce appropriate checks. Review must verify:

- Functions meet the hard 50-line body limit and single responsibility; naming/contracts reveal effects and failures.
- The diff solves a concrete problem without unrelated sweeps, invented layers or duplicate policy.
- State transitions, IDs, ordering, deadlines, concurrency and cleanup have a single clear owner and suitable tests.
- Interfaces serve actual consumers; provider internals stay at adapter boundaries.
- Compatibility is preserved or an intentional migration is explicitly approved and documented.
- Sensitive data is absent; diagnostics and examples do not expose credentials or customer payloads.
- Positive, negative, boundary and lifecycle tests cover the changed risks; fake results are described accurately.
- Docs distinguish existing behavior, new policy and unverified external claims; README languages remain consistent when behavior changes.

**Fail review** for any mandatory-rule violation, unapproved compatibility break, avoidable technical debt, unclear ownership, sensitive data exposure, unverifiable correctness or missing risk-appropriate tests. Only an explicit reviewer may approve a narrowly described exception under section 1. Agents must surface an unresolved exception rather than silently waive the rule or declare completion.
