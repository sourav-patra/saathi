# Evals

Every prompt or graph change runs these suites in CI. Results are pushed to Langfuse as dataset runs so that versions can be compared side by side.

The suites, their datasets, gates and phases are listed in `evals/registry.toml`, one row per suite. That file is the source of truth.

**Data rule:** real voice notes and transcripts go in `evals/datasets/private/`, which is git-ignored, and are used only with the elder's consent. Only synthetic or anonymised examples are committed.

## Harness (WU-23)

Run `uv run python -m evals.run --all` (or one suite name). `registry.toml` holds one row per suite. A missing dataset, n of 0, an invalid suite or a skip with no named reason exits 1. A suite that is unavailable with a named reason is printed as unavailable and is never counted as passed; `--strict` makes it exit 1. Every rate prints k, n, a bound and the n a target needs. No model score is printed.

The caregiver-written radar phrase set is held out. It is read only from a private path at run time: set `SAATHI_RADAR_HELDOUT_DIR` to a folder holding `manifest.json` and `cases.jsonl`, or place it under the git-ignored `private/` folder. Without it the suite `radar_heldout` reports unavailable. `scan` never loads it, and the repository holds only the synthetic fixtures in `evals/datasets/`.

A plan for a model-variable or paid run is checked with `--plan FILE [--approvals FILE]`; a paid plan without an approval row is refused. Phase 0 starts no model run.
