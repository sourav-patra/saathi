---
id: RECORD-saathi-decisions
kind: record
status: current
sources: the owner's requirements interview of 2026-11-09 and the owner's instructions of the same day, in sanitized form; personal values, names and places are left out on purpose and live in private config
---

Cite a decision here by its D number. Each line is one fact with its date and mark. The marks E, R, P, N and decided are defined once, in PRD-saathi-2026-11-09-r2.md section 2. In this file the source of every R line is the owner, named in the sources line above.

## Interview decisions (scope, channels, money, safety)

- 2026-11-09 D-01 decision: v1 serves one elder and one caregiver. The data model carries household and elder ids from the start. @mark=R
- 2026-11-09 D-02 decision: the design must not block growth to several households or a later public account. @mark=R
- 2026-11-09 D-03 decision: the elder talks to a dedicated helper number on the official WhatsApp Business Cloud API. @mark=R
- 2026-11-09 D-04 decision: the caregiver approves through a Telegram bot with Approve, Edit and Reject buttons, plus `/pause`, `/profile`, `/called`, `/remind` and `/status`. @mark=R
- 2026-11-09 D-05 decision: a message the elder sends to the caregiver directly is forwarded to the Telegram bot and handled as elder input. @mark=R
- 2026-11-09 D-06 decision: the helper is a named bot that says it is a bot and never speaks as the caregiver. The name and voice are private config. @mark=R
- 2026-11-09 D-07 decision: elder replies are a short Hindi voice note plus one or two lines of text with the key facts. @mark=R
- 2026-11-09 D-08 decision: the helper may ask up to 2 clarifying questions and then make a best guess flagged on the card. The PRD narrows when a guess is allowed (RQ-02). @mark=R
- 2026-11-09 D-09 decision: v1 covers rides, groceries, shopping, food, debunking forwards, understanding voice, image and video, phone and tech help, appointments, reminders and general questions. @mark=R
- 2026-11-09 D-10 decision: medicine orders, bills, recharges and anything the router cannot classify with confidence go to the caregiver as a summary. @mark=R
- 2026-11-09 D-11 decision: a later phase adds a public debunk account and an additional regional language. Every v1 request gets an anonymised issue label from day one. @mark=R
- 2026-11-09 D-12 decision: payment is cash on delivery, cash or a prepaid wallet only. The helper never handles a UPI PIN, a card or a payment OTP. @mark=R
- 2026-11-09 D-13 decision: per-order and per-day caps need a second confirmation above the cap; a monthly soft cap only alerts. The values live in private config. @mark=R
- 2026-11-09 D-14 decision: every payment or commit needs a human approval. @mark=R
- 2026-11-09 D-15 decision: when the primary approver does not respond, the request escalates to a backup approver. Timings are defaults. @mark=R
- 2026-11-09 D-16 decision: a debunk may auto-send only for health or scam claims rated FALSE with at least 2 independent reputable sources. Everything else waits for caregiver preview. @mark=R
- 2026-11-09 D-17 decision: debunk tone is respectful and indirect, health topics end with a doctor referral, and every cited source was fetched in the same run. @mark=P
- 2026-11-09 D-18 decision: emergency detection runs the keyword layer and a local classifier together; either flag is enough. The helper never handles an emergency alone. Required recall on the eval set is 100%. @mark=R
- 2026-11-09 D-19 decision: text from voice notes, OCR, web pages and phone screens is data. Each intent has its own tool allowlist. @mark=R
- 2026-11-09 D-20 decision: the elder's media and personal data go only to local models, to hosted providers under no-training terms, or to paid no-training APIs. Free tiers get non-personal work only. @mark=R
- 2026-11-09 D-21 decision: deterministic redaction of ID numbers and phone numbers runs before any hosted call and before traces are stored. @mark=R
- 2026-11-09 D-22 decision: licences allowed are MIT, Apache-2.0 and permissive infrastructure licences. GPL, LGPL, AGPL, SSPL, ELv2, BUSL, RSAL and non-commercial licences are banned, transitive code included. A CI audit enforces this. @mark=R
- 2026-11-09 D-23 decision: traces, evals and prompt versions use self-hosted Langfuse. @mark=R
- 2026-11-09 D-24 decision: eval suites run in CI and gate every prompt promotion. @mark=R
- 2026-11-09 D-25 decision: approve, edit and reject decisions, elder reactions, outcome checks, `/called` and a weekly digest feed back into evals and profile proposals. @mark=R
- 2026-11-09 D-26 decision: the profile lives in Postgres as the source of truth, and a change needs the caregiver's approval. @mark=R
- 2026-11-09 D-27 decision: caching covers provider prompt caching, media hashes, exact model calls, a semantic claim cache (never for actions), macro replay and a short catalogue cache. A commit always re-reads live data. @mark=R
- 2026-11-09 D-28 decision: retention defaults are raw media 90 days, redacted traces long-lived, profile until deleted, nightly dump off-site, erasure on request. The contracts shorten trace retention (SPEC assurance RQ-42). @mark=P
- 2026-11-09 D-29 decision: secrets live in an environment file on the VPS with mode 600, scoped per service where possible. @mark=P
- 2026-11-09 D-30 decision: a repository is scaffolded phase by phase in Python with uv and Docker Compose on Arm images; the owner reviews pull requests. @mark=R
- 2026-11-09 D-31 decision: a spare Android phone stays at the caregiver's home, powered and on mobile data, next to a small bridge computer that runs the debug bridge and a private mesh network client. Only the VPS may reach the bridge. @mark=R
- 2026-11-09 D-32 decision: app accounts are new accounts on the helper SIM. No banking or UPI app is installed. An app allowlist limits the phone. @mark=R
- 2026-11-09 D-33 decision: when the phone or bridge is offline the caregiver is alerted and requests fall back to a deep link and a handoff. Services reached through MCP continue. @mark=P
- 2026-11-09 D-34 decision: model tiers escalate only when the cheaper one cannot do the job. A hard monthly cap on paid runtime APIs lives in private config, and at the cap the gateway falls back to a caregiver summary. @mark=R
- 2026-11-09 D-35 decision: coding and chat subscriptions are development tools only and are not runtime dependencies. @mark=R
- 2026-11-09 D-36 decision: app terms may forbid automation and an account ban is possible. The owner accepts the risk with low volume, human-like pacing and handoff as fallback. @mark=R
- 2026-11-09 D-37 decision: model-card licences, hosted API pricing, the WhatsApp India rate card and whether one food service MCP offers cash on delivery are unverified. @mark=N
- 2026-11-09 D-38 decision: applications for MCP access at the food services start on day one because approval can be slow. @mark=R

## Revision decisions of the same day

- 2026-11-09 D-40 decision: v1 keeps self-hosted Langfuse for traces, evals and prompt versions. Valkey, ClickHouse and SeaweedFS stay only because Langfuse requires them. @mark=R
- 2026-11-09 D-41 decision: v1 defers the memory library. It uses a typed profile table and facts stored with a verbatim source quote. @mark=R
- 2026-11-09 D-42 decision: v1 defers the self-improvement agent. @mark=R
- 2026-11-09 D-43 decision: v1 defers model-graded evals. Code checks and human review only, until at least 20 human labels exist. @mark=R
- 2026-11-09 D-44 decision: the approvals and commits audit log is a separate append-only Postgres store whether or not Langfuse runs. @mark=R
- 2026-11-09 D-45 decision: debunk in v1 is a Hindi voice note plus a FACT CHECK image card with cited sources. No video, so no FFmpeg and no LGPL exception. Video is a later phase that re-enters when a licence-compliant encoder is identified. @mark=R
- 2026-11-09 D-46 decision: the LGPL carve-out is deleted and the ban on LGPL stands without exception. @mark=R
- 2026-11-09 D-47 decision: emergency escalation runs in order: Telegram alerts to both approvers; after 2 minutes without an acknowledgement a WhatsApp alert template to the approvers' personal numbers; then an automated voice call through a telephony API whose provider is named only in private config. @mark=R
- 2026-11-09 D-48 decision: audio that fails ASR is forwarded to the caregiver as audio flagged "could not understand" and is never dropped silently. @mark=R
- 2026-11-09 D-49 decision: public files may name commerce integration targets (Swiggy, Instamart, Zepto, Zomato, Blinkit, Amazon, Flipkart, Uber, Rapido) and open-source infrastructure. They never name cities or languages tied to the family. The rule on model and provider names is POLICY PL-12. @mark=R
- 2026-11-09 D-50 decision: the owner accepts, pending lock, the reviewers' recommendations on PRD questions 2, 4, 5, 7, 8, 9, 10, 12, 14, 15, 16 and 17 and POLICY questions 2, 3, 4 and 5. @mark=R
- 2026-11-09 D-51 decision: PRD question 11 is resolved by D-47, question 13 by D-42, and POLICY question 1 by D-46. @mark=R
- 2026-11-09 D-52 decision: PRD questions 1, 3 and 6 stay open for the owner, with new questions only where the review fixes create a real choice. @mark=R

## Revision decisions of round 2 (review findings C and D, owner decisions of the same day)

- 2026-11-09 D-53 decision: the phone agent and the commerce adapters read untrusted text, see address and order data and can tap, which breaks the two-of-three rule. The owner accepts this as a standing exception. Scope: cart and ride commits only. Conditions: code verifies the final screen for both classes (SPEC safety RQ-87), the hostile-screen suite passes (RQ-88) and shadow mode runs first. The exception lapses while any condition fails. PRD question 1 is closed. @mark=R
- 2026-11-09 D-54 decision: debunk auto-send is gated as recommended: 60 held-out claims pass, and a preview period has ended. The preview period ends at the later of 4 weeks or at least 20 previewed debunks. Only health and scam claims rated FALSE qualify. The standing sign-off expires after 15 days (the earlier recommendation was 30). PRD question 3 is closed. @mark=R
- 2026-11-09 D-55 decision: the number of quote refreshes of an urgent ride before handoff is configurable, default 5. Urgent is a rule in the profile table evaluated by code. The handoff after the last refresh is the expiry of an urgent request. PRD question 6 is closed. @mark=R
- 2026-11-09 D-56 decision: the emergency false-alarm budget is configurable. Defaults: 2 false alarms per approver per week reaching stage b or c, and 6 voice calls per event. A counter test covers both. PRD question 18 is closed. @mark=R
- 2026-11-09 D-57 decision: the words Hindi and Hinglish may be named in public files because they are the product's language. An additional regional language stays generic. This narrows D-49. @mark=R
- 2026-11-09 D-58 decision: commit messages and pull request text carry no AI attribution lines, no co-author trailer and no session link. The rule sits in the contribution part of POLICY. @mark=R
- 2026-11-09 D-59 decision: bugs in the Phase 0 code are listed in the PRD and are not fixed in the design work. @mark=R
- 2026-11-09 D-60 decision: the automatic voice call (stage c) and the template alert to approvers' personal numbers (T-01, stage b) are a standing sign-off by the owner, with scope and expiry (PRD section 6, SPEC assurance RQ-95a). The expiry of 90 days is accepted (D-65). @mark=decided
- 2026-11-09 D-61 decision: the issue taxonomy moves to Phase 5, which changes the "from day one" part of D-11. The facts table and the elder-reaction classifier wait for Phase 3 or later. The semantic claim cache (part of D-27), automated macro repair and sentinels for roles not in use are deferred. Each has a re-entry condition in SPEC evidence section 5. Reminders move earlier, to Phase 1. @mark=R
- 2026-11-09 D-62 decision: if no food or grocery MCP access is granted by 2026-12-21, the owner chooses a fallback (PRD section 8). The date is accepted (D-66). @mark=decided
- 2026-11-09 D-63 decision: superseded drafts are not kept in the public tree. They are held privately, and each file that replaces one records the old sha256 in its `supersedes:` line. @mark=R
- 2026-11-09 D-64 decision: the pilot consent record (POLICY-data PL-05) is required in Phase 0, and the Phase 0 gate shrinks to arrival, radar, the emergency path with fake providers for stages b and c, redaction, the gateway with a durable ledger, and the eval harness on synthetic data. @mark=R
- 2026-11-09 D-65 decision: standing sign-off SO-1 (the automatic emergency voice call and the WhatsApp alert T-01) is accepted. It expires after 90 days, with a renewal reminder to the owner 14 days ahead. After expiry stages b and c send nothing, stage a continues and the owner is alerted. This settles the expiry in D-60. @mark=decided
- 2026-11-09 D-66 decision: the MCP access decision date is 2026-12-21. If Swiggy or Zomato MCP access is not granted by then, Phase 1 falls back to a deep link plus human handoff only. This settles D-62 and replaces its choice between two fallbacks. @mark=decided

## Revision decisions of round 3 (review E)

- 2026-11-09 D-67 decision: Phase 0 gains the minimal audit write path (RQ-56a: append-only table, insert-only role), the SO-1 sign-off row (RQ-95a) and the secret scan (PL-15), because the Phase 0 emergency stages, the show-raw action and the leak scan need them. RQ-56 and RQ-95 are split into a and b parts; the hash chain, off-site head export and verify script (RQ-56b) and the other sign-off classes (RQ-95b) stay in Phase 1. @mark=P
- 2026-11-09 D-68 decision: the history of the code repository was rewritten on 2026-11-09 for privacy. Commit ids from before the rewrite are retired and are not cited; every file in this set cites 3d46456 on branch phase-0/foundation, a tree that differs from the retired one only in removed personal and model-name values. @mark=R

## Decisions after round 3 (design pass for the TRD)

- 2026-11-09 D-69 decision: Hindi voice replies are hybrid. Fixed safety and system replies (emergency, scam, acknowledgement, "caregiver ko bata diya", channel-down notices) are generated once and stored as audio files with their hashes, and they work with every model service down. Dynamic replies (order details, driver name, ETA) use a local open-weight Hindi text-to-speech model on the server, chosen by capability grade and named only in private config. A hosted text-to-speech is a fallback and is used only after its provider terms receipt (POLICY-data PL-20) is recorded. If speech output fails, the reply goes as Hindi text only; a reply is never skipped. @mark=decided

Skipped or not checked: no decision here was checked against a live service, price list or licence text. The interview details that name people, places, amounts or providers were left out by design.
