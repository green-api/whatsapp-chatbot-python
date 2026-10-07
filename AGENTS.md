# sw-core

Instructions for AI agents working with the repository.

---

## 0) Quick facts about this repository

* Purpose: a **runtime worker core** for managing live messenger instances through a custom WhatsApp server client.
* Current role: a lower-level runtime/service layer around `@green-api/sw-baileys`, responsible for live socket sessions, auth/session material, outbound commands, inbound events, webhook normalization, and local history.
* Current shape:
    * Maintains local `WaInstance` runtimes
    * Creates and supervises messenger sockets
    * Stores auth/session state in Mongo
    * Receives outbound commands through RabbitMQ and legacy REST paths
    * Sends messages through live socket/runtime methods
    * Receives messenger events from socket listeners
    * Normalizes incoming/outgoing events into webhook/journal formats
    * Publishes downstream webhook events through RabbitMQ
    * Persists normalized message/chat/contact history in Mongo
* Stack:
    * `TypeScript`
    * `NestJS`
    * `MongoDB`
    * `RabbitMQ`
    * Redis-like cache through `CacheFactory`
    * `@green-api/sw-baileys`
    * Custom Bayles/Baileys socket protocol
* Repository character:
    * Legacy-heavy codebase
    * Long-lived runtime sessions
    * Stateful socket lifecycle
    * Many explicit and implicit contracts
    * Several externally observed behaviors that must not change casually
    * Runtime behavior distributed across services, socket listeners, queues, caches, and persistence
* Key principle: this repository is **not** a clean standalone public API product, not a generic gateway, and not a place for broad architectural experiments.
* Architectural reality: many important contracts are implicit and historical. Queue names, message IDs, state changes, ACK/NACK behavior, retry behavior, webhook payloads, logs, caches, auth storage shape, and timing may be depended on by other services.
* Current core areas:
    * `apps/swcore/src/app/wapi/wainstances/wa.instances.service.ts` — registry/supervisor of local Wa instances, lifecycle orchestration, outbound command bridge, reboot/delete flows
    * `apps/swcore/src/app/wapi/wainstances/client/wa.instance.bayles.client.ts` — runtime of one Bayles instance: socket, auth state, reconnect, QR/pairing, event listeners, send methods, local caches
    * `apps/swcore/src/app/wapi/controller/wa.controller.ts` — legacy REST surface for send, auth, QR, history, media, and instance operations
    * `apps/swcore/src/app/wapi/wainstances/webhooks/webhook.service.bayles.ts` — incoming/outgoing event normalization, webhook queue publishing, journal/history mapping, dedupe/filter behavior
    * `apps/swcore/src/app/wapi/wainstances/mapper/*` — webhook and journal mapper tree
    * `apps/swcore/src/app/mongoauthstorage/authstore/use-multi-mongo-auth-state.ts` — Baileys auth state integration
    * `apps/swcore/src/app/mongoauthstorage/authstore/auth.data/db/auth.data.mongo.service.ts` — auth data facade and storage routing
    * `apps/swcore/src/app/wapi/rmq/*` — RabbitMQ topology, consumers, publishers, downloader integration
    * `apps/swcore/src/app/wapi/jobs/jobs.ts` — periodic lifecycle/consumer jobs
    * `apps/swcore/src/app/wapi/ws/qr.websocket.gateway.ts` — QR websocket gateway/cache behavior

> Agents: before making changes, read the exact files involved in the affected flow. For lifecycle, outbound send, auth, webhook, or RabbitMQ changes, do not rely on one file only. Follow the runtime path end-to-end.

---

## 1) Agent protocol

1. **Understand the task first:**
    * Determine whether the task changes:
        * Instance lifecycle
        * Socket/runtime behavior
        * Authorization / QR / pairing flow
        * Outbound send behavior
        * RabbitMQ command handling
        * REST legacy behavior
        * Incoming event handling
        * Webhook/journal normalization
        * Mongo history persistence
        * Auth/session storage
        * Cache/reconnect behavior
        * Media/downloader behavior
        * Security/logging behavior
        * Or only thin glue around existing behavior
2. **Assume legacy contracts exist:**
    * If behavior is currently observed by external systems, treat it as a contract even when it is not documented.
    * Do not change queue names, payload shapes, message ID semantics, status names, retry behavior, error behavior, delays, headers, or persistence shape without explicit intent.
3. **Trace the real runtime path:**
    * For any change, identify where the flow starts and where it ends.
    * Example: REST send starts in controller, goes through `WaInstancesService`, then into `WaInstanceBaylesClient`, then into Baileys socket, then later returns status through webhook events.
    * Example: incoming message starts in socket listener, goes through `WebhookServiceBayles`, mapper tree, RabbitMQ publish, and Mongo history persistence.
4. **Minimal sufficient diff:**
    * Modify only the files and sections required for the task.
    * Minimal diff means the smallest complete safe change, including tests/docs/config when required.
    * Do not perform broad cleanup, formatting sweeps, naming migrations, dependency upgrades, or framework reshaping unless explicitly required.
5. **Preserve runtime stability:**
    * This service manages live sessions. A harmless-looking refactor can cause reconnect loops, lost events, duplicate webhooks, auth loss, delayed sends, or queue backlogs.
6. **Respect external dependencies:**
    * SAPI/WaAdminJobs, RabbitMQ producers/consumers, Mongo collections, Media API, downloader service, Redis/cache, and downstream webhook consumers may depend on current behavior.
7. **Prefer explicit local fixes:**
    * Add small named helpers when they clarify a concrete path.
    * Avoid hiding runtime flow behind abstractions that make debugging harder.
8. **Avoid protocol invention:**
    * Do not invent new lifecycle states, queue protocols, message ID formats, outbox semantics, delivery semantics, or auth storage categories unless explicitly requested.
9. **Document behavior changes:**
    * If a real contract changes, update relevant docs, examples, configuration notes, and migration guidance.
10. **Be honest about uncertainty:**
    * If a behavior appears legacy, implicit, or externally consumed, say so in the change summary.
    * Do not present guesses as facts.

---

## 2) Architecture and code placement

### 2.1. Service shape

This repository is best understood as:

* A runtime-worker layer
* A local supervisor of live messenger instances
* A socket/session manager around Baileys
* A bridge between outbound command queues and live messenger sockets
* A normalizer between raw messenger events and downstream webhook/journal contracts
* A persistence layer for auth/session material and normalized history

It is **not** best understood as:

* A clean REST gateway
* A stateless controller-only service
* A generic public API product
* A full conversation platform
* A business workflow service
* A safe place for speculative architecture changes
* A repository where current behavior can be “cleaned up” without checking legacy consumers

### 2.2. Current code placement

**`apps/swcore/src/app/wapi/wainstances/wa.instances.service.ts`:**

* Registry of local `WaInstanceBaylesClient` objects
* Instance lifecycle orchestration
* Startup initialization
* Reboot/spawn/delete flows
* Outbound command processing from RabbitMQ
* REST-to-runtime bridge methods
* Per-instance send mutex behavior
* Consumer start/stop coordination
* Some retry/failure handling around outbound messages

**`apps/swcore/src/app/wapi/wainstances/client/wa.instance.bayles.client.ts`:**

* Runtime for one messenger instance
* Baileys socket creation
* Connection lifecycle
* Reconnect behavior
* QR and pairing events
* Auth state usage
* Socket event listeners
* Send methods
* Local caches and reconnect slots
* State/status transitions
* Calls into webhook handling

**`apps/swcore/src/app/wapi/controller/wa.controller.ts`:**

* Legacy REST surface
* Direct runtime send path
* QR/pairing endpoints
* History endpoints
* Media/download endpoints
* Instance operation endpoints

**`apps/swcore/src/app/wapi/wainstances/webhooks/webhook.service.bayles.ts`:**

* Main incoming/outgoing event pipeline
* Webhook conversion
* Journal conversion
* Message status processing
* Dedupe-ish and filter behavior
* RabbitMQ webhook queue publishing
* History persistence coordination
* Event logging behavior

**`apps/swcore/src/app/wapi/wainstances/mapper/interfaces.ts`:**

* Public-ish enums and payload interfaces
* State/account/status/message/webhook types
* Many values here are externally observed and must be treated as contract-like

**`apps/swcore/src/app/wapi/wainstances/mapper/webhook/*`:**

* Webhook payload mapping
* Incoming/outgoing/call/avatar/state payload shapes
* Downstream compatibility concerns

**`apps/swcore/src/app/wapi/wainstances/mapper/journal/*`:**

* Journal/history mapping
* Mongo persistence compatibility concerns

**`apps/swcore/src/app/mongoauthstorage/authstore/use-multi-mongo-auth-state.ts`:**

* Integration point between Baileys auth state and Mongo auth storage
* `creds`, `keys.get`, `keys.set`, and `saveCreds` behavior
* Very sensitive protocol boundary

**`apps/swcore/src/app/mongoauthstorage/authstore/auth.data/db/auth.data.mongo.service.ts`:**

* Facade for auth data persistence
* Routes auth categories to sessions, prekeys, base categories, and creds services
* Storage shape compatibility matters

**`apps/swcore/src/app/mongoauthstorage/model/*`:**

* Mongo entities for auth/session material and normalized history
* Collection names and field shapes may be contract-like

**`apps/swcore/src/app/wapi/rmq/topology/*`:**

* Queue naming and topology
* Do not rename or reshape casually

**`apps/swcore/src/app/wapi/rmq/rmq.consumer.service.ts`:**

* Consumer lifecycle setup
* Instance-specific outgoing command consumption

**`apps/swcore/src/app/wapi/rmq/rabbit/wa.message.consumer.ts`:**

* RabbitMQ low-level consume behavior
* `prefetch`, ACK/NACK, and handler invocation

**`apps/swcore/src/app/wapi/rmq/rmq.send.service.ts`:**

* RabbitMQ publish path for downstream webhook events

**`apps/swcore/src/app/wapi/rmq/rmq.downloader.service.ts`:**

* Downloader queue integration for media events

**`apps/swcore/src/app/wapi/ws/qr.websocket.gateway.ts`:**

* QR gateway/cache/websocket delivery
* QR status compatibility concerns

**`apps/swcore/src/app/wapi/jobs/jobs.ts`:**

* Periodic jobs
* Consumer/reconnect/lifecycle behavior

**`apps/swcore/src/app/common/factory/CacheFactory.ts`:**

* Cache connection creation
* Sensitive logging risk
* Redis-like runtime dependency

### 2.3. Main integration point

The main integration point is **not** a controller method. It is the runtime relation between:

```text
WaInstancesService
  -> WaInstanceBaylesClient
  -> @green-api/sw-baileys socket
  -> socket events
  -> WebhookServiceBayles
  -> mapper tree
  -> RabbitMQ / Mongo
```

Requirements:

* Keep this flow traceable.
* Do not hide key lifecycle decisions behind generic infrastructure abstractions.
* Do not duplicate behavior that belongs to Baileys, the messenger protocol, or existing mapper logic.
* Preserve local runtime semantics unless explicitly changing them.

### 2.4. Legacy reality

This repository contains significant legacy behavior.

Requirements:

* Treat strange-looking code as potentially intentional until proven otherwise.
* Search for downstream usage before changing payloads, IDs, enums, statuses, queue names, or persistence shape.
* Avoid “cleanup” changes that alter timing, ordering, retries, logs, side effects, or fallback behavior.
* Do not replace legacy behavior with a cleaner model unless the task explicitly asks for a migration.
* If a change intentionally breaks legacy behavior, make that explicit in the summary and docs.

---

## 3) Instance lifecycle and runtime state

### 3.1. Core runtime objects

The important runtime objects are:

```typescript
export class WaInstanceBaylesClient extends EventEmitter {
  public sock: WASocket | undefined = undefined
  private statusInstance: StatusInstance = StatusInstance.Offline
  private spawning = false
  private attemptToConnect = 0
  private breakingDisconnectReason: BreakingDisconnectReason | undefined = undefined
  private restartReason: number = undefined
  private stateAccount: StateAccount
}
```

The service has two different state layers:

```typescript
export enum StateAccount {
  NotAuthorized = 'notAuthorized',
  Authorized = 'authorized',
  Blocked = 'blocked',
  Starting = 'starting',
  PendingCode = 'pendingCode',
  YellowCard = 'yellowCard'
}

export enum StatusInstance {
  Online = 'online',
  Offline = 'offline'
}
```

Requirements:

* Do not merge `StateAccount` and `StatusInstance`.
* Do not assume `Authorized` means websocket is currently online.
* Do not assume `Online` means the account is semantically healthy.
* Preserve the distinction between auth/business state and live socket state.
* Treat state names as externally observed values.

### 3.2. No clean FSM

There are state enums, but there is no complete formal state machine.

State transitions are distributed across:

* `connection.update`
* `startAuthorization`
* `sendAuthorizationCode`
* `consumerJob`
* `logout`
* `resetSession`
* Reboot/spawn flows
* Reconnect handling
* Error branches

Requirements:

* Do not introduce a new FSM casually.
* Do not centralize state transitions as a cleanup unless explicitly requested and carefully tested.
* When changing state behavior, inspect all callers and downstream webhook behavior.
* State changes may emit webhooks, not just mutate local fields.

### 3.3. Startup lifecycle

At service startup:

* `WaInstancesService.onModuleInit()` subscribes to SAPI updates and starts schedulers.
* Instance lists normally come from WaAdminJobs/SAPI.
* `initInstances()` creates local `WaInstanceBaylesClient` objects in chunks.
* Existing auth state in Mongo can trigger reboot/spawn behavior.
* Consumer startup may depend on authorization state.

Requirements:

* Do not assume all instances are present at process start.
* Do not assume local memory is the source of truth.
* Do not bypass SAPI/cache-api driven settings.
* Do not introduce pod-level ownership assumptions unless explicitly designed.
* Be careful with chunking, scheduling, and reboot behavior.

### 3.4. Reconnect behavior

Reconnect/supervision is not a single separate process. It is distributed across:

* `connection.update` logic
* Periodic jobs
* Mutexes on reboot/connect/send
* Shared cache reconnect slots
* Local instance Map
* Status/account state transitions

Requirements:

* Do not remove or bypass reconnect guards because they appear redundant.
* Do not change reconnect delay/slot behavior without understanding multi-instance runtime effects.
* Do not add silent reconnect fallbacks that mask real auth/protocol failures.
* Do not assume there is a strong lease/heartbeat ownership model between pods unless you have verified it.

---

## 4) Authorization, QR, pairing, and auth storage

### 4.1. QR flow

QR flow spans multiple layers:

* REST QR endpoint
* QR websocket gateway/cache
* Service-level scan startup
* Baileys `connection.update`
* Socket event emission
* State/status changes

Typical QR behavior:

```typescript
if (qr) {
  await this.changeStatusInstance(StatusInstance.Offline)
  const qrImage = (await QRCode.toDataURL(qr)).replace('data:image/png;base64,', '')
  this.emit('qr', { type: QRStatus.qrCode, message: qrImage })
  return
}
```

Requirements:

* Do not change QR payload shape casually.
* Do not change QR cache/websocket behavior without checking REST and websocket consumers.
* Do not assume QR generation is independent of socket lifecycle.
* Preserve state/status side effects unless explicitly changing auth flow.

### 4.2. Pairing code flow

Pairing/mobile/phone-code flow uses separate endpoints and Baileys calls such as:

```typescript
requestRegistrationCode(/* ... */)
register(/* ... */)
requestPairingCode(/* ... */)
```

Requirements:

* Do not collapse QR and pairing code flow into one generic auth path without explicit design.
* Preserve existing mobile/phone-code semantics.
* Be careful with retry, state, and error behavior around pending code states.

### 4.3. Successful authorization

Successful authorization is detected through socket-level events such as:

```typescript
connection === 'open' || isNewLogin
```

and then usually leads to:

```typescript
await this.changeStatusInstance(StatusInstance.Online)
await this.changeStateAccount(StateAccount.Authorized)
await this.updateOnlineStatus()
```

Requirements:

* Do not mark accounts authorized based only on REST request success.
* Do not bypass socket-confirmed auth transitions.
* Preserve webhook side effects of state changes.
* Be careful with `isNewLogin` behavior.

### 4.4. Auth/session storage

Auth/session/crypto state is stored through Mongo auth store integration.

Important collections include:

* `auth_creds`
* `auth_sessions`
* `auth_pre_keys`
* `auth_sender_keys`
* `auth_app_state_sync_keys`

The Baileys auth state integration includes behavior like:

```typescript
state: {
  creds: authData.creds,
  keys: {
      get: async (category, ids) => ...
      set: async (data) => ...
  }
},
saveCreds: async () => authDataService.saveAuthCreds(idInstance, authData.creds)
```

Requirements:

* Treat auth storage as a protocol boundary.
* Do not change key categories, collection routing, serialization, or save timing casually.
* Do not rename auth collections or fields without migration planning.
* Do not add local encryption/decryption behavior without understanding infrastructure-level encryption and Baileys expectations.
* Do not log auth material, sessions, prekeys, sender keys, creds, or raw auth JSON.

---

## 5) Outbound commands and send behavior

### 5.1. There are two outbound paths

There are two important send paths:

1. **Legacy REST path**
    * Controller calls runtime directly.
    * It waits for runtime send promise.
    * It usually returns `idMessage`.
    * It is not an outbox API.

2. **RabbitMQ command path**
    * Commands arrive through `sw-outgoing-{id}`.
    * Consumer parses `ProducerMessage`.
    * Service dispatches by `producerMessage.type`.
    * Runtime send uses `producerMessage.messageID`.
    * ACK/NACK behavior controls retry/failure semantics.

Requirements:

* Do not merge REST and RabbitMQ semantics casually.
* Do not make REST send asynchronous/outbox-like unless explicitly requested.
* Do not remove RabbitMQ retry behavior when changing send methods.
* Do not assume send success means delivered/read.

### 5.2. REST send semantics

Typical REST path:

```typescript
const idMessage = await this.waInstancesService.sendMessage(...)
res.status(HttpStatus.OK).json({ idMessage })
```

Service path:

```typescript
const waInstance = await this.throwWaInstance(idInstance)
return waInstance.sendMessage(...)
```

Runtime path eventually calls Baileys socket send methods.

Requirements:

* Preserve synchronous REST response semantics unless intentionally changing the public behavior.
* Preserve response shape such as `{ idMessage }` when existing clients depend on it.
* Do not add queue indirection to REST send as a cleanup.
* Do not report delivery/read status from REST send unless the protocol actually provides it at that time.

### 5.3. RabbitMQ command semantics

Typical command handling:

```typescript
case TypesMessage.SendMessage:
  await waInstance.sendMessage(..., producerMessage.messageID, ...)
  break

case TypesMessage.SendFileByUrl:
  await waInstance.sendFileByUrl(..., producerMessage.messageID, ...)
  break
```

Requirements:

* Preserve queue names such as `sw-outgoing-{id}`.
* Preserve `ProducerMessage` shape unless explicitly changing producer/consumer contracts together.
* Preserve `producerMessage.messageID` semantics.
* Preserve ACK/NACK behavior.
* Preserve failure webhook behavior after retry exhaustion.
* Preserve per-instance send mutex unless explicitly redesigned.

### 5.4. Message ID contracts

Message IDs are not just arbitrary strings.

Current behavior includes:

* REST path may generate IDs internally.
* Some upload paths use `MessageIdService.generateID`.
* RabbitMQ path uses `producerMessage.messageID`.
* “Sent through API” classification may depend on structurally recognizable IDs.

Requirements:

* Do not change ID generation or parsing casually.
* Do not normalize IDs in ways that break downstream classification.
* Do not replace existing ID generation with UUIDs or random IDs unless explicitly required.
* Treat message ID structure as contract-like.

### 5.5. Send success vs delivery status

Send success means:

* Runtime accepted/sent through socket
* A message ID was returned or reused
* It does **not** mean delivered
* It does **not** mean read
* Later status events produce webhook/status updates

Requirements:

* Do not rename send success into delivery success.
* Do not collapse send response and outgoing status webhook into one concept.
* Preserve separate outgoing status pipeline.

### 5.6. Retry, throttling, and ordering

RabbitMQ send path includes:

* NACK for temporary errors
* ACK after final failure webhook
* Per-instance mutex
* Send delay through `delaySendMessagesMilliseconds`
* `MAX_DELAY_SEND_MESSAGES`
* Consumer `prefetch(1)`

Requirements:

* Treat retry and throttling as runtime behavior, not incidental implementation detail.
* Do not change `prefetch(1)` casually.
* Do not remove sleep/delay behavior as “unnecessary”.
* Do not convert per-instance serialized sends into parallel sends without explicit design.
* Be careful with timeout, socket-not-open, Mongo, uploader/downloader, and temporary infrastructure error branches.

---

## 6) Incoming events, webhooks, journal, and history

### 6.1. Socket listeners

Runtime socket listeners are created after socket spawn and include events such as:

```typescript
this.sock.ev.on('messages.upsert' /*, ...*/)
this.sock.ev.on('messages.update' /*, ...*/)
this.sock.ev.on('message-receipt.update' /*, ...*/)
this.sock.ev.on('presence.update' /*, ...*/)
this.sock.ws.on('CB:ack,class:message' /*, ...*/)
this.sock.ev.on('call_log' /*, ...*/)
```

Requirements:

* Do not remove event listeners because they appear duplicate.
* Do not reorder event handling without understanding status/webhook effects.
* Do not assume Baileys events are clean, unique, or already normalized.
* Do not add expensive blocking work directly inside socket listeners.

### 6.2. Main incoming pipeline

Incoming messages roughly flow as:

```text
messages.upsert
  -> handleIncomingMessagesReceived(...)
  -> convertToIncomingWebhooks(...)
  -> mapper.webhook.incoming.process.toWebhook(...)
  -> mapper.journal.incoming.process.toJournal(...)
  -> addToQueueWithRetry(...) for webhook queue
  -> repository upsert for history/journal
```

Requirements:

* Preserve the separation between raw socket event, webhook payload, journal payload, queue publication, and history persistence.
* Do not change webhook and journal mappers independently if they must stay aligned.
* Do not remove dedupe/filter behavior without checking duplicate webhooks and history impact.
* Do not assume incoming processing is HTTP delivery.

### 6.3. Webhook types

Webhook types include values such as:

* `incomingMessageReceived`
* `outgoingMessageStatus`
* `outgoingMessageReceived`
* `outgoingAPIMessageReceived`
* `stateInstanceChanged`
* `calls`
* `avatar`

Requirements:

* Treat webhook type names as external contracts.
* Do not rename, merge, or split webhook types casually.
* Do not add new webhook types without downstream compatibility planning.
* Preserve payload shape unless intentionally versioning or migrating consumers.

### 6.4. Webhook delivery

Webhook delivery is not direct HTTP from socket listeners.

Current shape:

```text
WebhookServiceBayles
  -> RmqSendService
  -> sw-incoming-{id}
  -> downstream consumer(s)
```

Requirements:

* Preserve queue-based webhook delivery unless explicitly changing architecture.
* Preserve queue naming conventions.
* Preserve retry publication behavior.
* Do not replace queue publish with direct HTTP delivery as a cleanup.
* Do not block socket event processing on slow downstream webhook consumers.

### 6.5. Webhook publication retry

Webhook queue publication uses retry behavior, currently with many attempts and long delay.

Requirements:

* Treat retry count/delay as operational behavior.
* Do not reduce retries casually.
* Do not silently drop webhook events.
* If adding failure handling, make it explicit and observable.

### 6.6. History persistence

Mongo collections include:

* `ga_incoming_messages`
* `ga_outgoing_messages`
* `ga_chats`
* `ga_contacts`

Requirements:

* Treat collection names and document shapes as externally useful.
* Do not rename fields casually.
* Do not change upsert keys or dedupe semantics without checking history queries.
* Preserve history endpoints behavior when touching persistence.
* Be careful with migrations and backfills.

### 6.7. History and polling APIs

Current swcore has history endpoints such as:

* `GetChatHistory`
* `lastIncomingMessages`
* `lastOutgoingMessages`

A polling notification API such as `receiveNotification/deleteNotification` was not identified in this service.

Requirements:

* Do not invent notification polling behavior in swcore unless explicitly requested.
* Do not present history endpoints as webhook delivery.
* Keep history behavior separate from live event delivery.

---

## 7) Media and downloader behavior

### 7.1. Media event flow

Media behavior may branch depending on configuration.

When downloader integration is enabled:

```text
media event
  -> sw-downloads-{pool}
  -> downloader service
```

There is also a lazy download endpoint that reads stored message data and uploads/downloads through Media API.

Requirements:

* Do not assume all media is downloaded immediately.
* Do not assume downloader service is always enabled.
* Do not bypass Media API integration casually.
* Preserve lazy download behavior unless intentionally changing media architecture.
* Do not load large media into memory without checking runtime impact.

### 7.2. Media contracts

Requirements:

* Preserve media metadata shape in webhook/journal payloads.
* Preserve downloader queue naming.
* Preserve failure behavior when media is unavailable.
* Do not silently mark media downloaded if download/upload failed.
* Do not leak media URLs, tokens, or raw payloads in logs.

---

## 8) Security, secrets, and sensitive data

### 8.1. REST security boundary

Many REST routes include `:apiTokenInstance`, but the middleware may be a legacy stub and may not enforce a real token check.

Requirements:

* Do not assume REST routes are securely protected by `apiTokenInstance`.
* Do not strengthen or change token validation casually because existing legacy clients may depend on current behavior.
* If asked to improve security, treat it as a contract and migration task, not a tiny middleware patch.
* Be explicit about compatibility impact.

### 8.2. Sensitive data

Sensitive data includes:

* Auth creds
* Sessions
* Prekeys
* Sender keys
* App state sync keys
* Message bodies
* Media URLs
* Phone numbers / contacts
* Webhook payloads
* Redis/Mongo/Rabbit credentials
* SAPI/cache-api tokens
* Instance identifiers when combined with auth material

Requirements:

* Do not log sensitive material.
* Do not add debug logs with raw event payloads in production paths.
* Do not expose auth/session material through REST responses.
* Do not include secrets in error messages.
* Do not store secrets in new places without explicit design.

### 8.3. Existing logging risks

Known risky areas include:

* Full event/webhook/journal logging under event logger flags
* Message body logging on send errors
* CacheFactory logging Redis password/user

Requirements:

* When touching these areas, reduce sensitive exposure if it is safe and compatible.
* Do not add new broad payload logs.
* Prefer structured, redacted logs.
* Preserve useful operational context without exposing secrets.

Example preferred style:

```typescript
this.logger.warn({
  idInstance,
  messageId,
  errorCode,
  temporary: true,
}, 'outgoing command failed temporarily')
```

Avoid:

```typescript
this.logger.warn({
  body: producerMessage,
  authData,
  redisPassword,
}, 'debug')
```

---

## 9) Cache, SAPI, and external configuration

### 9.1. Cache behavior

The service uses Redis-like cache through `CacheFactory`.

Cache may affect:

* Reconnect slots
* Per-instance prefixes
* Prekey TTL
* QR/cache delivery
* Runtime coordination
* SAPI/cache-api settings

Requirements:

* Do not treat cache as purely optional decoration.
* Do not change cache key prefixes casually.
* Do not change TTLs without checking runtime behavior.
* Do not log cache credentials.
* Do not replace cache logic with in-memory state unless explicitly designing single-process behavior.

### 9.2. SAPI/cache-api settings

Instance settings may come from SAPI/cache-api rather than local database tables.

Requirements:

* Do not add local settings as a competing source of truth.
* Do not bypass SAPI updates casually.
* Do not assume local memory has the latest instance configuration.
* Preserve subscription/update behavior at startup.

---

## 10) Public and implicit contracts

### 10.1. Contract types

The public and implicit contracts include:

* REST route paths
* REST request payloads
* REST response payloads
* REST status codes
* REST error behavior
* `apiTokenInstance` route shape
* RabbitMQ queue names
* RabbitMQ message shapes
* RabbitMQ ACK/NACK semantics
* Retry counts and retry delays
* Message ID format and classification
* Webhook event type names
* Webhook payload shape
* Journal/history payload shape
* Mongo collection names
* Mongo document fields
* Auth/session storage categories
* QR websocket payloads
* Status/state enum values
* Downloader queue names
* Media metadata shape
* Log formats consumed by operations
* Timing/order assumptions in consumers

Requirements:

* Treat contract changes as serious.
* If a change affects a contract, identify the consumers.
* If consumers are unknown, assume they exist.
* Preserve backward compatibility by default.
* Add compatibility aliases only when they do not create ambiguous behavior.
* Document intentional breaking changes.

### 10.2. Explicit vs implicit behavior

Requirements:

* Do not assume undocumented means unused.
* Do not assume weird means wrong.
* Do not assume duplicate means redundant.
* Do not assume legacy means safe to remove.
* Do not assume a controller path is the only entrypoint.
* Do not assume a queue path has the same semantics as REST.

---

## 11) Code style

sw-core code should be **careful, explicit, and boring in a good way**.

### 11.1. General principles

* Prefer clear procedural flow over abstract indirection.
* Keep runtime paths readable end-to-end.
* Use early returns instead of deep nesting when safe.
* Add small named helpers only when they reduce local risk.
* Avoid speculative abstractions.
* Avoid broad formatting-only diffs.
* Keep stateful behavior visible.
* Preserve operational logs, but redact sensitive values.
* Prefer compatibility over elegance.

### 11.2. Naming rules

**Data → nouns:**

```typescript
idInstance
producerMessage
messageId
payload
webhook
journal
authData
stateAccount
statusInstance
queueName
headers
metadata
```

**Actions → verbs:**

```typescript
spawn
reboot
connect
logout
resetSession
sendMessage
sendFileByUrl
requestPairingCode
changeStateAccount
changeStatusInstance
handleIncomingMessagesReceived
convertToIncomingWebhooks
addToQueueWithRetry
buildOutgoingStatusWebhook
```

Requirements:

* Use names that describe actual behavior.
* Do not use names that imply a cleaner or larger architecture than exists.
* Avoid vague infrastructure poetry such as `orchestrateFlow`, `processMagic`, `unifiedGateway`, `smartDispatcher`.
* Do not rename widely used legacy methods unless explicitly required.

### 11.3. Function structure pattern

Preferred local structure:

1. **Doing some checks** — validate input, instance state, socket availability, required fields
2. **Getting the data** — parse payload, read runtime instance, derive options, fetch config
3. **Defining the functions** — only for small local helpers that clarify the current function
4. **Main flow** — call runtime/socket/mapper/repository/queue
5. **Handling result** — return ID/status/payload or ACK/NACK
6. **Handling errors** — classify temporary/permanent errors, preserve retry semantics

Example:

```typescript
async sendMessageFromCommand(producerMessage: ProducerMessage): Promise<void> {

  // Doing some checks

  if (!producerMessage.idInstance)
    throw new Error('idInstance is required');


  // Getting the data

  const waInstance = await this.throwWaInstance(producerMessage.idInstance);


  await waInstance.sendMessage(
    producerMessage.chatId,
    producerMessage.message,
    producerMessage.messageID,
  );
}
```

### 11.4. Comments

Comments should explain **why**, not obvious syntax.

Use these markers consistently when useful:

```typescript
// Constants
// Variables
// Doing some checks
// Getting the data
// Defining the functions
// FIXME:
// TODO:
// NOTE:
```

Good comment:

```typescript
// NOTE: Keep this check before ACK/NACK handling: temporary socket errors must be retried by RabbitMQ.
```

Bad comment:

```typescript
counter++  // Increment counter by one
```

---

## 12) Runtime and infrastructure guidance

### 12.1. Runtime reality

Requirements:

* Treat RabbitMQ, Mongo, Redis/cache, SAPI/cache-api, Media API, downloader, and Baileys as part of the working system.
* Do not validate behavior only by reading controller code.
* For socket lifecycle changes, inspect actual spawn/reconnect/auth/event paths.
* For queue changes, inspect producer message shape, consumer setup, ACK/NACK behavior, and downstream effects.
* For persistence changes, inspect query/read paths as well as writes.

### 12.2. RabbitMQ guidance

Requirements:

* Preserve queue names unless explicitly changing topology.
* Preserve `prefetch(1)` unless intentionally changing send concurrency.
* Preserve ACK/NACK semantics.
* Preserve retry behavior for temporary errors.
* Preserve failed webhook behavior on final failure.
* Do not acknowledge failed commands before required failure side effects are completed.
* Do not NACK permanent errors forever.
* Do not publish malformed webhook payloads.

### 12.3. Mongo guidance

Requirements:

* Treat auth collections as protocol-critical.
* Treat history collections as externally useful.
* Do not rename collections or fields without migration.
* Do not change upsert keys casually.
* Do not introduce unbounded writes in high-volume event paths.
* Be careful with indexes and query shapes.
* Do not store raw payloads in new collections without data-retention review.

### 12.4. Baileys/socket guidance

Requirements:

* Do not reimplement messenger protocol behavior locally.
* Do not patch around SDK behavior with HTTP-layer hacks unless explicitly required.
* Do not swallow socket errors without observability.
* Do not change listener registration casually.
* Do not send messages when socket/account state says it is unsafe, unless existing behavior already does so and the task requires preserving it.
* Do not convert runtime state to stateless request/response logic.

### 12.5. Multi-pod/process caution

A clear lease/heartbeat ownership model for instances between pods may not exist.

Requirements:

* Do not add assumptions that only one pod can own an instance unless verified.
* Do not add local-only coordination for globally visible behavior.
* Be careful with shared cache reconnect slots.
* Be careful with duplicated consumers or duplicated socket sessions.
* If solving multi-pod ownership, treat it as an architecture task.

---

## 13) API growth and change management

### 13.1. Adding new behavior

Good fits for this repository:

* Small runtime-safe helpers around existing Baileys operations
* Explicit additions to existing send/auth/history/media flows
* Mapper extensions that preserve old payloads
* Operational fixes to reconnect/retry behavior
* Security hardening with compatibility planning
* Redacted logging improvements
* Bug fixes with narrow blast radius

Poor fits unless explicitly required:

* Full gateway redesign
* New public API product layer
* Generic outbox system
* Approval/workflow/business document logic
* New database ownership model
* New multi-pod lease system hidden inside a small bugfix
* Big FSM rewrite
* Queue topology migration without producer/consumer migration plan

### 13.2. Backward compatibility

When changing behavior:

* Consider whether clients depend on current response shape, status code, error text, message ID, queue payload, webhook event type, or Mongo field.
* Preserve compatibility intentionally.
* Add new fields instead of changing existing fields when possible.
* Do not remove legacy aliases unless explicitly requested.
* If strict validation is added, treat it as a possible breaking change.
* If defaults are changed, explain why.

### 13.3. Output behavior

Requirements:

* Keep webhook payload behavior predictable.
* Keep outgoing status behavior separate from send acceptance.
* Keep history writes aligned with webhook/journal mapping.
* Do not change media output shape casually.
* Do not change state/status event shape casually.
* Do not change ID generation casually.

---

## 14) Domain-specific decision rules

### 14.1. If the task touches lifecycle

Read:

* `wa.instances.service.ts`
* `wa.instance.bayles.client.ts`
* `jobs.ts`
* Auth store integration
* QR gateway if authorization is involved
* Rabbit consumer lifecycle if consumers are involved

Ask:

* Does this affect spawn/reboot/connect/logout/reset?
* Does this affect state/status webhooks?
* Does this affect existing sessions?
* Does this affect consumer start/stop?
* Does this affect reconnect behavior?

Default posture:

* Make the smallest safe change.
* Preserve existing state names and transitions.
* Avoid centralizing lifecycle logic unless explicitly required.

### 14.2. If the task touches outbound send

Read:

* Controller send endpoint if REST is involved
* `WaInstancesService` send methods
* `WaInstanceBaylesClient` send methods
* RabbitMQ consumer if queue commands are involved
* Webhook status handling if status behavior is involved
* Message ID utility if IDs are involved

Ask:

* Is this REST path or RabbitMQ path?
* Who owns `messageID`?
* What happens on temporary error?
* What happens after retry exhaustion?
* Does success mean accepted or delivered?
* Does this affect rate delay or ordering?

Default posture:

* Preserve accepted-vs-delivered semantics.
* Preserve ACK/NACK behavior.
* Preserve per-instance serialization.

### 14.3. If the task touches incoming events or webhooks

Read:

* Socket listener registration
* `WebhookServiceBayles`
* Mapper interfaces
* Webhook mapper tree
* Journal mapper tree
* RabbitMQ publisher
* History repositories/entities

Ask:

* Which raw event starts the flow?
* Which webhook type is emitted?
* Is history written?
* Is media involved?
* Is this incoming, outgoing, status, call, avatar, or state?
* Does dedupe/filter behavior change?

Default posture:

* Preserve payload shape.
* Preserve queue publication.
* Preserve history alignment.

### 14.4. If the task touches auth/session storage

Read:

* `use-multi-mongo-auth-state.ts`
* `auth.data.mongo.service.ts`
* Auth models
* Baileys socket creation path
* Reboot/spawn path

Ask:

* Does this affect existing sessions?
* Does this affect key categories?
* Does this affect save timing?
* Does this affect serialization?
* Does this leak sensitive material?
* Is migration required?

Default posture:

* Treat as high-risk.
* Avoid changes unless necessary.
* Never log raw auth data.

### 14.5. If the task touches security

Read:

* Middleware
* Controller route structure
* Instance account model
* Auth/session storage
* Logging paths
* Config/env usage

Ask:

* Is this changing a legacy behavior?
* Are existing clients relying on weak/no validation?
* Is rollout/migration needed?
* Are secrets or message bodies exposed?
* Can hardening be done with compatibility?

Default posture:

* Improve redaction safely.
* Do not silently break clients.
* Do not overstate current security guarantees.

### 14.6. If the task touches media

Read:

* Webhook media handling
* Downloader service
* Lazy download endpoint
* Media API client
* Stored message entities

Ask:

* Is downloader enabled?
* Is this lazy or eager download?
* Is metadata shape changing?
* Are files or URLs sensitive?
* Does this affect memory usage?

Default posture:

* Preserve branch behavior.
* Avoid loading large files in memory.
* Preserve Media API contract.

---

## 15) Documentation and verification

### 15.1. Documentation responsibilities

If functionality changes, update as needed:

* Route/API docs
* Queue contract docs
* Webhook payload examples
* State/status documentation
* Auth/session migration notes
* Environment/config notes
* Operational runbooks
* Comments near public behavior
* Examples used by downstream services

Requirements:

* Do not let code and docs diverge further.
* If docs are already incomplete, add narrowly useful notes near the changed behavior.
* Do not document guessed behavior as certain.

### 15.2. Verification priorities

Because this repository is a runtime integration layer, useful checks are usually flow-based.

Prefer:

* Real or integration-style send path checks
* RabbitMQ command handler checks
* ACK/NACK behavior checks
* Webhook payload shape checks
* History persistence checks
* Auth storage compatibility checks
* QR/pairing flow checks
* Reconnect/reboot smoke checks
* Media downloader/lazy download checks when touched

Less useful by itself:

* Decorative unit tests around mocked helpers that do not exercise runtime semantics
* Snapshot tests that bless accidental legacy behavior without explanation
* Controller-only checks for socket/event behavior

### 15.3. Change summary requirements

When summarizing a change, include:

* What flow was changed
* Which contracts were preserved
* Whether REST, RabbitMQ, webhook, Mongo, auth, or media behavior changed
* Whether retries/ACK/NACK/delays were changed
* Whether message ID or state/status semantics changed
* What was verified
* What remains risky or unverified

Example summary:

```text
Changed the RabbitMQ outbound SendFileByUrl handling to classify uploader timeout as a temporary error.

Preserved:
- sw-outgoing-{id} queue contract
- producerMessage.messageID usage
- per-instance send mutex
- ACK after final failed webhook
- REST send behavior

Verified:
- temporary timeout returns NACK
- permanent validation error is not retried
- failed webhook is emitted after retry exhaustion
```

---

## 16) Do Not

* ❌ Don’t describe this repository as a clean standalone REST API product.
* ❌ Don’t treat REST controllers as the core of the system.
* ❌ Don’t ignore `WaInstancesService` and `WaInstanceBaylesClient` when touching runtime behavior.
* ❌ Don’t change live socket lifecycle casually.
* ❌ Don’t change auth/session storage shape casually.
* ❌ Don’t log auth material, session keys, prekeys, sender keys, message bodies, media URLs, or credentials.
* ❌ Don’t assume `apiTokenInstance` is a strong security boundary.
* ❌ Don’t silently strengthen legacy REST auth without a compatibility plan.
* ❌ Don’t merge `StateAccount` and `StatusInstance`.
* ❌ Don’t add, rename, or remove state/status enum values casually.
* ❌ Don’t introduce a new FSM as a cleanup.
* ❌ Don’t change QR or pairing behavior without tracing the full auth flow.
* ❌ Don’t convert REST send into an async outbox API unless explicitly required.
* ❌ Don’t treat send success as delivered/read success.
* ❌ Don’t change message ID generation or parsing casually.
* ❌ Don’t rename RabbitMQ queues casually.
* ❌ Don’t change `ProducerMessage` shape casually.
* ❌ Don’t remove `prefetch(1)` casually.
* ❌ Don’t bypass ACK/NACK retry semantics.
* ❌ Don’t remove per-instance send mutexes casually.
* ❌ Don’t remove send delays/rate throttling as “cleanup”.
* ❌ Don’t replace queue-based webhook delivery with direct HTTP delivery casually.
* ❌ Don’t change webhook type names casually.
* ❌ Don’t reshape webhook payloads without downstream compatibility planning.
* ❌ Don’t change journal/history mapping independently from webhook behavior when they must stay aligned.
* ❌ Don’t rename Mongo collections or fields without migration.
* ❌ Don’t change auth key category routing without understanding Baileys expectations.
* ❌ Don’t add broad abstractions that hide runtime flow.
* ❌ Don’t migrate frameworks or rewrite services for style.
* ❌ Don’t perform formatting sweeps mixed with behavior changes.
* ❌ Don’t remove legacy behavior just because it looks strange.
* ❌ Don’t assume undocumented behavior is unused.
* ❌ Don’t assume duplicate-looking logic is redundant.
* ❌ Don’t add silent fallbacks that hide protocol/runtime failures.
* ❌ Don’t add local-only state for globally visible multi-pod behavior.
* ❌ Don’t bypass SAPI/cache-api as source of instance settings.
* ❌ Don’t treat Redis/cache as a harmless optimization.
* ❌ Don’t add downloader/media behavior that loads large files into memory without need.
* ❌ Don’t expose raw errors from Baileys, Mongo, RabbitMQ, Redis, or Media API if they contain sensitive data.
* ❌ Don’t deepen divergence between implementation, docs, queues, webhook examples, and operational reality.
* ❌ Don’t turn this legacy runtime-worker into architecture theater.

---

## 17) Pre-change checklist

* [ ] The affected flow was identified: lifecycle, auth, outbound, inbound, webhook, history, media, security, cache, or runtime setup.
* [ ] The real runtime path was traced end-to-end.
* [ ] Legacy contracts were considered before changing behavior.
* [ ] Public and implicit contracts were preserved unless intentionally changed.
* [ ] Queue names and RabbitMQ ACK/NACK behavior were preserved unless intentionally changed.
* [ ] Message ID semantics were preserved unless intentionally changed.
* [ ] Send accepted vs delivered/read semantics were not confused.
* [ ] StateAccount and StatusInstance semantics were preserved.
* [ ] Auth/session storage shape and save behavior were not broken.
* [ ] Sensitive data was not logged or exposed.
* [ ] Webhook payload shape and event types were preserved unless intentionally changed.
* [ ] Journal/history persistence stayed aligned with webhook behavior.
* [ ] Cache/SAPI/runtime assumptions were checked.
* [ ] Minimal sufficient diff was used.
* [ ] No broad refactors, formatting sweeps, or speculative abstractions were introduced.
* [ ] Documentation/examples/config notes were updated if behavior changed.
* [ ] Verification matched the real integration flow, not only isolated helper code.
* [ ] Remaining risk or uncertainty was stated clearly.
