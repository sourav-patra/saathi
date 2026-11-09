# Architecture

## Flow

```
Elder (WhatsApp, helper number)                         Caregiver (Telegram)
   │ voice / image / video / text                              ▲ approve · edit · reject
   ▼                                                           │
┌──────────────────────────── VPS (docker compose) ────────────┼──────────────────────┐
│ Caddy (HTTPS, webhook only) ─► FastAPI webhook ─► LangGraph pipeline                 │
│                                                    │                                 │
│  perceive   ASR (whisper.cpp) · vision (Phase 3) · media-hash cache                  │
│  radar      T0 emergency/scam keywords (+ T1 model later) ──► alert humans, END      │
│  redact     deterministic PII redaction before any model or trace                    │
│  understand intent + slots, cheapest tier first (T1 → T2 → T4)                       │
│  ── Phase 1+ ──────────────────────────────────────────────────────────────────────  │
│  plan       intent subgraph builds a Quote (commerce MCP / deep link / phone agent)  │
│  approve    LangGraph interrupt → Telegram card → signed ApprovalToken               │
│  commit     PolicyEngine.authorize_commit(approved, LIVE re-read, token) → commit    │
│  confirm    Hindi voice note + text to the elder, generated from the confirmation    │
│                                                                                      │
│  Langfuse (traces, evals, prompts) · Postgres+pgvector · Valkey · ClickHouse ·       │
│  SeaweedFS · Ollama (T1)                                                             │
└───────────────────────────────────────┬──────────────────────────────────────────────┘
                                        │ Tailscale (Phase 2)
                         Raspberry Pi bridge (ADB server) ─USB─ spare Android phone
```

## Invariants

1. **The LLM proposes; deterministic code commits.** Only `PolicyEngine.authorize_commit` can allow a commit. It requires all of the following:
   - an HMAC-signed approval token whose hash matches the approved quote
   - a live re-read quote that matches the approved one (rides may drift within a fare tolerance)
   - a quote that hasn't expired
   - COD/wallet payment only
   - spend within caps, unless a second confirmation was given
   - an allowlisted address
   - no blocked categories
   - the kill switch (`/pause`) is off
2. **External text is data, never instructions.** This covers transcripts, OCR, web pages and phone screens. Each intent can use only its own tools.
3. **Emergencies and scams short-circuit** to the humans. The bot never handles them alone.
4. **Personal data never reaches free tiers.** The gateway enforces this in code with `PrivacyViolation`.
5. **Paid model spend has a hard monthly cap** (`BudgetExceeded`). When it's hit, the request falls back to a summary for the caregiver.
6. **Tracing never breaks a request**, and traces are PII-masked.

## Model tiers

Each tier is an chat-completions-compatible endpoint configured in `.env`. An empty value disables the tier.

| Tier | What | Personal data? | Paid? |
|---|---|---|---|
| T0 | Code: radar, policy, caches, macro replay, templates | n/a | no |
| T1 | Local model via Ollama on the VPS CPU | yes | no |
| T2 | Hosted open-weight provider with a no-training policy | yes | flat-rate or low |
| T2_FREE | Free-tier endpoints | **no** (enforced) | no |
| T3 | Paid multimodal API (audio/video understanding) | yes | yes (capped) |
| T4 | Paid frontier reasoning model, for hard cases only | yes | yes (capped) |

## Memory

| Layer | Store | Holds |
|---|---|---|
| Profile | Postgres tables (`profile_facts`, `addresses`) | Source of truth. Agent-proposed changes need approval. |
| Episodic | pgvector, managed by a memory library (Phase 1+) | Past requests and their outcomes |
| Procedural | Phone-agent macros in Postgres (Phase 2) | Recorded app flows the agent can replay |
| Knowledge | pgvector (Phase 3+) | Fact-check claim cache; a RAG library once the data grows |

## Caching

Caches are listed cheapest first:

| Cache | Used for |
|---|---|
| Provider prompt caching | Every hosted call |
| Media SHA-256 cache | Repeated voice notes, images and videos |
| Exact LLM cache | Calls at temperature 0 (in-memory now, Valkey later) |
| Semantic claim cache | Viral forwards (**never** used for actions) |
| Phone-macro replay | Repeated app flows |
| 15-minute catalog cache | Product and price lookups |

Every commit re-reads live data, whatever the caches hold.

## License policy

- **Allowed:** MIT and Apache-2.0, plus permissive infrastructure licenses (PostgreSQL, BSD, ISC) and OFL for fonts.
- **Banned:** GPL, LGPL, AGPL, SSPL, ELv2, BUSL, RSAL and non-commercial licenses.
- **Enforcement:** `scripts/license_audit.py` runs in CI. Justified exceptions for unmodified, file-level-copyleft transitive dependencies live in `scripts/license_exceptions.toml`.

| Not used | License | Use instead |
|---|---|---|
| Arize Phoenix | ELv2 | Langfuse |
| `langgraph-api` | ELv2 | the LangGraph library |
| MinIO | AGPL | SeaweedFS |
| Redis 7.4+ | RSAL/SSPL | Valkey |
| psycopg | LGPL | asyncpg |
| python-telegram-bot | LGPL | aiogram |
| Neo4j | GPL | pgvector |
| FalkorDB | SSPL | pgvector |
| Remotion | custom | MoviePy |
| DroidRun Portal APK | AGPL | uiautomator2 + mobile-use |
