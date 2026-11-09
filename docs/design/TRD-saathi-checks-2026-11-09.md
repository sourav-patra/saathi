---
id: TRD-saathi-checks
kind: contract
governs: the design rules (TQ) that tie every RQ and PL id to a design element and a check, and the known limits of this design; part of TRD-saathi-2026-11-09.md
status: draft, new, pending review and owner lock (see docs/LOCKS.md)
upstream: PRD-saathi-2026-11-09-r2.md sha256 341ecc305674155b388184dd59323faeda447676c01d9d002c188b9aeadfdd8b; SPEC-saathi-requests-2026-11-09.md sha256 b798a89a5d876a26d7e30e3d8b3ad380ffa276cb8b133e5b87baf17c3c32ea72; SPEC-saathi-safety-2026-11-09-r2.md sha256 6b9a198e72f618e26af03ff63e1555abf9c056e7fcf8a3e4cdff7fd078b9c202; SPEC-saathi-assurance-2026-11-09-r2.md sha256 be50717893ed345c507fcc856d7f8694931b520e3bf4110200b922519007137e; SPEC-saathi-evidence-2026-11-09-r2.md sha256 482a276cd9216e76d527619579f554abad7f89d4c8ea13c6159d8a56b338eabf; SPEC-saathi-alternatives-2026-11-09.md sha256 79711ac92ad7aee8b368b0c282f93220b123510a76c3922f0e8b220748e89862; POLICY-saathi-2026-11-09-r2.md sha256 aee5383d7d124ddad9f2fbb8bf9dcc50ac066eb05d128d641f126a97f5495dd9; POLICY-saathi-data-2026-11-09.md sha256 993dc52a89eb0fda3333df48f110c5654a3b7f184923d71bcd39c073bc782b7f; RECORD-saathi-decisions-2026-11-09.md sha256 a1ec6109610478a4582467cebe12c11d4bd266b89b35a89fdbe44dbde9f86f45
sources: the contracts in upstream; src/, tests/, infra/ and .github/workflows/ci.yml on branch phase-0/foundation at 3d46456; scripts/design_check.py in this repository
checked-at: 3d46456, 2026-11-09. Code read with `git show`; the coverage script ran on this design set (section 3).
---

Read section 3 for the coverage result, then section 2 to find the rule and the check for any RQ or PL id.

# Saathi TRD checks: design rules and coverage

## 1. Scope and marks

Each row is a design rule (TQ), the RQ and PL ids it serves, the check that shows it holds, and an evidence mark. Marks follow PRD section 2. E rows cite the code at 3d46456 as file and line; every other mark is P unless shown. Phase of a row is the phase of the ids it serves in the PRD table, and nowhere else. In the Serves column a bare number continues the last prefix (RQ-79, 35 means RQ-79 and RQ-35), and "RQ-58 to 62" is a range.

## 2. Design rules

| Id | Design rule | Serves | Check | Mark |
|---|---|---|---|---|
| TQ-01 | The webhook inserts one inbox row with a unique provider id, commits, then returns 200; no process-local dedupe | RQ-79 | suite inbox-crash, 20 runs; grep test for a seen-set | E: `_Dedupe` and a background task, `app.py:36-49`, `:159`. N |
| TQ-02 | The signature is checked on the raw body before parsing; the sender allowlist and rate limit act before the pipeline; unknown senders leave a keyed-hash row; bad input gets 400 or 413 | RQ-81 | fixture tests per branch; the unknown-sender test sees no outbound call | E: `verify_signature`, `whatsapp.py:29-34`. N: the rest |
| TQ-03 | A worker leases rows with `SKIP LOCKED`; five failures end `dead` with a caregiver alert; every effect is keyed by the inbox id | RQ-79, 35 | inbox-crash; lease-expiry test | N |
| TQ-04 | One transition table per machine applied by compare-and-set; a table-driven test covers every row and refuses every pair outside the table | RQ-35, 36, 39 | `tests/test_states.py` over the states part; fault: delete a row | N |
| TQ-05 | Classification order (emergency, withdrawal, cancel, answer to an open question, amendment or new), concurrency and queue limits | RQ-39, 74 | precedence, "nahi", 3-card, urgent-queue and supersede-race tests | N |
| TQ-06 | The acknowledgement is code and a stored clip, sent within 10 s of the inbox commit; cancel and withdrawal phrases are code matches that pause, tier and budget never gate | RQ-74 | fake-clock latency test; cancel tests before and after step 1; withdrawal drill | N |
| TQ-07 | Timers and reminders are rows on the lease helper under a fake Clock; classes, refresh and expiry follow the SPEC timer table | RQ-20, 14 | fake-clock tests per timer row; refresh-count test | N |
| TQ-08 | The keyword radar runs first on every persisted message, rate-limited ones included; the classifier only adds flags; phrases are only added | RQ-16, 17 | suite radar; `tests/test_guards.py`; manifest check shown to fail on equal authors | E: keyword layer `radar.py:91-112`, 10 tests. N: classifier, recall |
| TQ-09 | An alert event is a set of rows; stages a, b and c run on the cadence, stop on a registered approver's acknowledgement and resume after a restart | RQ-18 | `tests/test_alerts.py` under a fake clock; six counter scripts | E: in-memory repeat, 5 times 120 s, `telegram.py:29-30`, `:83-88`. N |
| TQ-10 | Stages b and c need a valid SO-1 row; expiry silences them, alerts the owner and leaves stage a running | RQ-18, 95a; PL-26 | SO-1 expiry test; use-audit test; write-denied test for agent roles | N |
| TQ-11 | Stages a and c use no WhatsApp, model or ASR; with every model service down an emergency still alerts and plays the stored clip | RQ-16, 17, 18, 78 | all-fake-down test | N |
| TQ-12 | Audio that fails ASR is forwarded to the caregiver with a templated reply; two in 10 min start stage a | RQ-82 | ASR failure fixtures | E: failure becomes a placeholder, `pipeline.py:78-80`. N |
| TQ-13 | Replies follow D-69: stored clips with hashes, local dynamic speech, hosted fallback only after PL-20, Hindi text when speech fails; a reply is never skipped | RQ-01, 74, 82; PL-20 | `tests/test_voice_store.py`: hash mismatch, runtime down, no receipt | N |
| TQ-14 | Redaction is deterministic, text only, and runs before any hosted call, trace or cache write | RQ-41, 44; PL-03 | suite redaction with canaries per ID type; canary test across stores | E: five patterns, `pii.py:53-62`. N: voter, passport |
| TQ-15 | Transcripts live only in `raw_text`; cards and alerts hold a summary and slots; show raw reads the table and writes an audit row | RQ-97, 19; PL-07 | trace-canary suite; show-raw audit test | E: raw text reaches cards and traces, `telegram.py:57`, `pipeline.py:59` |
| TQ-16 | Spans hold ids, counts, hashes and durations through a field allowlist; tracing never fails a request | RQ-57 | trace-canary suite; backend-down test | E: whole node outputs traced, `pipeline.py:128-135` |
| TQ-17 | The gateway argument is `Sensitivity`, default PERSONAL; tier F takes a `PublicPayload` built in allowlisted modules | RQ-40; PL-02 | default-argument test; privacy-gate test; grep lint | E: `personal` is required and False reaches F, `gateway.py:182`; one test exists |
| TQ-18 | Grades and floors, fallback upward only, a schema per decision, refusal as a field, `cascade` raising when nothing is accepted | RQ-45, 46, 47, 49 | one test per floor row; refusal-not-retried test; all-rejected-raises test | E: `cascade` returns the last unaccepted result, `gateway.py:278-280` |
| TQ-19 | The paid cap and effort bounds run on a ledger: reserve before a call, an unset cap or missing price disables, a spent bound ends the request `blocked` | RQ-48, 92 | ledger tests; bound tests; emergency-over-limit test | E: in-memory ledger, `gateway.py:88-107` |
| TQ-20 | Roster entries carry receipts per data and media class; no receipt means no personal data and no route for that class | RQ-50, 96; PL-20 | roster loader test; handoff test per class | R: terms unverified (D-37) |
| TQ-21 | Cache keys carry prompt version, grade and sensitivity; content is redacted; no action intent is served from a cache | RQ-54 | one test per cache rule | E: exact cache, `gateway.py:110-125`, test passes. N |
| TQ-22 | The router returns typed intent, slots and confidence; code picks outcome (a), (b) or (c); medicine and bills take (c); guesses stay on optional slots and are flagged | RQ-02, 07 | pipeline cases per outcome; guess-scope test; 10 split cases | E: summary path tested. N: floors |
| TQ-23 | Urgency and ride type come from profile rules that code evaluates; the profile is versioned and agent changes need a card | RQ-05, 51 | one test per rule kind; model-cannot-set-urgent test; version test | E: urgency comes from the model, `router.py:51` |
| TQ-24 | Planners get typed slots only; each intent has a tool allowlist; external text is wrapped as data; perception and debunk sessions hold no tool | RQ-33, 12, 15 | allowlist test per intent; injection suite per action class | N |
| TQ-25 | CommerceAdapter has `quote`, `place_order`, `find_order` and a read-only `cancel_path`; no `find_order` means handoff only; MCP servers are pinned by version and schema hash | RQ-06, 94 | adapter contract tests with fakes; schema-change test; no-access test | N |
| TQ-26 | Shadow mode: the shadow adapter has no commit method, shows quotes to the primary approver only and feeds blind labels | RQ-04, 08, 13 | shadow suite; absent-method test | N |
| TQ-27 | The quote builder takes every value from adapter facts and the profile and binds an approval to the canonical hash | RQ-22, 29 | hash tests; ceiling tests | E: `quote.py:25-66`, tests pass |
| TQ-28 | Each policy rule has a unit test with a `rule("PE-nn")` marker; an unset cap denies; caps are checked on the ceiling | RQ-25, 26, 27, 28, 29 | `tests/test_policy.py`; `rule_index.py` | E: 32 tests; an unset cap raises, `engine.py:28-30` |
| TQ-29 | Only `authorize_commit` leads to `place_order` or the commit tap; the device refuses a commit selector without a grant | RQ-30 | AST call-graph test; grant test | E: `authorize_commit` and 9 commit tests. N: static test |
| TQ-30 | A tap decides by compare-and-set; a token is a row, single use, bound to request, quote hash, approver, action and expiry; only an authenticated callback of a registered approver mints one | RQ-21, 22, 23, 24 | concurrency test; replay, expiry and void-on-supersede tests; injection phrases | E: signature and hash bound, `quote.py:69-94`. N: expiry, request id |
| TQ-31 | The second factor over a cap is TOTP with a replay rule, lockout and an encrypted secret, enrolled on the owner's terminal | PL-27, RQ-26 | wrong, expired, replayed and locked-out code tests; canary test | N |
| TQ-32 | The commit has three steps, a persisted intent, a household lock, one external call per token, a reaper and `find_order` reconciliation; nothing retries by itself | RQ-80, 83, 31, 35 | kill-between-steps suite, 20 runs; race test, 100 runs | E: `commits` has no intent state, `001_init.sql:94-103`. N |
| TQ-33 | Pause is a row read live and fail-closed; any approver pauses, only the primary resumes | RQ-32, 91 | restart test; unreadable-flag test | E: in memory, `telegram.py:33-37`. N |
| TQ-34 | Final-screen verification is the seven steps of the ops part: one dump, exact-one selectors, compare in code, 2 s to the tap | RQ-87, 88 | fixture dumps per class; hostile-screen suite with its deliberate fault; test that fails when a value comes from a model | N |
| TQ-35 | Phone flows stop at 25 actions or 5 min; a macro failure is a handoff; macros are human-authored, versioned and signed before a commit | RQ-89, 53 | loop test; unsigned-macro test | N |
| TQ-36 | The phone and bridge are sandboxed and watched; offline means handoff `phone_offline` | RQ-34, 90 | fake-heartbeat test; package-drift test; outside port scan | N |
| TQ-37 | Channel states come from three signals; windows and templates are checked at every call site; a closed window with no template blocks | RQ-77, 78 | inventory test; fake-provider tests; silence test over 7 days | N |
| TQ-38 | Every table carries `household_id` and cross-household reads fail | RQ-38 | cross-household query test; schema test | E: `approvals` and `llm_spend` lack it, `001_init.sql:83-92`, `:107-114` |
| TQ-39 | The audit log is append-only through an insert-only role and a security-definer function; low-entropy values are keyed hashes; failures and denials stay | RQ-56a | UPDATE and DELETE denied test; keyed-hash canary; actor-present test | N |
| TQ-40 | The chain has one serialized writer and a nightly off-site head, checked by `audit_verify.py` | RQ-56b | chain-break, head-mismatch, concurrent-writer and genesis tests | N |
| TQ-41 | Sign-offs and config changes are rows in a schema the agent roles cannot write; each use writes an audit row; expiry behaves as off | RQ-95a, 95b; PL-26 | write-denied test; expiry test per class; pre-expiry alert | N |
| TQ-42 | A run record is written when a run ends in any state; an empty run is `failed`; a check that did not run is `unavailable` | RQ-55, 36 | schema test; empty-run test | N |
| TQ-43 | Approve, edit, reject and `/called` write audit rows; the weekly digest prints counts with n | RQ-66, 68 | feedback tests; digest snapshot | N |
| TQ-44 | Retention values sit in config; erasure covers every store the system controls and lists the others; no real data before a consent row | RQ-42, 98; PL-05, 06, 29, 30 | erasure canary test; retention dry run; consent gate test; drills | N |
| TQ-45 | A nightly encrypted dump goes off-site for 30 days; a monthly restore replays the erasure log | RQ-37 | `file` on the dump; restore exit code | N |
| TQ-46 | Every outbound host is a row in POLICY-data; model-driven containers reach only the egress proxy, which lists hosts per container | RQ-43, 102; PL-02, 04 | host-list test; `strace` run; reach test inside each container; compose test | E: one default network, `docker-compose.yml:55-223`. N |
| TQ-47 | Credentials are registered with an expiry and injected outside model-driven processes; `.env` is mode 600; keys are separate | RQ-93; PL-13, 14 | register test; environment test; `stat -c %a .env` | E: the whole `.env` goes to one service, `docker-compose.yml:201`. N |
| TQ-48 | A compromise has a kill switch, a runbook with checks and a quarterly drill | PL-28, RQ-91 | the revocation steps and the drill receipt | N |
| TQ-49 | Debunk: claim extraction, verdicts, fetch records and the auto-send gate with SO-2 | RQ-09, 10, 11; PL-26 | suite debunk; rule table tests; expiry test | N |
| TQ-50 | Shopping, tech help, Q&A and appointments reuse the adapters and the empty allowlists the PRD names | RQ-08, 12, 13, 15 | allowlist unit tests; shadow suite; safety suite | N |
| TQ-51 | The elder reply follows the language of the elder's message; the card follows the caregiver's | RQ-01 | 10-case language table test | N |
| TQ-52 | A caregiver forward enters the same inbox with source `caregiver_forward` | RQ-03 | fake Telegram update test | E: intake built, `telegram.py:136-165` |
| TQ-53 | The card shows summary, slots, doubt flag, one alternative, screenshot or lines, three buttons and the original audio | RQ-19, 24 | snapshot test; attachment test | N |
| TQ-54 | Issue labels come from a closed vocabulary by code (Phase 5) | RQ-73 | schema, canary-leak and export-gate tests | N |
| TQ-55 | Deferred items stay recorded `not applicable` until their re-entry condition holds | RQ-52, 67 | gate rows with a stated reason | N |
| TQ-56 | Every suite has a registry row; code grades first; every rate prints k, n and a bound; CI job `evals` fails on missing data; model-variable suites follow the plan rule; private suites leave receipts | RQ-58, 59, 60, 61, 64, 65, 99; PL-18 | registry check; empty and deleted dataset runs; report-format test; paid-run approval test | E: `evals/` holds a README only |
| TQ-57 | Every gate is shown to fail on a deliberate fault and the fault is recorded | RQ-62 | per gate: apply the fault, see invalid, restore | N |
| TQ-58 | `gate.py` prints rows up to phase N; unavailable and invalid never pass; manual receipts need a signer and a date; the rule index fails on a rule with no test | RQ-63, 100; PL-23 | registry-versus-PRD test; the faults of RQ-100; `rule_index.py` faults | N |
| TQ-59 | The doc lint runs before every commit and in CI | RQ-101; PL-19 | one failing fixture per rule; one clean fixture | N |
| TQ-60 | The licence audit covers packages, images, fonts and model files; exceptions never include LGPL; an unknown licence fails | RQ-71; PL-09, 10, 11 | audit on a package with no licence field | E: passes for Python packages (PRD section 2). N: images, models |
| TQ-61 | CI actions and images are pinned by digest and installs use hashes | RQ-72 | lint for a `uses:` line without 40 hex | E: pinned by tag, `ci.yml:11-12` |
| TQ-62 | A custom component has three alternatives and a comparison before its custom code merges | RQ-70 | one comparison record per row | N: BLOCKED, alternatives spec section 3 |
| TQ-63 | Repository hygiene: no elder media or personal value, no AI attribution, a secret scan, a protected `main`, review by another identity, drafts by agents, merges by the owner, a reporting route, locks | PL-08, 12, 15, 16, 17, 21, 22, 24, 25, 31 | leak scan; a planted trailer and key fail; settings read | E: no scanner job, `ci.yml`; `Settings()` leaves paid tiers off, `config.py:55`. N |
| TQ-64 | Data classes and destinations live in POLICY-data and are read by TQ-46 | PL-01 | reader check that each store and host names its class | N |

## 3. Coverage and checks run on this design set

`uv run python scripts/design_check.py` from the repository root reads the first column of every requirement table in the PRD, SPEC and POLICY files, expands the Serves column above, and fails on an id with no TQ row. It also checks that every `upstream:` pin and every index prefix equals `sha256sum`, every size limit, the closing line of each file, the absence of long dashes, and every edge of the architecture figure against the edge and components tables.

Result on this design set, run 2026-11-09 after the last edit:

```
coverage: 98 RQ ids and 31 PL ids in the contracts; 64 TQ rows; 129 mapped, 0 unmapped, 0 unknown
pins: 85 upstream pins checked
index: 19 rows checked
sizes, closing lines, dashes: checked
figure: 25 edges match the edge table; 20 of 22 components drawn; not drawn: C19, C20
result: OK
```

C19 and C20 are build-time and are omitted from figure 1 on purpose. The check was shown to fail on three deliberate faults, each applied to a scratch copy and then discarded: deleting the TQ-28 row printed `unmapped ids: RQ-25, RQ-27, RQ-28`; changing one hex digit of a pin printed `pin of ... is stale`; renaming edge E7 in the figure printed `figure edges differ from the edge table`. Each exit code was 1.

## 4. Known limits

| Id | Limit | Fix |
|---|---|---|
| L-1 | The SPEC state table has no request state for an emergency or scam message. This design opens an alert event and creates no request | state it in SPEC requests at the next revision |
| L-2 | The SPEC state table lists no token states. The states part derives four from `spent_at`, `voided_at` and `expires_at` | add them to the table at the next revision |
| L-3 | SPEC requests says a card in `awaiting_approval` has an unspent token, while the timer table counts expiry from the tap. The design mints a token at the tap | reword the SPEC row |
| L-4 | The grade floor table of SPEC assurance has no row for speech output. This design sets fixed clips at G0, dynamic speech at G1 and a hosted fallback at G2 with a receipt | add the row, citing D-69 |
| L-5 | POLICY RQ-102 defines a model-driven process as one that sends data to a model, acts on its output or reads untrusted content. This design reads "reads untrusted content" as feeding it to a model, so core, which runs patterns over text, has its own egress list | confirm or reword RQ-102 |
| L-6 | The commit grant (the device refuses a commit tap without a grant bound to the dump) is not in any SPEC row | add it to safety RQ-30 or RQ-87 if kept |
| L-7 | Credential injection by TLS termination means the egress proxy sees plaintext of listed hosts; commerce terms on proxies were not read | read the terms before Phase 1 (PL-20) |
| L-8 | RQ-42 is a Phase 1 gate row and Phase 0 step 8 builds its first part | none; the gate prints it from Phase 1 |

Skipped or not checked: the TQ checks are designs and none has run; the E marks cite 3d46456 and were not re-run; the coverage script proves that each id appears in a Serves cell, not that the rule serves it well; no reader check ran.
