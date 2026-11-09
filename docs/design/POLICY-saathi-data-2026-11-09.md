---
id: POLICY-saathi-data
kind: contract
governs: personal data in Saathi: classes, routes out of the server, consent, erasure and the law
status: draft, new in the r2 set, round-3 fixes applied, pending owner lock (see docs/LOCKS.md)
upstream: PRD-saathi-2026-11-09-r2.md sha256 341ecc305674155b388184dd59323faeda447676c01d9d002c188b9aeadfdd8b; SPEC-saathi-safety-2026-11-09-r2.md sha256 6b9a198e72f618e26af03ff63e1555abf9c056e7fcf8a3e4cdff7fd078b9c202; SPEC-saathi-assurance-2026-11-09-r2.md sha256 be50717893ed345c507fcc856d7f8694931b520e3bf4110200b922519007137e; RECORD-saathi-decisions-2026-11-09.md sha256 a1ec6109610478a4582467cebe12c11d4bd266b89b35a89fdbe44dbde9f86f45
sources: docs/design/RECORD-saathi-decisions-2026-11-09.md (D-nn); sections 2, 3 and 6 of POLICY-saathi-2026-11-09-r1.md (sha256 72906ab9911d42af0645f9c72a9f01b2998209ab3184cf333ac47310fe26e35b), revised here; src/saathi/channels/telegram.py and src/saathi/channels/whatsapp.py on branch phase-0/foundation at 3d46456; the Digital Personal Data Protection Act, 2023 as recalled, text not re-read (R); DOC-PHILOSOPHY.md
checked-at: 3d46456, 2026-11-09. Files read with `git show`. No leak scan, network capture or legal review was run; no platform terms were read.
---

Read section 2 to see which service receives which data, section 1 for the consent, erasure and receipt rules, and section 3 for the law.

# Saathi data policy: classes, routes, consent, erasure and law

## 1. Goal, scope and rules

Goal: personal data leaves the server only by a listed route, only after a recorded reason, and the elder can withdraw. Marks: PRD section 2. The phase of each rule is in the PRD table. This file holds PL-01 to PL-07, PL-20, PL-29 and PL-30.

| ID | Rule | Check | Evidence and state |
|---|---|---|---|
| PL-01 | Data has four classes. Restricted: ID numbers, card data, OTPs. Personal: elder voice, media, transcripts, address, phone number, profile, orders, caregiver identity. Public: public claims and web pages. Synthetic: generated test data. | reader checks that each store and host in section 2 names its class | P |
| PL-02 | Personal data goes only to the destinations in section 2. It never goes to tier F, the public repository, or a log outside the redaction path. | assurance RQ-40, RQ-41, RQ-43 | E: privacy gate tested. N: default and lint. Network enforcement is the separate rule POLICY RQ-102 |
| PL-03 | Restricted data is redacted before it is stored, sent or traced. | assurance RQ-41, RQ-44 | E: redaction tests pass. N: canary test across stores |
| PL-04 | A new outbound host needs a row in section 2 in the same pull request. | assurance RQ-43 test | N |
| PL-05 | No real elder data (voice notes, transcripts, address) is stored or used until the elder's consent is recorded. The pilot consent record is made first. It holds date, scope (voice, images, video, profile, ordering, alerts to approvers), language, how it was given, the consent text version and who recorded it, and holds no content. The elder can withdraw by saying so (requests RQ-74); withdrawal stops processing and starts erasure (PL-06). Until the record exists, only synthetic data is used. | reader checks the record exists before the first real message; a test that refuses a real-data path without a consent row; manual receipt (evidence RQ-100) for the recording | N |
| PL-06 | Erasure follows assurance RQ-42 and RQ-98, with the unreachable stores listed. | assurance RQ-42 test | N |
| PL-07 | Logs, run records and the audit trail hold ids and keyed hashes, not content. Raw text lives only in `raw_text` (assurance RQ-97). Debug traces are not the audit trail. | assurance RQ-56a, RQ-57, RQ-97 | N |
| PL-20 | Provider receipts. Before a hosted model tier, Telegram, WhatsApp, a commerce service or the telephony provider receives personal data, a receipt records the provider's terms on use, training and retention, the data and media classes covered, the date and who read them. Until a receipt exists a hosted model tier gets public or synthetic data only. For Telegram, WhatsApp and the commerce services, whose use is the product, reading their terms is an owner task listed in PRD section 7, recorded as a manual receipt (evidence RQ-100). | roster loader test (assurance RQ-50, RQ-96); manual receipts | R: no terms were read; hosted terms and model-card licences are unverified (D-37) |
| PL-29 | Law: DPDP Act 2023 handling (section 3): notice, consent, withdrawal, erasure, security safeguards and breach steps. | section 3 checks | R: law not re-read; legal review N |
| PL-30 | A data-principal request has one path (section 3) with a target of 30 days. | request drill | N |

## 2. Where data leaves the server

This table is the single source for outbound data (PL-04). The code sends nothing else once assurance RQ-43 passes, and the network enforces the same list for model-driven processes (POLICY RQ-102). Retention at each destination is set by that destination and was not read in this pass; every retention cell is N. Every owner cell reads `owner` because there is one maintainer.

| Destination | What leaves | Class | Control and state | Owner |
|---|---|---|---|---|
| WhatsApp Cloud API | every message to and from the elder, including voice, photos, video; alert templates to approvers | personal | unavoidable for the channel; terms R, not read (PL-20); AI assistant terms are an owner check (PRD section 7) | owner |
| Telegram Bot API | Telegram holds personal data: approval cards (one-line summary, slots, address label, screenshot, price), emergency alerts, the original voice note attached to each card and alert, raw text the caregiver opens with "show raw", digests | personal | no transcript on cards (assurance RQ-97); the platform keeps this data until deleted and erasure cannot reach it (assurance RQ-98); terms R, not read (PL-20) | owner |
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
| GitHub | code, documents, CI logs | none | POLICY PL-08, PL-12, PL-15 | owner |
| Package, image and model registries | package, image and model names | none | pulled by the operator | owner |

Self-hosted trace and storage services (Langfuse, ClickHouse, Valkey, SeaweedFS, Postgres) run on the server and send nothing out. N: no network capture has confirmed this; command `strace -f -e trace=connect` on each container host process. Valkey, ClickHouse and SeaweedFS run only because Langfuse requires them (D-40).

## 3. Law: DPDP Act 2023 (PL-29, PL-30)

Everything here is R: it is recalled from the Act, the text was not re-read, and no lawyer has reviewed it (N). Applicability: personal data processed by an individual for a personal or domestic purpose is outside the Act (R), so v1 for one family may fall outside it. A second household or a public account removes that ground (R). The design follows the obligations from the start so that growth needs no rewrite.

| Topic | Rule in this project | Check |
|---|---|---|
| Notice and consent | Before any real data, the elder is told in Hindi what is collected, why, who receives it (section 2) and how to withdraw; the consent record (PL-05) is made. Consent is per scope, not bundled. | consent row test; notice text reviewed by a reader |
| Withdrawal | The elder or the caregiver on the elder's behalf can withdraw by message or by calling the owner. Withdrawal is as easy as consent: one message and one confirmation (requests RQ-74). It stops processing at once and starts erasure. | withdrawal drill: stops the pipeline for that household |
| Erasure | Within 30 days of withdrawal or request, erasure runs (assurance RQ-42), the unreachable stores are listed (assurance RQ-98) and the record is kept. | erasure test with canaries |
| Safeguards and breach | Access limits, encryption of backups, the audit chain, egress control and PL-13 to PL-15 (POLICY) are the safeguards. A breach follows the runbook in POLICY section 6, and the owner records who was told and when; the duty to tell the regulator and the people affected is R and needs legal review before any second household. | breach drill record |
| Data-principal request path | One path: the request arrives from the registered elder number or the caregiver; the owner logs a `dsr_request` row (date, kind: access, correction, erasure, nomination, grievance); the system exports a summary of data categories and counts, applies a profile correction through the card flow, or runs erasure; the answer goes out within 30 days; the grievance contact is a private config value. The caregiver is recorded as the nominee. | request drill with each kind |
| Cross-border transfer | Hosted tiers and the messaging platforms process data outside India (R). Each receipt records the location. Whether any destination is restricted by notification is N. | receipt field check |

## 4. Decisions, out of scope and order of work

Decisions, accepted pending owner lock: private vulnerability reporting with a 7-day reply target (POLICY PL-25); approval cards travel through Telegram as a summary plus slots, with the address label shown only where an order needs it and the platform terms read as an owner task; reverse image search only on images classed as forwarded media; when the owner is the builder the reviewer is an independent agent session with no shared context, recorded in the pull request, until a second human reviewer exists (POLICY PL-16). Resolved 2026-11-09: the LGPL exception is removed and PL-09 stands without exception (D-46). There are no open owner questions in this file beyond the terms in PRD section 7.

Out of scope: legal advice, choosing the telephony provider (private config), and the content of the consent notice. Order of work: the PRD phase table.

Skipped or not checked: figures (tables carry the data flow); platform terms of WhatsApp, Telegram, hosted tiers, the telephony provider and commerce services; the DPDP text and any legal review; no leak scan, secret scan or network capture ran; the doc lint (evidence RQ-101) does not exist.
