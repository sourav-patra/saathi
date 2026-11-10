---
id: TRD-saathi-ports
kind: contract
governs: the Protocol ports, the data model migration plan and the database roles; part of TRD-saathi-2026-11-09.md
status: draft, review F fixes and strategy fold applied, pending owner lock (see docs/LOCKS.md)
upstream: PRD-saathi-2026-11-09-r2.md sha256 141b677d217017de6973d67f2f5d46eb549b2b40464d4f7426f4ee83d0ed15ec; SPEC-saathi-requests-2026-11-09.md sha256 0e595737a4216baecdc1ad6a1436d3b4351988e7fc8f26c4f752935930eb60b5; SPEC-saathi-safety-2026-11-09-r2.md sha256 711d673b563105b0115376a8fbb5b2fb6e9a12dfa06afdd4e60103bd53e8bc12; SPEC-saathi-assurance-2026-11-09-r2.md sha256 4d02d7a0ce53e02870ecb794d179f9863522f86df962c60489060c3e1069867a; POLICY-saathi-2026-11-09-r2.md sha256 2ef14b0294378ff7a44be0e96e1f74a94c90614844e59f390eb29f1471bf339c; POLICY-saathi-data-2026-11-09.md sha256 907e2bd010ffdebe8e22a43fd693258be096922972c1846bf73f64593bf3f6c4; RECORD-saathi-decisions-2026-11-09.md sha256 41bbe3270e83e1a23535f8903d7e5b60edeea9dae846b0b6feb4c3b152ff82d9; SPEC-saathi-platform-2026-11-10.md sha256 2ccd496e0116592e2461f70d0fdbe2190dd8373e11afda13b53206be1c303a99; SPEC-saathi-product-2026-11-10.md sha256 d39d13904842ec48b5cc0fc8284c06a0f863cfc23ff1dcdd044e401dc8682fb0
sources: the contracts in upstream; infra/sql/schema/001_init.sql, infra/sql/00-create-databases.sh, src/saathi/pipeline.py (ports at lines 33-44), channels/*.py, perception/asr.py, llm/gateway.py and policy/*.py on branch phase-0/foundation at 3d46456
checked-at: 3d46456, 2026-11-09. Code read with `git show`; nothing was executed. Signatures are P. Revised after review F.
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
class ElderChannel(Protocol):                       # WhatsApp primary, Telegram elder bot backup; C02, C15
    kind: Literal["whatsapp", "telegram_elder"]     # a reserved "voice_call" has no body (D-80)
    capabilities: frozenset[ChannelCap]             # voice_in, voice_out, image_in, image_out, video_in,
                                                    # text, proactive_free, templates (platform RQ-105)
    async def send_text(self, to: PersonRef, text: str, *, key: IdemKey) -> SendReceipt: ...
    async def send_audio(self, to: PersonRef, clip: VoiceClip, *, key: IdemKey) -> SendReceipt: ...
    async def send_image(self, to: PersonRef, image: ImageBlob, caption: str,
                         *, key: IdemKey) -> SendReceipt: ...      # the FACT CHECK card
    async def send_template(self, to: PersonRef, template: TemplateId,
                            params: Mapping[str, str], *, key: IdemKey) -> SendReceipt: ...
    # core code holds PersonRef, never a phone number or chat id; the implementation resolves it
    async def fetch_media(self, ref: MediaRef) -> MediaBlob: ...
    async def health(self) -> ChannelHealth: ...    # up | degraded | down, from the daily probe

class CaregiverChannel(Protocol):                   # Telegram caregiver bot, a different bot from the elder's; C12, C14
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
    async def on_dtmf(self, cb: VoiceCallback) -> VoiceAckResult: ...  # stage c only: digit 1 acknowledges;
    # signature, timestamp, call id and replay are checked first; the result names the approver acknowledged

class ASR(Protocol):                                # local G1; C03
    async def transcribe(self, audio: AudioBlob, *, hint: str = "hi") -> Transcript: ...
    # Transcript: text, language | None, confidence | None, engine

class AudioCodec(Protocol):                         # opusdec and opusenc as subprocesses; C03, C15
    async def to_wav16k(self, audio: AudioBlob, *, max_seconds: float) -> WavBlob: ...   # opusdec --rate 16000 --force-wav
    async def encode_clip(self, wav: WavBlob) -> VoiceClip: ...                          # opusenc; stored clips only

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
    manifest: AdapterManifest                       # access_basis, data_classes, hosts, payment_methods,
                                                    # terms_receipt (product RQ-115); the loader refuses a gap
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
    def tx(self, *, household: HouseholdId,
           lock_household: bool = False) -> AsyncContextManager[Tx]: ...  # required, no default; the first
                                                    # statement is SET LOCAL app.household_id (platform RQ-109)
    scan: ScanFns             # security-definer functions that return ids only: resolve_sender, lease_next,
                              # due_timers, due_alerts, stuck_commits; all later work uses tx(household=...)
    config: ConfigRepo        # household_config: load(household) -> HouseholdConfig with .version (RQ-110)
    consents: ConsentRepo     # append(event); allowed(subject, use, recipient) -> bool, fails closed (PL-33)
    keys: KeyRing             # seal(household, purpose, data) with the public half; open(...) in the core (RQ-111a)
    inbox: InboxRepo          # insert_if_new, lease (oldest per elder), renew, settle
    requests: RequestRepo     # create, get, transition(id, from_, event, to, reason) -> bool,
                              # step(tx, id, plan_step, slots) loads and writes the one row
    updates: UpdateRepo       # seen(update_id) -> bool, insert first; Telegram replay guard
    tokens: TokenRepo         # mint, spend(tx, ...) -> bool, void_for(request_id)
    commits: CommitRepo       # insert_attempting(tx, ...), record_result (late allowed), stuck(older_than),
                              # resolve_manual(owner, outcome) from a control row
    alerts: AlertRepo         # open (edge, worker), open_unscanned, due(now), attempt, ack
    flags: FlagRepo           # read_paused(tx) -> bool; raises FlagUnreadable
    ledger: LedgerRepo        # reserve(tier, worst_case_inr), settle, release, month_total
    voice: VoiceRepo          # clips by key and version
```

`cancel` is not a port method. The helper never cancels an order (PRD RQ-06, requests RQ-74); `cancel_path` returns the service's own route for the caregiver card.

## 3. Errors

| Port | Errors | Caller behaviour |
|---|---|---|
| ElderChannel | `ProactiveNotAllowed(reason)`, `TemplateMissing`, `ChannelDown`, `MediaExpired`, `RateLimited(retry_after)`, `SendRejected(code)` | `ProactiveNotAllowed` with no template ends the request `blocked`, reason `window_closed` (RQ-77); `ChannelDown` feeds the Switch alert (RQ-78); `MediaExpired` sets `media_lost` (RQ-79) |
| CaregiverChannel | `ChannelDown`, `RateLimited`, `SendRejected` | the alert path switches to stages b and c at once (RQ-78) |
| Escalator | `SignoffMissing` (stages b, c; raised before any send), `ChannelDown`, `ProviderRejected`, `CallbackRejected` (bad signature, unknown call id, repeat) | `SignoffMissing` alerts the owner and sends nothing (RQ-95a); `CallbackRejected` changes no state and writes an audit row `deny` |
| ASR, AudioCodec | `AsrUnavailable`, `AudioUndecodable`, `DecodeTimeout` | audio forward to the caregiver (RQ-82); a decode error never drops the row |
| TTS, VoiceStore | `TtsUnavailable`, `TtsTimeout`, `ReceiptMissing` (hosted), `ClipMissing`, `ClipHashMismatch` | the reply goes as Hindi text; a clip error also alerts the owner (D-69) |
| Vision | `ReceiptMissing`, `VisionUnavailable` | handoff with the original media and a summary (RQ-96) |
| Gateway | `PrivacyViolation`, `BudgetExceeded`, `ReceiptMissing`, `SchemaFailure` (after one retry), `NoAcceptedResult` | `NoAcceptedResult` replaces returning an unaccepted result; the request ends `blocked` with a summary (RQ-47). A refusal is a result field and stands |
| CommerceAdapter | `AdapterUnavailable` (no access, expired token, changed schema hash), `UnsupportedCapability`, `ServiceRejected`, `CallTimeout` (outcome unknown), `LookupFailed`, `StaleQuote` | `CallTimeout` sets intent `timeout` and queues `find_order`; `UnsupportedCapability` for `FIND_ORDER` means handoff only (RQ-06, 94) and, for an intent already in `timeout`, handoff `manual_reconcile` (states R39); `LookupFailed` retries with backoff until the owner timeout, then the same |
| PhoneDevice | `DeviceOffline`, `StaleDump` (target not in the dump, or more than 2 s old), `AmbiguousSelector`, `GrantRequired`, `PackageDrift` | `DeviceOffline` gives handoff `phone_offline`; the others abort with `verify_failed` (RQ-34, 87, 90) |
| AuditSink | `AuditUnavailable` | callers fail closed: no token minted, no step 1 (RQ-56a) |
| Store, KeyRing | `StoreUnavailable`, `ConflictLost`, `FlagUnreadable`, `TenantNotSet`, `KeyMissing` | `ConflictLost` means a lost race and no effect; `FlagUnreadable` denies (RQ-32); `TenantNotSet` and `KeyMissing` fail closed with no read or write (RQ-109, 111a) |

## 4. Data model: migrations from 001_init.sql

E at 3d46456: `001_init.sql` creates 11 tables (`households`, `elders`, `approvers`, `addresses`, `profile_facts`, `requests`, `quotes`, `approvals`, `commits`, `llm_spend`, `runtime_flags`). No application code reads or writes Postgres: `asyncpg` is a dependency and `store/__init__.py` is empty. Every new table carries `household_id` and, in its own migration, forced row-level security with the policy of RQ-109; a schema test fails otherwise (RQ-38). Three tables have no household column and the test lists them exactly: `rejected_inbound` (the sender is unknown), `voice_clips` (a clip holds no household data) and `control.heartbeats`. Migrations are numbered files applied in order by the migrator role, a non-superuser; a migration is forward only and creates no role.

| Phase | Migration | New or changed tables and key columns | Serves |
|---|---|---|---|
| 0 | 002 inbox and raw text | `inbox` (id, household_id, `provider_message_id` unique per household, source, sender_hmac, elder_id, kind, payload_sha256, media_ref, state, attempts, lease_until, lease_token, not_before, acked_at, radar_at, received_at); `rejected_inbound` (sender_hmac, reason, at; tenantless, insert only for the edge, read through a digest function); `raw_text` (inbox_id, household_id, part, `body_sealed`, key_id, expires_at; primary key inbox_id and part); `elder_identities` (elder_id, channel, `sender_hmac` under a deployment key, `person_ref`, `person_ref_enc` sealed with the content key, key_id, active), created here because the edge needs it for sender lookup; `elders.wa_phone_e164` is dropped. `requests`: drop `provider_message_id` unique (`:60`) and `transcript_redacted` (`:62`); add inbox_id, state, state_reason, questions_asked, urgent, expires_at, superseded_by and the resume columns `intent`, `slots` jsonb, `quote_id` and `plan_step` (states part, rule 5); `radar` keeps rule ids and flags only; `outcome` is replaced by `state` | RQ-79, 81, 97, 39, 35 |
| 0 | 003 alerts | `alert_events` (kind, state, stage, t0, next_due_at, acked_by, acked_at, false_alarm, audio_ref); `alert_attempts` (event_id, stage, approver_id, channel, idem_key unique, status, due_at); `tg_updates` (bot, update_id, household_id, kind, received_at; primary key bot and update_id, because ids repeat across two bots) | RQ-18, 23 |
| 0 | 004 audit, sign-offs, consent, config, keys | `audit_log` (seq identity, household_id, hseq null, ts, run_id, actor, event, subject, payload_hmac, key_id, prev_hash null, row_hash null); schema `control`: `signoffs` (scope, granter, granted_at, expires_at, revoked_at), `consent_events` (subject, use, recipient, event, capacity, guardian_ref, language, how, notice_text_sha256, recorded_by, recorded_at; insert only); `household_config` (household_id, version, values, created_by, created_at); `household_keys` (purpose, key_id, public_key, wrapped_key, created_at, retired_at); roles (section 5) | RQ-56a, 95a, 110, 111a; PL-05, 33, 34 |
| 0 | 005 ledger, voice, flags | `llm_reservations` (run_id, tier, reserved_inr, state, settled_inr); `llm_spend` gains household_id (`:107-114` lacks it) and reservation_id; `runtime_flags` gains paused_by and reason; `control.heartbeats` (component, beat_at, status; tenantless, written through `beat()`); `voice_clips` (reply_key, version, language, text, sha256, path, reviewed_by, reviewed_at, receipt_ref; a row without the review fields is not loadable, D-71); `approvals` gains household_id (`:83-92` lacks it) | RQ-48, 32, 38; D-69, D-71 |
| 1 | 010 tokens and intents | the first Phase 1 migration also seals `addresses`, `profile_facts.value` and the display names with the household content key, before any real row (D-91); `approval_tokens` (token_id, request_id, quote_id, quote_hash, approver_id, action, expires_at, spent_at, voided_at); `commits` becomes `commit_intents` (idempotency_key unique, request_id, quote_hash, state, started_at, finished_at, provider_ref_hmac); `cards` (request_id, approver_id, message_ref, quote_hash, nonce); `control.manual_resolutions` (commit_intent_id, outcome, owner, at, note) | RQ-21, 22, 80 |
| 1 | 011 second factor, config | `approver_totp` (approver_id, secret_enc, enrolled_at, last_step, failures, locked_until); `signoffs` gains the other classes; `config_changes` (who, what, at) | PL-27; RQ-95b |
| 1 | 012 timers, reminders, channels | `timers` (kind, request_id, due_at, fired_at); `reminders` (elder_id, schedule, created_by, state; times only, never doses); `templates` (template_id, language, category, submitted_at, approved_at); `channel_state` (household_id, channel, state, since, last_probe_at); `start_codes` (elder_id, code_hash, expires_at, spent_at) | RQ-14, 20, 77, 78, 106, 107 |
| 1 | 013 audit chain | `audit_heads` (household_id, hseq, row_hash, ts, exported_at); a genesis row per household; chain columns filled by one writer per household under an advisory lock keyed by the household | RQ-56b |
| 1 | 014 profile, credentials, shadow, privacy; media and TOTP keys | `household_keys` rows for `totp`; `approver_totp.secret_enc` names its `key_id` (RQ-111b); `profile_facts` gains version and supersedes_id; `profile_rules` (kind, params, urgent); `credentials` (owner, scope, issued_at, expires_at, rotation_due; no value); `mcp_pins` (server, version, schema_sha256, reviewed_at); `shadow_quotes`, `blind_labels`; `dsr_requests`; `erasure_log` (ids only); `model_cache` (key, redacted content, prompt_version) | RQ-05, 51, 54, 93, 94, 111b; PL-30 |
| 1 | 015 run records and model jobs | `run_records` (run_id, request_id, inbox_id, intent, prompt_versions, started_at, ended_at, state, reason, steps, checks, decisions, refs); `model_jobs` (request_id, step, input_ref, state, result), once the model worker is its own service | RQ-55, 33, 96 |
| 2 | 020 device and macros | `devices` (heartbeat_at, package_hash); `macros` (app, version, selectors, state, signed_by, signed_at) | RQ-34, 53, 90 |
| 3 | 030 claims | `claims` (request_id, category, verdict); `fetch_records` (claim_id, url, fetched_at, content_sha256, publisher) | RQ-09, 11 |
| 5 | 050 taxonomy | `issue_labels` (request_id, category, sub_type, outcome, week); `exports` (consent ref) | RQ-73 |

## 5. Database roles

E: one role `saathi` owns the database and runs the application (`00-create-databases.sh:6-7`); Langfuse has its own database (`:5`). Until the Phase 1 zone split the edge runs as its own service and the core and model modules run in the `worker` service (D-92), each with one connection pool per role below. The superuser init script creates every role; no migration does. No runtime role owns a table or has `BYPASSRLS`, and each pool sets `app.household_id` through `Store.tx(household=...)` (RQ-109).

| Role | Used by | May | May not |
|---|---|---|---|
| `saathi_migrator` | the migration job | DDL; owns tables; hands function ownership to `saathi_scan` and `saathi_audit` | run at runtime; create roles or be a superuser |
| `saathi_edge` | C01 | insert `inbox`, `raw_text`, `rejected_inbound`, `alert_events`; call `resolve_sender()`, which returns a household id, `rate_state()`, which returns a time, and `beat()`, which returns the core heartbeat; hold the public content key | read `raw_text`; hold a private or secret key; any other table; update or delete |
| `saathi_core` | C02, C07, C11 to C15, C18 | read and write the request-path tables; execute `audit_append()` and the scan functions | update or delete `audit_log`; write `control.*` |
| `saathi_scan` | owns the scan functions (security definer); policies let it read across households | log in; write request-path rows |
| `saathi_audit` | owns `audit_append()` (a security-definer function) | insert `audit_log`; use its sequence | update, delete, truncate |
| `saathi_model` | C03, C06, C08, C16 | read and write its own `model_jobs` rows (from the split); reserve and settle `llm_reservations`; insert `llm_spend` | write approvals, tokens, flags, `control.*` or audit |
| `saathi_adapter` | C09 | read and write its own job rows | everything else |
| `saathi_owner` | the owner's admin commands, run by `saathi-admin` on the host shell where the key file is readable | insert households, elders, approvers, identities and `control.*`; rotate and rewrap keys; revoke sign-offs; call `audit_append()`; enrol TOTP, write `config_changes` | be reachable from any container environment |
| `saathi_readonly` | verify scripts, backup | select, with `BYPASSRLS`; the only role that has it | write; be reachable from a container with a model client |

Skipped or not checked: no port, migration or role exists in code; no migration was applied to a database in this pass; field lists are examples for the LLD; the technology behind each port is chosen in the ops part.
