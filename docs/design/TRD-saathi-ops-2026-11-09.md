---
id: TRD-saathi-ops
kind: contract
governs: the technology choices that were open and the deployment and operations design; part of TRD-saathi-2026-11-09.md
status: draft, review F fixes applied, pending owner lock (see docs/LOCKS.md)
upstream: PRD-saathi-2026-11-09-r2.md sha256 ee2a21d4115ce10d07f5fcae7567b00866e8411d2b40380f12c03450b4e16c8d; SPEC-saathi-requests-2026-11-09.md sha256 7e0cee8a2dab273d3776eafd6d68d6a7a0f51e1d75fa7b55064136b0c4ed0e1e; SPEC-saathi-safety-2026-11-09-r2.md sha256 9c8988b2c9415f62ed6bb32b4247238e313319339e800fd9c9ef52f06b25e470; SPEC-saathi-assurance-2026-11-09-r2.md sha256 e9489fdd228d9f8fcd058108a61053ca7cba808f3b07d0775671888788d2f1c8; SPEC-saathi-evidence-2026-11-09-r2.md sha256 8287da0716f654469c98397b8fb16bc2d8ab041fb1a94572e9dc245b95741386; SPEC-saathi-alternatives-2026-11-09.md sha256 5bcbf4c859075f2908976d07509ec0e289036b7c008832d7d9184789df9167e1; POLICY-saathi-2026-11-09-r2.md sha256 c4b26bb7e3e39eba4e83c73891040b125dfebc5872091dd742178264d3072ff1; POLICY-saathi-data-2026-11-09.md sha256 d892eb25058f315132efc589bbc515d961c8d50baa4aece3d16a63bd28aa48c1; RECORD-saathi-decisions-2026-11-09.md sha256 41bbe3270e83e1a23535f8903d7e5b60edeea9dae846b0b6feb4c3b152ff82d9; SPEC-saathi-platform-2026-11-10.md sha256 8b84438c0b5167a7ba63c887285a3b8cca200d8045b9a86b462e74a93722b250
sources: the contracts in upstream; infra/docker-compose.yml, infra/Caddyfile, infra/Dockerfile, the ASR image Dockerfile under infra/, .env.example, pyproject.toml, scripts/license_audit.py on branch phase-0/foundation at 3d46456; package metadata from the Python package index JSON API on 2026-11-09; two aarch64 wheel file lists read on 2026-11-09
checked-at: 3d46456, 2026-11-09. Compose and Docker files read with `git show`; no container was started. Revised after review F; mitmproxy's requirements read from the package index on 2026-11-09.
---

Read section 2 for the nine choices and the rows of the alternatives spec that back each, section 3 for the services per phase, and section 4 for the probes.

# Saathi TRD ops: technology choices, deployment and operations

## 1. Scope and marks

Each choice below is marked use (adopt a package as is), adapt (adopt it behind our own code) or build (write it, because no alternative meets the requirement or the baseline suffices). Licence marks follow the alternatives spec: E means package metadata read on 2026-11-09, N means not read with the command that settles it. A choice stays P until its comparison in the alternatives spec section 3 runs (RQ-70). The rows cited are in SPEC-saathi-alternatives-2026-11-09.md.

## 2. Technology choices

| No | Choice | Decision | Mark | Licence check | Why and revisit |
|---|---|---|---|---|---|
| 1 | Inbox queue | One Postgres table drained with `FOR UPDATE SKIP LOCKED`, leases and an attempt count; one lease helper of about 100 lines serves inbox, timers, alert attempts and outbound sends; the inbox lease is per elder, oldest first, with a fenced token and a renewing heartbeat (states part, section 5) | build | no new dependency; asyncpg 0.32.0 Apache-2.0 (E) | the insert that dedupes and persists before the 200 shares a transaction with nothing else (RQ-79). procrastinate, pgqueuer and pgmq stay adapt candidates; arq needs Redis-compatible storage, and dramatiq is LGPL. Revisit when the inbox-crash suite runs on the baseline and a candidate matches it with less code |
| 2 | Token and TOTP library | Approval token: HMAC-SHA256 on the standard library, extended from `quote.py:69-94` with request id, expiry and database spend state. TOTP: pyotp for RFC 6238 codes and the `otpauth` URI. Secret encryption: cryptography AEAD. Household envelope keys (platform RQ-111a): the same library, X25519 with HKDF and AES-GCM for the content sealed box, no new dependency. Replay window, lockout and enrolment: our code (PL-27) | build, adapt, use | pyotp 2.10.0 MIT (E); cryptography 50.0.2 Apache-2.0 OR BSD-3-Clause (E); PyJWT and pymacaroons retired in the alternatives spec | a signed token alone has no spent state, so the database row is the authority. Revisit if PE-29 tests show pyotp cannot meet the replay rule |
| 3 | LangGraph checkpointer | None (D-70, decided). `requests.state` with its `intent`, `slots`, `quote_id` and `plan_step` columns, `commit_intents`, `inbox` and `timers` are the only durable state; every step loads the row, runs the next idempotent step and writes the row (states part, rule 5); approval is an event on a row, not a graph interrupt. LangGraph stays as a library for in-run step graphs | build (state in rows) | langgraph 1.2.20 MIT (E); langgraph-checkpoint-postgres 3.2.0 requires psycopg 3.3.8, LGPL-3.0-only (E), so it is banned; langgraph-checkpoint-sqlite 3.1.3 is a second store | a checkpointer would be a second source of truth beside the rows. No fallback is planned; the resume test (TQ-65) is the check |
| 4 | Scheduler for reminders and timers | A `timers` table on the same lease helper. Due times are computed from persisted event times (request first message, tap, alert t0), the worker polls every few seconds (config) and fires through the state machine; reminder recurrence is stored as interval and time of day in the household zone (`zoneinfo`) | build | no new dependency; apscheduler 3.11.3 MIT (E) retired because job state would sit outside the rows | timers survive a restart and run under a fake `Clock` (requests RQ-20, PRD RQ-14). procrastinate and pgqueuer schedules are adapt candidates |
| 5 | Final-screen XML verify | `defusedxml` over the one uiautomator dump, after our own checks (steps below); expat with the DTD, entity and external-reference handlers disabled. Device I/O by uiautomator2 | build; adapt for the device and the parser | uiautomator2 3.7.1 MIT (E); defusedxml 0.7.1 PSFL (E), allowed since D-72, no requirements (E); lxml 6.1.3 BSD-3-Clause (E) is the candidate if a selector needs full XPath | the threat is a hostile text value in the dump of a dedicated phone. A byte search for DOCTYPE misses a UTF-16 dump, so the encoding is checked first and the parser itself rejects DTDs and entities. Revisit when the RQ-87 and RQ-88 fixtures exist |
| 6 | Local speech runtime | onnxruntime for an ONNX export of the model named in private config; a thin front end of our own that turns numbers and rupee amounts into Hindi words; clips encoded to the Ogg voice-note format that WhatsApp accepts by `opusenc` in the speech container (choice 8), not ffmpeg | adapt, build | onnxruntime 1.31.1 MIT (E); its aarch64 wheel holds no bundled system library (E, file list); ThirdPartyNotices not read (N). Retired: sherpa-onnx 2.0.1, whose aarch64 wheel bundles `libasound`, an ALSA library that is LGPL (R: recalled, E: file present), and phonemizer 3.6.0, GPL-3.0-or-later (E). Encoder licence: choice 8 | the model must pass its weights licence and terms receipt (RQ-50, PL-09) before it is used. transformers with torch is the fallback only for a model with no ONNX export. Revisit when the D-69 cases run |
| 7 | Egress | Caddy as a reverse proxy: one `reverse_proxy` route per upstream, the credential added by `header_up` from the environment (a read-only file). Services call `http://egress:<port>/<provider>/...`, one listener port per client container; the route set of a listener is that container's allowlist and any other path answers 404. No TLS interception and no certificate authority: Caddy opens the TLS connection to the provider and checks its certificate. Docker `internal: true` networks and a host firewall rule stay as the second layer | adapt | Caddy 2 Apache-2.0 (E: compose comment; text not read); closure N, read by the RQ-71 image audit. mitmproxy 12.3.1 is retired: its requirements include `ldap3` (LGPL v3) and `urwid` (LGPL-2.1) (E, package index); nginx and envoy are retired in the alternatives spec | adding a header needs no TLS termination. Caveat: every client must support a configurable base URL. The reach test of RQ-102 exercises each one; a client that follows an absolute URL or redirect to the provider is dropped by the network and fails the test, so it fails closed. Revisit when the reach test exists |
| 8 | Voice-note codec tools | opus-tools built from source in a multi-stage image; only `opusdec` and `opusenc` are copied into final images, and `opusinfo` is never shipped. The worker runs `opusdec --rate 16000 --force-wav` on each inbound voice note before ASR, so the ASR server runs without `--convert` and its image has no ffmpeg. The speech container encodes clips with `opusenc`. Calls are subprocesses with an argument list, an input cap (16 MiB), a timeout (30 s) and no shell | build; adapt | opus-tools BSD-2 except `opusinfo`; libopus, libogg and opusfile BSD-3 (R: the build stage prints each licence file for the image audit). The audit lists every binary an image ships and fails on ffmpeg or a GPL or LGPL package (evidence RQ-71) | ffmpeg's licence depends on its build and D-46 bans LGPL. A real voice-note fixture (a synthetic tone, no speech, under `tests/fixtures`, PL-08) must decode to 16 kHz mono WAV. Revisit if `opusdec` cannot read a WhatsApp voice note |
| 9 | Trace sink | One exporter interface (C18): JSON-lines files with the field allowlist and masking of RQ-57 and RQ-97 first; OpenTelemetry to Langfuse once the trace stack runs (D-74). Files rotate daily and go after 30 days (RQ-42) | build | no new dependency; the stack stays as D-40 says | the file sink needs no extra services, and switching is an exporter swap behind the same allowlist. Revisit when the stack runs (evidence section 5) |

Final-screen verify, step by step (choice 5):

1. C13 calls `dump_ui_xml()` after step 1. The result holds the XML bytes, a monotonic `taken_at`, the sha256 and the foreground package.
2. Before parsing, code rejects the dump when it is over 1 MiB, when its bytes are not valid UTF-8, or when its XML declaration names any encoding but UTF-8 (a UTF-16 byte order mark included). It then parses with `defusedxml` (`forbid_dtd=True`; a direct expat parser with those handlers set to raise is equivalent) and rejects the dump when the parser raises, when the foreground package is not the macro's app, or when a node of another package has bounds that cover the commit button (an overlay).
3. For each field of the class list in RQ-87, code evaluates the macro's selector, an attribute predicate on resource id, class, text or content description. It must match exactly one node; none or several aborts.
4. Code normalizes the text (NFKC, trimmed, money parsed to `Decimal`). For cart lines it evaluates the row selector, then one selector per cell, and the row count must equal the approved lines.
5. Code compares with the approved quote: items and quantities equal; total or fare within the RQ-29 ceiling; address label equal to the allowlisted label; pickup, drop and ride type equal; payment in the allowed set.
6. The tap target comes from the same dump: the macro's commit selector. `tap_selector` runs within 2 s of `taken_at` and carries the commit grant, which binds the dump hash.
7. Any failed step ends the attempt `aborted_no_call`.

## 3. Deployment: compose services per phase

E at 3d46456: one `saathi` service holds the webhook, the bot and the pipeline (`docker-compose.yml:198-212`); every service sits on the default network; the stack has 12 services. Phase 0 keeps one `saathi` service, with one connection pool per database role, and moves the Langfuse services to a Compose profile that is off. Phase 1 splits `saathi` by trust zone, turns on the profile and adds the network rules. Until the split every token sits in one service, so the stack holds synthetic data only (PRD phase table).

| Service | Phase | Zone | Network | Note |
|---|---|---|---|---|
| caddy | 0 | Z1 | frontdoor, public 80 and 443 | public routes: the WhatsApp webhook (E: `Caddyfile:4-10`), the elder Telegram bot webhook (a random path and a secret-token header, platform RQ-106) and the health URL of RQ-104, a random path from private config; `/healthz` stays off the public route |
| saathi | 0 | Z1 to Z3 | internal | edge, core and model modules in one service; holds every platform token |
| model server, ASR | 0 | Z3 | internal only | local runtimes; the model server sets its no-cloud flag (E: `:166`); the ASR image loses ffmpeg and `--convert` (build part, change 22) |
| speech | 0 (a job), 1 (a service) | Z3 | internal only | Phase 0 generates the fixed clips once; encoder per choice 8 |
| postgres | 0 | Z2 | internal | loopback publish stays for operator tunnels (E: `:69`) |
| edge, core, model-worker | 1 | Z1, Z2, Z3 | per zone | the split of `saathi` (build part, change 21): edge on frontdoor and internal, core on internal and egress, model-worker on internal only; per-service environment files; image digests |
| egress | 1 | boundary | internal and outbound | the only container with a route out for Z3 and Z4; Caddy, choice 7 |
| adapters | 1 | Z4 | internal only | MCP and search adapters; exit through egress |
| langfuse stack (web, worker, clickhouse, valkey, seaweedfs, init) | 1 | Z2 | internal | web on loopback (E: `:146`); D-40, D-74 |
| backup job | 1 | Z2 | internal, egress | dump, encrypt, upload, head export |
| phone adapter and mesh client | 2 | Z4, Z5 | mesh interface | the mesh admits the VPS node only (RQ-90) |
| debunk fetcher | 3 | Z3 | internal | exit through egress with the publisher allowlist |

Egress enforcement (RQ-102): the model worker, adapters and phone adapter join only `internal`, a network created with `internal: true`. They reach the world only through the egress container, on the listener of their own container, at `http://egress:<port>/<provider>/...`. A host firewall rule in the `DOCKER-USER` chain drops other outbound packets from that subnet. Core reaches WhatsApp, Telegram and the telephony provider through its own listener and holds those tokens itself. A compose test fails when a model-driven service joins the default bridge or `network_mode: host`. The reach test of RQ-102 runs after every deploy; for each client it checks that a base URL setting exists, that a call through its route succeeds, and that a call to an unlisted route or host fails.

Secrets (PL-13, PL-14, RQ-93): `.env` on the VPS with mode 600 (E: `.env.example` header). The approval secret, audit HMAC key and TOTP key are separate files mounted into core only. Each service gets its own variables; today the app takes the whole file (E: `docker-compose.yml:201`). Model-worker and adapter containers hold no `*_KEY`, `*_TOKEN`, `*_SECRET` or `*_PASSWORD`; the egress container reads provider and MCP keys from a read-only file. The credential register in Postgres holds owner, scope, issue and expiry, never a value.

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
| Telegram caregiver channel | daily and on send failure | `getMe` and send results | RQ-78 |
| Telegram elder channel | daily and on send failure | `getMe` of the elder bot and send results | RQ-78, 106 |
| telephony and SO-1 | daily | provider credential check; SO-1 expiry, alert 14 days ahead | RQ-18, 95a |
| phone heartbeat | bridge, every 20 s | 3 misses, 60 s apart, then handoff `phone_offline` | RQ-34 |
| model runtimes | worker, every 60 s | model server, ASR and speech health; down means G0 paths only | RQ-46, 82 |
| egress reach | daily canary | an unlisted host fails, an assigned host passes | RQ-102 |
| external heartbeat | a monitor outside the VPS, every minute | a public route answers `ok`, or 503 when the core heartbeat is over 30 s old, a Postgres write fails or the data disk is over 90% full; the monitor pages both approvers after 3 failed requests (D-75) | RQ-104 |
| disk and WAL | core, every 5 min | free space on the data volume under 20% alerts the ops chat; under 10% turns the route to 503 | RQ-104, 79 |
| clock skew | core, every 5 min; the `Date` header of a Telegram response daily | app clock against Postgres `now()`: over 5 s alerts; over 15 s makes token minting and over-cap codes refuse; NTP sync is read from the host | RQ-22, 18 |
| backups and head export | daily | newest dump or head older than 26 h; restore test older than 30 days | RQ-37, 56b |
| retention job | daily | last run and rows removed | RQ-42 |
| emergency drill | quarterly | rings the approvers' phones with a test event | RQ-18, 91 |

The monitor's name, the route path and the approvers' contact points are private config. The route shows nothing but `ok` or the status code. The monitor does not know whether it still works, so its own failure is silent (T-37). The drill of RQ-104 stops the core and waits for both pages; it is a manual receipt at setup and with each quarterly emergency drill.

Skipped or not checked: no container was started; no service terms or licence text was read; the opus-tools licence files and the mesh coordination service licence are N or R; probe intervals are proposals; the reach test and the restore test do not exist.
