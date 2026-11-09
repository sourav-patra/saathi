# Evals

Every prompt or graph change runs these suites in CI. Results are pushed to Langfuse as dataset runs so that versions can be compared side by side.

| Suite | Phase | Metric | Gate |
|---|---|---|---|
| Safety radar (emergency/scam, Hindi + English) | 0 | recall | **100%** on emergencies |
| ASR on real Hindi/Hinglish voice notes | 0 | WER/CER | picks the ASR engine; regression < +2% |
| Intent + slots | 0 | accuracy, slot F1 | ≥ 95% intent accuracy |
| Policy engine attacks | 0 | must refuse | **100%** (`tests/test_policy.py`) |
| Cart / ride correctness (shadow mode) | 1–2 | exact match | no regression |
| Debunk faithfulness + citation validity | 3 | DeepEval faithfulness | ≥ 0.9, zero fabricated sources |

**Data rule:** real voice notes and transcripts go in `evals/datasets/private/`, which is git-ignored, and are used only with the elder's consent. Only synthetic or anonymised examples are committed.
