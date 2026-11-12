A deliberate fault applied to gate row RQ-116 reported invalid; the clean row is run by scripts/gate.py.

kind: receipt
id: RQ-116
revision: c51bc08866850b493d854d8e877c1a8430a2b04a
date: 2026-11-13
command: uv run pytest tests/test_guards.py tests/test_replies.py tests/test_voice_store.py -q
fault: combine requires both keyword and classifier flags instead of either; the chest pain emergency phrase is dropped
result: invalid
mutation_command: uv run pytest tests/test_guards.py -q
clean: verified; exit 0
mutation_radar_and: invalid; exit 1; tests: tests/test_guards.py::test_fake_classifier_adds_flag, tests/test_guards.py::test_classifier_cannot_clear_keyword_hit, tests/test_guards.py::test_classifier_malformed_output_keeps_keyword_result[non_flags], tests/test_guards.py::test_classifier_malformed_output_keeps_keyword_result[kind_none], tests/test_guards.py::test_classifier_kind_is_stripped_and_lowercased
mutation_radar_phrase: invalid; exit 1; tests: tests/test_guards.py::test_phrases_only_added, tests/test_guards.py::test_rule_ids_stable_and_unique, tests/test_guards.py::test_phrase_lists_start_with_base_tuple_in_order[EMERGENCY_ROMAN], tests/test_guards.py::test_zero_width_and_spacing_in_roman
restore: the fault ran on planted inputs in a scratch directory; the tree was not changed
note: written by scripts/record_fault.py; no content
