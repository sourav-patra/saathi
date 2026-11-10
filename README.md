# Saathi

**An agentic helper that handles everyday tech tasks for elderly family members, with a human approving every payment.**

Saathi is a working codename; the public name is chosen later (D-90).

The elder sends a WhatsApp voice note, photo or video in Hindi or everyday English. Saathi works out what they want and does everything up to the final step. A caregiver approves that step with one tap in Telegram.

| Saathi will… | How |
|---|---|
| Book rides (auto / cab), choosing the ride type by policy | Deep links + Android phone agent |
| Order groceries and food | Official commerce MCP servers where available, otherwise the phone agent |
| Order household items | Phone agent |
| Debunk viral forwards with a short Hindi explainer video | Fact-check search → script → TTS → code-rendered video |
| Answer "how do I…" and phone-help questions, set reminders, book appointments | Voice-note replies |
| Flag emergencies and scams immediately | Keyword radar + model check; never handled alone |

Saathi is not an emergency service; it alerts your family; call 112 or 108.

## Principles

1. **The LLM proposes; deterministic code commits.** Every order or booking passes the policy engine (`src/saathi/policy/`). It checks for a signed approval token bound to the quote's hash, COD/wallet-only payment, spend caps, an address allowlist and blocked categories.
2. **Cheapest capable tier first.** T0 (no model) → T1 (local model) → T2 (hosted open-weight, no-training provider) → T3/T4 (paid APIs, behind a hard monthly cap). Personal data never reaches free tiers.
3. **Observable and evaluated.** Every request is traced, and every prompt change is gated by evals. Approvals, edits and rejections feed back into evals and memory.
4. **License policy.** Only MIT/Apache-2.0 code, plus permissive infrastructure (PostgreSQL License, BSD, ISC). `scripts/license_audit.py` enforces it in CI.

## Status

Phase 0 build in progress; not usable yet. **Phase 0: foundation.** WhatsApp ingest → speech-to-text → emergency/scam radar → intent triage → caregiver summary in Telegram. Nothing is ordered yet. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Read next

- [00-INDEX.md](00-INDEX.md) lists the current design files.
- [The product requirements](docs/design/PRD-saathi-2026-11-09-r2.md) say what version 1 must do and in which phases.
- [The technical design](docs/design/TRD-saathi-2026-11-09.md) and [the low-level design](docs/design/LLD-saathi-2026-11-10.md) say how Phase 0 is built.

## Quick start (dev)

Install dependencies:

```bash
uv sync
```

Run the tests:

```bash
uv run pytest
```

Run the license audit:

```bash
uv run python scripts/license_audit.py
```

Bring up the full stack (Postgres, Valkey, ClickHouse, SeaweedFS, Langfuse, Ollama, whisper.cpp, Saathi):

```bash
cp .env.example .env && docker compose --env-file .env -f infra/docker-compose.yml up -d
```

## License

Apache-2.0
