---
id: LLD-saathi
kind: contract
governs: Phase 0 of the TRD at 3d46456: package layout, modules, data, flows, tests, CI and work units; Phase 1 as an outline
status: draft, pending review (see docs/LOCKS.md)
upstream: PRD-saathi-2026-11-09-r2.md sha256 141b677d217017de6973d67f2f5d46eb549b2b40464d4f7426f4ee83d0ed15ec; SPEC-saathi-requests-2026-11-09.md sha256 0e595737a4216baecdc1ad6a1436d3b4351988e7fc8f26c4f752935930eb60b5; SPEC-saathi-safety-2026-11-09-r2.md sha256 711d673b563105b0115376a8fbb5b2fb6e9a12dfa06afdd4e60103bd53e8bc12; SPEC-saathi-assurance-2026-11-09-r2.md sha256 4d02d7a0ce53e02870ecb794d179f9863522f86df962c60489060c3e1069867a; SPEC-saathi-evidence-2026-11-09-r2.md sha256 84e320c15d8d622da37071101ab30bedccfdcaa0ce671f3d5fccb2cd44eccf3d; SPEC-saathi-alternatives-2026-11-09.md sha256 9d566412ad77d4afd845e8067b55e316074e37827730cb1dfaeb77ddae07378d; SPEC-saathi-platform-2026-11-10.md sha256 2ccd496e0116592e2461f70d0fdbe2190dd8373e11afda13b53206be1c303a99; SPEC-saathi-product-2026-11-10.md sha256 d39d13904842ec48b5cc0fc8284c06a0f863cfc23ff1dcdd044e401dc8682fb0; POLICY-saathi-2026-11-09-r2.md sha256 2ef14b0294378ff7a44be0e96e1f74a94c90614844e59f390eb29f1471bf339c; POLICY-saathi-data-2026-11-09.md sha256 907e2bd010ffdebe8e22a43fd693258be096922972c1846bf73f64593bf3f6c4; TRD-saathi-2026-11-09.md sha256 88d14bcfec16d0b6b158a0437c2f2c7dd61ab84b9ab7f1c8b1bf94c51d322075; TRD-saathi-checks-2026-11-09.md sha256 cd0d5447cdcfbbbb1c176e2c5073288295e2ef342f1a0b0d73aa6827a48c632d; TRD-saathi-checks-product-2026-11-10.md sha256 562f213cee78e14aac15e70f4628f00b19331369f4934892768a320ec55b1d1d; TRD-saathi-states-2026-11-09.md sha256 6a6843a411f59ce326c9573fe7ed14e6bb76ed19e30c1b9a5e3514b4bda25db6; TRD-saathi-threats-2026-11-09.md sha256 6dfaf781738ec782a607d934c7f24c835c49905e0a9d26251d21e6a836300d21; TRD-saathi-ports-2026-11-09.md sha256 993e28969a4c98164e04767cd324f88930b1e73dbc2a854f74a725be1163ec3c; TRD-saathi-ops-2026-11-09.md sha256 4ee243846c99ce3fe67dc9ebe4dc783e2a4ab6363ba425c069f374ee75dfd1f1; TRD-saathi-build-2026-11-09.md sha256 6bed289223a9cd46d5e9af197f79dae293a6199e807403d3d668c8017452fe94; RECORD-saathi-decisions-2026-11-09.md sha256 41bbe3270e83e1a23535f8903d7e5b60edeea9dae846b0b6feb4c3b152ff82d9; RECORD-saathi-decisions-strategy-2026-11-10.md sha256 8fa8da2c82c49bdee5900b977dd29c721b20ce53afe10ef9cea7c14e86d4c2b8
parts: LLD-saathi-intake-2026-11-10.md, LLD-saathi-safety-2026-11-10.md, LLD-saathi-data-2026-11-10.md, LLD-saathi-grants-2026-11-10.md, LLD-saathi-tests-2026-11-10.md, LLD-saathi-build-2026-11-10.md
sources: the contracts in upstream; src/, tests/, scripts/, infra/ and .github/ on branch phase-0/foundation at 3d46456; DOC-PHILOSOPHY.md; the owner's LLD exemplar for its shape (built, proposed, mapping)
checked-at: 3d46456, 2026-11-10. Code read with `git show origin/phase-0/foundation:<path>`; nothing was run in this pass. Checks run on this set are in section 6.
---

Read section 2 for what happens to each existing file, section 3 to find the part that specifies a module, section 4 for how each departure from the TRD was resolved, and the build part for the work units and their order.

# Saathi LLD: Phase 0 design and the Phase 1 outline

## 1. Scope, marks and parts

The TRD fixes components, ports, states, tables and the Phase 0 build order by id. This LLD fixes files, signatures, DDL, flows, tests and work units so one agent can build each unit without guessing. It states no new requirement. Where it adds to or departs from the TRD it says so in section 4.

Marks follow PRD section 2. A code fact is E at 3d46456 with file and line; every design element is P. Names of tests and files are proposals until the unit that builds them ends on its command.

| Part | Holds |
|---|---|
| LLD-saathi-2026-11-10.md | this file: layout, disposition of existing files, module map, TRD conflicts, owner questions |
| LLD-saathi-intake-2026-11-10.md | modules M01 to M13: types, ports, store, keys, config, edge, channels, radar, redaction, worker, ASR |
| LLD-saathi-safety-2026-11-10.md | modules M14 to M24: alerts, caregiver bot, voice, audit, traces, gateway, health, states, gate and lints, evals, admin |
| LLD-saathi-data-2026-11-10.md | migrations 002 to 005 as SQL, row-level security, household keys and their rotation |
| LLD-saathi-grants-2026-11-10.md | the init script that creates roles, security-definer functions, policies, table, function and sequence grants, and the module-to-grant check |
| LLD-saathi-tests-2026-11-10.md | test files and names per module, crash, isolation and canary tests, the Phase 0 gate rows |
| LLD-saathi-build-2026-11-10.md | four flows with transaction boundaries, CI jobs, work units WU-01 to WU-26 with their order, the Phase 1 outline |

## 2. Package layout for Phase 0

Two processes share one image: the edge (`saathi-edge`, a web app that only inserts) and the worker (`saathi-worker`, one asyncio process that runs the request worker, the escalation service, the caregiver bot polling and the heartbeat). Phase 0 compose runs them as services `edge` and `worker` (C-3, D-92). The model worker, adapters and egress arrive in Phase 1.

```
src/saathi/
  __init__.py  app.py  admin.py  config.py  hosts.py  domain.py  errors.py  ids.py  ports.py  clock.py
  faults.py  media.py  health.py  householdcfg.py  replies.py
  store/      __init__.py db.py scan.py keys.py migrate.py repos/{inbox,requests,alerts,updates,flags,ledger,
              voice,config,consents,signoffs,identities}.py
  edge/       __init__.py app.py handler.py ratelimit.py health.py
  channels/   __init__.py whatsapp.py telegram_elder.py telegram.py
  guards/     __init__.py radar.py pii.py
  worker/     __init__.py loop.py lease.py steps.py summaries.py
  alerts/     __init__.py service.py plan.py stages.py
  voice/      __init__.py store.py tts.py clips.py codec.py
  audit/      __init__.py sink.py events.py
  trace/      __init__.py exporter.py allowlist.py jsonl.py
  llm/        __init__.py gateway.py ledger.py roster.py routing.py
  perception/ __init__.py asr.py
  policy/     __init__.py engine.py quote.py          (kept for Phase 1)
  states/     __init__.py tables.py machine.py
  understand/ __init__.py router.py                   (kept for Phase 1)
scripts/  gate.py gate_registry.toml rule_index.py rules.toml doc_lint.py doc_lint_words.txt ci_lint.py
          license_audit.py license_exceptions.toml leak_scan.py commit_msg_check.py repo_settings.py
          pilot_counts.py record_fault.py design_check.py
evals/    run.py registry.toml report.py stats.py plan.py suites/ datasets/
tests/    conftest.py markers.py fakes/ fixtures/ test_*.py (names in the tests part)
infra/sql/schema/  001_init.sql 002_inbox.sql 003_alerts.sql 004_audit_control.sql 005_ledger_voice_flags.sql
PHASE  docs/evidence/receipts/
```

Entry points: `saathi-edge = saathi.app:main_edge`, `saathi-worker = saathi.worker.loop:main`, `saathi-admin = saathi.admin:main`, `saathi-migrate = saathi.store.migrate:main`. E: `saathi-api` and `saathi-bot` exist (`pyproject.toml:35-37`) and go. New dependency: `cryptography` (Apache-2.0 OR BSD-3-Clause, TRD ops choice 2); `langfuse` moves to an optional extra `trace` for Phase 1. Run the licence audit after the change (the repository's agent instruction file requires it).

Disposition of every file at 3d46456 (K kept, C changed, R removed). Change numbers are rows of the TRD build part section 2.

| File | | What and why | Change |
|---|---|---|---|
| `src/saathi/__init__.py`, `channels/`, `guards/`, `llm/`, `perception/`, `policy/`, `understand/` package `__init__.py` | K | none | none |
| `src/saathi/app.py` | C | composition root `main_edge`; `_Dedupe`, the background task and `elder_phones` go; routes move to `edge/` | 1, 2, 27, 30 |
| `src/saathi/channels/whatsapp.py` | C | `WhatsAppElderChannel`, `PersonRef`, `ProactiveNotAllowed`, sticker and contact map to `unsupported` | 2, 30 |
| `src/saathi/channels/telegram.py` | C | caregiver bot only: cards from slots, durable alert path via `alerts/`, no in-memory pause or acknowledgement set | 5, 6 |
| `src/saathi/config.py` | C | deployment values only; household values move to rows; empty approval secret refused | 20, 29 |
| `src/saathi/domain.py` | C | `Source` replaces `Channel`; ids as `NewType`; no phone number field | 30 |
| `src/saathi/guards/radar.py` | C | `rule_ids`, `combine` with the `Classifier` port; phrases only added | 3 |
| `src/saathi/guards/pii.py` | C | voter and passport formats; `assert_clean` | 8 |
| `src/saathi/llm/gateway.py` | C | `Sensitivity`, `ModelRequest`, `NoAcceptedResult`, grade routing, Postgres ledger | 9, 10, 11, 12 |
| `src/saathi/observability.py` | R | whole-output tracing and the Langfuse client go; `trace/` replaces them | 7, 28 |
| `src/saathi/perception/asr.py` | C | takes the `ASR` port and `AudioBlob`; the real decode path is Phase 1 | 26 (Ph 1) |
| `src/saathi/pipeline.py` | R | replaced by `worker/`; its three ports move to `ports.py`; LangGraph stays a dependency for Phase 1 step graphs | 1, 3, 7 |
| `src/saathi/policy/engine.py`, `quote.py` | K | unchanged in Phase 0; rule markers are added to the tests; Phase 1 changes 14 to 16 | 14 to 16 (Ph 1) |
| `src/saathi/replies.py` | C | reply keys and constants with no name; clip rows | 4 |
| `src/saathi/store/__init__.py` | C | empty today; exports `Store`, `Role`, `Tx` | 19 |
| `src/saathi/understand/router.py` | K | adapts to `Gateway.complete` so it compiles; not called in Phase 0 | 12 (Ph 1) |
| `tests/test_channels_llm_pipeline.py` | C | split into `test_gateway.py`, `test_whatsapp.py`, `test_worker.py`; pipeline tests go with `pipeline.py` | 1, 7, 9 |
| `tests/test_guards.py` | K | extended (LT-12) | 3 |
| `tests/test_policy.py` | K | 32 tests keep passing; markers added | RQ-63 |
| `scripts/license_audit.py`, `license_exceptions.toml` | C, K | modes for image manifests and Dockerfiles | 22 |
| `.github/workflows/ci.yml` | C | jobs of the build part section 2 | 23, 25 |
| `infra/sql/schema/001_init.sql` | K | never edited; forward-only migrations change it | 19 |
| `infra/sql/00-create-databases.sh` | C | every role and login, created as superuser (no migration creates a role); the database is owned by `saathi_migrator`; it no longer runs 001 | 19 |
| `infra/docker-compose.yml` | C | services `edge` and `worker`; Langfuse behind a profile | 28 |
| `infra/Caddyfile`, `infra/Dockerfile` | C | health route and webhook routes; two commands | 27 |
| ASR image Dockerfile under `infra/` | C | no ffmpeg, no `--convert` | 22 |
| `evals/README.md` | K | the harness is added beside it | RQ-58 |
| `pyproject.toml`, `uv.lock`, `.env.example` | C | entry points, `cryptography`, variables in step with `config.py` | 20 |

Not Phase 0 (TRD changes 13 to 18, 21, 24, 26): cache keys, tokens, caps, ride TTL, durable pause, forward intake, the service split, issue labels, the opus decode path.

## 3. Module map (module to requirement)

Ids are the Phase 0 requirement ids of PRD section 8. Imports lists the modules a module imports; a module never imports one created by a later work unit. Every Phase 0 id appears in at least one row here and in at least one test row of the tests part; `scripts/design_check.py` checks both (section 6).

| Id | Module | Serves | TQ | Part | Imports |
|---|---|---|---|---|---|
| M01 | `domain.py`, `errors.py`, `ids.py` | RQ-105, 109; PL-01 | TQ-67 | intake | - |
| M02 | `config.py`, `hosts.py` | RQ-43, 110; PL-04, 12, 13, 14 | TQ-46, 47, 72 | intake | M01 |
| M03 | `ports.py`, `clock.py` | RQ-105, 18 | TQ-67 | intake | M01 |
| M04 | `store/` db, scan, repos | RQ-79, 109, 38, 56a, 35 | TQ-71 | intake | M01, M03, M21 |
| M05 | `store/keys.py` | RQ-111a, 56a | TQ-73 | intake | M01, M03, M04 |
| M06 | `householdcfg.py`, config and consent repos | RQ-110; PL-05, 33, 34 | TQ-72, 81 | intake | M01, M03, M04 |
| M07 | `edge/`, `app.py` | RQ-79, 81, 16, 18, 104 | TQ-01, 02, 08 | intake | M01, M02, M03, M04, M05, M08, M09, M10, M20 |
| M08 | `channels/whatsapp.py` | RQ-105, 81 | TQ-02, 67 | intake | M01, M03 |
| M09 | `channels/telegram_elder.py` | RQ-105 | TQ-67 | intake | M01, M03 |
| M10 | `guards/radar.py` | RQ-16, 17, 18 | TQ-08 | intake | M01, M03 |
| M11 | `guards/pii.py` | RQ-41, 44; PL-03 | TQ-14 | intake | M01 |
| M12 | `worker/` | RQ-79, 36, 97 | TQ-03, 06 | intake | M01, M03, M04, M06, M10, M13, M16, M17 |
| M13 | `perception/asr.py`, `voice/codec.py`, `media.py` | RQ-16 | TQ-12 | intake | M01, M03 |
| M14 | `alerts/` | RQ-18, 95a; PL-26 | TQ-09, 10, 11 | safety | M01, M03, M04, M06, M16, M17 |
| M15 | `channels/telegram.py` | RQ-97, 18 | TQ-15, 53 | safety | M01, M03, M04, M05, M06, M14, M17 |
| M16 | `voice/`, `replies.py` | RQ-17, 116 | TQ-13, 78 | safety | M01, M03, M04, M17 |
| M17 | `audit/` | RQ-56a, 95a; PL-07 | TQ-39, 41 | safety | M01, M03, M04, M05 |
| M18 | `trace/` | RQ-44, 57, 97; PL-07 | TQ-16 | safety | M01, M03, M11 |
| M19 | `llm/` gateway, roster, routing; `understand/router.py` | RQ-40, 45, 46, 47, 48, 49, 50, 96; PL-02 | TQ-17 to 20 | safety | M01, M03, M11 |
| M20 | `health.py` | RQ-104 | TQ-66 | safety | M01, M03, M04 |
| M21 | `states/` | RQ-36, 35, 39 | TQ-04 | safety | M01 |
| M22 | `scripts/` gate, rule index, doc lint, CI lint, licence audit, leak scan, commit check, repo settings, pilot counts, fault recorder | RQ-62, 63, 71, 72a, 100, 101, 117, 118, 119; PL-08, 09, 10, 11, 12, 15, 16, 17, 18, 19, 21, 22, 31 | TQ-57 to 61, 63, 79 | safety | - |
| M23 | `evals/` | RQ-58, 59, 60, 61, 99 | TQ-56 | safety | M10, M11 |
| M24 | `admin.py` | RQ-95a, 110, 111a; PL-32, 33 | TQ-41, 80 | safety | M01, M02, M04, M05, M06, M16, M17 |
| M25 | `policy/` (kept) | RQ-63; PL-36 | TQ-28, 82 | safety | - |
| M26 | `scripts/design_check.py` | RQ-103; PL-24 | TQ-59 | safety | - |
| M27 | `store/migrate.py`, the init script and migrations 002 to 005 | RQ-38, 109, 56a, 95a, 111a | TQ-71 | data, grants | - |
| M28 | `llm/ledger.py` | RQ-48 | TQ-19 | safety | M01, M03, M04 |
| M29 | `faults.py` | RQ-79 | TQ-01 | tests | - |

M27 to M29 are the migrator with the init script and migrations (data and grants parts), the Postgres ledger (safety part, M19) and the fault hooks (tests part, rule 4). M25 and M26 have no section of their own: M25 is unchanged code whose tests gain `@rule` markers and the no-money tests (LT-32, LT-33); M26 is the check of section 6; its check 8 reads the PRD Phase 0 row, the rows above and the `LT-` rows of the tests part, and its check 9 reads the Imports column and the Modules and After columns of the work units (build part section 3).

## 4. TRD and SPEC conflicts, resolved

The first LLD pass found twelve places where the design set did not agree. Each is now resolved by an edit to the TRD or SPEC file named, so the documents say the same thing. The ids stay as labels in the other parts.

| Id | Finding | Resolution and where it was made |
|---|---|---|
| C-1 | The edge may only insert, yet the rate limit of RQ-81c counts earlier rows and reads the config | the edge calls three security-definer functions that return an id, a time or the core heartbeat (`resolve_sender`, `rate_state`, `beat`): TRD ports section 5 edge row, TRD build step 2, SPEC safety RQ-81 |
| C-2 | RQ-105's suite runs on the Telegram class in Phase 0, while the phase table put the Telegram bot in Phase 1 | the class is Phase 0 against a fake client; the live bot, start codes and switch are Phase 1 (D-93): PRD phase rows 0 and 1, TRD build change 30 and step 3, SPEC platform order of work |
| C-3 | Ops part section 3 ran one `saathi` service in Phase 0 | two services of one image, `edge` and `worker` (D-92): TRD ops section 3 text and table, TRD build changes 21 and 31 and step 8, PRD Phase 0 row |
| C-4 | RQ-81 stores an HMAC of the sender, but a household key cannot be used before the household is known | a deployment key `SENDER_HMAC_KEY` in the PL-14 class: SPEC safety RQ-81, POLICY PL-14, TRD ports 002 row |
| C-5 | `001_init.sql` holds personal values in plain columns, and `elder_identities` was listed in 012 though the edge needs it in 002 | `elder_identities` is created in 002 with a sealed address and `elders.wa_phone_e164` is dropped; the other personal columns are sealed in the first Phase 1 migration before any real row (D-91): TRD ports 002, 010 and 012 rows, SPEC platform RQ-111b |
| C-6 | A sender not yet known has no household, so `rejected_inbound` cannot satisfy RQ-38 | the tables with no household column are `rejected_inbound`, `voice_clips` and `heartbeats`, an exact list in the schema test: TRD ports section 4, SPEC platform RQ-109 |
| C-7 | `saathi_owner` was reachable from no container, but key commands need the key-encryption key | `saathi-admin` runs on the host shell where `KEK_FILE` is readable, never in a container: TRD ports section 5 owner row |
| C-8 | No migration held a heartbeat row, and the edge may not write other tables | `control.heartbeats` (tenantless) and `control.beat()`, in 005: TRD ports 005 row and edge row |
| C-9 | RQ-16's radar set needs an author other than the keyword author | the caregiver writes about 100 Hindi and Hinglish phrases as a manual receipt (D-95): SPEC safety RQ-16, SPEC product G-6, SPEC evidence suite registry, SPEC alternatives, TRD build step 4 |
| C-10 | RQ-110 says agent roles only read `household_config`, but the Phase 1 `/channel` switch writes | in Phase 1 the core may insert a version for the switch alone, guarded by `created_by` naming an approver; no Phase 0 role can write: SPEC platform RQ-110 |
| C-11 | The ports part made `provider_message_id` unique and `update_id` the key | unique per `(household_id, provider_message_id)` and key `(bot, update_id)`: TRD ports 002 and 003 rows, SPEC safety RQ-79 |
| C-12 | Change 26 and TQ-12 place the opus decode path in Phase 1 | Phase 0 builds the `ASR` and `AudioCodec` ports with fakes and the `unscanned` alert and audio forward cover audio (A11): TRD build change 3, TQ-12 |

Functions beyond the TRD ports part: `rate_state`, `beat`, `resolve_approver`, `unscanned_audio`, `reap_inbox`, `unacked_inbox`, `rejected_digest`; and the `schema_migrations` table of the migrator.

## 5. Owner decisions applied

The five owner questions of the first LLD pass are decided and recorded in RECORD-saathi-decisions-strategy-2026-11-10.md.

| Id | Decision | Applied in |
|---|---|---|
| D-91 (Q22) | addresses and profile values are sealed with per-household keys in the first Phase 1 migration, before any real data; Phase 0 is synthetic only | data part section 3, TRD ports 010 row, SPEC platform RQ-111b |
| D-92 (Q23) | two compose services, `edge` and `worker` | TRD ops, TRD build, PRD, work unit WU-22 |
| D-93 (Q24) | the Telegram elder channel class is Phase 0 | PRD phase table, TRD build, SPEC platform, WU-08 |
| D-94 (Q25) | a name with no slash in the index is a file in `docs/design/` | SPEC evidence RQ-101 and RQ-103, `scripts/design_check.py` |
| D-95 (Q26) | the caregiver writes the radar set, about 100 phrases, as a manual receipt | SPEC safety RQ-16, SPEC product G-6, SPEC evidence registry, tests part section 4 |

Decision recorded with the first LLD pass: D-90, in the same record: `saathi` is a working codename; the public name follows the first stable test, after a private shortlist and a trademark registry search; the elder-facing persona name is private config and never the codename.

## 6. Checks run on this design set

`python3 scripts/design_check.py` has check 8 (every Phase 0 id has a module row and a test row) and check 9 (work unit order: each unit's After numbers are lower than its own, every module is created by one unit, and every module a unit creates imports only modules created by that unit or by a unit it depends on). It also size-checks `docs/LOCKS.md` and the files of `docs/archive/`. Result, run 2026-11-10 after the final review fixes:

```
coverage: 117 RQ ids and 37 PL ids in the contracts; 83 TQ rows; 154 mapped, 0 unmapped, 0 unknown
pins: 156 upstream pins checked
index: 30 rows checked
sizes, closing lines, dashes: checked
figure: 26 edges match the edge table; 20 of 22 components drawn; not drawn: C19, C20
lld: 68 Phase 0 ids; 29 module rows and 51 test rows; 0 without a module, 0 without a test
order: 26 work units; 29 modules; 80 import edges; 0 violations
result: OK
```

Deliberate faults, each on a scratch copy with pins, index and locks recomputed so that only the named check failed. Check 8: removing RQ-104 from the M07 and M20 rows printed `lld: Phase 0 ids with no module row: RQ-104`. Check 9: moving the creation of M01, M03 and M21 from WU-03 to WU-09, which reproduces the review I blocker, printed 19 violations, the first `M04 (WU-06) imports M01, created later in WU-09`; deleting the arrow 03 from the After cell of WU-06 printed 15, the first `M04 (WU-06) imports M01 from WU-03, which WU-06 does not depend on`. Size: padding `docs/LOCKS.md` to 28924 bytes printed `LOCKS.md: 28924 bytes over 16000` (before this pass the script skipped that file, which had no front matter). A changed hex digit in a pin printed `pin of ... is stale`.

Database run. The role section of the grants part ran as the PostgreSQL superuser, then 001 to 005 with their appends ran as `saathi_migrator` (a non-superuser) on PostgreSQL 16.15 in a scratch instance, which was stopped and removed afterwards. A smoke test of 46 assertions passed. The edge inserted into `inbox` (a repeat inserted nothing), `raw_text`, `rejected_inbound` and `alert_events`, and called `resolve_sender`, `rate_state` and `beat`; it could not read `inbox` or `raw_text`, call `lease_next` or `audit_append`, or write the core heartbeat. `saathi_scan` listed all households. `lease_next` leased one row and a second call returned none for the busy elder; the other scan functions ran and left the household setting as they found it. `audit_append` worked for core and owner and raised `TenantNotSet` with no household; `UPDATE` and `DELETE` on `audit_log` were denied to core, owner, readonly and audit. The owner created a household, elder, approver, identity and keys, rotated and rewrapped a key, granted and revoked a sign-off, and wrote consent, config and an audit row. No function held `EXECUTE` through `PUBLIC`, and only `saathi_readonly` had `BYPASSRLS`. Without the sequence grant the edge insert on `rejected_inbound` failed with `permission denied for sequence rejected_inbound_id_seq`; with it the insert passed. The run found three traps now written into the grants part: a schema-level default privilege cannot remove `PUBLIC` execute, grants must come before the ownership transfer, and the transfer needs `CREATE` on the schema for the new owner.

Skipped or not checked: no Python, test or script of the LLD ran, because none exists yet; the SQL ran with `psql` and not through `saathi.store.migrate`; two concurrent workers on `lease_next` were not run; the Phase 1 outline interfaces were not compared with the alternatives records; unit sizes were not estimated or timed; the reader check of this LLD did not run.
