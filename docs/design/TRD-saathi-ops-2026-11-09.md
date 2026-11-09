---
id: TRD-saathi-ops
kind: contract
governs: the technology choices that were open and the deployment and operations design; part of TRD-saathi-2026-11-09.md
status: draft, new, pending review and owner lock (see docs/LOCKS.md)
upstream: PRD-saathi-2026-11-09-r2.md sha256 341ecc305674155b388184dd59323faeda447676c01d9d002c188b9aeadfdd8b; SPEC-saathi-requests-2026-11-09.md sha256 b798a89a5d876a26d7e30e3d8b3ad380ffa276cb8b133e5b87baf17c3c32ea72; SPEC-saathi-safety-2026-11-09-r2.md sha256 6b9a198e72f618e26af03ff63e1555abf9c056e7fcf8a3e4cdff7fd078b9c202; SPEC-saathi-assurance-2026-11-09-r2.md sha256 be50717893ed345c507fcc856d7f8694931b520e3bf4110200b922519007137e; SPEC-saathi-evidence-2026-11-09-r2.md sha256 482a276cd9216e76d527619579f554abad7f89d4c8ea13c6159d8a56b338eabf; SPEC-saathi-alternatives-2026-11-09.md sha256 79711ac92ad7aee8b368b0c282f93220b123510a76c3922f0e8b220748e89862; POLICY-saathi-2026-11-09-r2.md sha256 aee5383d7d124ddad9f2fbb8bf9dcc50ac066eb05d128d641f126a97f5495dd9; POLICY-saathi-data-2026-11-09.md sha256 993dc52a89eb0fda3333df48f110c5654a3b7f184923d71bcd39c073bc782b7f; RECORD-saathi-decisions-2026-11-09.md sha256 a1ec6109610478a4582467cebe12c11d4bd266b89b35a89fdbe44dbde9f86f45
sources: the contracts in upstream; infra/docker-compose.yml, infra/Caddyfile, infra/Dockerfile, the ASR image Dockerfile under infra/, .env.example, pyproject.toml, scripts/license_audit.py on branch phase-0/foundation at 3d46456; package metadata from the Python package index JSON API on 2026-11-09; two aarch64 wheel file lists read on 2026-11-09
checked-at: 3d46456, 2026-11-09. Compose and Docker files read with `git show`; no container was started.
---

Read section 2 for the six choices and the rows of the alternatives spec that back each, section 3 for the services per phase, and section 4 for the probes.

# Saathi TRD ops: technology choices, deployment and operations

## 1. Scope and marks

Each choice below is marked use (adopt a package as is), adapt (adopt it behind our own code) or build (write it, because no alternative meets the requirement or the baseline suffices). Licence marks follow the alternatives spec: E means package metadata read on 2026-11-09, N means not read with the command that settles it. A choice stays P until its comparison in the alternatives spec section 3 runs (RQ-70). The rows cited are in SPEC-saathi-alternatives-2026-11-09.md.

## 2. Technology choices

| No | Choice | Decision | Mark | Licence check | Why and revisit |
|---|---|---|---|---|---|
| 1 | Inbox queue | One Postgres table drained with `FOR UPDATE SKIP LOCKED`, leases and an attempt count; one lease helper of about 100 lines serves inbox, timers, alert attempts and outbound sends | build | no new dependency; asyncpg 0.32.0 Apache-2.0 (E) | the insert that dedupes and persists before the 200 shares a transaction with nothing else (RQ-79). procrastinate, pgqueuer and pgmq stay adapt candidates; arq needs Redis-compatible storage, and dramatiq is LGPL. Revisit when the inbox-crash suite runs on the baseline and a candidate matches it with less code |
| 2 | Token and TOTP library | Approval token: HMAC-SHA256 on the standard library, extended from `quote.py:69-94` with request id, expiry and database spend state. TOTP: pyotp for RFC 6238 codes and the `otpauth` URI. Secret encryption: cryptography AEAD. Replay window, lockout and enrolment: our code (PL-27) | build, adapt, use | pyotp 2.10.0 MIT (E); cryptography 50.0.2 Apache-2.0 OR BSD-3-Clause (E); PyJWT and pymacaroons retired in the alternatives spec | a signed token alone has no spent state, so the database row is the authority. Revisit if PE-29 tests show pyotp cannot meet the replay rule |
| 3 | LangGraph checkpointer | None. `requests.state`, `commit_intents`, `inbox` and `timers` are the only durable state; each run is idempotent and re-runs from its inbox row; approval is an event on a row, not a graph interrupt. LangGraph stays as a library for in-run step graphs | build (state in rows) | langgraph 1.2.20 MIT (E); langgraph-checkpoint-postgres 3.2.0 requires psycopg 3.3.8, LGPL-3.0-only (E), so it is banned; langgraph-checkpoint-sqlite 3.1.3 is a second store | a checkpointer would be a second source of truth beside the rows. Fallback: a custom asyncpg saver, if the restart test shows a run whose redo costs more than the model cache absorbs. Owner question 1 |
| 4 | Scheduler for reminders and timers | A `timers` table on the same lease helper. Due times are computed from persisted event times (request first message, tap, alert t0), the worker polls every few seconds (config) and fires through the state machine; reminder recurrence is stored as interval and time of day in the household zone (`zoneinfo`) | build | no new dependency; apscheduler 3.11.3 MIT (E) retired because job state would sit outside the rows | timers survive a restart and run under a fake `Clock` (requests RQ-20, PRD RQ-14). procrastinate and pgqueuer schedules are adapt candidates |
| 5 | Final-screen XML verify | Standard-library `xml.etree.ElementTree` over the one uiautomator dump, after a 1 MiB cap and a DOCTYPE and ENTITY rejection; the steps are below. Device I/O by uiautomator2 | build; adapt for the device | uiautomator2 3.7.1 MIT (E); lxml 6.1.3 BSD-3-Clause (E) is the candidate if a selector needs full XPath; defusedxml 0.7.1 is PSFL (E) and PSF is not on the PL-09 list (owner question 3) | the threat is a hostile text value, and the parser sees only the dump of a dedicated phone. Revisit when the RQ-87 and RQ-88 fixtures exist |
| 6 | Local speech runtime | onnxruntime for an ONNX export of the model named in private config; a thin front end of our own that turns numbers and rupee amounts into Hindi words; clips encoded to the Ogg voice-note format that WhatsApp accepts, by a BSD-licensed encoder in the speech container, not ffmpeg | adapt, build | onnxruntime 1.31.1 MIT (E); its aarch64 wheel holds no bundled system library (E, file list); ThirdPartyNotices not read (N). Retired: sherpa-onnx 2.0.1, whose aarch64 wheel bundles `libasound`, an ALSA library that is LGPL (R: recalled, E: file present), and phonemizer 3.6.0, GPL-3.0-or-later (E). Encoder licence: N, check the image's package copyright files with the RQ-71 image audit | the model must pass its weights licence and terms receipt (RQ-50, PL-09) before it is used. transformers with torch is the fallback only for a model with no ONNX export. Revisit when the D-69 cases run |
| 7 | Egress proxy | mitmproxy in regular proxy mode with an internal certificate authority trusted only by the model worker and adapter containers; an addon checks the host against that container's list and adds the credential header from the register. Docker `internal: true` networks and a host firewall rule are the second layer | adapt | mitmproxy 12.3.1 MIT (E); licences of its dependencies N, run `license_audit.py` in its image. nginx with an SNI allowlist is the enforcement-only candidate (N) | credential injection needs TLS termination, and the termination stays inside our network. Revisit when the reach test of RQ-102 exists |

Final-screen verify, step by step (choice 5):

1. C13 calls `dump_ui_xml()` after step 1. The result holds the XML bytes, a monotonic `taken_at`, the sha256 and the foreground package.
2. Code rejects the dump when it is over the cap, holds a DOCTYPE or ENTITY, names a foreground package other than the macro's app, or has a node of another package whose bounds cover the commit button (an overlay).
3. For each field of the class list in RQ-87, code evaluates the macro's selector, an attribute predicate on resource id, class, text or content description. It must match exactly one node; none or several aborts.
4. Code normalizes the text (NFKC, trimmed, money parsed to `Decimal`). For cart lines it evaluates the row selector, then one selector per cell, and the row count must equal the approved lines.
5. Code compares with the approved quote: items and quantities equal; total or fare within the RQ-29 ceiling; address label equal to the allowlisted label; pickup, drop and ride type equal; payment in the allowed set.
6. The tap target comes from the same dump: the macro's commit selector. `tap_selector` runs within 2 s of `taken_at` and carries the commit grant, which binds the dump hash.
7. Any failed step ends the attempt `aborted_no_call`.

## 3. Deployment: compose services per phase

E at 3d46456: one `saathi` service holds the webhook, the bot and the pipeline (`docker-compose.yml:198-212`); every service sits on the default network; the stack has 12 services. The target splits the process by trust zone in Phase 0 so that Phase 1 only adds network rules.

| Service | Phase | Zone | Network | Note |
|---|---|---|---|---|
| caddy | 0 | Z1 | frontdoor, public 80 and 443 | one public route, the WhatsApp webhook (E: `Caddyfile:4-10`) |
| edge | 0 | Z1 | frontdoor, internal | role `saathi_edge`; inserts inbox rows only |
| core | 0 | Z2 | internal, egress | worker, state machine, escalation, bot, policy, audit writer, scheduler from Phase 1; holds platform tokens |
| model-worker | 0 | Z3 | internal only | perception, router, planners, gateway; no key, no internet |
| model server, ASR, speech | 0 | Z3 | internal only | local runtimes; the model server sets its no-cloud flag (E: `:166`); the ASR image loses ffmpeg (build part, change 22) |
| postgres | 0 | Z2 | internal | loopback publish stays for operator tunnels (E: `:69`) |
| langfuse stack (web, worker, clickhouse, valkey, seaweedfs, init) | 0 | Z2 | internal | web on loopback (E: `:146`); D-40 |
| egress | 1 | boundary | internal and outbound | the only container with a route out for Z3 and Z4 |
| adapters | 1 | Z4 | internal only | MCP and search adapters; exit through egress |
| backup job | 1 | Z2 | internal, egress | dump, encrypt, upload, head export |
| phone adapter and mesh client | 2 | Z4, Z5 | mesh interface | the mesh admits the VPS node only (RQ-90) |
| debunk fetcher | 3 | Z3 | internal | exit through egress with the publisher allowlist |

Egress enforcement (RQ-102): the model worker, adapters and phone adapter join only `internal`, a network created with `internal: true`. They reach the world only at `http://egress:8080`. A host firewall rule in the `DOCKER-USER` chain drops other outbound packets from that subnet. Core reaches WhatsApp, Telegram and the telephony provider through the same proxy with its own list, and holds those tokens itself. A compose test fails when a model-driven service joins the default bridge or `network_mode: host`; the reach test of RQ-102 runs after every deploy.

Secrets (PL-13, PL-14, RQ-93): `.env` on the VPS with mode 600 (E: `.env.example` header). The approval secret, audit HMAC key and TOTP key are separate files mounted into core only. Each service gets its own variables; today the app takes the whole file (E: `docker-compose.yml:201`). Model-worker and adapter containers hold no `*_KEY`, `*_TOKEN`, `*_SECRET` or `*_PASSWORD`; egress reads provider and MCP keys from a read-only file. The credential register in Postgres holds owner, scope, issue and expiry, never a value.

Backups (RQ-37, 56b): a nightly `pg_dump` of the `saathi` database, encrypted in chunks with the cryptography package under a key kept apart from the bucket credential, uploaded to off-site storage, kept 30 days. A monthly restore into an empty database replays the erasure log. The audit chain head goes off-site nightly.

Pi bridge link (RQ-90, POLICY-data section 2): a Tailscale private mesh from the VPS node to the bridge board; the access rule admits the VPS node only; SSH on the board takes keys from the VPS only. The mesh client and its coordination service were not read for terms or licence (N). The coordination service sees device names and addresses, so a PL-20 receipt covers it before Phase 2. The check is a connection attempt from a non-VPS node that must fail.

## 4. Health probes and operations

Alerts go to the ops chat (E: `config.py:36`). A probe that cannot run reports `unavailable`, never ok.

| Probe | Where and when | Signal | Serves |
|---|---|---|---|
| liveness and readiness | edge, every 30 s; `/healthz` stays off the public route (E: `app.py:130-132`) | process up; a test insert into a scratch table | RQ-79 |
| inbox lag | core, every 30 s | oldest `received` row older than 60 s | RQ-74, 79 |
| worker heartbeat | core, row updated every 10 s | stale for 30 s | RQ-35 |
| WhatsApp channel | daily | send failure codes, token expiry (alert 7 days ahead), one probe call; webhook silence never counts | RQ-78 |
| Telegram channel | daily and on send failure | `getMe` and send results | RQ-78 |
| telephony and SO-1 | daily | provider credential check; SO-1 expiry, alert 14 days ahead | RQ-18, 95a |
| phone heartbeat | bridge, every 20 s | 3 misses, 60 s apart, then handoff `phone_offline` | RQ-34 |
| model runtimes | worker, every 60 s | model server, ASR and speech health; down means G0 paths only | RQ-46, 82 |
| egress reach | daily canary | an unlisted host fails, an assigned host passes | RQ-102 |
| backups and head export | daily | newest dump or head older than 26 h; restore test older than 30 days | RQ-37, 56b |
| retention job | daily | last run and rows removed | RQ-42 |
| emergency drill | quarterly | rings the approvers' phones with a test event | RQ-18, 91 |

Skipped or not checked: no container was started; no service terms or licence text was read; the clip encoder and the mesh coordination service licences are N; probe intervals are proposals; the reach test and the restore test do not exist.
