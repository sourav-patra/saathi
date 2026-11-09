---
id: TRD-saathi-states
kind: contract
governs: the transitions of the request, commit_intent, alert event, inbox and approval token machines; part of TRD-saathi-2026-11-09.md
status: draft, new, pending review and owner lock (see docs/LOCKS.md)
upstream: PRD-saathi-2026-11-09-r2.md sha256 341ecc305674155b388184dd59323faeda447676c01d9d002c188b9aeadfdd8b; SPEC-saathi-requests-2026-11-09.md sha256 b798a89a5d876a26d7e30e3d8b3ad380ffa276cb8b133e5b87baf17c3c32ea72; SPEC-saathi-safety-2026-11-09-r2.md sha256 6b9a198e72f618e26af03ff63e1555abf9c056e7fcf8a3e4cdff7fd078b9c202; SPEC-saathi-assurance-2026-11-09-r2.md sha256 be50717893ed345c507fcc856d7f8694931b520e3bf4110200b922519007137e; POLICY-saathi-2026-11-09-r2.md sha256 aee5383d7d124ddad9f2fbb8bf9dcc50ac066eb05d128d641f126a97f5495dd9; RECORD-saathi-decisions-2026-11-09.md sha256 a1ec6109610478a4582467cebe12c11d4bd266b89b35a89fdbe44dbde9f86f45
figures: figures/commit-sequence.svg
sources: SPEC-saathi-requests section 3 (the only state table); safety RQ-18, RQ-21, RQ-22, RQ-79, RQ-80, RQ-83; requests RQ-20, RQ-39, RQ-74; POLICY PL-27; src/saathi/app.py, pipeline.py, channels/telegram.py at 3d46456
checked-at: 3d46456, 2026-11-09. Code read with `git show`; nothing was executed.
---

Read section 2 for the request machine and section 3 for the commit; every row of every table is one test case (section 7).

# Saathi TRD states: transitions of the five machines

## 1. Scope and conventions

State names and meanings live in SPEC-saathi-requests section 3 and are not restated. This part adds the transitions: from, event, to, guard and side effect. It adds no stored state. The token machine (section 6) uses four states derived from columns, which that table does not list (checks part, limit L-2).

1. Each transition is one Postgres transaction that updates with a compare on the old state (`UPDATE ... WHERE id = $1 AND state = $2`). Zero rows changed means the event lost a race: nothing else happens, and an approval event writes an audit row `deny`.
2. A terminal state is never rewritten. A later event on a terminal row is logged and ignored.
3. Every outbound effect carries an idempotency key built from the inbox id, the attempt id or the commit_intent id (RQ-79, RQ-80).
4. A restart resumes every non-terminal row from Postgres by its due time. Marks: P for every row; the code facts are in the checks part.

## 2. Request machine (RQ-39)

An emergency or scam flag opens an alert event (section 4) and creates no request; the alert is the card (limit L-1). Classification order and queue limits are in RQ-39 and are not repeated.

| Id | From | Event | To | Guard | Side effect |
|---|---|---|---|---|---|
| R01 | new | worker creates the request from a leased inbox row | received | kind supported; consent row present (PL-05) | templated acknowledgement within 10 s, no model call; run record opened |
| R02 | received | consent missing or withdrawn | blocked `consent_missing` | PL-05 | summary to caregiver |
| R03 | received | router outcome (a) | quoting | confidence at or above the floor; required slots present | planner starts |
| R04 | received | router outcome (b) | clarifying | a slot missing or confidence between floors; fewer than 2 questions asked | one question to the elder; `questions_asked` plus 1 |
| R05 | received | outcome (c), medicine or bill intent, router failure | handoff `summary_only` | none | summary to caregiver; elder hears "caregiver ko bata diya" |
| R06 | received | intent needs no commit and the reply is sent | done | effects sent | reply sent; a reminder writes its timer row |
| R07 | clarifying | elder answers | quoting | required slots now present | none |
| R08 | clarifying | elder answers | clarifying | still missing; fewer than 2 questions asked | next question |
| R09 | clarifying | elder answers | handoff `summary_only` | still missing after 2 questions | summary to caregiver |
| R10 | quoting | adapter facts in; `evaluate` returns OK or needs-second-confirmation | awaiting_approval | quote hash set; household consented | card to primary; reminder and backup timers set (RQ-20) |
| R11 | quoting | `evaluate` returns deny | blocked `policy_denied` | none | summary with reasons |
| R12 | quoting | no access, no `find_order`, phone offline, or macro checkpoint failed | handoff with that reason | one of the four | card with cart text and a deep link, or the failed step |
| R13 | awaiting_approval | approve tap | approved | registered approver; card hash equals current hash; no cap needs a second code | token `approve` inserted; audit `decided`; card edited |
| R14 | awaiting_approval | approve tap over a cap | awaiting_approval | registered approver; hash matches | token `approve` inserted; code prompt; 300 s window |
| R15 | awaiting_approval | valid second code | approved | `approve` token unspent and unexpired; step greater than `last_step` | token `confirm2` inserted; `last_step` set; audit |
| R16 | awaiting_approval | reject tap | rejected | registered approver | reason chip stored; tokens voided; audit |
| R17 | awaiting_approval | edit tap with text | quoting | registered approver | tokens voided; new quote and hash; new card |
| R18 | awaiting_approval | cart quote TTL, reminder or backup due, urgent refresh due | quoting | timer fired | live re-read; card edited in place with the new hash; tokens voided |
| R19 | awaiting_approval | amendment targets this request | superseded | before commit step 1 | tokens voided; card edited; new request created |
| R20 | received, clarifying, quoting, awaiting_approval | request expiry | expired | class has an expiry (not an urgent ride) | caregiver summary; elder hears "abhi nahi ho paya, caregiver ko bata diya" |
| R21 | awaiting_approval | `urgent_refresh_max` refreshes with no tap | handoff `urgent_refresh_limit` | urgent ride | elder and card told to phone |
| R22 | approved | step 1 commits (section 3) | committing | household lock; token verified and spent; pause false; `authorize_commit` allows on the live re-read; caps checked on the ceiling | commit_intent `attempting`; audit `token_spent`, `commit_intent` |
| R23 | approved | step 1 denies | blocked `paused`, `policy_denied` or `consent_missing` | the transaction rolled back | tokens voided in a second transaction; audit `deny`; caregiver summary |
| R24 | committing | intent `committed` | done | none | elder reply from the confirmation; RQ-31 compare |
| R25 | committing | intent `rejected_by_service` | failed `service_rejected` | none | caregiver summary |
| R26 | committing | intent `aborted_no_call` | blocked `verify_failed` or `paused` | none | caregiver alert |
| R27 | committing | intent `timeout` | timeout | none | caregiver alert; reconcile job queued |
| R28 | committing | reaper finds no step 3 after the call timeout | commit_unknown | none | caregiver alert; reconcile job queued |
| R29 | timeout, commit_unknown | `find_order` found the order | done, reason `reconciled` | none | outcome to caregiver and elder |
| R30 | timeout, commit_unknown | `find_order` found no order | failed `reconciled_not_placed` | none | caregiver summary; a new attempt needs a new quote and tap |
| R31 | timeout, commit_unknown | `find_order` errors | unchanged | none | retry with backoff; owner alert after the configured count; no retry of the order |
| R32 | received, clarifying, quoting, awaiting_approval, approved | cancel phrase | cancelled | before step 1; never gated by pause, tier or budget | confirmation to the elder; tokens voided; card edited |
| R33 | committing | cancel phrase | handoff `cancel_after_commit_start` | after step 1 | card with the service's cancel path; the helper never cancels; the intent still settles and its outcome goes on the card |
| R34 | any state before step 1 | withdrawal confirmed ("haan") | cancelled `withdrawal` | one confirmation sent | approvals cancelled; tokens voided; card edited; erasure starts (PL-29) |
| R35 | committing, timeout, commit_unknown | withdrawal confirmed | unchanged | from step 1 on | the commit finishes or times out, `find_order` reconciles, the caregiver gets the outcome, rows are erased once terminal |
| R36 | received, clarifying, quoting, awaiting_approval | channel down, window closed, budget spent, refusal | blocked with that reason | the rule holds at that moment | caregiver summary; refusal never retried on another tier |

## 3. commit_intent machine (RQ-80)

| Id | From | Event | To | Guard | Side effect |
|---|---|---|---|---|---|
| I01 | new | step 1 transaction commits | attempting | R22 guards | token `spent_at`; audit rows; household lock released at commit |
| I02 | attempting | step 2 check fails (RQ-87 or cart compare), or pause or consent fails on re-read | aborted_no_call | no call and no tap happened | audit `commit_result`; token stays spent |
| I03 | attempting | call returned with a provider reference; step 3 commits | committed | none | audit `commit_result` with the reference hash |
| I04 | attempting | service refused; step 3 commits | rejected_by_service | none | audit `commit_result` |
| I05 | attempting | call hit its time cap; step 3 commits | timeout | none | alert; `find_order` queued |
| I06 | attempting | reaper: no step 3 after the call timeout | commit_unknown | none | alert; `find_order` queued |
| I07 | timeout, commit_unknown | `find_order` found the order | reconciled_placed | none | audit; request R29 |
| I08 | timeout, commit_unknown | `find_order` found no order | reconciled_not_placed | none | audit; request R30; day total drops it |

The intent never returns to `attempting` and nothing retries it. A terminal request state is not overwritten when its intent settles (R33).

![Figure 2: approval and three-step commit sequence. Alt text in the file.](figures/commit-sequence.svg)

Figure 2. Shows: the eleven messages from the card to the outcome, the three steps of RQ-80 and the two crash points. Omits: the backup approver's card and timers, the second-confirmation exchange beyond its note, the difference between an MCP flow and a phone flow inside message 8, reminders, and every message to the elder except the outcome. Checked against: safety RQ-21, RQ-22, RQ-80, RQ-83 and RQ-87, the transitions R13 to R30 and I01 to I08 above, and the components C09, C12, C13 and X1 in the core part. Inspected: rendered to PNG by the drafting agent and read; one label overlap fixed after the first render; reader check not run.

## 4. Alert event machine (RQ-18)

The `stage` field holds the highest stage started; earlier stages keep their cadence until their cap.

| Id | From | Event | To | Guard | Side effect |
|---|---|---|---|---|---|
| A01 | new | radar flag persisted | open, stage a | emergency or scam | event and first attempts in one transaction; Telegram to primary and backup with the original audio; stored clip to the elder |
| A02 | open | cadence due | open | no acknowledgement; stage a under 20 sends per approver | attempt row with key; sends at 0, 60, 120, 180, 240 s, then every 5 min |
| A03 | open | t0 plus 2 min | open, stage b | emergency; no acknowledgement; SO-1 valid; WhatsApp not down | template T-01 per approver, every 2 min, 3 at most |
| A04 | open | t0 plus 4 min | open, stage c | emergency; no acknowledgement; SO-1 valid | call primary, backup 60 s later; every 3 min; 3 per approver and `voice_calls_per_event` per event |
| A05 | open | t0 plus 2 or 4 min with SO-1 missing or expired | open | none | stages b and c send nothing; owner alerted; stage a continues |
| A06 | open | acknowledgement on any channel | acknowledged | registered approver | unsent attempts cancelled; a ringing call may finish; follow-up at 15 min |
| A07 | open | every cap spent with no acknowledgement | blocked `no_human_reached` | none | ops chat record; audit row |
| A08 | acknowledged | false-alarm tap | acknowledged | registered approver | flag set; counted per approver when stage b or c had started |
| A09 | open | t0 plus 10 min with no acknowledgement | open | emergency | the 108 and 112 clip sent to the elder again |
| A10 | any non-terminal | restart | unchanged | none | resume by `next_due_at` |

A scam alert uses stage a only (A01, A02, A06, A07).

## 5. Inbox machine (RQ-79, RQ-81)

| Id | From | Event | To | Guard | Side effect |
|---|---|---|---|---|---|
| N01 | new | valid webhook | received | signature valid; sender registered; body within the size limit; `provider_message_id` unique | insert, commit, then 200; a unique conflict inserts nothing and still returns 200 |
| N02 | new | invalid signature | no row | none | 401; counter plus 1 |
| N03 | new | unregistered sender | no row | none | `rejected_inbound` row with a keyed hash; no reply; weekly digest count |
| N04 | new | sender over the rate limit | received | 20 messages in 10 min | row has `not_before`; one acknowledgement; radar still runs |
| N05 | received | worker leases | leased | `SKIP LOCKED`; `not_before` passed; attempts under 5 | `lease_until` set |
| N06 | leased | lease expired or attempt failed | received | attempts under 5 | `attempts` plus 1 |
| N07 | leased | effects sent | processed | every effect keyed by the inbox id | request created or updated, or an alert event opened |
| N08 | leased | kind unsupported | unsupported | sticker, contact, location | summary to caregiver |
| N09 | leased | media fetch failed inside the URL lifetime | media_lost | none | flagged notice to caregiver |
| N10 | leased | fifth failed attempt | dead | none | caregiver alert; audit row |

A body that is not valid JSON after a good signature returns 400 and writes no row; a body over the limit returns 413.

## 6. Approval token machine (RQ-22)

Stored columns: `spent_at`, `voided_at`, `expires_at`. The states are read from them: issued (all clear, `now` before `expires_at`), spent, voided, expired.

| Id | From | Event | To | Guard | Side effect |
|---|---|---|---|---|---|
| K01 | new | R13 or R14 (approve tap) | issued | registered approver; request in awaiting_approval | `expires_at` is the tap time plus 300 s for a cart or 60 s for a ride |
| K02 | new | R15 (valid code) | issued, action `confirm2` | `approve` token unspent | `expires_at` is 300 s after the first tap |
| K03 | issued | commit step 1 | spent | request id, quote hash, approver and action match; unexpired; not voided; one `UPDATE ... WHERE spent_at IS NULL` changes one row | audit `token_spent` |
| K04 | issued | supersede, edit, reject, cancel, withdrawal, refresh, step 1 denial, revocation runbook | voided | none | `voided_at` set; audit `token_voided` naming the request |
| K05 | issued | clock passes `expires_at` | expired | none | none |
| K06 | spent, voided, expired | any use | unchanged | none | audit `deny`; no effect |

The SPEC wording "a card is out; the token is unspent" is read here as: a token exists only after a tap, because the timers table counts expiry from the tap.

## 7. Tests

A table-driven test loads sections 2 to 6, runs one case per row, and asserts that every (from, event) pair absent from a table is refused and leaves the row unchanged. A row added with no case fails the test. The test is shown to fail by deleting one row (TQ-04).

Skipped or not checked: figure 2 rendered and inspected by the drafting agent, reader check not run; no machine exists in code; the tables were compared with SPEC-saathi-requests section 3 by reading, not by a script; the Hindi phrases are examples; timings are config defaults from the SPEC and were not measured.
