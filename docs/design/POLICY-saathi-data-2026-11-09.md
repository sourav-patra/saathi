---
id: POLICY-saathi-data
kind: contract
governs: personal data in Saathi: classes, routes out of the server, consent per use, erasure and the law
status: draft, new in the r2 set, round-3, TRD review and strategy fold applied, pending owner lock (see docs/LOCKS.md)
upstream: PRD-saathi-2026-11-09-r2.md sha256 141b677d217017de6973d67f2f5d46eb549b2b40464d4f7426f4ee83d0ed15ec; SPEC-saathi-safety-2026-11-09-r2.md sha256 711d673b563105b0115376a8fbb5b2fb6e9a12dfa06afdd4e60103bd53e8bc12; SPEC-saathi-assurance-2026-11-09-r2.md sha256 4d02d7a0ce53e02870ecb794d179f9863522f86df962c60489060c3e1069867a; RECORD-saathi-decisions-2026-11-09.md sha256 41bbe3270e83e1a23535f8903d7e5b60edeea9dae846b0b6feb4c3b152ff82d9
sources: docs/design/RECORD-saathi-decisions-2026-11-09.md (D-nn); sections 2, 3 and 6 of POLICY-saathi-2026-11-09-r1.md (sha256 72906ab9911d42af0645f9c72a9f01b2998209ab3184cf333ac47310fe26e35b), revised here; src/saathi/channels/telegram.py and src/saathi/channels/whatsapp.py on branch phase-0/foundation at 3d46456; the Digital Personal Data Protection Act, 2023 as recalled, text not re-read (R); DOC-PHILOSOPHY.md
checked-at: 3d46456, 2026-11-09. Files read with `git show`. No leak scan, network capture or legal review was run; no platform terms were read.
---

Read section 2 to see which service receives which data, section 1 for the consent, erasure and receipt rules, and section 3 for the law.

# Saathi data policy: classes, routes, consent, erasure and law

## 1. Goal, scope and rules

Goal: personal data leaves the server only by a listed route, only after a recorded reason, and the elder can withdraw. Marks: PRD section 2. The phase of each rule is in the PRD table. This file holds PL-01 to PL-07, PL-20, PL-29, PL-30, PL-32 and, in section 5, PL-33 to PL-35. The record per data use, capacity and withdrawal per use (PL-33 to PL-35) are in section 5.

| ID | Rule | Check | Evidence and state |
|---|---|---|---|
| PL-01 | Data has four classes. Restricted: ID numbers, card data, OTPs. Personal: elder voice, media, transcripts, address, phone number, profile, orders, caregiver identity. Public: public claims and web pages. Synthetic: generated test data. | reader checks that each store and host in section 2 names its class | P |
| PL-02 | Personal data goes only to the destinations in section 2. It never goes to tier F, the public repository, or a log outside the redaction path. | assurance RQ-40, RQ-41, RQ-43 | E: privacy gate tested. N: default and lint. Network enforcement is the separate rule POLICY RQ-102 |
| PL-03 | Restricted data is redacted before it is stored, sent or traced. | assurance RQ-41, RQ-44 | E: redaction tests pass. N: canary test across stores |
| PL-04 | A new outbound host needs a row in section 2 in the same pull request. | assurance RQ-43 test | N |
| PL-05 | No real elder data (voice notes, transcripts, address) is stored or used until the elder's consent is recorded. The first consent record is made first, with the per-use rows and the capacity and guardian fields of PL-33 and PL-34. The elder can withdraw by saying so, per use or in full (requests RQ-74, PL-35); full withdrawal stops processing and starts erasure (PL-06). Until the record exists, only synthetic data is used. | reader checks the record exists before the first real message; a test that refuses a real-data path without a consent row; manual receipt (evidence RQ-100) for the recording | N |
| PL-06 | Erasure follows assurance RQ-42 and RQ-98, with the unreachable stores listed. | assurance RQ-42 test | N |
| PL-07 | Logs, run records and the audit trail hold ids and keyed hashes, not content. Raw text lives only in `raw_text` (assurance RQ-97). Debug traces are not the audit trail. | assurance RQ-56a, RQ-57, RQ-97 | N |
| PL-20 | Provider receipts. Before a hosted model tier, a commerce service, the telephony provider or the uptime monitor receives personal data, a receipt records the provider's terms on use, training and retention, the data and media classes covered, the date and who read them. Until a receipt exists a hosted model tier gets public or synthetic data only. For the messaging platforms see PL-32. For the commerce services, whose use is the product, reading their terms is an owner task listed in PRD section 7, recorded as a manual receipt (evidence RQ-100). | roster loader test (assurance RQ-50, RQ-96); manual receipts | R: no terms were read; hosted terms and model-card licences are unverified (D-37) |
| PL-32 | Platform terms receipts, a Phase 0 gate item. Before the elder is onboarded, the owner reads and records, as a manual receipt (evidence RQ-100): the current WhatsApp Business terms on AI assistants (version, date, the clause cited, how Saathi is described under it, and a decision of allowed, not allowed or unclear) and the Telegram bot terms for personal data (retention and encryption). A decision of not allowed or unclear, or a missing receipt, makes Telegram the active elder channel (platform RQ-107) until a later reading allows WhatsApp. A receipt is valid 90 days and is re-read when the platform announces a change. A receipt holds a clause number and the owner's summary line, no terms text and no personal data. | gate row: a manual receipt dated within 90 days with a signer; a test that registering an elder identity on WhatsApp is refused without a valid allowing receipt; a test with decision unclear selects Telegram | R: reported, not read, that the WhatsApp terms restrict assistants whose main function is AI (D-78); Telegram terms not read |
| PL-29 | Law: DPDP Act 2023 handling (section 3): notice, consent, withdrawal, erasure, security safeguards and breach steps. | section 3 checks | R: law not re-read; legal review N |
| PL-30 | A data-principal request has one path (section 3) with a target of 30 days. | request drill | N |

## 2. Where data leaves the server

This table is the single source for outbound data (PL-04). The code sends nothing else once assurance RQ-43 passes, and the network enforces the same list for model-driven processes (POLICY RQ-102). Retention at each destination is set by that destination and was not read in this pass; every retention cell is N. Every owner cell reads `owner` because there is one maintainer.

| Destination | What leaves | Class | Control and state | Owner |
|---|---|---|---|---|
| WhatsApp Cloud API | every message to and from the elder, including voice, photos, video; alert templates to approvers | personal | unavoidable for the channel; terms R, not read (PL-20); AI assistant terms are an owner check (PRD section 7) | owner |
| Telegram Bot API, caregiver bot | Telegram holds personal data: approval cards (one-line summary, slots, address label, screenshot, price), emergency alerts, the original voice note attached to each card and alert, raw text the caregiver opens with "show raw", digests | personal | no transcript on cards (assurance RQ-97); the platform keeps this data until deleted and erasure cannot reach it (assurance RQ-98); terms R, not read (PL-20) | owner |
| Telegram Bot API, elder bot | every message to and from the elder on the backup channel, including voice, photos and video | personal | a separate bot from the caregiver bot (platform RQ-106); bot chats are not end-to-end encrypted and the platform keeps them until deleted (R, not read); terms receipt (PL-32) | owner |
| Telephony API | approver phone numbers and a fixed alert script; no elder name or content | personal | the provider name and key are private config; receipt before first use (PL-20); standing sign-off SO-1; N | owner |
| Local models (G1) | nothing | n/a | stays on the VPS | owner |
| Hosted open-weight tier (G2) | redacted text, images, screens (media classes per receipt) | personal | receipt required (PL-20); N today | owner |
| Paid multimodal tier (G3) | elder audio and video per receipt | personal | receipt required; capped (assurance RQ-48) | owner |
| Paid reasoning tier (G4) | redacted text, a screenshot after a macro handoff review | personal | receipt required; capped | owner |
| Free tier (F) | public claim text, synthetic eval data, code tasks | public, synthetic | the gateway refuses personal data (PL-02) | owner |
| Commerce MCP servers and apps | items, quantities, delivery address, the elder's contact number, payment method | personal | needed to place an order; the service's terms govern retention; receipt (PL-20); N | owner |
| Fact-check and web search APIs | redacted claim text | public | N: queries not checked for personal content | owner |
| Reverse image search | a forwarded image the router classed as forwarded media, never a photo of a person | personal until classed | runs only after the classification; skipped when the class is unsure (accepted pending lock) | owner |
| Hosted speech output, if used | reply text without address or phone number | personal | the local engine is the default until a receipt exists | owner |
| Private mesh network to the bridge board | debug bridge commands and phone screenshots | personal | the bridge accepts the VPS only (POLICY RQ-90); N | owner |
| Off-site object storage | nightly encrypted database dump; nightly audit chain head | personal (dump), none (head) | encryption checked with `file` on the dump (assurance RQ-37); N | owner |
| External uptime monitor | a request to the health URL every minute; the approvers' contact points for paging; no elder data | personal (contact points) | the URL answers `ok` and nothing else (POLICY RQ-104); receipt before first use (PL-20); N | owner |
| GitHub | code, documents, CI logs | none | POLICY PL-08, PL-12, PL-15 | owner |
| Package, image and model registries | package, image and model names | none | pulled by the operator | owner |

Self-hosted trace and storage services (Postgres, and Langfuse, ClickHouse, Valkey and SeaweedFS once the trace stack runs, D-74) run on the server and send nothing out. Until then traces are JSON-lines files on the server disk. N: no network capture has confirmed this; command `strace -f -e trace=connect` on each container host process. Valkey, ClickHouse and SeaweedFS run only because Langfuse requires them (D-40).

## 3. Law: DPDP Act 2023 (PL-29, PL-30)

Everything here is R: it is recalled from the Act, the text was not re-read, and no lawyer has reviewed it (N). Applicability: personal data processed by an individual for a personal or domestic purpose is outside the Act (R), so v1 for one family may fall outside it. A second household or a public account removes that ground (R). The design follows the obligations from the start so that growth needs no rewrite.

| Topic | Rule in this project | Check |
|---|---|---|
| Notice and consent | Before any real data, the elder is told in Hindi what is collected, why, who receives it (section 2), that Saathi is not an emergency service, and how to withdraw; the consent records (PL-05, PL-33) are made. Consent is per use and recipient, not bundled; where the elder cannot consent, a guardian does (PL-34). | consent row test; notice text reviewed by a reader |
| Withdrawal | The elder or the caregiver on the elder's behalf can withdraw by message or by calling the owner. Withdrawal is as easy as consent: one message and one confirmation (requests RQ-74), for one use or for all (PL-35). It stops that processing at once; full withdrawal starts erasure. | withdrawal drill: stops the pipeline for that household |
| Erasure | Within 30 days of withdrawal or request, erasure runs (assurance RQ-42), the unreachable stores are listed (assurance RQ-98) and the record is kept. | erasure test with canaries |
| Safeguards and breach | Access limits, encryption of backups, the audit chain, egress control and PL-13 to PL-15 (POLICY) are the safeguards. A breach follows the runbook in POLICY section 6, and the owner records who was told and when; the duty to tell the regulator and the people affected is R and needs legal review before any second household. | breach drill record |
| Data-principal request path | One path: the request arrives from the registered elder number or the caregiver; the owner logs a `dsr_request` row (date, kind: access, correction, erasure, nomination, grievance); the system exports a summary of data categories and counts, applies a profile correction through the card flow, or runs erasure; the answer goes out within 30 days; the grievance contact is a private config value. The caregiver is recorded as the nominee. | request drill with each kind |
| Cross-border transfer | Hosted tiers and the messaging platforms process data outside India (R). Each receipt records the location. Whether any destination is restricted by notification is N. | receipt field check |

## 4. Decisions, out of scope and order of work

Decisions, accepted pending owner lock: private vulnerability reporting with a 7-day reply target (POLICY PL-25); approval cards travel through Telegram as a summary plus slots, with the address label shown only where an order needs it and the platform terms read as an owner task; reverse image search only on images classed as forwarded media; when the owner is the builder the reviewer is an independent agent session with no shared context, recorded in the pull request, until a second human reviewer exists (POLICY PL-16). Resolved 2026-11-09: the LGPL exception is removed and PL-09 stands without exception (D-46). There are no open owner questions in this file beyond the terms in PRD section 7.

Out of scope: legal advice, choosing the telephony provider (private config), and the content of the consent notice. Order of work: the PRD phase table.

## 5. Consent per use, capacity and withdrawal (PL-33 to PL-35)

Goal: the elder's agreement says who may hear or read their voice and text and which services may process it, and the elder can take back any one use as easily as they gave it (D-84). PL-05 stays the rule that no real data is used before consent; the record format and the withdrawal detail are here. The DPDP statements are R. PL-33 and PL-34 are Phase 0 because the first record must already have its final shape; PL-35 is Phase 1 because most enforcement points (cards, show raw, tier routing for audio) first run there. The DPDP rows of section 3 point here.

One consent record exists per use and recipient. Enforcement point is where code checks the record before the use happens.

| Use | Recipient | Covers | Enforcement point |
|---|---|---|---|
| `voice_to_primary`, `voice_to_backup` | one approver | the original voice note attached to a card (PRD RQ-19) | card renderer |
| `text_to_primary`, `text_to_backup` | one approver | raw text read through show raw (assurance RQ-97) | show-raw action |
| `hosted_text`, `hosted_image`, `hosted_audio_video` | a tier class (G2 or G4, G2 or G4, G3) | redacted text; images and screenshots; audio and video (assurance RQ-96) | gateway routing, with the receipt of PL-20 |
| `order_data` | commerce services | items, address, contact number, payment method | adapter call |
| `profile` | the household | preferences and address labels kept for later requests | profile writes |
| `alerts_to_approvers` | both approvers | emergency and scam alerts, including the original audio of stage a | escalation service |

`alerts_to_approvers` is the base use: without it there is no service, and withdrawing it is a full withdrawal (PL-35). Every other use is optional, and a refused use does not block the rest.

| ID | Rule | Check | Evidence and state |
|---|---|---|---|
| PL-33 | Consent record per use. The table `control.consent_events` is append-only. Each row holds: `household_id`, `subject` (`elder` or `caregiver`: whose data), `use` and `recipient` (section 2), `event` (`grant` or `withdraw`), `capacity` and `guardian_ref` (PL-34), `language`, `how` (spoken, written or by a guardian), `notice_text_sha256`, `recorded_by` and `recorded_at`. It holds no content. The current state of a use is its latest event. One function `allowed(subject, use, recipient)` answers from the table and fails closed: no row means no. A use with no active grant is denied at its enforcement point and the request takes the summary path (a summary to the caregiver, no hosted call, no audio attached). The notice that is read or spoken lists, per use, who receives what (POLICY-data section 2), says that Saathi is not an emergency service and gives 112 and 108 (product RQ-116), and says how to withdraw. Its text version has a hash recorded in each row. The first record is made with teach-back, one use at a time, in the elder's language (POLICY-data PL-05). Visibility settings can only narrow a grant (product RQ-113). | schema test for the columns and for insert-only; `allowed` tests for grant, withdraw, no row and a wrong recipient; one denial test per enforcement point with a fake; notice test: the text version holds the sentence and the numbers and its hash is in the row; a test that a refused optional use leaves the others active | N. E: the Phase 0 plan has `consents` with scope, language, how, text version, recorded_by and withdrawn_at (TRD ports migration 004), with no recipient |
| PL-34 | Capacity and guardian. Each household record states the elder's `capacity`: `self`, `guardian` or `unknown`. `unknown` counts as consent missing and only synthetic data is used (PL-05). With `guardian`, a lawful guardian gives the consent and the row holds `guardian_ref` (a person id in the household, never a name) and a coded note of how the guardian's authority was confirmed. The elder is still told in plain words, and the elder's refusal of a use stops that use. A household whose capacity is in doubt is onboarded only with a guardian record and advice from a person outside the household (P). Who counts as a lawful guardian is R and needs counsel before a second household. | test that `unknown` and a missing guardian reference deny real data; a `guardian` row without `guardian_ref` is refused by the schema; refusal-by-elder test under `guardian` | N. R: the Act's guardian provisions are recalled |
| PL-35 | Withdrawal per use. The elder, or the caregiver on the elder's behalf, can withdraw one use or all. By message, a phrase set per use is matched by code (as in requests RQ-74) and gets one confirmation, "<use in plain words> band karna chahte hain? haan ya nahi"; on "haan" a `withdraw` event is written and the use stops at once at its enforcement point. Withdrawing one use stops that use; data held only for it is erased within 30 days (POLICY-data PL-30). Withdrawing `alerts_to_approvers`, or every use, is the full withdrawal of requests RQ-74: processing stops, erasure starts (PL-06) and the radar stays on. Withdrawal is as easy as consent: one message, one confirmation. A caregiver's withdrawal on the elder's behalf sets `recorded_by` and is told to the elder in the elder's language. A withdrawn use can be granted again by a new event. | drill per use: after the event the next call to the enforcement point is denied; `voice_to_primary` withdrawn leaves a card with no audio; `hosted_audio_video` withdrawn leaves no G3 route; one message plus one confirmation; use-specific canary erased; full withdrawal reaches RQ-74 | N |

Skipped or not checked: figures (tables carry the data flow); platform terms of WhatsApp, Telegram, hosted tiers, the telephony provider and commerce services; the DPDP text and any legal review; no leak scan, secret scan or network capture ran; the doc lint (evidence RQ-101) does not exist.
