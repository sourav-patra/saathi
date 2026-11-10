---
id: LLD-saathi-safety
kind: contract
governs: Phase 0 modules M14 to M24: escalation, caregiver bot, voice, audit, traces, gateway and ledger, health, state tables, gate, lints, evals, admin; part of LLD-saathi-2026-11-10.md
status: draft, pending review
upstream: TRD-saathi-2026-11-09.md sha256 88d14bcfec16d0b6b158a0437c2f2c7dd61ab84b9ab7f1c8b1bf94c51d322075; TRD-saathi-states-2026-11-09.md sha256 6a6843a411f59ce326c9573fe7ed14e6bb76ed19e30c1b9a5e3514b4bda25db6; SPEC-saathi-evidence-2026-11-09-r2.md sha256 84e320c15d8d622da37071101ab30bedccfdcaa0ce671f3d5fccb2cd44eccf3d; LLD-saathi-2026-11-10.md sha256 bda71cce7ddb7c9fe5b18223d08d360f881cfc660c1d3befaf70294488692309
sources: src/saathi/, scripts/, tests/, evals/ on branch phase-0/foundation at 3d46456; the TRD components table, ports, states and build parts; DOC-PHILOSOPHY.md
checked-at: 3d46456, 2026-11-10. Code read with `git show`. Nothing below was run in this pass.
---

Read the module you build, then its Never line. Shared types are in LLD-saathi-intake-2026-11-10.md; tables are in LLD-saathi-data-2026-11-10.md. Every name is P unless an E cites 3d46456.

# Saathi LLD safety modules: alerts, audit, gateway, gates

## M14 `saathi/alerts/` (service.py, plan.py, stages.py)

```python
class AlertService:
    def __init__(self, store: Store, caregivers: CaregiverChannel, elders: Mapping[Source, ElderChannel],
                 voice: VoiceStore, stages: Mapping[Literal["a", "b", "c"], Escalator], audit: AuditSink,
                 signoffs: SignoffRepo, clock: Clock, cfg: AlertConfig) -> None: ...
    async def run_forever(self) -> None: ...            # tick every 2 s
    async def tick(self) -> int: ...                    # scan.due_alerts, then step each
    async def step(self, household: HouseholdId, event: EventId) -> None: ...
    async def acknowledge(self, household: HouseholdId, event: EventId, approver: ApproverId,
                          via: Literal["tap", "dtmf"]) -> bool: ...
    async def false_alarm(self, household: HouseholdId, event: EventId, approver: ApproverId) -> bool: ...
@dataclass(frozen=True)
class Planned: stage: Literal["a", "b", "c"]; approver: ApproverId; n: int; due_at: datetime
def plan(ev: AlertView, approvers: Sequence[ApproverId], now: datetime, *, so1_valid: bool,
         whatsapp_down: bool, cfg: AlertConfig) -> tuple[list[Planned], datetime | None]: ...   # pure
CADENCE_A_S = (0, 60, 120, 180, 240); THEN_EVERY_S = 300; STAGE_A_CAP = 20
STAGE_B_AT_S = 120; STAGE_B_EVERY_S = 120; STAGE_B_MAX = 3
STAGE_C_AT_S = 240; STAGE_C_EVERY_S = 180; STAGE_C_PER_APPROVER = 3; BACKUP_CALL_DELAY_S = 60
REPEAT_CLIP_AT_S = 600; FOLLOWUP_S = 900
```
`step` does three things, each its own transaction: (1) lock the event row, return when not `open`; call `plan`; insert `alert_attempts` rows with `idem_key = idem_key("alert", event, stage, approver, n)` using `ON CONFLICT DO NOTHING`; set `next_due_at` to the next planned time; (2) send every `due` attempt outside a transaction through its `Escalator.notify(..., key=idem_key)`; stage a with the original audio fetched by `ElderChannel.fetch_media(MediaRef)` when `consent.gate` allows it; (3) mark each attempt `sent`, `failed` or `delivered`. A crash between (2) and (3) repeats a send with the same key. Cases: `scam` and `unscanned` use stage a only (A01, A11); stages b and c need `so1_valid` (SO-1 row, unexpired, not revoked), else `plan` returns no b or c attempt, the owner is alerted once and a `signoff_expired` audit row is written (A05); `whatsapp_down` removes stage b only; when every cap is spent the event ends `blocked`, reason `no_human_reached` (A07). `acknowledge` is one compare-and-set on `state = 'open'`, cancels unsent attempts, sets `followup_due_at` and writes `ack_tap` or `ack_voice` naming the approver (A06, A12). `stages.py` holds `TelegramStageA` (a real Escalator over `CaregiverChannel`), `UnavailableEscalator` (raises `ChannelDown`, used for b and c in production until Phase 1) and the fakes live in `tests/fakes/escalators.py` (call answered with no key press, voicemail, forged callback).
- Errors: `SignoffMissing`, `ChannelDown`, `ProviderRejected`, `CallbackRejected`. Serves: RQ-18, 95a, 78 (alert path); PL-26; TQ-09, 10, 11. Never: depend on a model, ASR, WhatsApp (stages a and c) or the worker; send b or c with SO-1 missing or expired; put elder content in b or c; stop on anything but a registered approver's tap or key press of 1.

## M15 `saathi/channels/telegram.py` (caregiver bot, Phase 0 subset)

```python
class TelegramCaregiverChannel:                       # implements CaregiverChannel; aiogram client (E: telegram.py, MIT)
    def __init__(self, bot: Bot, store: Store, clock: Clock) -> None: ...
    def updates(self) -> AsyncIterator[ApproverUpdate]: ...   # polls; resolve_approver first; others counted and dropped
class Card(BaseModel): title: str; lines: list[str]; buttons: list[Button]   # slots only, never a transcript
def alert_card(ev: AlertView, alias: str) -> Card: ...         # buttons Acknowledge ("ack:<event>"), False alarm ("fa:<event>")
def summary_card(slots: SummarySlots, request: RequestId) -> Card: ...   # button Show raw ("raw:<inbox>")
class CallbackRouter:
    async def handle(self, up: ApproverUpdate) -> None: ...   # UpdateRepo.first_seen, then one transition, then offset advance
async def show_raw(store: Store, keys: KeyRing, audit: AuditSink, up: ApproverUpdate, inbox: InboxId) -> str: ...
```
E: the module holds `RuntimeFlags` in memory, `_repeat_until_ack` and a `set` of acknowledgements (`telegram.py:33-37`, `:83-92`); Phase 0 removes all three and the `/pause` and `/resume` handlers, because an in-memory pause is not a safe pause (the durable pause is Phase 1, change 17). Phase 0 commands: `/start`, `/status` (counts of open alerts, newest inbox lag). The Telegram offset advances only after the transaction of `CallbackRouter.handle` commits. `show_raw` reads `raw_text` with `KeyRing.open`, checks `consent.gate("transcript", approver)`, writes audit `show_raw`, and sends the text as a reply.
- Serves: RQ-97, 19 (partial), 23 (registered only), 18; TQ-15, 53. Never: mint a token; read "approved" from text; put the transcript on a card or alert; cache a token; let a non-registered chat act.

## M16 `saathi/voice/` (store.py, tts.py, clips.py) and `saathi/replies.py`

```python
class ReplyKey(StrEnum): EMERGENCY="emergency"; SCAM="scam"; ACK="ack"; TOLD_CAREGIVER="told_caregiver"; NOT_UNDERSTOOD="not_understood"; EXPIRED="expired"; CHANNEL_DOWN="channel_down"
class PgVoiceStore:                                  # VoiceStore
    async def fixed(self, key: ReplyKey, language: str = "hi") -> VoiceClip: ...   # newest reviewed version; reads file; checks sha256
class LocalTTS:                                      # TTS; HTTP to the speech container
    async def synthesize(self, text: str, *, voice: VoiceId, max_seconds: float) -> VoiceClip: ...
async def send_reply(ch: ElderChannel, to: PersonRef, key: ReplyKey, *, voice: VoiceStore, language: str,
                     idem: IdemKey, owner_alert: Callable[[str], Awaitable[None]]) -> SendReceipt: ...
async def generate_fixed(owner: Store, tts: TTS, codec: AudioCodec, out_dir: Path) -> list[VoiceClip]: ...   # a job, not a service
def reply_text(key: ReplyKey, language: str) -> str: ...      # E: replies.py:9-26 interpolates a caregiver name; removed
```
`fixed` raises `ClipMissing`, `ClipHashMismatch` or `ClipUnreviewed`. `send_reply` sends the clip and the text; on any clip error it sends the text alone, calls `owner_alert("clip_error")` and writes audit `clip_error`; it never skips a reply. Reply text lives in `saathi/replies.py` as constants with no name, number (except 108 and 112) or address; `EMERGENCY` and `SCAM` texts must contain `NOT_EMERGENCY_MARKER` and both numbers (RQ-116; test `test_reply_keys_hold_marker_and_numbers`). `generate_fixed` writes `<reply_key>.v<n>.ogg` and inserts `voice_clips` rows with `reviewed_*` null; `saathi-admin clips review <key> <version> --receipt <path>` sets them after a manual receipt exists (D-71).
- Serves: RQ-16, 17, 74 (ack), 78 (notices), 116; D-69, D-71; TQ-13, 78. Never: skip a reply because audio failed; send a clip whose hash differs; load a clip with no review; put a name, number or address in a stored clip; send text to a hosted speech service without a receipt.

## M17 `saathi/audit/` (sink.py, events.py)

```python
Actor = Annotated[str, StringConstraints(pattern=r"^(elder|approver:[\w-]+|system:[\w-]+|agent:[\w-]+)$")]
class AuditEvent(BaseModel):
    household: HouseholdId; run_id: RunId; actor: Actor; event: AuditName; subject: str | None = None
    detail: Mapping[str, SafeValue] = {}            # SafeValue: int, bool, or str matching ^[\w:.-]{0,64}$
    sensitive: Mapping[str, str] = {}               # raw values; only their keyed hash is stored
class PgAuditSink:                                  # AuditSink
    def __init__(self, store: Store, keys: KeyRing) -> None: ...
    async def append(self, ev: AuditEvent, *, tx: Tx | None = None) -> AuditRef: ...
class AuditName(StrEnum): DENY; ACK_TAP; ACK_VOICE; CALL_NO_ACK; SHOW_RAW; SIGNOFF_USED; SIGNOFF_EXPIRED; CONSENT_DENIED; INBOX_DEAD; CLIP_ERROR; CONFIG_LOADED; ALERT_BLOCKED; FALSE_ALARM; KEY_ROTATED
```
`append` computes `payload_hmac` and a hashed `subject` through `KeyRing.hmac` (audit key) and calls `control.audit_append` in the caller's `tx`, or its own `tx(household)`. Any store error raises `AuditUnavailable`; callers fail closed. Denials and failures are kept. The chain columns are Phase 1.
- Serves: RQ-56a, 95a, 56b (columns); PL-07; TQ-39, 41. Never: update or delete a row; store a phone number, address or text unhashed; take a row with no actor or run id; accept a write from the model role.

## M18 `saathi/trace/` (exporter.py, allowlist.py, jsonl.py)

```python
ALLOWED_FIELDS = frozenset({"run_id","request_id","inbox_id","event_id","household_id","intent","confidence",
    "flags","rule_ids","count","bytes","sha256","duration_ms","state","reason","stage","grade","attempt","ok"})
class JsonlExporter:                                 # TraceExporter
    def __init__(self, directory: Path, clock: Clock, retention_days: int = 30) -> None: ...
    def span(self, name: str, **fields: SpanValue) -> None: ...   # drops unlisted keys, long strings, restricted patterns
class NullExporter: ...
def traced(name: str, exporter: TraceExporter): ...  # decorator; records ids and duration only
```
Files `trace-<YYYY-MM-DD>.jsonl`, one line per span, rotated by `clock`, pruned at 30 days. A string value over 64 characters, or one failing `assert_clean`, is replaced by `"<dropped>"` and `dropped` is counted. `span` catches every exception (unwritable file, full disk), counts `export_errors` and returns. E: `pipeline.py:128-135` traces whole node outputs and `observability.py:26-33` masks ID patterns only; both go, and the Langfuse client moves behind the Phase 1 exporter (changes 7, 28).
- Serves: RQ-57, 44; TQ-16. Never: export text, prompts or whole node outputs; fail a request; stand in for the audit trail.

## M19 `saathi/llm/` (gateway.py, ledger.py, roster.py, routing.py) and `saathi/understand/router.py`

```python
class Sensitivity(StrEnum): PERSONAL="personal"; PUBLIC="public"        # E: personal: bool, gateway.py:177-186
class Grade(IntEnum): G0=0; G1=1; G2=2; G3=3; G4=4
class ModelRequest(BaseModel, Generic[T]):
    step: str; messages: list[Message]; schema_: type[T]; run: RunId; sensitivity: Sensitivity = Sensitivity.PERSONAL
    intent: str | None = None; media: tuple[MediaPart, ...] = ()
class ModelResult(BaseModel, Generic[T]):
    value: T | None; refusal: bool; refusal_reason: str | None; grade: Grade; tokens: int; cost_inr: Decimal; cached: bool
class PublicPayload(BaseModel): ...                  # built only in modules listed in PUBLIC_BUILDERS
class Gateway:                                       # implements the Gateway port
    async def complete(self, req: ModelRequest[T]) -> ModelResult[T]: ...
ROUTING: Mapping[str, Grade] = {"keywords": G0, "asr": G1, "intent": G1, "quote": G0, "macro": G0, "vision": G2,
    "qa": G2, "debunk_extract": G2, "debunk_verdict": G4}      # RQ-46; one test per row
class PgLedger:                                      # LedgerRepo
    async def reserve(self, tx: Tx, run: RunId, tier: str, worst_case_inr: Decimal, cap: Decimal | None) -> int: ...
    async def settle(self, tx: Tx, reservation: int, actual_inr: Decimal) -> None: ...
    async def release(self, tx: Tx, reservation: int) -> None: ...
    async def month_total(self, tx: Tx, month: str) -> Decimal: ...
class Roster:
    @classmethod
    def load(cls, entries: Sequence[RosterEntry]) -> Roster: ...    # RosterEntry carries receipts per data class and media class
    def tiers(self, grade: Grade, sensitivity: Sensitivity, media: frozenset[MediaClass]) -> list[TierSpec]: ...
```
Rules in code. `complete` defaults sensitivity to PERSONAL and calls `assert_clean` on text before a hosted tier; tier F takes only a `PublicPayload`; fallback goes up one grade on schema failure, low confidence or outage and never down; a refusal is a result field and is never retried; `cascade` raises `NoAcceptedResult` and never returns an unaccepted result (E: it returns the last result, `gateway.py:278-280`); one schema retry at the same grade then `SchemaFailure`. `reserve` raises `BudgetExceeded` when `cap` is None, a price is missing, or the month total plus the reservation passes the cap; the 80% alert is a returned flag the caller sends. T4 leaves the router cascade (change 12). `understand/router.py` (E, kept for Phase 1) is adapted in the gateway unit to call `Gateway.complete` so that the package compiles; no Phase 0 path calls it. `Roster.load` raises `ReceiptMissing` for an entry with no receipt on a class; `tiers` omits a tier with no receipt for any media class in the request.
- Errors: `PrivacyViolation`, `BudgetExceeded`, `ReceiptMissing`, `SchemaFailure`, `NoAcceptedResult`. Serves: RQ-40, 45 to 50, 96; PL-02, 03; TQ-17 to 20. Never: default to PUBLIC; send personal data to tier F; retry a refusal on another tier; return an unaccepted result; call a paid tier with the cap unset or a price missing; hold a provider key.

## M20 `saathi/health.py`

```python
async def beat_loop(store: Store, clock: Clock, probes: Sequence[Probe], every_s: int = 10) -> None: ...   # calls control.beat
async def disk_probe(path: Path, warn_free_pct: float = 20, fail_free_pct: float = 10) -> ProbeResult: ...
async def skew_probe(store: Store, clock: Clock, warn_s: float = 5, refuse_s: float = 15) -> ProbeResult: ...
def decide(core_beat_at: datetime | None, core_status: str, now: datetime, max_age_s: int = 30) -> int: ...  # 200 or 503
```
The edge route (M07) imports `decide` and calls `control.beat('edge', now)` which returns the newest core beat and status, so a failed write or an old beat gives 503; the body is `ok` or empty. A probe that cannot run is `unavailable`, never ok. Data part section 2 adds the table and function (C-8).
- Serves: RQ-104; TQ-66. Never: answer `ok` on a stale heartbeat, a failed write or a full disk.

## M21 `saathi/states/` (tables.py, machine.py)

```python
@dataclass(frozen=True)
class Row: id: str; from_: str; event: str; to: str; guard: str; phase: int       # one per row of the TRD states part
INBOX: tuple[Row, ...]; ALERT: tuple[Row, ...]; REQUEST: tuple[Row, ...]; TOKEN: tuple[Row, ...]; INTENT: tuple[Row, ...]
class Machine:
    def __init__(self, rows: Sequence[Row], *, phase: int) -> None: ...
    def lookup(self, from_: str, event: str) -> Row | None: ...      # None for a pair outside the table or a later phase
```
Rows carry the TRD ids (N01 to N10, A01 to A13, R01 to R39, K01 to K06, I01 to I12) with `phase` 0 for the rows Phase 0 runs (N01 to N10, A01 to A13, R01, R05, R06 for no-commit replies) and 1 for the rest. Repos call `lookup` before every `UPDATE ... WHERE state = from_`. A row of a later phase is not callable in Phase 0.
- Serves: RQ-35, 36, 39 (tables); TQ-04, 65. Never: rewrite a terminal state; take a transition the table lacks.

## M22 `scripts/` (gate.py, gate_registry.toml, rule_index.py, rules.toml, doc_lint.py, doc_lint_words.txt, ci_lint.py, license_audit.py)

```python
class Row(BaseModel): id: str; phase: int; kind: Literal["auto","manual"]; command: list[str] | None = None; receipt: str; valid_days: int = 90
State = Literal["verified", "invalid", "unavailable", "not_applicable"]
def load_registry(path: Path) -> list[Row]: ...
def parse_receipt(path: Path) -> Receipt: ...          # first line a plain sentence; then `key: value` lines (RQ-100 fields)
def evaluate(row: Row, receipts: Path, today: date, run: Runner = subprocess_runner) -> State: ...
def main(argv: Sequence[str]) -> int: ...              # --phase N, --list, --registry, --receipts; exit 0 only when every printed row is verified or not_applicable
def collect_markers(tests: Path) -> dict[str, list[str]]: ...      # rule_index.py: AST scan for @rule("PE-nn")
def check_rules(rules: Path, markers: Mapping[str, list[str]], phase: int) -> list[str]: ...
RULES: Mapping[str, Callable[[Path, str], list[Finding]]]          # doc_lint.py: one function per rule name
def check_actions_pinned(workflow: Path) -> list[str]: ...         # ci_lint.py: `uses:` without 40 hex
def audit_manifest(manifest: Path) -> list[str]: ...               # license_audit.py: image binary list, fails on ffmpeg, opusinfo, GPL, LGPL, unknown
def scan_tree(root: Path, names: Path | None) -> list[Finding]: ...  # leak_scan.py: key patterns, media files, private names, attribution trailers
def check_message(text: str) -> list[str]: ...                      # commit_msg_check.py; the commit-msg hook calls it
def read_setting(check_id: str) -> State: ...                       # repo_settings.py: `gh api`, unavailable without auth (PL-16, 17, 18, 21, 22)
def count_pilot(log: Path) -> dict[str, int]: ...                   # pilot_counts.py: coded fields only; free text fails (RQ-117)
def record_fault(gate_id: str, fault: str, result: State) -> Path: ...   # record_fault.py: writes RECEIPT-fault-<id>-<date>.md
```
Receipts: `docs/evidence/receipts/RECEIPT-<id lowercase>-<YYYY-MM-DD>.md`. `gate.py` reads the PRD phase table through `00-INDEX.md` and a test compares ids and phases with the registry (RQ-100). `rules.toml` mirrors the rule table of SPEC evidence section 3 (id, implements, phase); `PHASE` holds `0`. `doc_lint.py` rule names: `filename_date`, `filename_space`, `dash`, `filler`, `size`, `index_row`, `record_shape`, `closing_line`, `bullet_bold`, `names` (unavailable without `--names-file`). `license_audit.py` (E: 127 lines, Python packages only) gains `--manifest` and `--dockerfiles` modes; the live image audit is the CI job `image-audit` on `infra/**` changes.
- Serves: RQ-62, 63, 71, 72a, 100, 101, 117; PL-08 to 12, 15 to 19, 21, 22, 31; TQ-56 to 61, 63, 79. Never: print or run a row of a later phase; turn unavailable or invalid into a pass; accept a manual receipt with no signer or past `valid_days`.

## M23 `evals/` (run.py, registry.toml, report.py, stats.py, suites/)

```python
class Suite(Protocol):
    name: str
    def load(self, root: Path) -> Dataset: ...            # reads datasets/<name>/manifest.json
    def run(self, ds: Dataset) -> Results: ...            # code-graded only in Phase 0
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]: ...
def rule_of_three(n: int) -> float: ...                   # 3 / n when k = 0
def format_rate(k: int, n: int, target_n: int | None = None) -> str: ...   # "k/n, 95% bound, n needed"
def check_manifest(m: Manifest) -> list[str]: ...         # author and keyword_author set and different; sealed sha256
```
CLI `python -m evals.run <suite>|--all`. It exits 1 when a dataset is missing, n is 0 or a suite is skipped without a named reason (RQ-61). Phase 0 suites: `radar`, `false_alarm`, `redaction`, `trace_canary`. `inbox_crash` and `policy` run as pytest and have registry rows. A paid or model-variable run needs an approval row; the runner refuses without one (RQ-99, plan only in Phase 0: `plan.py` validates the plan fields).
- Serves: RQ-58 to 62, 64, 65, 99; TQ-56, 57. Never: hold real elder data in CI; print a rate without k, n and a bound; start a paid run without an approval row.

## M24 `saathi/admin.py` (entry `saathi-admin`)

Each command opens the `saathi_owner` pool from `SAATHI_DB_URL_OWNER`, sets `app.household_id` to the household it names, and writes an audit row `system:owner_cli` through `audit_append` (the owner role holds `EXECUTE` on it). Commands, in the order a new household needs them:
1. `household create <household> --name <text>`, `elder add <household> <elder> --name <text> --lang hi`, `approver add <household> <approver> --telegram-id <n> [--primary]`: inserts into `households`, `elders`, `approvers`.
2. `keys init <household>`, `keys rotate <household> <purpose>`, `keys rewrap --old-file F1 --new-file F2` (data part section 4).
3. `identity add <household> <elder> --channel <name> --sender <id>`: computes `sender_hmac` under `SENDER_HMAC_KEY`, seals the platform address with the content key and inserts `elder_identities`; refuses `whatsapp` without a valid allowing terms receipt (PL-32). It needs step 2 first.
4. `signoff grant SO-1 --days 90 | revoke`, `consent record --file <json>`, `config set <household> --file <json>`, `clips review <key> <version> --receipt <path>`.

The grants in the grants part give the owner exactly these writes: insert on the four identity tables, keys, sign-offs, consent and config; update of `retired_at` and `wrapped_key` on keys, of `revoked_at` on sign-offs, of `active` on identities, of the review columns on clips. The command reads no secret from a container environment that holds a model client.
- Serves: RQ-95a, 110, 111a; PL-05, 32, 33, 34; D-71; TQ-41, 80. Never: run from a container; write on behalf of an agent role; create an identity before the household has keys.

Skipped or not checked: no signature was run; the cadence constants are the SPEC defaults and were not measured; stage b and c providers are not designed here (Phase 1); `AlertView`, `SummarySlots` and `Probe` fields are left to the work unit; the licence of each library named is read by the audit, not here.
