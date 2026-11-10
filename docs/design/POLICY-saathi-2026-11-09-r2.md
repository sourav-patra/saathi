---
id: POLICY-saathi
kind: contract
revision: r2
governs: the public repository, the server, the devices and every contributor, human or agent
status: draft r2, round-3, TRD review and strategy fold applied, pending owner lock (see docs/LOCKS.md)
supersedes: POLICY-saathi-2026-11-09-r1.md sha256 72906ab9911d42af0645f9c72a9f01b2998209ab3184cf333ac47310fe26e35b, which had replaced a first draft with sha256 45e5b4b934ec2474a04fc94bd913b3f63b5b7a017785503f90c49f2b1d5688ea. Sections 3 and 6 of that file moved to POLICY-saathi-data-2026-11-09.md.
upstream: PRD-saathi-2026-11-09-r2.md sha256 6fec2edf126fb45918af05e81e44ff177beb96ae376cb1cba34ab979073f2e29; SPEC-saathi-safety-2026-11-09-r2.md sha256 48a9ed8e7f581bdb18422f3474eb1c11b693e778884470e179ade38d72266e0f; SPEC-saathi-assurance-2026-11-09-r2.md sha256 d00fd79913c6a1fdb3c3a542f940adc7d1a71bb4540505c8baf61e5fbff0bbae; SPEC-saathi-evidence-2026-11-09-r2.md sha256 8041fc8279a240c0b252c0078669793594ff960a7e68f3477b436007b6b8ae96; SPEC-saathi-alternatives-2026-11-09.md sha256 301567dcfe03a8d3bfc1aa5367281ce6e3f7a56e72bcb525de53ac2953ff7833; POLICY-saathi-data-2026-11-09.md sha256 1dd650a7e3b7a14f874017dacebb20c176d0751ca068508bb915484e83d2877d; RECORD-saathi-decisions-2026-11-09.md sha256 41bbe3270e83e1a23535f8903d7e5b60edeea9dae846b0b6feb4c3b152ff82d9
sources: docs/design/RECORD-saathi-decisions-2026-11-09.md (D-nn); the repository's agent instruction file, docs/ARCHITECTURE.md, docs/DECISIONS.md, scripts/license_audit.py, scripts/license_exceptions.toml, .github/workflows/ci.yml, src/saathi/config.py and .env.example on branch phase-0/foundation at 3d46456; DOC-PHILOSOPHY.md
checked-at: 3d46456, 2026-11-09. Files read with `git show`. No leak scan, network capture, branch-protection read or legal review was run.
---

Read section 2 for the repository, licence and secret rules, section 3 for contribution, section 5 for the second factor, and section 6 for credentials, the device and server sandbox, and the compromise runbook.

# Saathi POLICY: repository, licences, secrets, contribution, release, sandbox and compromise

## 1. Goal, scope and marks

Goal: the public repository leaks nothing personal, every dependency is permissively licensed, a change reaches `main` only through review and checks, and a lost device or stolen account can be contained in minutes. Scope: rules for the repository, servers, devices and people. This file holds PL-08 to PL-19, PL-21 to PL-28, PL-31, PL-36, PL-37, RQ-90, RQ-91, RQ-93, RQ-102 and RQ-104. Data routes, consent, erasure and law are in POLICY-saathi-data-2026-11-09.md. Marks: PRD section 2. A rule row names the requirement it implements; the phase of each rule is in the PRD table and nowhere else.

## 2. Repository, licences, secrets and documents

| ID | Rule | Check | Evidence and state |
|---|---|---|---|
| PL-08 | Elder media in the repository, CI logs, issues and pull requests is forbidden. Fixtures are synthetic. | leak scan over the tree and history (PL-15) | E: `.gitignore` excludes `media/`, `*.ogg`, `*.mp4`, `evals/datasets/private/`. N: scan |
| PL-09 | Licences. Allowed: MIT, Apache-2.0, BSD, ISC, PostgreSQL, PSF-2.0 (D-72), public domain, and OFL for fonts. Banned: GPL, LGPL, AGPL, SSPL, ELv2, BUSL, RSAL and non-commercial licences. The ban has no exception, including for an external program run in its own process (D-46). It covers transitive packages, container images, fonts, datasets and model weights. | `uv run python scripts/license_audit.py` (evidence RQ-71) | E: passes for Python packages at 3d46456 (PRD section 2). N: images, models, datasets |
| PL-10 | An unknown or missing licence fails the audit. | run the audit on a package with no licence field; it must fail | N |
| PL-11 | An exception lists package, licence, reason and date, and holds only an unmodified transitive package under a file-level weak licence that is not on the PL-09 banned list. A banned licence never qualifies, linked or not. | read `scripts/license_exceptions.toml` | E: 2 entries (MPL-2.0 files) |
| PL-12 | The public repository holds no personal value: no cap, budget, name, address, phone number, account, subscription, city or family detail. It names no concrete AI model or provider, and no additional regional language. It may name Hindi and Hinglish (D-57), the commerce integration targets (Swiggy, Instamart, Zepto, Zomato, Blinkit, Amazon, Flipkart, Uber, Rapido) and open-source infrastructure. This is the only place the model and provider name rule is stated; grades and tier labels are the public names (assurance section 1). Personal values live in household config rows (platform RQ-110) and have no code default: the three caps are required `PolicyConfig` fields, the paid cap defaults to 0 (paid tiers off) and `.env.example` lists placeholders. The commit denial for an unset cap is safety RQ-26. | scan of the tree with the owner's private name list (evidence RQ-101), run before each release; test that `Settings()` with an empty environment leaves every paid tier disabled | E at 3d46456: `PolicyConfig` caps are required fields with no defaults (`src/saathi/policy/engine.py:28-30`); `paid_monthly_cap_inr` defaults to 0.0 (`src/saathi/config.py:55`) and `t1_model` to "" (`:40`); `.env.example` uses placeholders (lines 42, 56). Two public technical defaults remain: `ride_fare_tolerance_abs = 20` (engine.py:35), which private config overrides, and `ride_quote_ttl` of 3 min (engine.py:36), which conflicts with requests RQ-20 (no ride TTL): a known code change for the Phase 0 or Phase 1 build, with `test_ride_quote_expires_fast`. Not implemented: safety RQ-26 "an unset cap denies"; an unset cap raises `TypeError` today. N: the scan and the `Settings()` test |
| PL-36 | No money movement, a permanent invariant (D-89). Saathi never holds, routes or initiates money movement: no balance, transfer, collect request, top-up, refund or payment link created by Saathi. Payment is cash on delivery, or a wallet that the person chooses inside the merchant's own flow (D-12). No phase, adapter or setting lifts this rule; if prepaid payment is ever needed the flow hands off to the payer's own regulated app. | (1) the `Capability` set has no payment member and a test fails when one is added; (2) a dependency and import test fails on a payment SDK, a UPI intent or a collect-request call in the code tree and is shown to fail on a planted import; (3) policy tests PE-01 and PE-31: a quote whose payment method is outside cash on delivery or a wallet inside the merchant flow is denied, and an adapter manifest listing any other method is refused (product RQ-115); (4) the payment-screen abort (safety RQ-25) | E: PE-01 tests exist (evidence rule index). N: tests 1, 2 and the PE-31 tests |
| PL-13 | No credential enters a model context or a model-driven process (RQ-93). Model sessions get no `*_KEY`, `*_TOKEN`, `*_SECRET` or `*_PASSWORD` variable. App logins and OTP entry on the phone are human steps through screen sharing. | test that the model client and phone-agent subprocess environments hold no matching variable | N |
| PL-14 | Secrets live in `.env` on the VPS with mode 600, each key scoped to one service. The approval signing secret (at least 32 bytes), the audit HMAC key and the key that encrypts TOTP secrets are separate from every other secret and from each other. A suspected leak means rotate first, then record an audit row. | `stat -c %a .env` prints 600; rotation drill | N |
| PL-15 | A secret scanner runs in CI on every push and over history before the first release. | CI exit code; run on a planted fake key, it must fail | E: no scanner job in `.github/workflows/ci.yml` at 3d46456. N |
| PL-18 | A change to a prompt, a policy rule or a graph runs the eval gate (evidence RQ-61) before merge. | required status check `evals` (PL-22) | E: no `evals` job |
| PL-19 | Documents follow `DOC-PHILOSOPHY.md`. The doc lint (evidence RQ-101) runs before every commit and in CI. | the lint's exit code, and the deliberate-fault receipt | N: the lint is not written |
| PL-22 | `main` is protected: pull request required, checks `test`, `compose` and `evals` required, `leak-scan` added to the list when PL-15 passes, no administrator bypass, actions pinned by digest (evidence RQ-72a). | settings read; a push to `main` is refused | N. E: CI has `test` and `compose` only |
| PL-24 | `docs/LOCKS.md` lists each reviewed contract with its sha256 and review result and points to `docs/evidence/reviews/RECEIPT-design-reviews-2026-11-09.md`. A changed file needs a new review and a new row. | `sha256sum` against `00-INDEX.md` and `docs/LOCKS.md` | E: r2 hashes listed; reviews pending |
| PL-25 | Reporting route: private vulnerability reporting on the repository's Security tab, reply target 7 days. Reports never go to public issues. | owner opens the Security tab; manual receipt (evidence RQ-100) | N: needs the repository setting (accepted pending lock) |

## 3. Contribution and review

| ID | Rule | Check | Evidence and state |
|---|---|---|---|
| PL-16 | The builder never reviews its own work. A pull request names the builder and the reviewer; the reviewer is a different identity with no shared session. | setting "approval from someone other than the last pusher"; reviewer line in the PR template | N |
| PL-17 | Agents open draft pull requests only. They do not merge, tag, publish or edit protected branches. | branch protection read | N |
| PL-21 | Only the owner merges, tags and publishes. | repository settings read | N |
| PL-37 | Licence, contribution and name (D-88). The code is Apache-2.0. Every commit to the code repository carries a Developer Certificate of Origin sign-off line (`Signed-off-by:`) by its human author; a sign-off is not an attribution line under PL-31. There is no contributor licence agreement, so no one, the owner included, can relicense contributions later. Copyleft is never used to protect the project (PL-09). The project name and any certification mark are used under a written mark policy: only builds that pass the conformance suite, once it exists, may claim compatibility. "Saathi" is a working name for a common word; the helper's display name and the product name are config (D-06), and a trademark search receipt is required before any public launch or certification. | a CI step fails on a commit with no sign-off line in the pushed range and is shown to fail on a planted unsigned commit; `LICENSE` read; manual receipt for the trademark search (evidence RQ-100) | N |
| PL-31 | Commit messages and pull request text carry no AI attribution: no co-author trailer naming a model or tool, and no session link (D-58). The message says what changed and why in plain words. | a commit-message hook and a CI step that fail on a `Co-Authored-By:` trailer or a session link in the pushed range; run on a planted trailer, it must fail | N |

## 4. Release

| ID | Rule | Check | Evidence and state |
|---|---|---|---|
| PL-23 | A release follows the steps below. A check that did not run blocks the tag. | the release note lists each step, command and result | N |

Only the owner merges, tags or publishes. Steps, each ending on a check:

1. `uv run python scripts/gate.py --phase N` exits 0 for the phase being released. Check: exit code 0 and a printed list with no `unavailable` or `invalid` row.
2. Branch `main` is green on `test`, `compose`, `evals` and `leak-scan`. Check: four green checks on the release commit.
3. Private eval receipts exist for that commit. Check: the receipt validator exits 0.
4. `uv run python scripts/license_audit.py` exits 0 and the doc lint's name check exits 0 with the owner's list. Check: both exit codes.
5. `scripts/audit_verify.py` prints no broken sequence and no head mismatch on the production audit log, and the monthly restore test is under 30 days old.
6. The owner tags `v<version>` and `git rev-parse v<version>^{commit}` equals the commit checked above.

The release note lists each step with its command and result, and names any step that did not run. A step that did not run blocks the tag. Steps 1, 3 and 5 cannot run until their scripts exist (N).

## 5. Second factor over a cap (PL-27) and standing sign-offs (PL-26)

| ID | Rule | Check | Evidence and state |
|---|---|---|---|
| PL-26 | Standing sign-offs (SO-1, SO-2, PRD section 6) have scope and expiry and sit where agents cannot write (assurance RQ-95a, RQ-95b). | assurance RQ-95a and RQ-95b tests | N |
| PL-27 | Re-authentication over a cap (safety RQ-26). The approver enrols a time-based one-time code (RFC 6238: SHA-1, 6 digits, 30 s step) in an authenticator app. Enrolment: the owner runs an admin command on the server; it creates a random 20-byte secret, shows the enrolment code only on the owner's terminal with the approver present, never through Telegram or WhatsApp, and writes the row only after the approver enters two consecutive valid codes. Storage: table `approver_totp(approver_id, secret_enc, enrolled_at, last_step, failures, locked_until)`; `secret_enc` is encrypted with the key of PL-14, and the secret is never logged, traced or sent. Window: the current step and one step on either side. Replay: a code is accepted only when its step is greater than `last_step`, and `last_step` is updated in the transaction that spends the `confirm2` token. Lockout: 5 wrong codes in 10 min lock over-cap confirmations for 15 min, write `totp_fail` rows and alert the other approver. The code window is the expiry of the approval token from the first tap: 300 s for a cart and 180 s for a ride over a cap (D-77, requests RQ-20); the `confirm2` token never outlives that token. A Telegram account takeover alone cannot pass it. | tests with a wrong, an out-of-window, a replayed and a locked-out code; enrolment test needing two codes; canary test that the secret appears in no log, trace or message | N |

## 6. Credentials, sandbox, egress, heartbeat and compromise

| ID | Requirement | Check | Evidence and state |
|---|---|---|---|
| RQ-93 | Credentials. Every credential has an owner, a scope, an issue date and an expiry in a register in the database, with an alert 14 days before expiry. The owner of every credential is `owner`; there is one maintainer. Where a provider supports it, tokens are short-lived (90 days or less); a long-lived key is listed with a rotation date. Keys are injected into outbound requests by the gateway, the adapters or an outbound proxy, so model-driven processes never hold them. | register test that fails on a credential with no expiry; environment test of PL-13 | N |
| RQ-90 | [EXP] Device sandbox. Phone: a dedicated device with no personal account and no banking or UPI app; USB debugging authorised for the bridge host key only; before each run code compares installed packages with the app allowlist and blocks and alerts on any extra package; storage is encrypted; app logins stay human steps. Bridge board: runs only the debug bridge and the private mesh client; no open port outside the mesh; the mesh rule admits the VPS node only; the only secret on it is the mesh key; SSH accepts keys from the VPS only; security updates are applied by a scheduled job. The standing exception E-1 (PRD section 6; safety RQ-33) relies on this sandbox. | package-drift test, port scan from outside, a connection attempt from a non-VPS mesh node that must fail | N |
| RQ-102 | Egress enforcement. A model-driven process is any process that sends data to a model, acts on model output or reads untrusted content: the model client in the worker, the debunk fetcher, the commerce adapters and the phone-agent process. Each runs in a container on an internal network with no route to the internet. Its only exits are an egress container and, for the phone agent, the private mesh interface. The egress container is a reverse proxy with no TLS interception and no certificate authority: each client container has its own listener and one route per provider (`http://egress:<port>/<provider>/...`), the route set is that process's allowlist (the POLICY-data hosts it needs), and the credential header is added from a read-only environment file. Every client must accept a configurable base URL. A host firewall rule drops every other outbound packet from those container networks, and containers resolve no external name. The list in code (assurance RQ-43) is a second layer; the network rule holds when the code is wrong. | from inside each model-driven container, a connection to an unlisted address fails, a connection to a listed host not assigned to that process fails, and a request through the assigned route succeeds, and each client's base URL setting is exercised; a compose test fails when such a service joins the default bridge or uses host networking | N |
| RQ-104 | External heartbeat. A free uptime-monitor service, named only in private config, requests a health URL every minute from outside the VPS and pages both approvers by its own channel when 3 requests in a row fail. The URL is a public route with a random path segment from private config. It answers 200 with the body `ok` and nothing else, or 503 when the core heartbeat is older than 30 s, a write to Postgres fails, or the data disk is over 90% full. It shows no name, number, count, version or state. The monitor holds the approvers' contact points, which are personal data (POLICY-data section 2, PL-20). Values are P defaults in config. | response-body test (`ok` only); drill: stop the core, and both approvers are paged within 5 min; block the route, same; fill a scratch disk, 503; manual receipt at setup and with each quarterly emergency drill (evidence RQ-100) | N |
| RQ-91 | Compromise. A kill switch: `/pause` from either approver (safety RQ-32) freezes commits and the phone agent at once; for a device loss the owner also disables the bridge in the mesh admin so the VPS loses its path to the phone. The table and runbook below apply; the phone row applies once a phone exists. | reading `runtime_flags`; a failed connection to the bridge; the quarterly drill receipt | N |
| PL-28 | A compromise has a runbook and a kill switch (RQ-91). | the quarterly drill receipt (RQ-100 type) | N |

Compromise events (RQ-91):

| Event | How it is noticed | First 5 minutes | Recovery | Check |
|---|---|---|---|---|
| Helper phone stolen or reset | heartbeat misses (safety RQ-34); package-drift alert | pause; disable the bridge; sign out app sessions from each service's account page | new device, new app accounts, new macro sign-off for each app | an order attempt from the old device fails |
| Elder SIM or WhatsApp number taken over | WhatsApp device-change notice; messages after a long gap or in an unusual pattern; the elder reports it | pause; the primary phones the elder; the sender allowlist keeps the number but every action still needs a human tap | re-register the number on the elder's device; recheck the allowlist | a test message from a second number is rejected (safety RQ-81) |
| Approver Telegram account taken over | an approval outside usual hours; the approver reports it; an over-cap attempt fails the one-time code (PL-27) | the other approver pauses; remove the approver row; revoke the bot token and issue a new one | re-register the approver with a new authenticator secret | the old chat id gets no card |
| MCP or API token lost | the provider alert; a register entry past expiry | revoke at the provider; rotate; record an audit row | new token in the register with an expiry | the old token returns an auth error |
| Server secret leaked | scanner hit; an unexpected audit row | rotate first (PL-14); pause until rotation ends | rotate signing secret, which voids every open token | a token minted before rotation fails |

Revocation runbook:

1. Pause. Check: `/status` shows paused and an audit `pause` row exists.
2. Void open tokens: `UPDATE approval_tokens SET voided_at = now() WHERE spent_at IS NULL AND voided_at IS NULL AND expires_at > now()`. Check: `SELECT count(*) FROM approval_tokens WHERE spent_at IS NULL AND voided_at IS NULL AND expires_at > now()` returns 0, and each voided row names its `request_id` in an audit `token_voided` row.
3. Revoke the affected credential at its provider and delete it from the register. Check: a call with the old credential fails.
4. Reconcile any request in `commit_unknown` or `timeout` (safety RQ-80). Check: none remain unreconciled.
5. Resume as primary only after the register shows new credentials. Check: an audit `resume` row names the primary.

A drill runs once per quarter on a test household (P); the result is a receipt of the type in evidence RQ-100.

## 7. Out of scope

Legal advice, choosing the telephony provider (private config), and the content of the consent notice. Order of work: the PRD phase table.

Skipped or not checked: figures (tables and steps carry the content); no leak scan, secret scan, egress test or network capture ran; branch protection was not read; the doc lint (evidence RQ-101) does not exist, so no file here was checked by a lint.
