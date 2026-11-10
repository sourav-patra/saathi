---
id: LLD-saathi-build
kind: contract
governs: Phase 0 flows with transaction boundaries, the CI workflow, work units WU-01 to WU-26 with their order, and the Phase 1 outline; part of LLD-saathi-2026-11-10.md
status: draft, pending review
upstream: TRD-saathi-build-2026-11-09.md sha256 6bed289223a9cd46d5e9af197f79dae293a6199e807403d3d668c8017452fe94; TRD-saathi-states-2026-11-09.md sha256 6a6843a411f59ce326c9573fe7ed14e6bb76ed19e30c1b9a5e3514b4bda25db6; TRD-saathi-ops-2026-11-09.md sha256 4ee243846c99ce3fe67dc9ebe4dc783e2a4ab6363ba425c069f374ee75dfd1f1; LLD-saathi-2026-11-10.md sha256 bda71cce7ddb7c9fe5b18223d08d360f881cfc660c1d3befaf70294488692309
sources: .github/workflows/ci.yml, pyproject.toml and src/ on branch phase-0/foundation at 3d46456; the TRD build, states and ops parts; DOC-PHILOSOPHY.md
checked-at: 3d46456, 2026-11-10. Nothing below was run in this pass.
---

Read section 1 for the four flows, section 3 to pick the next work unit, and section 4 for what Phase 1 adds. Module ids M01 to M26 are in the hub file.

# Saathi LLD build: flows, CI, work units and the Phase 1 outline

## 1. Flows with transaction boundaries

TX names a database transaction; a step outside a TX is an external call. Every outbound send carries `idem_key(...)` and the fake refuses a repeat key (a real platform may deliver twice after a crash: at least once, never silently dropped).

### 1.1 Voice note to caregiver summary and elder acknowledgement (RQ-79, 81, 97)

1. Edge: read the raw body; over the limit returns 413; verify the signature (401, counter); parse JSON (400); `channel.parse_webhook` gives messages.
2. Edge, per message: `scan.resolve_sender(hmac)`. No match: `scan.reject_inbound` (one tenantless insert, no household), no reply, 200.
3. TX-E opens `tx(household)`: `control.rate_state(now, elder)` returns `not_before` or none; radar on text or caption; `InboxRepo.insert_if_new` writes the inbox row and sealed `raw_text` parts; on a keyword hit `AlertRepo.open` (origin `radar_edge`); an audio row with `not_before` opens an `unscanned_edge` alert. Fault point `edge.before_commit`; commit; fault point `edge.after_commit`.
4. Edge returns 200 only after the commit. A failed insert returns 5xx. A repeat provider id inserts nothing and returns 200.
5. Worker `ack_pass`: `scan.unacked_inbox`; per row, send the stored acknowledgement clip and text (key `inbox:ack`), then TX `mark_acked`.
6. Worker: `scan.lease_next(now, 60)` (one atomic function call) returns a `Lease`. The `LeaseRenewer` renews it every 20 s with the token.
7. TX-W1: load the inbox row, decrypt parts, `ConfigRepo.load`, `consent.gate`; `RequestRepo.create_first` (`r_<inbox>`, `new` to `received`, R01). A closed gate ends `blocked`, reason `consent_missing` (R02) and sends the summary path.
8. Media fetch through `ElderChannel.fetch_media` and `MediaStore.put`. A failure inside the URL lifetime sets `media_lost` (N09) and a flagged summary.
9. `AudioCodec.to_wav16k`, then `ASR.transcribe`: fakes in Phase 0 (C-12); the real decode path is Phase 1. An error forwards the audio to the caregiver with a templated reply (step 12) and no transcript.
10. TX-W2: `add_transcript` (sealed, token checked), radar `combine` on the transcript, `AlertRepo.open` on a hit (origin `radar_worker`). Commit.
11. Triage is a fixed outcome in Phase 0: TX-W3 `transition(received, handoff, summary_only)` (R05), compare-and-set.
12. Send, keyed by the inbox id: caregiver summary card (and the original audio when `consent.gate` allows), then the elder reply `told_caregiver`.
13. TX-W4: `settle(lease, processed)`. A stale token returns false and writes nothing. The row is never `processed` before steps 11 and 12 succeed.
Check: LT-08, LT-47 `test_voice_note_to_summary_and_ack`.

### 1.2 Emergency to stage a, fake stages b and c, acknowledgement (RQ-16, 18, 95a)

1. Text or caption: the hit and the `alert_events` row commit in TX-E of flow 1.1. Audio: TX-W2 inserts it. No request is created (L-1).
2. `AlertService.tick` polls `scan.due_alerts(now)`; `next_due_at` equals `t0` for a new event (A01).
3. TX-A1: lock the event, `plan(...)`, insert stage a attempts (primary and backup, n = 0) with unique keys, set `next_due_at`. Commit.
4. Send stage a through `CaregiverChannel` (alert card with Acknowledge and False alarm, original audio when allowed) and the stored emergency clip to the elder (`send_reply`, key `alert:<event>:clip`). No WhatsApp, model or ASR is needed.
5. TX-A2: mark attempts `sent`. The cadence continues at 60, 120, 180, 240 s, then every 5 min, 20 sends per approver at most (A02).
6. At t0 + 120 s: TX-A1 reads `control.signoffs` for SO-1. Valid: stage b attempts go to the fake escalator (3 at most, every 2 min). Missing or expired: nothing is sent, the owner is alerted once, audit `signoff_expired` (A05).
7. At t0 + 240 s: stage c attempts go to the fake escalator, backup 60 s later. The fake answers with a key press of 1: the callback enters `AlertService.acknowledge(via="dtmf")` after the escalator checks signature, call id and replay (`CallbackRejected` otherwise).
8. TX-A3: `ack` compare-and-set from `open`, cancel `due` attempts, `followup_due_at = now + 900`, audit `ack_voice` naming the approver. A tap on the card does the same through `CallbackRouter` after `UpdateRepo.first_seen` (TX-U) with audit `ack_tap`.
9. A call answered with no key press leaves the event open (A13). At t0 + 600 s the elder clip is sent again (A09). All caps spent: `blocked`, `no_human_reached`, audit row (A07).
Check: LT-19, LT-47 `test_emergency_to_stage_a_and_fake_b_c`.

### 1.3 Heartbeat (RQ-104)

1. Worker `beat_loop` every 10 s runs `disk_probe` and `skew_probe`, then one TX calls `control.beat('core', now, status)`. A probe that cannot run sets status `unavailable`, which is not ok.
2. The monitor requests the public health path every minute. The edge calls `control.beat('edge', now)`: a write, and it returns the newest core beat and status.
3. `decide` returns 200 with the body `ok` only when the write worked, the core beat is under 30 s old, its status is `ok`, and free disk is above 10%. Otherwise 503 with no body. After 3 failed requests the monitor pages both approvers (outside the code, a manual drill receipt).
Check: LT-30, LT-47 `test_heartbeat_ok_then_503`.

### 1.4 Channel switch from WhatsApp to Telegram (RQ-107; built in Phase 1)

1. An approver sends `/channel telegram` to the caregiver bot; `CallbackRouter` replies with a confirmation card.
2. The confirmation tap: TX-U inserts the update id; TX-S checks an active `elder_identities` row for `telegram_elder`, else refuses with the reason.
3. TX-S inserts a new `household_config` version with `elder_channel_active = "telegram"` and a `config_changes` row (approver identity), and an audit row. No request, reminder or consent row changes.
4. Send the fixed switch note on both channels; resend an open clarifying question once on Telegram (Phase 1).
5. Inbound on either channel is processed; a reply goes to the channel of the message; proactive sends read the active channel at send time.
Phase 0 holds the field, the `ElderChannel` contract suite and `test_switch_is_phase_1_xfail`; C-10 in the hub notes that Phase 1 gives the core a guarded insert on `household_config`.

## 2. CI workflow (`.github/workflows/ci.yml`, change 23, D-73)

Triggers: push to `main` and every pull request. Every `uses:` ends in a 40 hex commit digest with the tag as a comment, resolved once with `gh api repos/<owner>/<repo>/commits/<tag> --jq .sha` and re-checked by `ci_lint.py`. Installs use `uv sync --frozen` (the lockfile carries hashes). The Postgres service image is pinned by digest. E at 3d46456: jobs `test` and `compose`, actions pinned by tag (`ci.yml:11-12`, `:23`).

| Job | Runs | Fails the build when |
|---|---|---|
| `lint` | `uv run ruff check src tests scripts evals`; `uv run python scripts/ci_lint.py .github/workflows` | a rule fires or a `uses:` has no 40 hex digest |
| `test` | service `postgres`; `apt-get install strace`; `uv run pytest -q` with `CI=true` and `SAATHI_TEST_DSN` | a test fails, or a prerequisite is missing (no silent skip) |
| `license-audit` | `uv run python scripts/license_audit.py --packages --dockerfiles --manifest tests/fixtures/image_manifest.txt` | an unknown or banned licence, ffmpeg, `opusinfo` or `--convert` |
| `design-check` | `python3 scripts/design_check.py` | any check fails (D-73, RQ-103) |
| `doc-lint` | `uv run python scripts/doc_lint.py 00-INDEX.md README.md docs` | a rule fires (RQ-101) |
| `evals` | `uv run python -m evals.run --all` | a dataset is missing, n is 0 or a suite is skipped with no reason (RQ-61) |
| `leak-scan` | `uv run python scripts/leak_scan.py` (no names file in CI: that check reports unavailable and the step still scans keys, media and attribution) | a key pattern, media file or attribution trailer (PL-15, PL-31) |
| `compose` | the existing validation (E) | the compose file is invalid |
| `gate-report` | `uv run python scripts/gate.py --phase 0 --report` | never; it prints every row and writes a step summary, and becomes required at the Phase 0 exit (WU-23) |

Required status checks for `main` (PL-22): `lint`, `test`, `license-audit`, `design-check`, `doc-lint`, `evals`, `compose`; `leak-scan` joins after its first green run. The `image-audit` job (build the ASR and speech images, write the package manifest, run the audit) runs on changes under `infra/` only.

## 3. Work units

One unit is one agent session: it changes the listed files, adds the listed tests, and ends on its command. A unit starts only after the units in its After column pass their commands. Each unit ends with the lint job clean (`uv run ruff check src tests scripts evals`). The Modules column lists the modules (hub section 3) a unit creates; a later unit may extend a module's files.

Home of the design set. At WU-01 the design set is merged into the code branch: `docs/design/`, `00-INDEX.md`, `DOC-PHILOSOPHY.md` with its sidecar, `scripts/design_check.py`, `docs/LOCKS.md` with `docs/archive/`, and `docs/evidence/reviews/`. `gate.py` reads the PRD phase table through `00-INDEX.md`, and the CI jobs `design-check` and `doc-lint` read the rest, so they must sit in the branch they run on. The copy is byte for byte (`sha256sum` against the index) and `python3 scripts/design_check.py` passes in the code branch before WU-01 ends.

| WU | Work | Files | Modules | Tests that must pass | Serves | Done when | After |
|---|---|---|---|---|---|---|---|
| WU-01 | gate, registry skeleton, receipts, rule index, `PHASE`, markers on the 32 policy tests; the design set merged in | scripts/gate.py, gate_registry.toml, rule_index.py, rules.toml, PHASE, tests/markers.py, tests/test_policy.py, the design set files above | M22, M25 | LT-33, 34, 35 | RQ-63, 100 | `uv run pytest tests/test_gate.py tests/test_rule_index.py tests/test_policy.py -q && python3 scripts/design_check.py` | none |
| WU-02 | doc lint, CI lint, design check sync, CI skeleton with pinned actions | scripts/doc_lint.py, doc_lint_words.txt, ci_lint.py, design_check.py, .github/workflows/ci.yml, tests/fixtures/doc_lint/ | M26 | LT-36, 37, 38 | RQ-101, 103, 72a; PL-19, 24 | `uv run pytest tests/test_doc_lint.py tests/test_ci_lint.py tests/test_design_check.py -q` | 01 |
| WU-03 | shared foundations: domain, errors, ids, clock, ports, state tables, every fake (including `FakeVoiceStore`) | saathi/domain.py, errors.py, ids.py, ports.py, clock.py, states/, tests/fakes/ | M01, M03, M21 | LT-17, LT-51 | RQ-105, 109, 35, 36, 39; PL-01 | `uv run pytest tests/test_foundation.py tests/test_states.py tests/test_policy.py -q` | 01 |
| WU-04 | init script with every role, migrator, Postgres fixture, CI service, migration 002 | infra/sql/00-create-databases.sh, infra/sql/schema/002_inbox.sql, saathi/store/migrate.py, tests/conftest.py, .github/workflows/ci.yml | M27 | LT-01 | RQ-38, 109 | `uv run pytest tests/test_schema.py -q` | 02 |
| WU-05 | migrations 003 to 005 with the appended policies, functions, grants and sequence grants | infra/sql/schema/003 to 005 | none | LT-03 | RQ-56a, 95a, 109 | `uv run pytest tests/test_schema.py tests/test_roles.py -q` | 04 |
| WU-06 | Store, Tx, ScanFns, repos | saathi/store/__init__.py, db.py, scan.py, repos/ (inbox, requests, alerts, updates, flags, identities) | M04 | LT-02, LT-49 | RQ-109, 38, 35, 36 | `uv run pytest tests/test_cross_tenant.py tests/test_request_repo.py -q` | 03, 05 |
| WU-07 | Settings, KeyRing, HouseholdConfig, consent and config repos | saathi/config.py, store/keys.py, householdcfg.py, repos/config.py, consents.py | M02, M05, M06 | LT-04, 05, 06 | RQ-110, 111a, 56a; PL-05, 13, 14, 33, 34 | `uv run pytest tests/test_keys.py tests/test_config.py tests/test_consent.py -q` | 06 |
| WU-08 | WhatsApp and Telegram elder classes, contract suite | saathi/channels/whatsapp.py, telegram_elder.py | M08, M09 | LT-10, 11 | RQ-105, 81 | `uv run pytest tests/test_channels_contract.py tests/test_whatsapp.py -q` | 03 |
| WU-09 | radar combine and classifier port, redaction additions | saathi/guards/radar.py, pii.py | M10, M11 | LT-12, 13 | RQ-16, 17, 18, 41; PL-03 | `uv run pytest tests/test_guards.py tests/test_redaction.py -q` | 03 |
| WU-10 | audit sink, sign-off repo | saathi/audit/, store/repos/signoffs.py | M17 | LT-21 | RQ-56a, 95a | `uv run pytest tests/test_audit.py tests/test_roles.py -q` | 07 |
| WU-11 | voice store, replies, fixed clip job | saathi/voice/store.py, tts.py, clips.py, replies.py, store/repos/voice.py | M16 | LT-22, 23 | RQ-17, 116; D-71 | `uv run pytest tests/test_voice_store.py tests/test_replies.py -q` | 10 |
| WU-12 | admin CLI: households, elders, approvers, identities, keys, sign-offs, consent, config, clips review | saathi/admin.py | M24 | LT-46 | RQ-95a, 110, 111a; PL-32 | `uv run pytest tests/test_admin.py -q` | 10, 11 |
| WU-13 | health probes and the decision function | saathi/health.py | M20 | LT-30 | RQ-104 | `uv run pytest tests/test_health.py -q` | 06 |
| WU-14 | edge handler, rate state, health route, app composition | saathi/edge/, app.py | M07 | LT-07, LT-50 | RQ-79, 81, 16, 18, 104, 105 | `uv run pytest tests/test_edge.py tests/test_elder_bot_authority.py -q` | 07, 08, 09, 13 |
| WU-15 | lease helper, worker, ack pass, summaries, media store, ASR and codec ports with fakes; `pipeline.py` and its tests removed | saathi/worker/, perception/asr.py, voice/codec.py, media.py | M12, M13 | LT-09, 14, 15, 16 | RQ-79, 36, 16 | `uv run pytest tests/test_lease.py tests/test_worker.py tests/test_empty_run.py tests/test_asr.py -q` | 07, 09, 11 |
| WU-16 | fault hooks, crash tests, end-to-end flow 1.1 | saathi/faults.py, tests/test_inbox_crash.py, tests/test_flows.py (flow 1.1) | M29 | LT-08, LT-47 flow 1.1 | RQ-79 | `uv run pytest tests/test_inbox_crash.py -q && uv run pytest tests/test_flows.py -k voice_note -q` | 14, 15 |
| WU-17 | alert plan, service, stage a, fakes | saathi/alerts/, tests/fakes/escalators.py | M14 | LT-18, 19, 20, 47 emergency flow | RQ-18, 95a | `uv run pytest tests/test_alert_plan.py tests/test_alerts.py tests/test_signoff.py -q && uv run pytest tests/test_flows.py -k emergency -q` | 10, 11, 16 |
| WU-18 | caregiver bot: cards, callbacks, show raw; drop in-memory pause | saathi/channels/telegram.py | M15 | LT-24 | RQ-97, 18 | `uv run pytest tests/test_cards.py -q` | 17 |
| WU-19 | trace exporter, canary test, remove the old tracing | saathi/trace/, observability.py removed | M18 | LT-25 | RQ-44, 57, 97 | `uv run pytest tests/test_trace_canary.py -q` | 16, 18 |
| WU-20 | gateway: sensitivity, cascade, routing, roster; `understand/router.py` adapted | saathi/llm/gateway.py, routing.py, roster.py, understand/router.py; tests/test_channels_llm_pipeline.py removed after its gateway tests move | M19 | LT-26, 28 | RQ-40, 45, 46, 47, 49, 50, 96; PL-02 | `uv run pytest tests/test_gateway.py tests/test_roster.py -q` | 03, 09 |
| WU-21 | Postgres ledger | saathi/llm/ledger.py | M28 | LT-27 | RQ-48 | `uv run pytest tests/test_ledger.py -q` | 06, 20 |
| WU-22 | hosts table, env secrets test, compose services `edge` and `worker`, Langfuse profile | saathi/hosts.py, infra/docker-compose.yml, tests/test_flows.py (heartbeat) | none | LT-29, 31, 47 heartbeat flow | RQ-43, 104; PL-04, 13, 14 | `uv run pytest tests/test_hosts.py tests/test_env_secrets.py -q && uv run pytest tests/test_flows.py -k heartbeat -q` | 14, 16 |
| WU-23 | eval harness, registry, radar and redaction suites with fixture sets | evals/ | M23 | LT-40 | RQ-58, 59, 60, 61, 99 | `uv run pytest tests/test_evals.py -q && uv run python -m evals.run --all` | 09 |
| WU-24 | licence audit modes, ASR image without ffmpeg, no-money tests | scripts/license_audit.py, the ASR image Dockerfile under infra/, tests/test_no_money.py | none | LT-32, 39 | RQ-71; PL-09, 10, 11, 36 | `uv run pytest tests/test_license_audit.py tests/test_no_money.py -q` | 02 |
| WU-25 | leak scan, commit-message check, repo settings reader | scripts/leak_scan.py, commit_msg_check.py, repo_settings.py, .githooks/commit-msg | none | LT-42, 43, 44 | PL-08, 12, 15, 16, 17, 18, 21, 22, 31 | `uv run pytest tests/test_leak_scan.py tests/test_commit_msg.py tests/test_repo_settings.py -q` | 02 |
| WU-26 | pilot counts, fault recorder and receipts, CI final, the gate | scripts/pilot_counts.py, record_fault.py, gate_registry.toml, ci.yml | none | LT-41, 45 | RQ-62, 117 and all Phase 0 ids | `uv run python scripts/gate.py --phase 0` exits 0 | 01 to 25 |

Order. The After column holds every arrow, and the order WU-01 to WU-26 satisfies all of them. `scripts/design_check.py` check 9 proves it: each unit's After numbers are lower than its own, and every module a unit creates imports only modules created by that unit or by a unit it depends on, directly or through other arrows (the Imports column of the module map in the hub). Units with no arrow between them may run at once: 08, 09 and 13 after their predecessors; 23 after 09; 24 and 25 after 02.

Owner actions the gate waits on (not units): the radar set of about 100 phrases written by the caregiver (D-95, C-9), the clip review receipts (D-71), SO-1 sign-off, first consent record, platform terms receipts, the heartbeat drill, the pilot receipts.

## 4. Phase 1 outline

The Phase 0 design does not block these; each hook it relies on is named.

| Added | Modules and tables | Interface (P) | Phase 0 hook |
|---|---|---|---|
| Router and planners | `router/`, `planners/`, `quote/` | `route(text: DataBlock) -> RouteResult`; `build_quote(facts: QuoteFacts, profile: Profile) -> Quote` | `Gateway`, `ROUTING`, `requests.slots` and `plan_step`, `Machine(phase=1)` |
| Commerce adapters | `commerce/` (MCP, deep link, manifest loader, fence); `mcp_pins` (014) | `CommerceAdapter` of the TRD ports part | `AdapterManifest` rows, `HouseholdConfig.experimental` |
| Approval service | `approval/` (tokens, TOTP, cards, backup); 010, 011 | `ApprovalService.on_callback(up: ApproverUpdate) -> None`; `TokenRepo.spend(tx, ...) -> bool` | `CallbackRouter`, `tg_updates`, `KeyRing` (`totp` key), `control.signoffs` |
| Commit executor | `commit/` (3 steps, reaper, reconcile); `commit_intents` | `CommitExecutor.run(token: TokenId) -> CommitOutcome`; the only caller of `place_order` | `Store.tx(lock_household=True)`, `PolicyEngine.authorize_commit`, `audit_append` |
| Trace stack | `trace/langfuse.py`; `run_records` (015) | `LangfuseExporter(TraceExporter)`, same allowlist | `TraceExporter` port, `ALLOWED_FIELDS` |
| Live stages b and c | `alerts/stage_b.py`, `stage_c.py`; telephony callback route in `edge/` | `Escalator.notify`, `on_dtmf` (already in the port) | `AlertService`, `UnavailableEscalator`, SO-1 row |
| Elder bot and switch | live `telegram_elder`, start codes (012), `channel_state` | `/channel <name>` flow of 1.4 | `ElderChannel`, `elder_identities`, `elder_channel_active` |
| Voice decode and encode | `voice/codec.py` `OpusCodec` over `opusdec` and `opusenc`; image audit | `AudioCodec` port (change 26) | `FakeCodec`, `ASR` port |
| Audit chain, backup, erasure | `audit/chain.py`, `scripts/audit_verify.py`, backup job, retention job; 013 | `audit_verify(household) -> ChainResult` | `audit_log` chain columns, `key_id`, `control.household_keys` |
| Service split and egress | per zone services, Caddy egress | env files per service | `saathi_edge`, `saathi_core`, `saathi_model` roles, `Role` in `Store` |

Skipped or not checked: unit sizes were not estimated or timed; the digest of each action and of the Postgres image was not resolved; `strace` availability on the runner image was not checked; the Phase 1 interfaces are outlines and were not compared with the alternatives records.
