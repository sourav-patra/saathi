# Architecture

Read the [TRD](design/TRD-saathi-2026-11-09.md) for components, trust domains and design rules, then the [LLD hub](design/LLD-saathi-2026-11-10.md) for modules and work units. This page only orients a new reader.

Overview, as locked in the design set:

1. Saathi is one Postgres database and a few restartable processes: an edge, a core worker, a model worker, and adapters that arrive in later phases.
2. The edge checks the webhook signature and the sender, then inserts an inbox row (and an alert event on an emergency or scam keyword hit) in one transaction before it returns 200.
3. The worker leases inbox rows, runs each request step from the `requests` row and writes the result back. There is no LangGraph checkpointer (D-70), so a restart resumes from the rows.
4. Models propose slots, drafts and flags. Code decides what is sent, approved or committed.
5. The policy engine (`PolicyEngine.authorize_commit`) is the only path to a commit, and every commit re-reads live data.
6. Emergencies and scams go to the humans. Elder content is personal data and never reaches a free model tier.

Skipped or not checked: this overview was not compared with the code in `src/`, only with the TRD and D-70.
