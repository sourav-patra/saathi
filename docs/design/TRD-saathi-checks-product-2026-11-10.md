---
id: TRD-saathi-checks-product
kind: contract
governs: the design rules (TQ) for the strategy fold: channels, tenancy, consent, autonomy, the fence, the pilot, and the invariants; part of TRD-saathi-2026-11-09.md
status: draft, new in the strategy fold, pending owner lock (see docs/LOCKS.md)
upstream: SPEC-saathi-platform-2026-11-10.md sha256 8b84438c0b5167a7ba63c887285a3b8cca200d8045b9a86b462e74a93722b250; SPEC-saathi-product-2026-11-10.md sha256 b0543f42556020349f6e86f688f66e73832113fed3a67c10a69115123dd080b1; POLICY-saathi-data-2026-11-09.md sha256 d892eb25058f315132efc589bbc515d961c8d50baa4aece3d16a63bd28aa48c1; POLICY-saathi-2026-11-09-r2.md sha256 c4b26bb7e3e39eba4e83c73891040b125dfebc5872091dd742178264d3072ff1; TRD-saathi-ports-2026-11-09.md sha256 ff04d8c694fdea9b73761416dcaf147963c9693aa450993079cc8a7c459596ca; RECORD-saathi-decisions-strategy-2026-11-10.md sha256 2d6d32e8f7f743b397f9daa4b429c009b5275b47232cb441fc4d2e6a8754d931
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
