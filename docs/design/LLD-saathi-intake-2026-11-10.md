---
id: LLD-saathi-intake
kind: contract
governs: Phase 0 modules M01 to M13: shared types, ports, store, keys, config, edge, elder channels, radar, redaction, worker, ASR; part of LLD-saathi-2026-11-10.md
status: draft, pending review
upstream: TRD-saathi-ports-2026-11-09.md sha256 993e28969a4c98164e04767cd324f88930b1e73dbc2a854f74a725be1163ec3c; TRD-saathi-states-2026-11-09.md sha256 6a6843a411f59ce326c9573fe7ed14e6bb76ed19e30c1b9a5e3514b4bda25db6; TRD-saathi-2026-11-09.md sha256 88d14bcfec16d0b6b158a0437c2f2c7dd61ab84b9ab7f1c8b1bf94c51d322075; LLD-saathi-2026-11-10.md sha256 bda71cce7ddb7c9fe5b18223d08d360f881cfc660c1d3befaf70294488692309
sources: src/saathi/ and tests/ on branch phase-0/foundation at 3d46456; the TRD components table and ports part; DOC-PHILOSOPHY.md
checked-at: 3d46456, 2026-11-10. Code read with `git show`. Nothing below was run in this pass.
---

Read the module you build, then its Never line. Tables are in LLD-saathi-data-2026-11-10.md. Python 3.12, async, pydantic v2; every name is P unless an E cites 3d46456. Ports are `typing.Protocol` classes in `saathi/ports.py`.

# Saathi LLD intake modules: from webhook to worker

## M01 `saathi/domain.py`, `saathi/errors.py`, `saathi/ids.py`

```python
HouseholdId = NewType("HouseholdId", str)   # likewise ElderId, ApproverId, InboxId, RequestId, EventId,
RunId = NewType("RunId", str)               # PersonRef (opaque), IdemKey, ReplyKey, TemplateId
class DataClass(StrEnum): RESTRICTED="restricted"; PERSONAL="personal"; PUBLIC="public"; SYNTHETIC="synthetic"   # PL-01
class Source(StrEnum): WHATSAPP="whatsapp"; TELEGRAM_ELDER="telegram_elder"; CAREGIVER_FORWARD="caregiver_forward"
class MediaKind(StrEnum): TEXT; AUDIO; IMAGE; VIDEO; DOCUMENT; UNSUPPORTED      # E: OTHER at domain.py:30-36
class MediaRef(BaseModel, frozen=True): source: Source; handle: str; mime: str | None = None
class InboundMessage(BaseModel):           # parsed by a channel, never stored whole
    source: Source; provider_message_id: str; sender_ref: str; kind: MediaKind
    text: str | None = None; media: MediaRef | None = None; received_at: datetime
class RawPart(BaseModel): part: Literal["text", "caption", "transcript"]; text: str
class NewInbox(BaseModel):                  # one inbox row plus its parts, inserted in one transaction
    id: InboxId; household_id: HouseholdId; elder_id: ElderId; msg: InboundMessage
    sender_hmac: str; payload_sha256: str; not_before: datetime | None; parts: list[RawPart]
class Lease(BaseModel): household_id: HouseholdId; inbox_id: InboxId; elder_id: ElderId; token: UUID
class AlertOpen(BaseModel): kind: Literal["emergency", "scam", "unscanned"]; origin: str; rule_ids: list[str]
class SummarySlots(BaseModel): ...        # enums and counts only; fields set by the unit that builds summaries (WU-15)
class AlertView(BaseModel): ...            # one alert event as the service reads it (WU-17)
def new_id() -> str: ...                    # E: domain.py:21, kept; ids.py adds derive_id(prefix, parent)
def idem_key(*parts: str) -> IdemKey: ...   # ":".join, no empty part
```

These two view types sit in `domain.py` so the caregiver bot (M15) does not import the worker (M12). `errors.py` defines `SaathiError(Exception)` and every error of the TRD ports part section 3, each a subclass (`ProactiveNotAllowed(reason)`, `ChannelDown`, `AsrUnavailable`, `ClipHashMismatch`, `ReceiptMissing`, `NoAcceptedResult`, `TenantNotSet`, `KeyMissing`, `ConflictLost`, `FlagUnreadable`, `AuditUnavailable`, `SignoffMissing`, `CallbackRejected`). Removed from domain: `Channel` and `Elder.wa_phone_e164` (change 30); `Approver`, `Quote`, `Slots`, `Understanding` stay for Phase 1.
- Serves: RQ-105, 109; PL-01; TQ-67. Never: a field named for a platform in core modules; a phone number or chat id in a core type.

## M02 `saathi/config.py`, `saathi/hosts.py`

`Settings(BaseSettings)` (E: `config.py:11-67`, env prefix `SAATHI_`) keeps deployment values only: `env`, `database_url` per role (`db_url_edge`, `db_url_core`, `db_url_model`, `db_url_owner`), `kek_file`, `sender_hmac_key_file`, `approval_secret_file`, `wa_*` tokens, `tg_caregiver_token`, `tg_elder_token`, `tg_elder_secret`, `egress_*` base URLs, `trace_dir`, `media_dir`, `health_path`. Removed: `elder_phones`, `tg_primary_approver_id`, `tg_backup_approver_ids`, `caregiver_name`, `persona_name`, `paid_monthly_cap_inr` (E: `config.py:40`, `:55`), which move to `household_config` (RQ-110, change 29).
```python
def require_secrets(s: Settings) -> None: ...   # raises ConfigError on an empty or under 32 byte approval secret (change 20, PL-14)
ALLOWED_HOSTS: Mapping[str, str]                 # hosts.py: host to its POLICY-data row (PL-02, PL-04)
def configured_hosts(s: Settings) -> set[str]: ...   # every base URL the code can call
```
- Serves: RQ-43, 110; PL-04, 12, 13, 14. Never: a household value; a default for a secret; a base URL whose host is not a row.

## M03 `saathi/ports.py`, `saathi/clock.py`

`ports.py` holds the TRD ports section 2 verbatim (ElderChannel, CaregiverChannel, Escalator, ASR, AudioCodec, TTS, VoiceStore, Vision, Gateway, AuditSink, Clock, Store) and adds the protocols below. The states tables (M21) and every fake are built in the same unit (WU-03), before the store, so that no later unit needs a type that comes after it:
```python
class Classifier(Protocol):          # G1 local; Phase 0 has a fake only
    async def flags(self, text: str, *, run: RunId) -> list[ClassifierFlag]: ...   # ClassifierFlag(kind, score)
class TraceExporter(Protocol):
    def span(self, name: str, **fields: SpanValue) -> None: ...   # never raises, never blocks the caller
class Tx(Protocol):                  # what a repo needs from a transaction: execute, fetch, household, now
    household: HouseholdId; now: datetime
class Sealer(Protocol):              # implemented by KeyRing (M05); repos take it so M04 does not import M05
    def seal(self, public: PublicKey, plaintext: bytes, *, aad: bytes) -> bytes: ...
class Opener(Protocol):
    async def open(self, tx: Tx, blob: bytes, key_id: str, *, aad: bytes) -> bytes: ...
class MediaStore(Protocol):
    async def put(self, ref: MediaRef, blob: MediaBlob, *, household: HouseholdId) -> str: ...  # returns a local path key
    async def get(self, key: str) -> MediaBlob: ...
```
`clock.py`: `SystemClock` (UTC `now`, `monotonic`, `sleep_until`) and `FakeClock(start)` with `advance(seconds)`, which wakes sleepers in order. Ports raise only the errors listed in the TRD (rule 2). Every port has a fake in `tests/fakes/` that fails on demand per error.
- Serves: TQ-67; RQ-105, 18, 82. Never: a port method that takes a phone number; `datetime.now()` outside `SystemClock`.

## M04 `saathi/store/` (db.py, scan.py, repos/, migrate.py)

```python
class Role(StrEnum): EDGE="edge"; CORE="core"; MODEL="model"; OWNER="owner"; MIGRATOR="migrator"
class PgTx:                                   # implements ports.Tx: one transaction on one connection
    conn: asyncpg.Connection; household: HouseholdId; now: datetime
class Store:
    def __init__(self, pool: asyncpg.Pool, role: Role, clock: Clock) -> None: ...
    @asynccontextmanager
    async def tx(self, *, household: HouseholdId, lock_household: bool = False) -> AsyncIterator[PgTx]: ...
    @property
    def scan(self) -> ScanFns: ...            # raises PermissionError for a role without scan grants
```
`tx` opens a transaction, runs `SELECT set_config('app.household_id', $1, true)` first, and with `lock_household` takes `pg_advisory_xact_lock(hashtextextended(household, 1))`. An empty household raises `TenantNotSet` before any statement. A `StoreUnavailable` wraps connection errors.
```python
class ScanFns:                                # one method per security-definer function (grants part, section 2)
    async def resolve_sender(self, hmac: str) -> tuple[HouseholdId, ElderId] | None: ...
    async def resolve_approver(self, telegram_id: int) -> tuple[HouseholdId, ApproverId] | None: ...
    async def lease_next(self, now: datetime, lease_s: int = 60) -> Lease | None: ...
    async def reap_inbox(self, now: datetime) -> list[tuple[HouseholdId, InboxId]]: ...
    async def unacked_inbox(self, now: datetime, limit: int = 50) -> list[tuple[HouseholdId, InboxId]]: ...
    async def due_alerts(self, now: datetime, limit: int = 50) -> list[tuple[HouseholdId, EventId]]: ...
    async def unscanned_audio(self, now: datetime, max_s: int) -> list[tuple[HouseholdId, InboxId]]: ...
    async def rejected_digest(self, since: datetime) -> tuple[int, int]: ...
    async def reject_inbound(self, sender_hmac: str, reason: str) -> None: ...   # tenantless insert, no household (C-6)
```
Repos are stateless classes with static async methods that take `Tx` first (names from the TRD Store port):
- `InboxRepo.insert_if_new(tx, n: NewInbox, *, public_key: PublicKey, sealer: Sealer, expires_at) -> bool` inserts the row and one sealed `raw_text` row per part with `ON CONFLICT DO NOTHING`, no RETURNING; reads the tag; `False` on a repeat. Also `renew(tx, lease, until) -> bool`, `settle(tx, lease, state) -> bool`, `mark_acked(tx, inbox_id, now)`, `set_radar_at`, `add_transcript(tx, lease, text, sealer, public_key)`. Every worker write names `lease.token` and returns `False` on a stale token.
- `RequestRepo.create_first(tx, inbox_id, elder_id, intent) -> RequestId` (id `r_<inbox>`), `transition(tx, id, from_, event, to, reason) -> bool` (calls `Machine.lookup` of M21, built in WU-03, then one `UPDATE ... WHERE id AND state = from_`), `get`.
- `AlertRepo.open(tx, inbox_id, elder_id, a: AlertOpen, t0, audio_ref) -> bool`, `due(tx, event_id) -> AlertView`, `add_attempt(tx, ...) -> bool` (unique `idem_key`), `mark_attempt`, `ack(tx, event_id, approver, via: Literal["tap","dtmf"]) -> bool`, `set_stage`, `set_next_due`, `false_alarm`, `block(tx, event_id, reason)`.
- `SignoffRepo.valid(tx, scope, now) -> SignoffState` (valid, expires_at, revoked); inserts only through the owner role.
- `IdentityRepo.person_ref(tx, elder_id, source) -> PersonRef`, `register(tx, ...)`; the `PersonDirectory` of M08 opens `person_ref_enc` with the private half in the core.
- `UpdateRepo.first_seen(tx, bot, update_id, kind) -> bool`; `FlagRepo.read_paused(tx) -> bool` (raises `FlagUnreadable`); `VoiceRepo` (M16); `LedgerRepo` (M19); `ConsentRepo` and `ConfigRepo` (M06).

`migrate.py`: `async def migrate(dsn: str, dir: Path) -> list[str]` applies files `NNN_*.sql` in order inside one transaction each, records them in `schema_migrations(version, sha256, applied_at)`, and refuses an applied file whose hash changed.
- Serves: RQ-79, 109, 38, 35 (resume rows), 56a; TQ-71. Never: a raw `asyncpg` connection outside `saathi/store/`; a lease write without the token; a `DELETE` on a request path table.

## M05 `saathi/store/keys.py` (RQ-111a)

```python
class KeyRing:                                # implements Sealer and Opener of ports.py; imports the store, never the reverse
    def __init__(self, kek: bytes | None, reader: KeyReader) -> None: ...
    async def public(self, tx: Tx, purpose: Literal["content"]) -> PublicKey: ...    # edge and core
    def seal(self, public: PublicKey, plaintext: bytes, *, aad: bytes) -> bytes: ...  # edge may call
    async def open(self, tx: Tx, blob: bytes, key_id: str, *, aad: bytes) -> bytes: ...  # core only
    async def hmac(self, tx: Tx, domain: str, value: str) -> tuple[str, str]: ...        # (hex, key_id), audit key
    def wrap(self, secret: bytes, *, household: HouseholdId, purpose: str, key_id: str) -> bytes: ...
def sender_hmac(key: bytes, channel: str, sender: str) -> str: ...
```
Formats are in the data part, section 4. The edge builds `KeyRing(kek=None)`: `open`, `hmac` and `wrap` raise `KeyMissing`.
- Errors: `KeyMissing`. Serves: RQ-111a, 56a; TQ-73. Never: log or return a private key; use the HMAC key for the chain hash; hold the KEK in the edge.

## M06 `saathi/householdcfg.py`, `store/repos/config.py`, `store/repos/consents.py`

```python
class HouseholdConfig(BaseModel, extra="forbid"):    # the `values` column; all fields without defaults unless noted
    version: int
    caps: Caps | None = None                         # None denies (RQ-26); Caps(order_inr, day_inr, monthly_soft_inr: Decimal)
    approvers: list[ApproverEntry]; elder_channel_active: Literal["whatsapp", "telegram"]
    channels_enabled: frozenset[str]; helper_name: str; reply_language: str = "hi"; asr_hint: str = "hi"
    models_local_only: bool = True; paid_model_cap_inr: Decimal | None = None; false_alarm_budget_per_approver_week: int = 2; voice_calls_per_event: int = 6
    radar_audio_max_s: int = 120; rate_limit_msgs: int = 20; rate_limit_window_s: int = 600
    approval_mode: Literal["every_order"] = "every_order"; experimental: bool = False
class ConfigRepo:
    async def load(tx: Tx) -> HouseholdConfig: ...   # newest version; raises ConfigMissing
class ConsentRepo:
    async def append(tx: Tx, ev: ConsentEvent) -> None: ...        # owner role only
    async def allowed(tx: Tx, subject: str, use: str, recipient: str) -> bool: ...   # latest event wins; no row is False
```
`allowed` also returns `False` when the household `capacity` is `unknown` (PL-34). `consent.gate(tx, use, recipient)` wraps it for the enforcement points: the worker before a request (R01 guard), the alert audio attachment, hosted routes. It never raises on a missing row.
- Serves: RQ-110; PL-05, 33, 34; TQ-72, 81. Never: read a household value from `Settings`; allow on an unreadable row.

## M07 `saathi/edge/` (app.py, handler.py, ratelimit.py, health.py) and `saathi/app.py`

```python
def create_edge_app(settings: Settings, store: Store, keys: KeyRing, channels: Mapping[Source, ElderChannel],
                    radar: Callable[[str | None], RadarResult], clock: Clock) -> FastAPI: ...
async def handle_inbound(raw: bytes, headers: Mapping[str, str], source: Source, *, deps: EdgeDeps) -> HTTPStatus: ...
async def rate_state(tx: Tx, elder: ElderId, now: datetime) -> datetime | None: ...   # control.rate_state: not_before or None (C-1)
```
Routes: `GET`/`POST /wa/<webhook path>`, `POST /tg/elder/<random path>` (Phase 1 registration), health route of M20. `saathi/app.py` (E: `app.py:77-165`) becomes the composition root `main_edge()`; `saathi-api` points at it.
`handle_inbound` order: (1) size over limit: 413; (2) signature or secret-token header on the raw body, else 401 and a counter; (3) JSON parse, else 400; (4) `channel.parse` to messages; (5) per message: `resolve_sender`, unknown: `rejected_inbound` row, no reply; (6) in `Store.tx(household)`: `rate_state`, radar on text or caption, `InboxRepo.insert_if_new`, and on a hit `AlertRepo.open` (origin `radar_edge`), or for audio over the limit an `unscanned_edge` event; (7) commit; (8) return 200. A failed insert returns 5xx.
- Serves: RQ-79, 81, 16, 18; TQ-01, 02, 08. Never: return 200 before the commit; parse before the signature; keep a seen-set; call a model; answer an unknown sender; skip the keyword match on a rate-limited message; hold a private key.

## M08 `saathi/channels/whatsapp.py` and M09 `channels/telegram_elder.py`

```python
class WhatsAppElderChannel:                    # implements ElderChannel; kind = "whatsapp"
    def __init__(self, client: WhatsAppClient, people: PersonDirectory, clock: Clock) -> None: ...
def verify_signature(app_secret: str, body: bytes, header_value: str | None) -> bool: ...   # E: whatsapp.py:29-34, kept
def parse_webhook(payload: dict[str, Any]) -> list[InboundMessage]: ...                      # E: :41-80, changed
class TelegramElderChannel:                    # kind = "telegram_elder"; capabilities exclude templates
    def __init__(self, client: TelegramClient, people: PersonDirectory) -> None: ...
def verify_secret_token(expected: str, header_value: str | None) -> bool: ...   # hmac.compare_digest
def parse_update(update: dict[str, Any]) -> list[InboundMessage]: ...
class PersonDirectory(Protocol):               # resolves a PersonRef to a platform address, in the core only
    async def address(self, ref: PersonRef, channel: str) -> str: ...
```
WhatsApp changes: sticker, contact and location parse to `MediaKind.UNSUPPORTED` (E: sticker maps to image, `:25`); the client takes `PersonRef` through `PersonDirectory`; a closed window raises `ProactiveNotAllowed("window_closed")`. Phase 0 builds the Telegram class and its contract rows against a fake HTTP client; the live bot, start codes and switch are Phase 1 (C-2 in the hub).
- Serves: RQ-105, 81; TQ-67, 02. Never: a platform name outside these two modules and `edge/`; a phone number or chat id in a return value.

## M10 `saathi/guards/radar.py` (RQ-16, 17, 18)

```python
@dataclass(frozen=True)
class RadarResult: emergency: bool; scam: bool; matches: tuple[str, ...]; rule_ids: tuple[str, ...]  # E: radar.py:78-88, adds rule_ids
def scan(text: str | None) -> RadarResult: ...                       # E: :91, kept, never raises
async def combine(text: str | None, clf: Classifier | None, *, run: RunId) -> RadarResult: ...   # keyword OR classifier
```
`combine` ORs the classifier's flags into the result; a classifier error or absence returns the keyword result; no classifier output clears a hit. Phrases are only added: `tests/test_guards.py` holds a frozen `PHRASES_V1` set, and a test fails when one is missing from the module (RQ-16). Rule ids are `RD-<kind>-<n>`, stable per phrase.
- Never: let a model clear a hit; wait for the lease queue, ASR or a model; remove a phrase without a test that names it.

## M11 `saathi/guards/pii.py` (RQ-41)

`redact(text: str | None) -> RedactionResult` (E: `pii.py:74`, kept), `RedactionResult(text, entities)`. Adds `VOTER_RE` (3 letters and 7 digits) and `PASSPORT_RE` (1 letter and 7 digits) with a checksum-free, word-bounded match (change 8). `assert_clean(text) -> None` raises `RestrictedDataLeft` when a restricted pattern remains; the gateway and trace exporter call it. Text only; media is never claimed clean.
- Never: call a model; pass text that still matches a restricted pattern.

## M12 `saathi/worker/` (loop.py, lease.py, steps.py, summaries.py)

```python
class Worker:
    def __init__(self, store: Store, deps: WorkerDeps, clock: Clock, cfg: WorkerConfig) -> None: ...
    async def run_forever(self) -> None: ...                 # polls every cfg.poll_s (2 s); stops on cancel
    async def run_once(self) -> bool: ...                    # True when a row was processed; used by tests
class LeaseRenewer:                                          # renews a lease every lease_s / 3 while a step runs
    async def __aenter__(self) -> Lease: ...
    async def __aexit__(self, *exc) -> None: ...
async def ack_pass(store: Store, deps: WorkerDeps, clock: Clock) -> int: ...   # RQ-74, outside per-elder order
async def process(lease: Lease, deps: WorkerDeps) -> Outcome: ...             # steps 7 to 13 of flow 1.1
def summary_slots(kind: MediaKind, rule_ids: Sequence[str], intent: str) -> SummarySlots: ...
@dataclass(frozen=True)
class RunProduced: quotes: int; sources: int; steps: int; effects: int    # all zero: the request ends failed, reason no_result (RQ-36)
```
Phase 0 `process` follows flow 1.1 (build part) in order. (1) TX-W1: load the inbox row and its `raw_text`, `ConfigRepo.load`, `consent.gate`, `RequestRepo.create_first` (`new` to `received`, R01); a closed gate ends `blocked`, reason `consent_missing` (R02). (2) For media, `ElderChannel.fetch_media` and `MediaStore.put`; a failure inside the URL lifetime sets `media_lost` (N09) and a flagged summary. (3) For audio, `AudioCodec.to_wav16k` and `ASR.transcribe` (fakes in Phase 0); an error forwards the audio with a templated reply and no transcript. (4) TX-W2: `InboxRepo.add_transcript`, radar `combine`, `AlertRepo.open` (origin `radar_worker`). (5) TX-W3: transition `received` to `handoff`, reason `summary_only` (R05). (6) Send the caregiver summary and the elder acknowledgement, each keyed by `idem_key(inbox_id, "<effect>")`. (7) TX-W4: `InboxRepo.settle(lease, processed)`. A kind `unsupported` ends `unsupported` with a summary (N08). The step never calls a model and never marks a row processed before its effects are sent. `ack_pass` sends the stored acknowledgement clip or text to rows with `acked_at` null within 10 s of `received_at`. `summary_slots` builds the one-line summary from enums and counts, never from transcript text (RQ-97).
- Serves: RQ-79, 35 (partial), 36, 74 (ack), 82; TQ-03, 06, 12. Never: mark a row processed before its effects are sent; call a model; hold request state in memory between steps; drop a message; write after a lost lease.

## M13 `saathi/perception/asr.py`, `saathi/voice/codec.py`, `saathi/media.py`

```python
class LocalAsrClient:                          # ASR; E: the existing client class, asr.py:37-56, renamed
    async def transcribe(self, audio: AudioBlob, *, hint: str = "hi") -> Transcript: ...
class OpusCodec:                               # Phase 1 (change 26, C-12): subprocess, no shell, 16 MiB cap, 30 s timeout
    async def to_wav16k(self, audio: AudioBlob, *, max_seconds: float) -> WavBlob: ...
    async def encode_clip(self, wav: WavBlob) -> VoiceClip: ...
class LocalMediaStore:                         # MediaStore: files under `media_dir`, mode 600, pruned at 30 days
```
Phase 0 builds the `ASR` and `AudioCodec` ports with `FakeASR(script)` and `FakeCodec` (scripted text, `AsrUnavailable`, `AudioUndecodable`, `DecodeTimeout`); `LocalAsrClient` keeps posting what it is given and the real decode path waits for Phase 1. The ASR image loses ffmpeg and `--convert` now (change 22).
- Errors: `AsrUnavailable`, `AudioUndecodable`, `DecodeTimeout`. Serves: RQ-16 (audio forward and the `unscanned` alert); TQ-12. Never: send audio to a hosted tier without a receipt; clear a radar flag.

Skipped or not checked: no signature or query was run; the `LeaseRenewer` interface and the rate state shape are proposals; the module sizes were not estimated; the Telegram client library calls were not read.
