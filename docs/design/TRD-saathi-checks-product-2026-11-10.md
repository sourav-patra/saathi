---
id: TRD-saathi-checks-product
kind: contract
governs: the design rules (TQ) for the strategy fold: channels, tenancy, consent, autonomy, the fence, the pilot, and the invariants; part of TRD-saathi-2026-11-09.md
status: draft, new in the strategy fold, pending owner lock (see docs/LOCKS.md)
upstream: SPEC-saathi-platform-2026-11-10.md sha256 e874fac34ac50c865c93b5b0633cb2a8381501671b055667433c90d185116717; SPEC-saathi-product-2026-11-10.md sha256 6e864a6cfce94ca9edafe734e6f984d8b266eb715dd3d16faf32cea14534ecff; POLICY-saathi-data-2026-11-09.md sha256 1dd650a7e3b7a14f874017dacebb20c176d0751ca068508bb915484e83d2877d; POLICY-saathi-2026-11-09-r2.md sha256 b9dc7525931d3f867a5f2623e89e0ebf7121d9631e917bde4e411629fdd51f5b; TRD-saathi-ports-2026-11-09.md sha256 df4144480f106b00a4b64cba2eaa9d1a8a8555a50cfc6ce9f31b9df474bf95f7; RECORD-saathi-decisions-strategy-2026-11-10.md sha256 183c28fa2840ae37acd9675e61025ca4b520822ad477bab1f824e3ad49731496
sources: the contracts in upstream; scripts/design_check.py in this repository
checked-at: 2026-11-10. Contracts read in this repository; the coverage script ran on this design set (TRD-saathi-checks-2026-11-09.md section 3); no design rule here has been run as a test.
---

Read the table: each row ties one new RQ or PL id to a design element and a check. The older rows are in TRD-saathi-checks-2026-11-09.md; the coverage script reads both files.

# Saathi TRD checks, strategy fold: design rules and limits

## 1. Scope and marks

Marks follow PRD section 2. Every rule is P until its check runs. The TQ numbering continues from TQ-66.

## 2. Design rules

| Id | Design rule | Serves | Check | Mark |
|---|---|---|---|---|
| TQ-67 | The core reaches the elder only through `ElderChannel`; two implementations and a fake pass one contract suite; core code holds `PersonRef` and reads `capabilities` | RQ-105 | contract suite on all three; a grep test for platform names in core modules | N |
| TQ-68 | The elder bot is a second bot with its own token, secret and chat registry; it has no callback handler and no path to a token; the caregiver token is held only by the approval service | RQ-106, 23 | environment test for each token; the "approved" and forged callback tests; start code tests | N |
| TQ-69 | The active channel is a field of the household config; inbound on any enabled channel is processed; replies follow the inbound channel; a switch moves no request state | RQ-107, 78 | switch test over `clarifying`, `awaiting_approval` and a due reminder; WhatsApp `down` test with the Switch button | N |
| TQ-70 | Parity matrix of kinds on both channels with fakes, with the 10 s acknowledgement on both | RQ-108 | parity test; oversize media test | N. R: Telegram limits not read |
| TQ-71 | Every household table has forced row-level security keyed to `app.household_id`; `Store.tx` takes the household; scans return ids only | RQ-109, 38 | cross-tenant suite; schema test shown to fail on a planted table | N |
| TQ-72 | Household values are versioned `household_config` rows loaded at commit step 1 under the household lock; the audit row names the version; agents cannot write | RQ-110, 83, 26 | two-household load test; agent write-denied test; unset-value deny test | N |
| TQ-73 | One key per household and purpose; `content` is a sealed box with only the public half at the edge; a `key_id` beside every encrypted value and keyed hash; keys are destroyed on erasure | RQ-111a, 111b, 42 | canary and cross-household decrypt tests; edge environment test; erasure and restore tests | N |
| TQ-74 | One audit chain per household with its own head; the chain hash does not use the HMAC key | RQ-56a, 56b | two-household concurrent writer test; chain verifies after the audit key is destroyed | N |
| TQ-75 | `approval_mode` is read at step 1 and only `every_order` commits in v1; visibility narrows consent and never widens it; emergency alerts keep their audio | RQ-112, 113 | mode, unknown-mode and snapshot tests; narrower-of test; emergency under `summary_only` | N |
| TQ-76 | The routine-order sign-off class is rejected by the row schema in v1; when built it reaches a commit only through `authorize_commit` | RQ-114 | SO-3 row rejection test; when built, the AST and match tests | N. Off in v1 |
| TQ-77 | Each adapter has a manifest with `access_basis`; the core refuses a commit from a `device_automation` adapter without the household flag, and rejects a second flagged household | RQ-115 | loader and fence tests; the conformance manifest holds no device automation | N |
| TQ-78 | Every emergency and scam text, the consent notice and the public documents carry the not-an-emergency-service sentence and 112 and 108 | RQ-116 | reply-key test; grep over public documents; fixture with the sentence removed fails | N |
| TQ-79 | The manual pilot logs coded fields only, keeps raw data in the private folder, commits aggregate counts as a receipt and ends with a go, narrow or stop outcome | RQ-117, 118, 119 | count script shown to fail on free text; leak scan; receipt validator | N |
| TQ-80 | WhatsApp onboarding is refused without a valid allowing terms receipt; an unclear reading selects Telegram | PL-32 | gate row; refusal and selection tests | N. R: terms not read |
| TQ-81 | `allowed(subject, use, recipient)` fails closed at each enforcement point; a record holds capacity and guardian; withdrawal per use takes effect at the next call | PL-33, 34, 35 | one denial test per point; guardian schema test; withdrawal drill per use | N |
| TQ-82 | No capability, dependency or adapter manifest can move money; payment methods are cash on delivery or a wallet inside the merchant flow | PL-36 | capability-set test; import test shown to fail on a planted import; PE-31 tests | N |
| TQ-83 | A sign-off line on every commit of the code repository, no contributor licence agreement, a mark policy and a trademark search receipt before launch | PL-37 | CI step shown to fail on an unsigned commit; manual receipt | N |

## 3. Known limits

| Id | Limit | Next step |
|---|---|---|
| L-10 | Row-level security trusts the value that `Store.tx` sets. A bug that sets the wrong household passes the policy, and the suite tests the helper, not every call site | a lint that bans raw connections outside `Store`; cross-tenant tests on every new repository |
| L-11 | The superuser, the backup role and the holder of the key-encryption key see every household; key destruction reaches backups only after the restore test of platform RQ-111b | a second operator and a break-glass rule when a second household exists |
| L-12 | The Telegram elder bot is on a platform that keeps chats and does not offer end-to-end encryption for bots (R, not read); a leaked bot token lets an attacker write to the elder as the helper | the terms receipt (PL-32) and the registration of both channels at onboarding |

Skipped or not checked: no rule here ran as a test; the coverage script proves each id has a Serves cell, not that the rule serves it well; no reader check ran.
