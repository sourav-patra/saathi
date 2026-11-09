# Roadmap

Each phase ships with an exit test.

| Phase | Scope | Exit criteria |
|---|---|---|
| **0. Foundation** ← *current* | Compose stack (Langfuse, Postgres, Valkey, ClickHouse, SeaweedFS, Ollama, whisper.cpp, Caddy); WhatsApp webhook; Hindi ASR; emergency/scam radar; PII redaction; tiered gateway; intent triage; caregiver summaries in Telegram; Telegram forward intake; policy engine (built ahead of use); license audit in CI | Every elder message gives the caregiver a correct summary within 60 s, and the radar catches 100% of the safety eval set |
| **1. Food + groceries (MCP)** | Commerce MCP adapters; profile store; cart builder; asyncpg checkpointer; approval interrupt and cards; quote hash binding; COD; backup-approver escalation; Hindi TTS voice confirmations; approvals recorded as Langfuse scores | 10 real orders, 0 wrong commits, first-pass approval ≥ 80% |
| **2. Rides (phone agent)** | Pi bridge; mobile-use + uiautomator2; shadow mode; Uber deep link; Rapido and a regional open ride network app (configured privately); ride-type policy; macro recording and replay; payment-screen kill switch | 10 real rides, macro hit rate ≥ 60%, quote card in < 4 min |
| **3. Debunk + help** | Claim extraction (vision/video); fact-check ladder; quick reply (voice note + image card); code-rendered Hindi explainer video; claim cache; tech help; general Q&A | 60-claim eval: verdict ≥ 85% correct, 0 fabricated sources |
| **4. More commerce** | Blinkit, Amazon Now, Amazon, Flipkart via the phone agent; MCP fallbacks; appointments; reminders (template messages) | 10 real orders across apps |
| **5. Grow** | an additional regional language; RAG over documents; weekly digest auto-fixes; multi-household; v2 Instagram debunk account built from the anonymised issue taxonomy | n/a |
