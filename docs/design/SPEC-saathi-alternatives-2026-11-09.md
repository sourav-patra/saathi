---
id: SPEC-saathi-alternatives
kind: contract
governs: v1; the alternatives considered before any custom component is built, and the state of each comparison
status: draft, new in the r2 set, round-3 and TRD review fixes applied, rows added for the TRD choices, pending owner lock (see docs/LOCKS.md)
upstream: PRD-saathi-2026-11-09-r2.md sha256 141b677d217017de6973d67f2f5d46eb549b2b40464d4f7426f4ee83d0ed15ec; SPEC-saathi-evidence-2026-11-09-r2.md sha256 84e320c15d8d622da37071101ab30bedccfdcaa0ce671f3d5fccb2cd44eccf3d; RECORD-saathi-decisions-2026-11-09.md sha256 41bbe3270e83e1a23535f8903d7e5b60edeea9dae846b0b6feb4c3b152ff82d9
sources: section 4 of SPEC-saathi-evidence-2026-11-09.md (sha256 955dd4d2129090bdc3114a819282f26c777e815a54ff8cdfc797663143e6a4c1), expanded here; version and licence fields read from the Python package index JSON API (`https://pypi.org/pypi/<name>/json`) on 2026-11-09; DOC-PHILOSOPHY.md
checked-at: 2026-11-09. Package metadata was read for every Python row below (E for the read; licence texts were not read). The aarch64 wheel file lists of sherpa-onnx and onnxruntime were read in the TRD pass. The source hosting service answered 403 in this pass, so non-Python rows are N.
---

Read section 2 for the alternatives per component, section 3 for the state of each comparison, and section 1 for the rule each comparison follows.

# Saathi alternatives spec: what exists before we build

## 1. Goal, scope and marks

Goal: no custom component exists without at least three alternatives (or a recorded search), each with version, licence, cost, data handling, a source link and a choice. This file holds RQ-70. Marks: PRD section 2. A licence marked N has no declared value in package metadata or was not read; the command that settles it is in the row. Cost is effort to install and maintain (low, medium, high; P) plus money (none for every row). Dependency licence closure: an alternative's licence is that of everything it pulls in. Follow `requires_dist` from the index for a Python package; read the image audit (evidence RQ-71) for a container program. A banned licence anywhere in the closure retires the row (PL-09). A use or adapt row has its closure read before its code merges; a Licence cell naming only the package leaves the closure N.

| ID | Requirement | Check | Evidence and state |
|---|---|---|---|
| RQ-70 | Each component in section 2 has a simpler baseline, a measure and a pass rule set before the run, and the same token budget for both arms. A component is marked use, adapt, build or retire, and no claim that a custom part is better rests on a comparison that has not run. The alternative is adopted only when it meets the same unit and adversarial tests as the custom part, passes the licence audit (POLICY PL-09), and needs less code or maintenance on the stated measure at the same token budget. | one comparison record per row, as a receipt of the type in SPEC evidence RQ-100 | N: every comparison is BLOCKED (section 3) |

## 2. Components and alternatives

Components. Every owner is `owner` because there is one maintainer.

| Component | Simpler baseline | Owner | Custom part and the requirement no alternative meets |
|---|---|---|---|
| Policy engine | plain Python functions (current) | owner | The commit gate (safety RQ-80, RQ-83) is a database transaction around the evaluation: token spend, household lock, live re-read |
| Radar | keyword and regex list (current) | owner | A Hindi and Hinglish phrase layer where no model clears a hit (safety RQ-16) |
| Gateway | direct HTTP calls to one tier | owner | Sensitivity default, receipts per media class, floors, reservation ledger, refusal field (assurance RQ-40, RQ-47, RQ-48, RQ-96) |
| Phone agent | deep link and handoff only | owner | The app allowlist and the code-only final verification (safety RQ-87) |
| Macros | the agent drives the app every time | owner | Versioned store, shadow replay, human sign-off (assurance RQ-53) |
| Approval token | HMAC over canonical JSON with the standard library (current) | owner | Single use, request binding and expiry checked against database rows (safety RQ-22) |
| Audit hash chain | none | owner | A chain in the same Postgres with keyed hashes, a serialized writer and an exported head (assurance RQ-56b) |
| Inbox queue | one Postgres table drained with `FOR UPDATE SKIP LOCKED` | owner | The insert that dedupes and persists before the 200, in one transaction (safety RQ-79) |
| Second factor (TOTP) | none | owner | Replay window, lockout and encrypted secret storage per approver (POLICY PL-27) |
| Redaction | regex rules in code (current) | owner | Hindi-context ID formats and checksums for Aadhaar, PAN, voter and passport (assurance RQ-41) |
| Scheduler (reminders, timers) | Postgres table `timers` polled with `FOR UPDATE SKIP LOCKED` | owner | Due times come from persisted events and run under a fake clock (requests RQ-20, PRD RQ-14) |
| Final-screen verify | standard-library expat parser with a size cap, a UTF-8 check and handlers that reject DOCTYPE and entities | owner | The per-macro selector rules and the compare with the approved quote (safety RQ-87) |
| Egress proxy | Docker internal networks and a host firewall, no proxy | owner | A per-container route allowlist and credential injection with no TLS interception (POLICY RQ-102, RQ-93) |
| Voice-note codec | none: no audio decoded or encoded | owner | Decode of inbound voice notes and encode of stored clips, no GPL or LGPL program in an image (RQ-82, D-69) |
| Local speech output | none: the reply goes as Hindi text | owner | A runtime for a local open-weight model with no GPL front end (RECORD D-69) |
| Secret encryption (TOTP) | none | owner | AEAD encryption of each TOTP secret with the key of POLICY PL-14 (PL-27) |

Alternatives. Data handling says what leaves the process. Source is a package index page for read rows and a repository page for N rows.

| Component | Alternative | Version | Licence | Cost | Data handling | Source | Choice |
|---|---|---|---|---|---|---|---|
| Policy engine | pycasbin | 2.8.0 | Apache-2.0 (E) | low | library, nothing leaves the process | https://pypi.org/project/pycasbin/2.8.0/ | adapt: may express the rules; has no token spend, household lock or live re-read |
| Policy engine | cedarpy | 4.12.2 | N: no value in package metadata. Check: `d=$(mktemp -d) && pip download cedarpy==4.12.2 --no-deps --no-binary :all: -d "$d" && tar -tzf "$d"/*.tar.gz \| grep -i licen`, then read the file | low | library, nothing leaves the process (N) | https://pypi.org/project/cedarpy/4.12.2/ | adapt only if the licence passes RQ-71 |
| Policy engine | open policy engine server (Go) | N | N. Check: `gh api repos/open-policy-agent/opa/license --jq .license.spdx_id` | medium: separate process | server receives each decision input (N) | https://github.com/open-policy-agent/opa | retire unless rules outgrow Python |
| Radar | pyahocorasick | 2.3.1 | BSD-3-Clause and public domain (E) | low | library, nothing leaves the process | https://pypi.org/project/pyahocorasick/2.3.1/ | adapt as the keyword matcher if the list outgrows regex |
| Radar | flashtext | 2.7 | MIT (E, classifier only) | low | library, nothing leaves the process | https://pypi.org/project/flashtext/2.7/ | adapt candidate, same use |
| Radar | llm-guard | 0.3.16 | MIT (E) | medium | library; its scanners download models at first use (N) | https://pypi.org/project/llm-guard/0.3.16/ | retire for the emergency path: scanners are English-first and model-based (N: not tested on Hindi); candidate for the classifier layer |
| Radar | nemoguardrails | 0.24.1 | Apache-2.0 (E) | high | library; routes decisions through a model, so a model could clear a keyword hit | https://pypi.org/project/nemoguardrails/0.24.1/ | retire |
| Gateway | litellm | 1.108.3 | MIT (E; N: the enterprise folder licence is a separate file, check in the audit) | medium | library calls the provider directly; the optional proxy server is a separate process (N) | https://pypi.org/project/litellm/1.108.3/ | adapt: transport behind our privacy gate |
| Gateway | any-llm-sdk | 1.47.0 | Apache-2.0 (E) | medium | library calls the provider directly (N) | https://pypi.org/project/any-llm-sdk/1.47.0/ | adapt candidate, same use |
| Gateway | portkey-ai | 2.3.4 | MIT (E) | medium | SDK; whether it routes through the vendor's cloud by default is N | https://pypi.org/project/portkey-ai/2.3.4/ | retire until the routing default is read |
| Phone agent | uiautomator2 | 3.7.1 | MIT (E) | medium | talks to the phone over the debug bridge on the private mesh, nothing else leaves | https://pypi.org/project/uiautomator2/3.7.1/ | adapt: device control and XML dump |
| Phone agent | adbutils | 2.12.0 | MIT (E) | low | same as above | https://pypi.org/project/adbutils/2.12.0/ | adapt candidate, device control |
| Phone agent | Appium-Python-Client | 6.1.5 | Apache-2.0 (E) | high: a server is needed (N) | server receives every command (N) | https://pypi.org/project/Appium-Python-Client/6.1.5/ | retire unless uiautomator2 fails the comparison |
| Phone agent | droidrun | 0.7.4 | N: no value in package metadata. Check: the same command as for cedarpy with `droidrun==0.7.4` | medium | sends screens to a model provider (N) | https://pypi.org/project/droidrun/0.7.4/ | retire until the licence is read and passes RQ-71 |
| Macros | uiautomator2 scripts | 3.7.1 | MIT (E) | medium | as above | https://pypi.org/project/uiautomator2/3.7.1/ | adapt: selectors in versioned macro files |
| Macros | flow-file test runner (Maestro) | N | N. Check: `gh api repos/mobile-dev-inc/maestro/license --jq .license.spdx_id` | medium; needs a device runner (N) | runs locally (N) | https://github.com/mobile-dev-inc/maestro | adapt candidate |
| Macros | recorded ADB input replay | n/a | n/a | low | local | n/a | retire: breaks on layout change and holds coordinates, not selectors |
| Approval token | PyJWT | 2.15.1 | MIT (E) | low | library, nothing leaves the process | https://pypi.org/project/pyjwt/2.15.1/ | retire: a signed token alone has no spent state; the token never leaves the server |
| Approval token | itsdangerous | 2.2.0 | BSD-3-Clause (E, classifier only) | low | library, nothing leaves the process | https://pypi.org/project/itsdangerous/2.2.0/ | adapt candidate for the signature only |
| Approval token | pymacaroons | 0.13.0 | MIT (E) | medium | library, nothing leaves the process | https://pypi.org/project/pymacaroons/0.13.0/ | retire: caveat model adds concepts the binding does not need |
| Audit hash chain | pgaudit (Postgres extension) | N | N. Check: `gh api repos/pgaudit/pgaudit/license --jq .license.spdx_id` | medium | logs statements inside Postgres; no chain or off-site head (N) | https://github.com/pgaudit/pgaudit | retire as the chain; candidate as a second log |
| Audit hash chain | immudb (server) with immudb-py | client 1.5.0 | client Apache-2.0 (E); server N. Check: `gh api repos/codenotary/immudb/license --jq .license.spdx_id` | high: a second database to run | payloads go to the immudb server (N) | https://pypi.org/project/immudb-py/1.5.0/ | retire unless the Postgres chain fails its tests |
| Audit hash chain | pymerkle | 7.0.0 | GPL-3.0 or later (E) | n/a | n/a | https://pypi.org/project/pymerkle/7.0.0/ | retire: banned licence (PL-09) |
| Inbox queue | procrastinate | 3.10.0 | MIT (E, classifier only) | medium | library on Postgres, nothing leaves | https://pypi.org/project/procrastinate/3.10.0/ | adapt candidate for the worker side |
| Inbox queue | pgqueuer | 2.0.0 | MIT (E) | medium | library on Postgres, nothing leaves | https://pypi.org/project/pgqueuer/2.0.0/ | adapt candidate |
| Inbox queue | pgmq | 1.1.4 | Apache-2.0 (E) | medium | client for a Postgres extension (N: extension licence) | https://pypi.org/project/pgmq/1.1.4/ | adapt candidate |
| Inbox queue | arq | 0.28.0 | MIT (E) | medium | needs a Redis-compatible store; Valkey runs only for Langfuse (D-40) | https://pypi.org/project/arq/0.28.0/ | retire: adds a dependency the inbox does not need |
| Inbox queue | dramatiq | 2.2.1 | LGPL-3.0 or later (E) | n/a | n/a | https://pypi.org/project/dramatiq/2.2.1/ | retire: banned licence (PL-09) |
| Second factor | pyotp | 2.10.0 | MIT (E) | low | library, nothing leaves the process | https://pypi.org/project/pyotp/2.10.0/ | adapt: RFC 6238 code and `otpauth` URI |
| Second factor | onetimepass | 1.0.1 | MIT (E) | low | library, nothing leaves the process | https://pypi.org/project/onetimepass/1.0.1/ | adapt candidate, same use |
| Second factor | passlib | 1.7.4 | BSD (E) | low | library, nothing leaves the process | https://pypi.org/project/passlib/1.7.4/ | adapt candidate; larger than needed |
| Second factor | django-otp | 1.7.5 | Unlicense (E) | high: needs Django | library inside Django | https://pypi.org/project/django-otp/1.7.5/ | retire: framework dependency |
| Redaction | presidio-analyzer | 2.2.366 | MIT (E) | high | library; pulls NLP models (N: Hindi support) | https://pypi.org/project/presidio-analyzer/2.2.366/ | adapt candidate for the classifier layer; not for fixed-format IDs |
| Redaction | scrubadub | 2.0.1 | N: metadata says MIT and the classifier says Apache. Check: read the file named LICENSE in the source distribution | medium | library, nothing leaves the process | https://pypi.org/project/scrubadub/2.0.1/ | retire until the licence is read |
| Redaction | python-stdnum | 2.2 | LGPL (E) | n/a | n/a | https://pypi.org/project/python-stdnum/2.2/ | retire: banned licence (PL-09); the Verhoeff checksum is written in our code |
| Redaction | phonenumbers | 9.0.43 | Apache-2.0 (E) | low | library, nothing leaves the process | https://pypi.org/project/phonenumbers/9.0.43/ | adapt: phone number detection |
| Scheduler | apscheduler | 3.11.3 | MIT (E) | low | library, nothing leaves the process | https://pypi.org/project/apscheduler/3.11.3/ | retire: job state would sit outside the `requests` rows; due times must come from Postgres |
| Scheduler | procrastinate | 3.10.0 | MIT (E, classifier only) | medium | library on Postgres, nothing leaves | https://pypi.org/project/procrastinate/3.10.0/ | adapt candidate: scheduled tasks, same package as the inbox row |
| Scheduler | pgqueuer | 2.0.0 | MIT (E) | medium | library on Postgres, nothing leaves | https://pypi.org/project/pgqueuer/2.0.0/ | adapt candidate: schedules, same package as the inbox row |
| Final-screen verify | lxml | 6.1.3 | BSD-3-Clause (E) | low | library, nothing leaves the process | https://pypi.org/project/lxml/6.1.3/ | adapt candidate: full XPath, only if the standard-library path syntax cannot express a macro selector |
| Final-screen verify | defusedxml | 0.7.1 | PSFL (E); PSF-2.0 is allowed since D-72; no requirements (E) | low | library, nothing leaves the process | https://pypi.org/project/defusedxml/0.7.1/ | adapt: parse with it after the UTF-8 check; a byte search for DOCTYPE misses UTF-16 input |
| Egress proxy | mitmproxy | 12.3.1 | MIT for the package (E). Closure fails: it requires `ldap3` (LGPL v3, E: metadata of 2.9.1) and `urwid` (LGPL-2.1, E: metadata of 5.1.0, requirement range 2.6.14 to 4.0.13 not read) | medium | sees plaintext and holds a certificate authority | https://pypi.org/project/mitmproxy/12.3.1/ | retire: a banned transitive package (PL-09, D-46) |
| Egress proxy | Caddy as a reverse proxy, one `reverse_proxy` route per provider with `header_up` from the environment | 2 (compose tag) | Apache-2.0 (E: compose comment; text not read); closure N: compiled-in Go modules, read by the RQ-71 image audit | low | sees plaintext on the private network; no TLS interception or certificate authority; clients call `http://egress/<provider>/...` and need a configurable base URL (RQ-102 reach test) | https://github.com/caddyserver/caddy | adapt: route set is the allowlist; already in the stack for the public route |
| Egress proxy | nginx with a stream SNI allowlist | N | N. Check: `gh api repos/nginx/nginx/license --jq .license.spdx_id` | low | sees host names only | https://github.com/nginx/nginx | retire: enforces but cannot inject, and Caddy does both |
| Egress proxy | envoy | N | N. Check: `gh api repos/envoyproxy/envoy/license --jq .license.spdx_id` | high | sees host names only (N) | https://github.com/envoyproxy/envoy | retire unless Caddy fails the reach test |
| Local speech output | onnxruntime | 1.31.1 | MIT (E); the aarch64 wheel holds no bundled system library (E, file list read); ThirdPartyNotices not read (N) | medium | local, nothing leaves | https://pypi.org/project/onnxruntime/1.31.1/ | adapt: runtime for an ONNX export of the model named in private config |
| Local speech output | transformers with torch | 5.22.2 and 2.15.0 | Apache-2.0 (E) and Apache-2.0 AND BSD (E) | high: large image on an Arm CPU (N) | local, nothing leaves | https://pypi.org/project/transformers/5.22.2/ | adapt candidate, only when the chosen model has no ONNX export |
| Local speech output | sherpa-onnx | 2.0.1 | Apache-2.0 (E); its aarch64 wheel bundles `libasound` (E, file list), an ALSA library under LGPL (R: recalled, read the wheel notices); "espeak" appears 12 times in its shared library (E, `grep -c -a`), GPL embedding N | medium | local, nothing leaves | https://pypi.org/project/sherpa-onnx/2.0.1/ | retire: a bundled LGPL library breaks PL-09 |
| Local speech output | phonemizer | 3.6.0 | GPL-3.0-or-later (E) | n/a | n/a | https://pypi.org/project/phonemizer/3.6.0/ | retire: banned licence (PL-09) |
| Voice-note codec | ffmpeg | N | N: build dependent; distribution builds include LGPL or GPL parts (R) | medium | local | https://ffmpeg.org | retire: banned licence risk (D-46) |
| Voice-note codec | PyAV | N | N: BSD wrapper (R); its wheels bundle ffmpeg libraries (R) | medium | local | https://pypi.org/project/av/ | retire until the bundled libraries are read |
| Voice-note codec | opus-tools built from source: `opusdec`, `opusenc` only | N | BSD-2 except `opusinfo`, never shipped; libopus, libogg, opusfile BSD-3 (R: read the licence files in the build stage) | low | local | https://gitlab.xiph.org/xiph/opus-tools | adapt: multi-stage image, copy two binaries |
| State checkpointer | langgraph-checkpoint-postgres | 3.2.0 | MIT (E); requires psycopg 3.3.8, LGPL-3.0-only (E) | n/a | n/a | https://pypi.org/project/langgraph-checkpoint-postgres/3.2.0/ | retire: a banned transitive package (PL-09) |
| State checkpointer | langgraph-checkpoint-sqlite | 3.1.3 | MIT (E) | medium | a second store file beside Postgres, no row lock | https://pypi.org/project/langgraph-checkpoint-sqlite/3.1.3/ | retire: a second source of truth |
| Secret encryption | cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause (E) | low | library, nothing leaves the process | https://pypi.org/project/cryptography/50.0.2/ | use: AEAD for `secret_enc` |
| Secret encryption | pynacl | 1.6.2 | Apache-2.0 (E) | low | library, nothing leaves the process | https://pypi.org/project/pynacl/1.6.2/ | adapt candidate, same use |
| Secret encryption | pycryptodome | 4.4.0 | BSD-2-Clause AND Unlicense AND MIT (E) | low | library, nothing leaves the process | https://pypi.org/project/pycryptodome/4.4.0/ | adapt candidate, larger than needed |

Other baselines kept (RQ-70):

| Component | Simpler baseline | Measure | Pass rule |
|---|---|---|---|
| Tier cascade | one G2 tier | cost and accuracy on the intent suite | keep the cascade only if cost falls at equal accuracy |
| Macro replay | the agent drives the app every time | cost, time and success per flow | keep macros if cost falls with equal success |
| Document RAG (Phase 5) | pgvector plus plain loaders | answer accuracy on held-out questions | decide when documents exist |

## 3. State of each comparison

No comparison has run. A comparison is BLOCKED until its prerequisite exists, because the pass rule needs the same tests on both arms.

| Component | State | Prerequisite |
|---|---|---|
| Policy engine | BLOCKED | The 32 policy test functions at 3d46456 run against each candidate through an adapter, and a candidate adapter for token spend exists (not written) |
| Radar | BLOCKED | The radar eval set exists, written by the caregiver and never shown to the keyword author (assurance and safety RQ-16, D-95) |
| Gateway | BLOCKED | The privacy-gate and cascade tests ported to each candidate; hosted terms read (POLICY-data PL-20) |
| Phone agent, macros | BLOCKED | The bridge board and a dedicated phone exist (POLICY RQ-90) |
| Approval token | BLOCKED | The `approval_tokens` table and the replay and expiry tests exist |
| Audit hash chain | BLOCKED | The `audit_log` schema and the chain-break, head-mismatch and concurrent-writer tests exist |
| Inbox queue | BLOCKED | The kill test (safety RQ-79) runs against the baseline |
| Second factor | BLOCKED | The wrong, expired and replayed code tests exist (rule PE-29) |
| Redaction | BLOCKED | The `redaction` suite with canaries per ID type exists, including Hindi-context fixtures |
| Scheduler | BLOCKED | The timer tests of requests RQ-20 and PRD RQ-14 run under a fake clock against the baseline |
| Final-screen verify | BLOCKED | The fixture dumps of safety RQ-87 and RQ-88 exist, with a UTF-16 dump and a DOCTYPE dump that must be rejected |
| Egress proxy | BLOCKED | The per-container reach test of POLICY RQ-102 exists, including each client's base URL setting |
| Voice-note codec | BLOCKED | A real voice-note fixture decodes to 16 kHz WAV; the image audit (RQ-71) passes |
| Local speech output | BLOCKED | The D-69 cases exist (stored clip with every model service down, dynamic audio, fallback to text) and a model named in private config has a licence and terms receipt |
| State checkpointer | decided (D-70) | none built; a restart test over a request in each non-terminal state (safety RQ-79, RQ-80) checks the rows |
| Secret encryption | BLOCKED | The canary test that a TOTP secret appears in no log, trace or message (POLICY PL-27) exists |

## 4. Out of scope and order of work

Out of scope: benchmarks beyond the pass rule, and any component not in section 2. Order of work: the PRD phase table; a comparison runs when its prerequisite exists and before the component's custom code is merged.

Skipped or not checked: licence texts were not read, only package metadata; repository-hosted licences and versions (rows marked N) were not reachable; no data-handling claim was tested by network capture; no comparison ran.
