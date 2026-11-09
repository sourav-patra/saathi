---
id: TRD-saathi-ports
kind: contract
governs: the Protocol ports, the data model migration plan and the database roles; part of TRD-saathi-2026-11-09.md
status: draft, new, pending review and owner lock (see docs/LOCKS.md)
upstream: PRD-saathi-2026-11-09-r2.md sha256 341ecc305674155b388184dd59323faeda447676c01d9d002c188b9aeadfdd8b; SPEC-saathi-requests-2026-11-09.md sha256 b798a89a5d876a26d7e30e3d8b3ad380ffa276cb8b133e5b87baf17c3c32ea72; SPEC-saathi-safety-2026-11-09-r2.md sha256 6b9a198e72f618e26af03ff63e1555abf9c056e7fcf8a3e4cdff7fd078b9c202; SPEC-saathi-assurance-2026-11-09-r2.md sha256 be50717893ed345c507fcc856d7f8694931b520e3bf4110200b922519007137e; POLICY-saathi-2026-11-09-r2.md sha256 aee5383d7d124ddad9f2fbb8bf9dcc50ac066eb05d128d641f126a97f5495dd9; POLICY-saathi-data-2026-11-09.md sha256 993dc52a89eb0fda3333df48f110c5654a3b7f184923d71bcd39c073bc782b7f; RECORD-saathi-decisions-2026-11-09.md sha256 a1ec6109610478a4582467cebe12c11d4bd266b89b35a89fdbe44dbde9f86f45
sources: the contracts in upstream; infra/sql/schema/001_init.sql, infra/sql/00-create-databases.sh, src/saathi/pipeline.py (ports at lines 33-44), channels/*.py, perception/asr.py, llm/gateway.py and policy/*.py on branch phase-0/foundation at 3d46456
checked-at: 3d46456, 2026-11-09. Code read with `git show`; nothing was executed. Signatures are P.
---

Read section 2 for the ports, section 4 for the tables added in each phase, and section 5 for which database role may write what.

# Saathi TRD ports: interfaces, data model and roles

## 1. Scope and rules

A port is a `typing.Protocol` in the core package; each side effect in the components table sits behind one, so graphs and machines run with fakes (the convention of the repository's agent instruction file; E: three ports exist at `pipeline.py:33-44`). This part gives signatures and error types only. Bodies, retries and field lists belong to the LLD.

1. Every port that sends or writes takes `key: IdemKey`, built from the inbox id, attempt id or commit_intent id (RQ-79, RQ-80).
2. A port raises only its listed errors. Any other exception ends the run `failed`, reason `no_result` (RQ-35, RQ-36).
3. Every port has a fake in the test package; the fake can fail on demand for each listed error.

## 2. Ports

```python
class ElderChannel(Protocol):                       # WhatsApp; used by C02, C15
    async def send_text(self, to: ElderId, text: str, *, key: IdemKey) -> SendReceipt: ...
    async def send_audio(self, to: ElderId, clip: VoiceClip, *, key: IdemKey) -> SendReceipt: ...
    async def send_template(self, to: PersonRef, template: TemplateId,
                            params: Mapping[str, str], *, key: IdemKey) -> SendReceipt: ...
    async def fetch_media(self, ref: MediaRef) -> MediaBlob: ...
    async def health(self) -> ChannelHealth: ...    # up | degraded | down, from the daily probe

class CaregiverChannel(Protocol):                   # Telegram; used by C12, C14
    async def send_card(self, to: ApproverId, card: Card, *, key: IdemKey) -> CardRef: ...
    async def edit_card(self, ref: CardRef, card: Card) -> None: ...
    async def send_text(self, to: ApproverId, text: str, *, key: IdemKey) -> SendReceipt: ...
    async def send_audio(self, to: ApproverId, audio: AudioBlob | VoiceClip,
                         caption: str, *, key: IdemKey) -> SendReceipt: ...
    def updates(self) -> AsyncIterator[ApproverUpdate]: ...  # registered approvers only
    async def health(self) -> ChannelHealth: ...

class Escalator(Protocol):                          # one implementation per stage, C14
    stage: Literal["a", "b", "c"]                   # Telegram, WhatsApp template T-01, voice call
    async def notify(self, event: AlertEventRef, approver: ApproverId,
                     *, key: IdemKey) -> AttemptResult: ...   # delivered | failed

class ASR(Protocol):                                # local G1; C03
    async def transcribe(self, audio: AudioBlob, *, hint: str = "hi") -> Transcript: ...
    # Transcript: text, language | None, confidence | None, engine

class TTS(Protocol):                                # local runtime; hosted only after PL-20
    async def synthesize(self, text: str, *, voice: VoiceId, max_seconds: float) -> VoiceClip: ...

class VoiceStore(Protocol):                         # fixed clips of D-69; C15
    async def fixed(self, key: ReplyKey, language: str = "hi") -> VoiceClip: ...  # checks sha256

class Vision(Protocol):                             # G2 with an image receipt; C03, C09
    async def read(self, image: ImageBlob, task: VisionTask, *, run: RunId) -> VisionResult: ...
    # VisionResult: ocr_text (a DataBlock), labels, refusal: bool

class Gateway(Protocol):                            # C16, the only model client
    async def complete(self, req: ModelRequest[T]) -> ModelResult[T]: ...
    # ModelRequest: step, messages, schema: type[T], sensitivity = PERSONAL, run, intent, media = ()
    # ModelResult: value | None, refusal, refusal_reason, tier_grade, tokens, cost_inr, cached

class CommerceAdapter(Protocol):                    # C09
    service: ServiceId
    capabilities: frozenset[Capability]             # QUOTE, PLACE, FIND_ORDER
    async def quote(self, req: QuoteRequest) -> QuoteFacts: ...
    async def place_order(self, approved: ApprovedCommit, *, key: IdemKey,
                          timeout_s: float) -> PlaceResult: ...      # only C13 can build ApprovedCommit
    async def find_order(self, ref: CommitRef) -> OrderLookup: ...   # read only: found | not_found
    async def cancel_path(self, ref: CommitRef) -> CancelPath: ...   # read only: text or link

class PhoneDevice(Protocol):                        # C09 over the mesh; C13 for step 2
    async def dump_ui_xml(self) -> UiDump: ...      # xml, taken_at (monotonic), sha256, foreground package
    async def tap_selector(self, selector: Selector, *, dump: UiDump,
                           grant: CommitGrant | None = None) -> TapResult: ...
    async def screenshot(self) -> Screenshot: ...
    async def heartbeat(self) -> DeviceHealth: ...  # online, installed-package hash

class AuditSink(Protocol):                          # C17
    async def append(self, ev: AuditEvent, *, tx: Tx | None = None) -> AuditRef: ...

class Clock(Protocol):                              # fake in every machine test
    def now(self) -> datetime: ...                  # UTC
    def monotonic(self) -> float: ...
    async def sleep_until(self, when: datetime) -> None: ...

class Store(Protocol):                              # X1; repos below take a Tx
    def tx(self, *, household: HouseholdId | None = None,
           lock_household: bool = False) -> AsyncContextManager[Tx]: ...
    inbox: InboxRepo          # insert_if_new, lease, settle
    requests: RequestRepo     # create, get, transition(id, from_, event, to, reason) -> bool
    tokens: TokenRepo         # mint, spend(tx, ...) -> bool, void_for(request_id)
    commits: CommitRepo       # insert_attempting(tx, ...), record_result, stuck(older_than)
    alerts: AlertRepo         # open, due(now), attempt, ack
    flags: FlagRepo           # read_paused(tx) -> bool; raises FlagUnreadable
    ledger: LedgerRepo        # reserve(tier, worst_case_inr), settle, release, month_total
    voice: VoiceRepo          # clips by key and version
```

`cancel` is not a port method. The helper never cancels an order (PRD RQ-06, requests RQ-74); `cancel_path` returns the service's own route for the caregiver card.

## 3. Errors

| Port | Errors | Caller behaviour |
|---|---|---|
| ElderChannel | `WindowClosed`, `TemplateMissing`, `ChannelDown`, `MediaExpired`, `RateLimited(retry_after)`, `SendRejected(code)` | `WindowClosed` with no template ends the request `blocked`, reason `window_closed` (RQ-77); `MediaExpired` sets `media_lost` (RQ-79) |
| CaregiverChannel | `ChannelDown`, `RateLimited`, `SendRejected` | the alert path switches to stages b and c at once (RQ-78) |
| Escalator | `SignoffMissing` (stages b, c; raised before any send), `ChannelDown`, `ProviderRejected` | `SignoffMissing` alerts the owner and sends nothing (RQ-95a) |
| ASR | `AsrUnavailable` | audio forward to the caregiver (RQ-82) |
| TTS, VoiceStore | `TtsUnavailable`, `TtsTimeout`, `ReceiptMissing` (hosted), `ClipMissing`, `ClipHashMismatch` | the reply goes as Hindi text; a clip error also alerts the owner (D-69) |
| Vision | `ReceiptMissing`, `VisionUnavailable` | handoff with the original media and a summary (RQ-96) |
| Gateway | `PrivacyViolation`, `BudgetExceeded`, `ReceiptMissing`, `SchemaFailure` (after one retry), `NoAcceptedResult` | `NoAcceptedResult` replaces returning an unaccepted result; the request ends `blocked` with a summary (RQ-47). A refusal is a result field and stands |
| CommerceAdapter | `AdapterUnavailable` (no access, expired token, changed schema hash), `UnsupportedCapability`, `ServiceRejected`, `CallTimeout` (outcome unknown), `StaleQuote` | `CallTimeout` sets intent `timeout` and queues `find_order`; `UnsupportedCapability` for `FIND_ORDER` means handoff only (RQ-06, 94) |
| PhoneDevice | `DeviceOffline`, `StaleDump` (target not in the dump, or more than 2 s old), `AmbiguousSelector`, `GrantRequired`, `PackageDrift` | `DeviceOffline` gives handoff `phone_offline`; the others abort with `verify_failed` (RQ-34, 87, 90) |
| AuditSink | `AuditUnavailable` | callers fail closed: no token minted, no step 1 (RQ-56a) |
| Store | `StoreUnavailable`, `ConflictLost`, `FlagUnreadable` | `ConflictLost` means a lost race and no effect; `FlagUnreadable` denies (RQ-32) |

## 4. Data model: migrations from 001_init.sql

E at 3d46456: `001_init.sql` creates 11 tables (`households`, `elders`, `approvers`, `addresses`, `profile_facts`, `requests`, `quotes`, `approvals`, `commits`, `llm_spend`, `runtime_flags`). No application code reads or writes Postgres: `asyncpg` is a dependency and `store/__init__.py` is empty. Every new table carries `household_id` (RQ-38). Migrations are numbered files applied in order by the migrator role; a migration is forward only.

| Phase | Migration | New or changed tables and key columns | Serves |
|---|---|---|---|
| 0 | 002 inbox and raw text | `inbox` (id, household_id, `provider_message_id` unique, source, sender_ref, kind, payload_sha256, media_ref, state, attempts, lease_until, not_before, received_at); `rejected_inbound` (sender_hmac, reason, at); `raw_text` (inbox_id, text, expires_at). `requests`: drop `provider_message_id` unique (`:60`) and `transcript_redacted` (`:62`); add inbox_id, state, state_reason, questions_asked, urgent, expires_at, superseded_by; `radar` keeps rule ids and flags only; `outcome` is replaced by `state` | RQ-79, 81, 97, 39 |
| 0 | 003 alerts | `alert_events` (kind, state, stage, t0, next_due_at, acked_by, acked_at, false_alarm); `alert_attempts` (event_id, stage, approver_id, channel, idem_key unique, status, due_at) | RQ-18 |
| 0 | 004 audit, sign-offs, consent | `audit_log` (seq identity, ts, run_id, actor, event, subject, payload_hmac, prev_hash null, row_hash null); schema `control`: `signoffs` (scope, granter, granted_at, expires_at, revoked_at), `consents` (scope[], language, how, text_version, recorded_by, recorded_at, withdrawn_at); roles (section 5) | RQ-56a, 95a; PL-05 |
| 0 | 005 ledger, runs, voice, jobs | `llm_reservations` (run_id, tier, reserved_inr, state, settled_inr); `llm_spend` gains household_id (`:107-114` lacks it) and reservation_id; `run_records` (run_id, request_id, inbox_id, intent, prompt_versions, started_at, ended_at, state, reason, steps, checks, decisions, refs); `runtime_flags` gains paused_by and reason; `voice_clips` (reply_key, version, language, text, sha256, path, reviewed_by, reviewed_at); `model_jobs` (request_id, step, input_ref, state, result); `approvals` gains household_id (`:83-92` lacks it) | RQ-48, 55, 32, 38; D-69 |
| 1 | 010 tokens and intents | `approval_tokens` (token_id, request_id, quote_id, quote_hash, approver_id, action, expires_at, spent_at, voided_at); `commits` becomes `commit_intents` (idempotency_key unique, request_id, quote_hash, state, started_at, finished_at, provider_ref_hmac); `cards` (request_id, approver_id, message_ref, quote_hash, nonce) | RQ-21, 22, 80 |
| 1 | 011 second factor, config | `approver_totp` (approver_id, secret_enc, enrolled_at, last_step, failures, locked_until); `signoffs` gains the other classes; `config_changes` (who, what, at) | PL-27; RQ-95b |
| 1 | 012 timers, reminders, channels | `timers` (kind, request_id, due_at, fired_at); `reminders` (elder_id, schedule, created_by, state; times only, never doses); `templates` (template_id, language, category, submitted_at, approved_at); `channel_state` (channel, state, since, last_probe_at) | RQ-14, 20, 77, 78 |
| 1 | 013 audit chain | `audit_heads` (seq, row_hash, ts, exported_at); genesis row; chain columns filled by one advisory-locked writer | RQ-56b |
| 1 | 014 profile, credentials, shadow, privacy | `profile_facts` gains version and supersedes_id; `profile_rules` (kind, params, urgent); `credentials` (owner, scope, issued_at, expires_at, rotation_due; no value); `mcp_pins` (server, version, schema_sha256, reviewed_at); `shadow_quotes`, `blind_labels`; `dsr_requests`; `erasure_log` (ids only); `model_cache` (key, redacted content, prompt_version) | RQ-05, 51, 54, 93, 94; PL-30 |
| 2 | 020 device and macros | `devices` (heartbeat_at, package_hash); `macros` (app, version, selectors, state, signed_by, signed_at) | RQ-34, 53, 90 |
| 3 | 030 claims | `claims` (request_id, category, verdict); `fetch_records` (claim_id, url, fetched_at, content_sha256, publisher) | RQ-09, 11 |
| 5 | 050 taxonomy | `issue_labels` (request_id, category, sub_type, outcome, week); `exports` (consent ref) | RQ-73 |

## 5. Database roles

E: one role `saathi` owns the database and runs the application (`00-create-databases.sh:6-7`); Langfuse has its own database (`:5`).

| Role | Used by | May | May not |
|---|---|---|---|
| `saathi_migrator` | the migration job | DDL; owns tables | run at runtime |
| `saathi_edge` | C01 | insert `inbox`, `rejected_inbound`; read sender registration | any other table |
| `saathi_core` | C02, C07, C11 to C15, C18 | read and write the request-path tables; execute `audit_append()` | update or delete `audit_log`; write `control.*` |
| `saathi_audit` | owns `audit_append()` (a security-definer function) | insert `audit_log`; use its sequence | update, delete, truncate |
| `saathi_model` | C03, C06, C08, C16 | read and write its own `model_jobs` rows; reserve and settle `llm_reservations`; insert `llm_spend` | write approvals, tokens, flags, `control.*` or audit |
| `saathi_adapter` | C09 | read and write its own job rows | everything else |
| `saathi_owner` | the owner's admin commands | insert `control.*`, enrol TOTP, write `config_changes` | be reachable from any container environment |
| `saathi_readonly` | verify scripts, backup | select | write |

Skipped or not checked: no port, migration or role exists in code; no migration was applied to a database in this pass; field lists are examples for the LLD; the technology behind each port is chosen in the ops part.
