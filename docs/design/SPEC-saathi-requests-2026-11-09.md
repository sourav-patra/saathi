---
id: SPEC-saathi-requests
kind: contract
governs: v1; how an elder message becomes a request, which states it can reach, which timers and templates apply
status: draft, new in the r2 set, round-3 and TRD review fixes applied, pending owner lock (see docs/LOCKS.md)
upstream: PRD-saathi-2026-11-09-r2.md sha256 d5f23838159bf7e481bb3f5650dd9e7047e579f20e219a7b6c944416953d8466; RECORD-saathi-decisions-2026-11-09.md sha256 c04a9cf5bcf98fc95fe4715ef8ed40b800522aac020463262e88ccc307e5fce4
sources: docs/design/RECORD-saathi-decisions-2026-11-09.md (D-nn); RQ-20, RQ-39, RQ-74, RQ-77 and RQ-78 as written in PRD-saathi-2026-11-09-r1.md (sha256 021742ba14e4d154f291d059aaf1add458e2e3b90c36992990d7518ffae1434a); src/saathi/pipeline.py and src/saathi/domain.py on branch phase-0/foundation at 3d46456; DOC-PHILOSOPHY.md
checked-at: 3d46456, 2026-11-09. Code read with `git show`; nothing in this file was executed.
---

Read section 2 for the order in which a message is classified, section 3 for the one state table, and section 4 for timers and templates.

# Saathi requests spec: lifecycle, states, timers, acknowledgement, windows, channels

## 1. Goal, scope and marks

Goal: every elder message ends in a named state, a later message cannot cancel, amend or duplicate the wrong request, and no timer or template lives only in code. This file holds RQ-20, RQ-39, RQ-74, RQ-77 and RQ-78. Marks: PRD section 2. All rows are P unless the state column says otherwise. The phase of each row is in the PRD phase table.

## 2. Classification, concurrency and acknowledgement

| ID | Requirement | Check | Evidence and state |
|---|---|---|---|
| RQ-39 | Request lifecycle. Code classifies every inbound message in this order, and the first match wins. (1) Emergency or scam (safety RQ-16): runs on every message at once and never queues. (2) Withdrawal of consent (RQ-74). (3) Cancel (RQ-74). (4) Answer to an open question: while a clarifying question is open, a bare "nahi", "no", "haan" or "yes" answers it and is never a cancel. (5) Amendment or new request: a schema-parsed G1 step (assurance RQ-49) returns `kind` (amendment or new), `target_request_id` or null, and slots. Code accepts a target only when it belongs to the household and is in `clarifying`, `quoting` or `awaiting_approval`; otherwise the message is a new request. An unclear target costs one of the 2 questions of RQ-02. Concurrency: at most one request per elder is active, meaning `clarifying` or `committing`. Up to 3 requests per elder may be `awaiting_approval` at once, each with its own card and token. A new request that arrives while one is active queues, 3 deep at most; beyond that the elder hears "ek ek karke". An urgent request (PRD RQ-05) moves ahead of the queue and may use a fourth card slot. An amendment before commit step 1 (safety RQ-80) turns the old request into `superseded`, voids its unspent tokens and edits its card to say so. After step 1 nothing supersedes. State names and meanings are in section 3. | state-machine test over every row of section 3 (the inbox and alert event rows by the tests of safety RQ-79 and RQ-18); precedence test (a message with an emergency phrase and "ruko" starts RQ-18); open-question "nahi" test; 3-card test; urgent-queue test; supersede race test; amendment-target test | N |
| RQ-74 | Acknowledgement, cancel and withdrawal. The elder gets a templated acknowledgement within 10 s of the inbox row being committed, built by code and sent with no model call. The separate acknowledgement is skipped when the real reply (a clarifying question, a cancel confirmation) is due within 10 s. Cancel: a phrase ("ruko", "nahi chahiye", "rehne do", "cancel") matched by code on text and ASR text is never gated by pause, a model tier or the budget, and ends the request `cancelled` before commit step 1. After step 1 the request ends `handoff`, reason `cancel_after_commit_start`: the caregiver gets a card saying the elder asked to cancel, with the service's cancel path. The helper never cancels an order itself. Withdrawal: a phrase ("band karo", "mera data hata do", "stop saathi") gets one confirmation message, "kya aap sach mein Saathi band karna chahte hain? haan ya nahi". On "haan" processing for the household stops and erasure starts (POLICY-data PL-05, PL-29), and open requests are settled as follows. Before commit step 1 (`received`, `clarifying`, `quoting`, `awaiting_approval`, `approved`): each pending approval is cancelled, the request ends `cancelled` with reason `withdrawal`, unspent tokens are voided, the card is edited to say so and no new request starts. From step 1 on (`committing`, `timeout`, `commit_unknown`): the commit finishes or times out, then `find_order` reconciles it (safety RQ-80), the caregiver gets the outcome, and the order's rows are erased once the request is terminal. The radar stays on, so an emergency still reaches the approvers. | latency test with a fake clock; skip-ack test; cancel tests before and after step 1; cancel during pause; withdrawal test with "haan", "nahi" and no answer; withdrawal with one `awaiting_approval` and one `committing` request (first cancelled and token voided, second reconciled then erased); emergency after withdrawal test | N |

## 3. State table (RQ-39)

One table covers request states, commit_intent states (safety RQ-80), the meanings of `handoff`, inbox states (safety RQ-79, RQ-81) and alert event states (safety RQ-18). The Level column tells apart names used at two levels, such as `received`. A row is terminal when marked yes. `blocked` and `failed` carry a reason string.

| State | Level | Meaning and what the humans get | Request state it is or sets | Terminal |
|---|---|---|---|---|
| `received` | request | The inbox row is committed and not yet classified. | itself | no |
| `clarifying` | request | One question is open to the elder (2 at most). | itself | no |
| `quoting` | request | The helper is building or refreshing a quote. | itself | no |
| `awaiting_approval` | request | A card is out; the token is unspent. | itself | no |
| `approved` | request | A valid tap exists; step 1 has not started. | itself | no |
| `committing` | request | Step 1 is committed and step 2 may be running. | itself | no |
| `done` | request | The outcome is confirmed, or a request that needed no commit was answered. | itself | yes |
| `handoff` | request | The helper stopped and a human continues with a card holding what is known. Nothing was placed by the helper after the handoff. Reasons below. | itself | yes |
| `cancelled` | request | The elder cancelled before step 1, or withdrew consent (reason `withdrawal`, RQ-74). | itself | yes |
| `superseded` | request | An amendment replaced it before step 1. | itself | yes |
| `rejected` | request | The caregiver rejected the card. | itself | yes |
| `expired` | request | The request expiry in section 4 passed. | itself | yes |
| `blocked` | request | A rule stopped it. Reasons: `verify_failed`, `paused`, `policy_denied`, `refusal`, `budget`, `window_closed`, `channel_down`, `consent_missing`. | itself | yes |
| `failed` | request | The run ended without its outcome. Reasons: `no_result`, `service_rejected`, `reconciled_not_placed`. | itself | yes |
| `timeout` | request | The external call hit its time cap and the outside effect is unknown until reconciled. | itself | no |
| `commit_unknown` | request | How the commit ended is not established. Anything that depends on it waits for reconciliation. | itself | no |
| `attempting` | commit_intent | Step 1 committed; the external call may be running. | `committing` | no |
| `committed` | commit_intent | Step 3 recorded a provider reference. | `done` | yes |
| `rejected_by_service` | commit_intent | The service refused the order. | `failed`, reason `service_rejected` | yes |
| `aborted_no_call` | commit_intent | The final check failed, or pause or consent failed, before the external call or tap; no call or tap happened. The token stays spent; a new quote and a new approval are needed. | `blocked`, reason `verify_failed` or `paused` | yes |
| `timeout` | commit_intent | The call hit its time cap. | `timeout` | no |
| `commit_unknown` | commit_intent | A crash or a lost step 3 left `attempting`; the reaper sets this after the call timeout. | `commit_unknown` | no |
| `reconciled_placed` | commit_intent | `find_order` found the order. | `done`, reason `reconciled` | yes |
| `reconciled_not_placed` | commit_intent | `find_order` found no order. | `failed`, reason `reconciled_not_placed` | yes |
| `handoff` reason `no_access` | handoff | No MCP or app access (not granted, lapsed, token expired): the card has the cart as text and a deep link. | `handoff` | yes |
| `handoff` reason `no_find_order` | handoff | The adapter has no `find_order`: the card has the cart and a deep link. | `handoff` | yes |
| `handoff` reason `cancel_after_commit_start` | handoff | The elder asked to cancel after step 1: the card gives the service's cancel path. | `handoff` | yes |
| `handoff` reason `macro_failed` | handoff | A macro checkpoint failed: the card says which step; repair is a human task (assurance RQ-53). | `handoff` | yes |
| `handoff` reason `urgent_refresh_limit` | handoff | An urgent ride used its last refresh without a tap: the card and the elder get a prompt to phone the caregiver. This is the expiry of an urgent request. | `handoff` | yes |
| `handoff` reason `manual_reconcile` | handoff | `find_order` is unsupported or kept failing until the owner timeout (safety RQ-80): the owner is alerted and records the outcome by hand. The intent stays `timeout` or `commit_unknown` and its day-cap hold stays until that record. | `handoff` | yes |
| `handoff` reason `phone_offline` | handoff | The phone or bridge is offline (safety RQ-34): deep link and summary. | `handoff` | yes |
| `handoff` reason `summary_only` | handoff | Outcome (c) of RQ-02, or medicine or a bill: summary and no action. | `handoff` | yes |
| `received` | inbox | The webhook committed the row before its 200, or an expired lease returned it; a worker has not taken it. | none | no |
| `leased` | inbox | A worker holds the lease; each failed attempt counts toward 5. | none | no |
| `processed` | inbox | The effects (acknowledgement, card, reply) were sent, each keyed by the inbox id. | a request row is created or updated | yes |
| `unsupported` | inbox | The kind is not supported (sticker, contact, location); the caregiver got a summary. | none | yes |
| `media_lost` | inbox | The media fetch failed inside the URL lifetime; the caregiver got a flagged notice. | none | yes |
| `dead` | inbox | 5 attempts failed; the caregiver was alerted. | none | yes |
| `open` | alert_event | The radar flag is persisted and stage a, b or c is sending on the cadence; the `stage` field says which. | none | no |
| `acknowledged` | alert_event | An approver acknowledged on any channel; unsent attempts are cancelled; a follow-up at 15 min asks whether it is resolved. | none | yes |
| `blocked` reason `no_human_reached` | alert_event | Every cap was spent with no acknowledgement; the ops chat got a record and an audit row was written. | none | yes |

A False alarm tap sets a `false_alarm` flag on an alert event and does not change its state. A restart resumes every non-terminal row from Postgres. The day cap counts commit_intents in `attempting`, `committed`, `commit_unknown` and `timeout` (safety RQ-26) until a reconcile or a recorded manual resolution; `aborted_no_call` does not count.

## 4. Timers, templates, windows and channels

| ID | Requirement | Check | Evidence and state |
|---|---|---|---|
| RQ-20 | Timers, from the first persisted elder message of the request, config defaults (table below). On expiry the elder hears "abhi nahi ho paya, caregiver ko bata diya" and the caregiver gets a summary. A reminder or backup card is sent only after a live re-read, and the card is edited in place with the new quote hash. A cart has a quote TTL. A ride has no TTL: its approval is checked by the live re-read at commit step 1 and the fare tolerance (safety RQ-29). A tap past a cart TTL re-quotes and needs a new tap. An urgent ride has no 60 min expiry. Every `urgent_refresh_interval` (default 3 min) until a tap, code re-reads the quote and sends a refreshed card needing a fresh tap; after `urgent_refresh_max` refreshes (default 5, about 15 min) the request ends `handoff`, reason `urgent_refresh_limit`. No approval carries over a refresh. | fake-clock tests for each row; refresh-count test with the default and a changed value | N |
| RQ-77 | WhatsApp window and templates. The 24 hour window after the elder's last message applies to every outbound message from the helper number: replies, reminders, expiry notices and outcomes. Inside it, free text is allowed; outside it, only an approved template from the inventory below. With a closed window and no matching template the message is not sent; the caregiver gets a summary and the state is `blocked`, reason `window_closed`. | inventory test: every outbound call site names a template id; closed-window test | N. R: approval lead times are unknown |
| RQ-78 | Channel states `up`, `degraded` and `down`. Detection uses three signals only: send failure codes from the platform, the expiry date of each token (checked daily, alert 7 days ahead) and one daily probe call to the platform. Webhook silence never counts: an elder who sends nothing is not a fault. When WhatsApp is `down` (banned, token expired, probe failing), the primary and backup get a Telegram alert, open requests end `blocked` reason `channel_down` at expiry, and the caregiver is told to phone the elder. Emergency stages a and c (safety RQ-18) do not depend on WhatsApp. When Telegram is `down`, emergencies use stages b and c at once. | fake-provider tests per state; silence-does-not-trigger test over 7 simulated days with no inbound message | N |

Timers (RQ-20):

| Class | Reminder to primary | Card to backup | Request expiry | Cart quote TTL, from the latest live re-read | Approval token expiry, from the tap |
|---|---|---|---|---|---|
| Cart (food, groceries, shopping) | 5 min | 15 min | 60 min | 30 min | 300 s |
| Ride | 2 min | 3 min | 60 min | none | 60 s |
| Urgent ride | 2 min | 3 min | none; `handoff` after the last refresh | none | 60 s |
| Ride or urgent ride over a cap | as the ride | as the ride | as the ride | none | 180 s (D-77) |

The code window of the second confirmation (safety RQ-26) is the expiry of the approval token, counted from the first tap: 300 s for a cart and 180 s for a ride over a cap (D-77); a ride under every cap needs no code and keeps 60 s. A `confirm2` token never outlives the approval token. The longer ride token does not widen the price guard: the live re-read at commit step 1 and the fare tolerance (safety RQ-29) still apply.

WhatsApp template inventory (RQ-77). Category is proposed; the platform decides. Lead time is N: the template registry records submission and approval times.

| Id | Purpose | Category | Language | Approval lead time |
|---|---|---|---|---|
| T-01 | Emergency alert to an approver's personal number (safety RQ-18 stage b), under sign-off SO-1 | utility | Hindi and English | N |
| T-02 | Reminder to the elder (medicine time, appointment) | utility | Hindi | N |
| T-03 | Request expired, caregiver informed | utility | Hindi | N |
| T-04 | Outcome (order delivered, ride done, handoff made) outside the window | utility | Hindi | N |
| T-05 | Invitation to reply, to reopen the window | utility | Hindi | N |

## 5. Out of scope and order of work

Out of scope: the wording of the templates (private config), group or multi-elder households, and any state not in section 3. Order of work: the PRD phase table; inside Phase 1, the state-machine test (RQ-39) comes before the commit tests in the safety spec.

Skipped or not checked: figures (the state table carries the transitions; no diagram or reader check ran); no state machine, timer or template exists in code; platform template rules were not read; the Hindi phrases are examples and were not reviewed by a Hindi reader.
