---
id: LLD-saathi
kind: contract
governs: Phase 0 of the TRD at 3d46456: package layout, modules, data, flows, tests, CI and work units; Phase 1 as an outline
status: draft, pending review (see docs/LOCKS.md)
upstream: PRD-saathi-2026-11-09-r2.md sha256 ee2a21d4115ce10d07f5fcae7567b00866e8411d2b40380f12c03450b4e16c8d; SPEC-saathi-requests-2026-11-09.md sha256 7e0cee8a2dab273d3776eafd6d68d6a7a0f51e1d75fa7b55064136b0c4ed0e1e; SPEC-saathi-safety-2026-11-09-r2.md sha256 9c8988b2c9415f62ed6bb32b4247238e313319339e800fd9c9ef52f06b25e470; SPEC-saathi-assurance-2026-11-09-r2.md sha256 e9489fdd228d9f8fcd058108a61053ca7cba808f3b07d0775671888788d2f1c8; SPEC-saathi-evidence-2026-11-09-r2.md sha256 8287da0716f654469c98397b8fb16bc2d8ab041fb1a94572e9dc245b95741386; SPEC-saathi-alternatives-2026-11-09.md sha256 5bcbf4c859075f2908976d07509ec0e289036b7c008832d7d9184789df9167e1; SPEC-saathi-platform-2026-11-10.md sha256 8b84438c0b5167a7ba63c887285a3b8cca200d8045b9a86b462e74a93722b250; SPEC-saathi-product-2026-11-10.md sha256 b0543f42556020349f6e86f688f66e73832113fed3a67c10a69115123dd080b1; POLICY-saathi-2026-11-09-r2.md sha256 c4b26bb7e3e39eba4e83c73891040b125dfebc5872091dd742178264d3072ff1; POLICY-saathi-data-2026-11-09.md sha256 d892eb25058f315132efc589bbc515d961c8d50baa4aece3d16a63bd28aa48c1; TRD-saathi-2026-11-09.md sha256 4fa28f253d0ea99931584fde4f9ed025890200dd2fae175fb15ac16864af6bd2; TRD-saathi-checks-2026-11-09.md sha256 7d330c42037220e5a2eaf8945f55d9cbaf27304f3f5bae38431bd13c56973b91; TRD-saathi-checks-product-2026-11-10.md sha256 c6d68a965a9bb612b722e0c1252534b0453aa85374692331adddfd0934e720ef; TRD-saathi-states-2026-11-09.md sha256 9968ebcd8b7add49595cc4aebfa3ebb3ddbd2127609017273e5611939b7c9076; TRD-saathi-threats-2026-11-09.md sha256 1abc227d24b8ecacc9eb03bd2b103c8f881136211521b7f848f0f8e6f5c7f122; TRD-saathi-ports-2026-11-09.md sha256 ff04d8c694fdea9b73761416dcaf147963c9693aa450993079cc8a7c459596ca; TRD-saathi-ops-2026-11-09.md sha256 ba5fcd3d6265616d215104fec7d51709cca029a45c90622602f769285fc39743; TRD-saathi-build-2026-11-09.md sha256 c98c0a6044a1694458f961420d8fd5615961dbd57677033f7c737a5f195529f6; RECORD-saathi-decisions-2026-11-09.md sha256 41bbe3270e83e1a23535f8903d7e5b60edeea9dae846b0b6feb4c3b152ff82d9; RECORD-saathi-decisions-strategy-2026-11-10.md sha256 2d6d32e8f7f743b397f9daa4b429c009b5275b47232cb441fc4d2e6a8754d931
parts: LLD-saathi-intake-2026-11-10.md, LLD-saathi-safety-2026-11-10.md, LLD-saathi-data-2026-11-10.md, LLD-saathi-tests-2026-11-10.md, LLD-saathi-build-2026-11-10.md
sources: the contracts in upstream; src/, tests/, scripts/, infra/ and .github/ on branch phase-0/foundation at 3d46456; DOC-PHILOSOPHY.md; the owner's LLD exemplar for its shape (built, proposed, mapping)
checked-at: 3d46456, 2026-11-10. Code read with `git show origin/phase-0/foundation:<path>`; nothing was run in this pass. Checks run on this set are in section 6.
---

Read section 2 for what happens to each existing file, section 3 to find the part that specifies a module, section 4 for where this LLD departs from or adds to the TRD, and the build part for the work units and their order.

# Saathi LLD: Phase 0 design and the Phase 1 outline

## 1. Scope, marks and parts

The TRD fixes components, ports, states, tables and the Phase 0 build order by id. This LLD fixes files, signatures, DDL, flows, tests and work units so one agent can build each unit without guessing. It states no new requirement. Where it adds to or departs from the TRD it says so in section 4.

Marks follow PRD section 2. A code fact is E at 3d46456 with file and line; every design element is P. Names of tests and files are proposals until the unit that builds them ends on its command.

| Part | Holds |
|---|---|
| LLD-saathi-2026-11-10.md | this file: layout, disposition of existing files, module map, TRD conflicts, owner questions |
| LLD-saathi-intake-2026-11-10.md | modules M01 to M13: types, ports, store, keys, config, edge, channels, radar, redaction, worker, ASR |
| LLD-saathi-safety-2026-11-10.md | modules M14 to M24: alerts, caregiver bot, voice, audit, traces, gateway, health, states, gate and lints, evals, admin |
| LLD-saathi-data-2026-11-10.md | migrations 002 to 005 as SQL, roles and grants, row-level security, household keys and their rotation |
| LLD-saathi-tests-2026-11-10.md | test files and names per module, crash, isolation and canary tests, the Phase 0 gate rows |
| LLD-saathi-build-2026-11-10.md | four flows with transaction boundaries, CI jobs, work units WU-01 to WU-23, the Phase 1 outline |

## 2. Package layout for Phase 0

Two processes share one image: the edge (`saathi-edge`, a web app that only inserts) and the worker (`saathi-worker`, one asyncio process that runs the request worker, the escalation service, the caregiver bot polling and the heartbeat). Phase 0 compose runs them as services `edge` and `worker` (C-3). The model worker, adapters and egress arrive in Phase 1.

```
src/saathi/
  __init__.py  app.py  admin.py  config.py  hosts.py  domain.py  errors.py  ids.py  ports.py  clock.py
  faults.py  media.py  health.py  householdcfg.py  replies.py
  store/      __init__.py db.py scan.py keys.py migrate.py repos/{inbox,requests,alerts,updates,flags,ledger,
              voice,config,consents,signoffs}.py
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
| `src/saathi/__init__.py` | K | none | none |
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
| `infra/sql/00-create-databases.sh` | C | roles and logins; owner is `saathi_migrator` | 19 |
| `infra/docker-compose.yml` | C | services `edge` and `worker`; Langfuse behind a profile | 28 |
| `infra/Caddyfile`, `infra/Dockerfile` | C | health route and webhook routes; two commands | 27 |
| ASR image Dockerfile under `infra/` | C | no ffmpeg, no `--convert` | 22 |
| `evals/README.md` | K | the harness is added beside it | RQ-58 |
| `pyproject.toml`, `uv.lock`, `.env.example` | C | entry points, `cryptography`, variables in step with `config.py` | 20 |

Not Phase 0 (TRD changes 13 to 18, 21, 24, 26): cache keys, tokens, caps, ride TTL, durable pause, forward intake, the service split, issue labels, the opus decode path.

## 3. Module map (module to requirement)

Ids are the Phase 0 requirement ids of PRD section 8. Every Phase 0 id appears in at least one row here and in at least one test row of the tests part; `scripts/design_check.py` checks both (section 6).

| Id | Module | Serves | TQ | Part |
|---|---|---|---|---|
| M01 | `domain.py`, `errors.py`, `ids.py` | RQ-105, 109; PL-01 | TQ-67 | intake |
| M02 | `config.py`, `hosts.py` | RQ-43, 110; PL-04, 12, 13, 14 | TQ-46, 47, 72 | intake |
| M03 | `ports.py`, `clock.py` | RQ-105, 18 | TQ-67 | intake |
| M04 | `store/` db, scan, repos, migrate | RQ-79, 109, 38, 56a, 35 | TQ-71 | intake |
| M05 | `store/keys.py` | RQ-111a, 56a | TQ-73 | intake |
| M06 | `householdcfg.py`, config and consent repos | RQ-110; PL-05, 33, 34 | TQ-72, 81 | intake |
| M07 | `edge/`, `app.py` | RQ-79, 81, 16, 18, 104 | TQ-01, 02, 08 | intake |
| M08 | `channels/whatsapp.py` | RQ-105, 81 | TQ-02, 67 | intake |
| M09 | `channels/telegram_elder.py` | RQ-105 | TQ-67 | intake |
| M10 | `guards/radar.py` | RQ-16, 17, 18 | TQ-08 | intake |
| M11 | `guards/pii.py` | RQ-41, 44; PL-03 | TQ-14 | intake |
| M12 | `worker/` | RQ-79, 36, 97 | TQ-03, 06 | intake |
| M13 | `perception/asr.py`, `voice/codec.py`, `media.py` | RQ-16 | TQ-12 | intake |
| M14 | `alerts/` | RQ-18, 95a; PL-26 | TQ-09, 10, 11 | safety |
| M15 | `channels/telegram.py` | RQ-97, 18 | TQ-15, 53 | safety |
| M16 | `voice/`, `replies.py` | RQ-17, 116 | TQ-13, 78 | safety |
| M17 | `audit/` | RQ-56a, 95a; PL-07 | TQ-39, 41 | safety |
| M18 | `trace/` | RQ-44, 57, 97; PL-07 | TQ-16 | safety |
| M19 | `llm/` | RQ-40, 45, 46, 47, 48, 49, 50, 96; PL-02 | TQ-17 to 20 | safety |
| M20 | `health.py` | RQ-104 | TQ-66 | safety |
| M21 | `states/` | RQ-36, 35, 39 | TQ-04 | safety |
| M22 | `scripts/` gate, rule index, doc lint, CI lint, licence audit, leak scan, commit check, repo settings, pilot counts, fault recorder | RQ-62, 63, 71, 72a, 100, 101, 117, 118, 119; PL-08, 09, 10, 11, 12, 15, 16, 17, 18, 19, 21, 22, 31 | TQ-57 to 61, 63, 79 | safety |
| M23 | `evals/` | RQ-58, 59, 60, 61, 99 | TQ-56 | safety |
| M24 | `admin.py` | RQ-95a, 110, 111a; PL-32, 33 | TQ-41, 80 | safety |
| M25 | `policy/` (kept) | RQ-63; PL-36 | TQ-28, 82 | safety |
| M26 | `scripts/design_check.py` | RQ-103; PL-24 | TQ-59 | safety |

M25 and M26 have no section of their own: M25 is unchanged code whose tests gain `@rule` markers and the no-money tests (LT-32, LT-33); M26 is the check of section 6, whose new function `check_lld(root)` reads the PRD phase-0 row, the rows above and the `LT-` rows of the tests part.

## 4. Where this LLD departs from or adds to the TRD

None of these changes a TRD file. Each is a proposal the owner can reverse by editing the named part.

| Id | Finding | LLD handling |
|---|---|---|
| C-1 | The edge may only insert (build step 2), yet the rate limit of RQ-81c counts earlier rows and reads the config | security-definer function `control.rate_state(now, elder)` returns the `not_before` time, no content |
| C-2 | RQ-105's check runs a contract suite on the Telegram implementation in Phase 0, while the platform spec and PRD phase 1 place the Telegram bot in Phase 1 and build step 3 lists WhatsApp and a fake | Phase 0 builds the `TelegramElderChannel` class and its rows of the suite against a fake client (WU-06); the live bot, start codes and switch stay Phase 1 |
| C-3 | Ops part section 3: Phase 0 runs one `saathi` service | two services of one image, `edge` and `worker`: the kill tests stop each alone and the edge pool holds the public key only; the zone split stays Phase 1 |
| C-4 | RQ-81 stores an HMAC of the sender, but the household key cannot be used before the household is known | `SENDER_HMAC_KEY`, a deployment secret in the PL-14 class, used for sender lookup only |
| C-5 | `001_init.sql` holds personal values in plain columns (`elders.wa_phone_e164`, `display_name`, `addresses`, `profile_facts.value`, E: lines 15, 31-39, 41-47); RQ-111a covers `raw_text` and hashes; `elder_identities` is listed in 012 though the edge needs it for RQ-81 | 002 creates `elder_identities` with a sealed address and drops the plain phone column; the other columns stay under row-level security and are an owner question (Q22) |
| C-6 | A sender not yet known has no household, so `rejected_inbound` cannot satisfy RQ-38 | the table is tenantless, insert-only for the edge, read through `rejected_digest()`; the schema test lists it as the one exception with `voice_clips` |
| C-7 | `saathi_owner` is reachable from no container (ports part section 5) but key commands need the key-encryption key | `saathi-admin` runs on the host shell where `KEK_FILE` is readable, never in a container |
| C-8 | No migration holds a heartbeat row, yet RQ-104 needs one and the edge may not write tables other than its four | `control.heartbeats` (tenantless) and `control.beat()`, added to 005 and called by edge and worker |
| C-9 | RQ-16's radar set must be written by someone other than the keyword author; no agent can supply that author | WU-20 commits a labelled fixture set and the harness; the real set is an owner action and the RQ-16 row reports `unavailable` until it exists |
| C-10 | RQ-110 says agent roles only read `household_config`; the Phase 1 `/channel` switch writes a version for an approver | the core gets an insert guarded by `created_by LIKE 'approver:%'` in Phase 1; no Phase 0 role can write |
| C-11 | The ports part makes `provider_message_id` unique and `tg_updates.update_id` the key | unique per `(household_id, provider_message_id)` and key `(bot, update_id)`: no cross-household existence signal, and ids repeat across two bots |
| C-12 | Build change 26 and TQ-12 place the opus decode path and RQ-82 in Phase 1 | Phase 0 builds the `ASR` and `AudioCodec` ports with fakes only; the `unscanned` alert and audio forward cover audio (A11) |

New tables and functions beyond the ports part section 4 list: `elder_identities` (early), `control.heartbeats`, functions `rate_state`, `beat`, `resolve_approver`, `unscanned_audio`, `reap_inbox`, `unacked_inbox`, `rejected_digest`, and the `schema_migrations` table of the migrator.

## 5. Owner questions

Each has a recommendation; none blocks WU-01 to WU-07.

| Id | Question | Recommendation |
|---|---|---|
| Q22 | Seal `addresses`, `profile_facts.value` and the display names (C-5) before the first real household row, or leave them readable under row-level security | seal them in a Phase 1 migration 014 item and keep Phase 0 synthetic-only; the consent gate already blocks real data |
| Q23 | Accept two compose services in Phase 0 (C-3) | accept |
| Q24 | Accept the Telegram elder class in Phase 0 with the live bot in Phase 1 (C-2), or move RQ-105 to Phase 1 | accept, so RQ-105 keeps its phase |
| Q25 | Accept the index form that lists paths under `docs/design/` by file name only (index limit 2 KB, section 6) | accept; the other way is to move the LLD parts under one listed hub |
| Q26 | Who writes the radar set of RQ-16, 100 positives per class (C-9) | the caregiver, in Hinglish, before WU-20 ends |

Decision recorded with this LLD: D-90, in RECORD-saathi-decisions-strategy-2026-11-10.md: `saathi` is a working codename; the public name follows the first stable test, after a private shortlist and a trademark registry search; the elder-facing persona name is private config and never the codename.

## 6. Checks run on this design set

`python3 scripts/design_check.py` gains check 8: every Phase 0 id of the PRD phase table appears in the Serves cell of a module row (`| M` rows of the hub) and of a test row (`| LT-` rows of the tests part), and every id in those cells exists in a contract. Output and the deliberate faults are in the commit that adds this LLD and are repeated in the report of the session. The index lists paths under `docs/design/` by file name only, as its first line says. Pins: every part pins this hub and the contracts it relies on; this file pins all 20 upstream contracts.

Skipped or not checked: no code, SQL or test ran; the TRD was read in full but the threats part only by search; hosted terms and licences were not read; the module signatures were not type-checked; the work unit sizes were not estimated; the reader check of this LLD did not run.
