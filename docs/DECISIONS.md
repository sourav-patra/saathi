# Decision records

## ADR-001: Langfuse for observability, evals and prompts

- **Status:** accepted
- **Decision:** Self-host Langfuse with only its MIT features; leave the `ee` license key unset.
- **Why not Arize Phoenix?** It is ELv2, which our license policy bans.
- **Storage swaps:** SeaweedFS replaces MinIO (AGPL), and Valkey replaces Redis 7.4+.

## ADR-002: A thin in-house model gateway instead of LiteLLM

- **Status:** accepted
- **Context:** Every tier we use exposes an chat-completions-compatible `/chat/completions` endpoint. What we actually need from a gateway is:
  - a privacy gate (personal data must never reach free tiers)
  - a hard monthly budget for paid tiers
  - a cascade from the cheapest capable tier upward
  - an exact-match cache

  LiteLLM would add a large dependency surface for features we don't use.
- **Decision:** `src/saathi/llm/gateway.py`, about 250 lines on httpx and fully unit-tested.
- **Revisit when:** we need provider-specific features (e.g. native audio input) that the chat-completions-compatible endpoints don't expose.

## ADR-003: asyncpg instead of psycopg

- **Status:** accepted
- **Context:** psycopg is LGPL-3.0. `langgraph-checkpoint-postgres` depends on it.
- **Decision:** Use asyncpg (Apache-2.0). Phase 1 adds a small custom LangGraph checkpointer on asyncpg, which lets approvals stay pending for hours. Phase 0 uses no checkpointer.

## ADR-004: whisper.cpp server for local ASR

- **Status:** accepted
- **Decision:** Run speech-to-text as a separate container, the whisper.cpp HTTP server (MIT), with ffmpeg inside that container.
- **Why:** Keeps FFmpeg-bundling Python wheels (PyAV, imageio-ffmpeg) out of our dependency tree, and runs well on ARM CPUs.
- **Model choice:** The ASR eval on real Hindi voice notes decides whether a hosted engine becomes the primary.

## ADR-005: Official commerce MCP servers first, phone agent second

- **Status:** accepted
- **Order of preference:** official MCP or API, then a documented deep link, then the Android phone agent (minitap mobile-use + uiautomator2, both Apache/MIT) through a Raspberry Pi ADB bridge.
- **Note:** DroidRun's Portal APK is AGPL, so it is not used.
