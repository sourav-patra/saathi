---
id: LLD-saathi-tests
kind: contract
governs: the Phase 0 test plan: fixtures, test files and names per module, crash, isolation and canary tests, lint faults, and the gate registry rows; part of LLD-saathi-2026-11-10.md
status: draft, pending review
upstream: TRD-saathi-checks-2026-11-09.md sha256 7d330c42037220e5a2eaf8945f55d9cbaf27304f3f5bae38431bd13c56973b91; TRD-saathi-checks-product-2026-11-10.md sha256 c6d68a965a9bb612b722e0c1252534b0453aa85374692331adddfd0934e720ef; SPEC-saathi-evidence-2026-11-09-r2.md sha256 8287da0716f654469c98397b8fb16bc2d8ab041fb1a94572e9dc245b95741386; LLD-saathi-2026-11-10.md sha256 a4217aeeee13ff47e529f8e567012358e2efe65b32b23f4f482d06470c3ba482
sources: tests/ and evals/ on branch phase-0/foundation at 3d46456 (73 test cases in 53 functions, PRD section 2); the TRD checks parts and build part; DOC-PHILOSOPHY.md
checked-at: 3d46456, 2026-11-10. No test below exists yet; names are proposals.
---

Read section 2 to find the file and test names for a module, section 3 for the crash, isolation and canary tests, and section 4 for the rows `gate.py` holds in Phase 0.

# Saathi LLD tests: files, names, fakes, Postgres and the gate registry

## 1. Fixtures and rules

1. Needs column: `fake` runs with fakes only; `pg` needs Postgres; `strace` needs `strace`; `subprocess` starts the edge and worker. CI provides all. A local run without them skips with a stated reason; under `CI=true` a missing prerequisite fails the test, never skips (RQ-61, RQ-62).
2. Postgres: a CI service container, image pinned by digest (WU-02 records it), `postgres:16` family. `tests/conftest.py` reads `SAATHI_TEST_DSN` (a superuser DSN), builds one template database by `migrate()` plus the role logins, and clones it per test with `CREATE DATABASE ... TEMPLATE`. Seeding uses the superuser (it bypasses row-level security); checks use the role under test.
3. Fakes live in `tests/fakes/`: `FakeClock`, `FakeElderChannel`, `FakeCaregiverChannel`, `FakeEscalator` (scripted: delivered, failed, answered_no_ack, voicemail, key press 1, forged callback), `FakeASR`, `FakeTTS`, `FakeClassifier`, `FakeGateway`, `FakeAudit`, `FakeMediaStore`, each able to fail with every error its port lists.
4. Test hook `saathi/faults.py`: `fault(point: str)` kills the process with `SIGKILL` when `SAATHI_ENV=test` and `SAATHI_FAULT_POINT` equals `point`; it is a no-op otherwise and the import test checks it is inert outside `test`. Points: `edge.after_commit`, `edge.before_commit`, `worker.before_settle`, `alert.between_send_and_mark`.
5. Rule markers: every test of a policy or safety rule carries `@rule("PE-nn")` (`tests/markers.py`). The 32 tests of `tests/test_policy.py` (E at 3d46456) get their markers in WU-01.
6. Deliberate faults: each lint and gate test file holds the fault as a named test (`test_..._fails`) and the fault run is recorded as `RECEIPT-fault-<id>-<date>.md` (RQ-62).

## 2. Test files, per module

Every `Serves` cell is read by `scripts/design_check.py`. Tests are `def test_...` names, `;` separates them, `[x]` is a parameter.

| Id | File | Tests | Module | TQ | Serves | Needs |
|---|---|---|---|---|---|---|
| LT-01 | tests/test_schema.py | test_every_household_table_has_forced_rls; test_planted_table_without_policy_fails; test_key_id_beside_every_encrypted_column; test_tenantless_list_is_exact; test_migrations_apply_in_order; test_changed_applied_file_refused | M04 | TQ-71, 73 | RQ-38, 109, 111a | pg |
| LT-02 | tests/test_cross_tenant.py | test_other_household_row_untouched[role,table]; test_insert_other_household_refused[role,table]; test_unset_household_returns_nothing; test_tx_without_household_raises; test_scan_functions_return_ids_only; test_sender_lookup_returns_no_content | M04 | TQ-71 | RQ-109, 38 | pg |
| LT-03 | tests/test_roles.py | test_core_cannot_update_or_delete_audit_log; test_model_cannot_write_approvals_or_control; test_edge_inserts_only_its_tables; test_edge_cannot_read_raw_text; test_only_readonly_has_bypassrls; test_no_runtime_role_owns_a_table | M04 | TQ-39, 41, 71 | RQ-56a, 95a, 109 | pg |
| LT-04 | tests/test_keys.py | test_seal_open_roundtrip; test_cross_household_decrypt_fails; test_blob_moved_to_other_row_fails_aad; test_edge_keyring_cannot_open; test_stored_bytes_hold_no_canary; test_rotation_reads_both_key_ids; test_rewrap_keeps_plaintext; test_init_one_active_key_per_purpose; test_audit_hmac_uses_audit_key | M05 | TQ-73 | RQ-111a, 56a | fake, pg |
| LT-05 | tests/test_config.py | test_two_households_different_caps; test_new_version_applies_next_load; test_agent_role_cannot_write_config; test_unset_cap_denies; test_no_household_value_read_from_environment; test_short_approval_secret_refused | M02, M06 | TQ-72 | RQ-110; PL-13, 14 | pg |
| LT-06 | tests/test_consent.py | test_no_row_is_false; test_grant_then_withdraw; test_wrong_recipient_denied; test_unknown_capacity_denies; test_guardian_needs_ref; test_events_insert_only; test_withdrawal_applies_next_call; test_denial_at_worker; test_denial_at_alert_audio; test_denial_at_show_raw; test_notice_hash_stored | M06 | TQ-81, 44 | PL-05, 33, 34 | pg |
| LT-07 | tests/test_edge.py | test_bad_signature_401_no_row; test_bad_json_400; test_large_body_413; test_unknown_sender_hash_row_no_outbound; test_sticker_stored_unsupported; test_rate_limit_persists_acks_once_scans_radar; test_text_emergency_alert_in_same_tx_worker_stopped; test_failed_insert_returns_5xx; test_duplicate_id_inserts_nothing_returns_200; test_no_seen_set_in_modules; test_edge_never_reads_or_calls_model | M07 | TQ-01, 02, 08 | RQ-79, 81, 16, 18 | pg |
| LT-08 | tests/test_inbox_crash.py | test_kill_after_200_one_ack_one_request_one_summary[run 1..20]; test_kill_before_commit_retry_inserts_once; test_text_in_raw_text_after_kill; test_replay_creates_nothing; test_worker_kill_before_settle_effects_once | M07, M12 | TQ-01, 03 | RQ-79 | pg, subprocess |
| LT-09 | tests/test_lease.py | test_oldest_first_per_elder; test_not_before_row_does_not_block; test_renewal_keeps_slow_step; test_stalled_worker_writes_nothing; test_fifth_failure_dead_and_alert; test_two_workers_skip_locked; test_ack_pass_within_10s | M04, M12 | TQ-03, 06 | RQ-79 | pg |
| LT-10 | tests/test_channels_contract.py | test_contract[whatsapp,telegram_elder,fake]: each send method, fetch_media, health, each error; test_capability_sets; test_no_platform_name_in_core; test_window_maps_to_proactive_not_allowed; test_core_holds_person_ref_only | M03, M08, M09 | TQ-67 | RQ-105 | fake |
| LT-11 | tests/test_whatsapp.py | test_signature_valid_and_invalid; test_sticker_contact_location_unsupported; test_parse_text_caption_audio_image | M08 | TQ-02 | RQ-81 | fake |
| LT-12 | tests/test_guards.py | the 10 existing tests; test_phrases_only_added; test_removed_phrase_fails; test_fake_classifier_adds_flag; test_classifier_cannot_clear_keyword_hit; test_classifier_error_keeps_keyword_result | M10 | TQ-08 | RQ-16, 17, 18 | fake |
| LT-13 | tests/test_redaction.py | test_each_id_type_redacted[aadhaar,pan,voter,passport,phone,card,otp]; test_30_canaries_per_type; test_assert_clean_raises; test_media_not_claimed_clean | M11 | TQ-14 | RQ-41; PL-03 | fake |
| LT-14 | tests/test_worker.py | test_asr_failure_forwards_audio_with_template; test_asr_text_hit_opens_alert_worker; test_unsupported_kind_summary; test_no_model_called; test_not_processed_before_effects; test_consent_missing_blocks; test_ends_handoff_summary_only | M12 | TQ-03, 12 | RQ-79, 16, 36 | pg |
| LT-15 | tests/test_empty_run.py | test_no_quote_no_sources_no_steps_is_failed; test_unrun_check_is_unavailable | M12, M21 | TQ-42 | RQ-36 | pg |
| LT-16 | tests/test_asr.py | test_fake_asr_scripted_text; test_asr_unavailable_forwards_audio; test_undecodable_forwards_audio; test_decode_timeout_forwards_audio; test_audio_row_unscanned_alert_after_120s | M13 | TQ-12 | RQ-16 | fake |
| LT-17 | tests/test_states.py | test_every_row_has_a_case; test_pairs_outside_table_refused_unchanged; test_terminal_state_not_rewritten; test_later_phase_row_not_callable | M21 | TQ-04 | RQ-36 | pg |
| LT-18 | tests/test_alert_plan.py | test_cadence_constants_literal; test_stage_a_cap_20; test_stage_b_at_120s_three_max; test_stage_c_at_240s_backup_60s_later; test_scam_stage_a_only; test_unscanned_not_in_false_alarm_budget; test_six_counter_scripts | M14 | TQ-09 | RQ-18 | fake |
| LT-19 | tests/test_alerts.py | test_stop_on_tap_each_stage; test_answered_call_without_key_does_not_stop; test_key_press_1_acks_and_names_approver; test_forged_or_replayed_callback_changes_nothing; test_replayed_update_id_ignored; test_restart_mid_stage_resumes; test_telegram_down_goes_to_b_and_c; test_so1_expired_silences_b_c_and_alerts_owner; test_all_caps_spent_blocked_no_human_reached; test_acknowledge_single_winner; test_audio_attached_only_with_consent; test_unscanned_audio_event_after_120s; test_all_models_down_still_alerts_and_plays_clip; test_kill_between_send_and_mark_repeats_same_key | M14 | TQ-09, 10, 11 | RQ-18, 16 | pg |
| LT-20 | tests/test_signoff.py | test_agent_role_write_denied; test_expiry_behaves_as_off; test_14_day_pre_expiry_alert; test_each_use_writes_audit | M14, M24 | TQ-41 | RQ-95a | pg |
| LT-21 | tests/test_audit.py | test_core_update_delete_denied; test_phone_number_in_no_row; test_actor_and_run_required; test_denials_and_failures_kept; test_unavailable_fails_closed; test_detail_rejects_long_string | M17 | TQ-39 | RQ-56a | pg |
| LT-22 | tests/test_voice_store.py | test_hash_mismatch_sends_text_and_alerts_owner; test_missing_file_sends_text; test_unreviewed_clip_refused; test_speech_runtime_down_sends_text; test_no_clip_text_has_name_or_number; test_every_reply_key_has_a_clip | M16 | TQ-13 | RQ-16, 17 | pg |
| LT-23 | tests/test_replies.py | test_emergency_and_scam_hold_marker_112_108; test_consent_notice_holds_marker; test_reply_text_holds_no_name | M16 | TQ-78 | RQ-116 | fake |
| LT-24 | tests/test_cards.py | test_alert_card_slots_only; test_summary_card_buttons; test_show_raw_reads_table_and_audits; test_show_raw_denied_without_consent; test_unregistered_chat_counted_not_acted; test_offset_advances_after_commit | M15 | TQ-15, 53 | RQ-97, 18 | pg |
| LT-25 | tests/test_trace_canary.py | test_canary_only_in_raw_text_and_show_raw; test_unlisted_fields_dropped; test_unwritable_file_does_not_fail_request; test_no_whole_node_output; test_spans_hold_ids_counts_hashes | M18 | TQ-14, 15, 16 | RQ-44, 57, 97; PL-07 | pg |
| LT-26 | tests/test_gateway.py | test_default_sensitivity_personal; test_public_payload_only_for_tier_f; test_privacy_gate_blocks_free_tier; test_floor_per_routing_row; test_fallback_upward_only; test_refusal_not_retried; test_all_rejected_raises; test_schema_retry_once; test_assert_clean_before_hosted; test_public_outside_allowlist_lint | M19 | TQ-17, 18 | RQ-40, 45, 46, 47, 49; PL-02 | fake |
| LT-27 | tests/test_ledger.py | test_unset_cap_disables; test_missing_price_disables; test_concurrent_reservations_stay_under_cap; test_restart_keeps_reservations; test_80_percent_flag; test_failed_call_releases; test_g0_paths_run_over_cap | M19 | TQ-19 | RQ-48 | pg |
| LT-28 | tests/test_roster.py | test_entry_without_receipt_gets_no_personal_data; test_no_route_for_unreceipted_class; test_handoff_per_class; test_tier_loader | M19 | TQ-20 | RQ-45, 50, 96 | fake |
| LT-29 | tests/test_hosts.py | test_base_urls_are_policy_rows; test_unlisted_host_fails; test_strace_connect_only_listed_hosts | M02 | TQ-46 | RQ-43; PL-04 | strace |
| LT-30 | tests/test_health.py | test_body_is_ok_only; test_503_stale_heartbeat; test_503_failed_write; test_503_full_disk; test_unrun_probe_not_ok; test_skew_alert_and_refusal | M07, M20 | TQ-66 | RQ-104 | pg |
| LT-31 | tests/test_env_secrets.py | test_no_secret_in_model_env; test_env_file_mode_600; test_secrets_are_separate_files; test_empty_approval_secret_refused | M02 | TQ-47 | PL-13, 14 | fake |
| LT-32 | tests/test_no_money.py | test_capability_set_has_no_payment; test_import_test_fails_on_planted_payment_import; test_payment_method_outside_set_denied | M25 | TQ-82 | PL-36 | fake |
| LT-33 | tests/test_policy.py | the 32 existing tests with `@rule` markers; test_rule_markers_name_existing_rules | M25 | TQ-28 | RQ-63 | fake |
| LT-34 | tests/test_gate.py | test_registry_matches_prd_phase_table; test_later_phase_row_not_printed; test_missing_receipt_unavailable_exit1; test_auto_false_invalid_exit1; test_aged_receipt_unavailable; test_receipt_without_signer_not_verified; test_list_prints_all_ids; test_not_applicable_needs_reason | M22 | TQ-58 | RQ-100 | fake |
| LT-35 | tests/test_rule_index.py | test_rule_without_test_fails; test_unknown_marker_fails; test_later_phase_rule_not_evaluated | M22 | TQ-58 | RQ-63 | fake |
| LT-36 | tests/test_doc_lint.py | test_fails_per_rule[filename_date,filename_space,dash,filler,size,index_row,record_shape,closing_line,bullet_bold]; test_names_unavailable_without_file; test_clean_fixture_passes; test_design_set_passes | M22 | TQ-59 | RQ-101; PL-19 | fake |
| LT-37 | tests/test_design_check.py | test_repo_is_ok; test_stale_pin_fails; test_deleted_tq_row_unmapped; test_lld_rq_without_module_fails; test_lld_rq_without_test_fails; test_index_base_path_resolves | M26 | TQ-59 | RQ-103; PL-24 | fake |
| LT-38 | tests/test_ci_lint.py | test_unpinned_action_fails; test_digest_pinned_passes | M22 | TQ-61 | RQ-72a | fake |
| LT-39 | tests/test_license_audit.py | test_package_without_licence_fails; test_manifest_with_ffmpeg_fails; test_manifest_with_lgpl_fails; test_unknown_licence_fails; test_exception_never_lgpl; test_dockerfile_convert_flag_fails | M22 | TQ-60 | RQ-71; PL-09, 10, 11 | fake |
| LT-40 | tests/test_evals.py | test_row_per_suite; test_missing_dataset_fails; test_empty_dataset_fails; test_report_prints_k_n_bound; test_equal_authors_fail_manifest; test_paid_run_needs_approval_row; test_inconclusive_rule; test_no_model_score_in_report | M23 | TQ-56 | RQ-58, 59, 60, 61, 99 | fake |
| LT-41 | tests/test_fault_receipts.py | test_every_auto_row_has_a_fault_receipt | M22 | TQ-57 | RQ-62 | fake |
| LT-42 | tests/test_leak_scan.py | test_planted_key_fails; test_names_file_hit_fails; test_media_extension_fails; test_clean_tree_passes | M22 | TQ-63 | PL-08, 12, 15 | fake |
| LT-43 | tests/test_commit_msg.py | test_attribution_trailer_fails; test_session_link_fails; test_plain_message_passes | M22 | TQ-63 | PL-31 | fake |
| LT-44 | tests/test_repo_settings.py | test_unavailable_without_gh_auth; test_each_check_id_reads_one_setting | M22 | TQ-63 | PL-16, 17, 18, 21, 22 | fake |
| LT-45 | tests/test_pilot_counts.py | test_free_text_field_fails; test_output_is_counts_only; test_receipt_validator | M22 | TQ-79 | RQ-117, 118, 119 | fake |
| LT-46 | tests/test_admin.py | test_commands_use_owner_role; test_signoff_grant_writes_row; test_clips_review_needs_receipt; test_whatsapp_register_needs_terms_receipt | M24 | TQ-41, 80 | RQ-95a, 110, 111a; PL-32 | pg |
| LT-47 | tests/test_flows.py | test_voice_note_to_summary_and_ack; test_emergency_to_stage_a_and_fake_b_c; test_heartbeat_ok_then_503; test_switch_is_phase_1_xfail | M07, M12, M14, M20 | TQ-01, 09, 66 | RQ-79, 18, 104 | pg |
| LT-48 | manual: reader check of POLICY-data section 2 | each store and host names its class; receipt `RECEIPT-pl-01-<date>.md` | M01 | TQ-64 | PL-01 | manual |

## 3. Crash, isolation and canary tests

Crash and kill (LT-08, LT-19, run 20 times each in CI): the test starts `saathi-edge` and `saathi-worker` as subprocesses on a free port with `SAATHI_FAULT_POINT` set, sends a signed fixture webhook, waits for the 200, then restarts both and runs the worker to quiescence. Asserts: one `inbox` row, its `raw_text` part decrypts to the fixture text, one request `r_<inbox>` in `handoff` with reason `summary_only`, one caregiver summary and one acknowledgement in the fakes (the fake records `IdemKey`s and refuses a second send with the same key), no card. A kill at `edge.before_commit` leaves no row and no 200; the second delivery inserts once. A kill at `worker.before_settle` or `alert.between_send_and_mark` repeats the send with the same key, so the fake sees one effect.

RLS isolation (LT-02): for each table from `pg_class` with `household_id` and each runtime role, seed households A and B as superuser, connect as the role, set A, then select, update and delete B's row (zero rows or `InsufficientPrivilege`, never data), insert a B row (refused), and with no setting select (zero rows). The planted-table fault creates `planted(household_id text)` with no policy in a scratch database and expects `find_unprotected_tables()` to name it.

Privacy canary (LT-25): the run feeds a text and an ASR transcript holding `CANARY-<uuid>` and a synthetic phone number through edge, worker and alerts with fakes, then reads every table by `COPY ... TO STDOUT`, every trace file, every fake channel payload and the captured logs. The canary appears only in the decrypted `raw_text` and in the `show raw` reply; the phone number appears in no row (only its keyed hash). The fault: a patched exporter that writes the transcript makes the test fail.

Lint faults (RQ-62): `test_doc_lint` holds one fixture per rule that must fail with that rule's name; `test_design_check` runs the script on a copy with a stale pin, a deleted TQ row and an LLD row with no test; `test_ci_lint` plants an unpinned `uses:`; `test_license_audit` plants an LGPL manifest; `test_gate` sets a command to `false` and ages a receipt. Each result is saved as `RECEIPT-fault-<id>-<date>.md` by `scripts/record_fault.py`.

## 4. Gate registry rows for Phase 0

`scripts/gate_registry.toml` holds one row per id; ids that share a command are listed together here and written one per row. `auto` rows run the command; `manual` rows need a dated receipt with signer and revision. `uv run python scripts/gate.py --phase 0 --list` must print every id below (RQ-100).

| Ids | Kind | Command or receipt |
|---|---|---|
| RQ-16 | auto | `uv run python -m evals.run radar` (needs the owner-authored set, C-9 in the hub) |
| RQ-17, 116 | auto | `uv run pytest tests/test_guards.py tests/test_replies.py tests/test_voice_store.py -q` |
| RQ-18 | auto | `uv run pytest tests/test_alert_plan.py tests/test_alerts.py tests/test_signoff.py -q` |
| RQ-36 | auto | `uv run pytest tests/test_empty_run.py tests/test_states.py -q` |
| RQ-40, 45, 46, 47, 48, 49, 50, 96; PL-02 | auto | `uv run pytest tests/test_gateway.py tests/test_ledger.py tests/test_roster.py -q` |
| RQ-41; PL-03 | auto | `uv run pytest tests/test_redaction.py -q` |
| RQ-43; PL-04 | auto | `uv run pytest tests/test_hosts.py -q` |
| RQ-44, 57, 97; PL-07 | auto | `uv run pytest tests/test_trace_canary.py tests/test_cards.py -q` |
| RQ-56a | auto | `uv run pytest tests/test_audit.py tests/test_roles.py -q` |
| RQ-58, 59, 60, 61, 99 | auto | `uv run pytest tests/test_evals.py -q` |
| RQ-62 | auto | `uv run pytest tests/test_fault_receipts.py -q` |
| RQ-63 | auto | `uv run python scripts/rule_index.py --phase 0` |
| RQ-71; PL-09, 10, 11 | auto | `uv run python scripts/license_audit.py --packages --dockerfiles --manifest tests/fixtures/image_manifest.txt` |
| RQ-72a | auto | `uv run python scripts/ci_lint.py .github/workflows` |
| RQ-79, 81, 105 | auto | `uv run pytest tests/test_inbox_crash.py tests/test_lease.py tests/test_edge.py tests/test_channels_contract.py tests/test_whatsapp.py -q` |
| RQ-95a | auto | `uv run pytest tests/test_signoff.py tests/test_admin.py -q` |
| RQ-100 | auto | `uv run pytest tests/test_gate.py -q` |
| RQ-101; PL-19 | auto | `uv run python scripts/doc_lint.py 00-INDEX.md README.md docs` |
| RQ-103; PL-24 | auto | `python3 scripts/design_check.py` |
| RQ-104 | manual | `RECEIPT-rq-104-<date>.md`: the drill (core stopped, both approvers paged); tests in `tests/test_health.py` |
| RQ-109, 111a | auto | `uv run pytest tests/test_schema.py tests/test_cross_tenant.py tests/test_keys.py -q` |
| RQ-110; PL-13, 14 | auto | `uv run pytest tests/test_config.py tests/test_env_secrets.py -q` |
| RQ-117 | auto | `uv run pytest tests/test_pilot_counts.py -q` |
| RQ-118, 119 | manual | pilot receipts: aggregate counts; exit outcome go, narrow or stop |
| PL-01 | manual | reader check of POLICY-data section 2 (LT-48) |
| PL-05 | manual | `RECEIPT-pl-05-<date>.md`: first consent record, teach-back done |
| PL-33, 34 | auto | `uv run pytest tests/test_consent.py -q` |
| PL-08, 12, 15 | auto | `uv run python scripts/leak_scan.py --names-file "$SAATHI_NAMES_FILE"` (unavailable without the file) |
| PL-16, 17, 18, 21, 22 | auto | `uv run python scripts/repo_settings.py --check <id>` (unavailable without an authenticated `gh`) |
| PL-31 | auto | `uv run pytest tests/test_commit_msg.py -q` |
| PL-32 | manual | `RECEIPT-pl-32-<date>.md`: WhatsApp and Telegram terms read, version and date |
| PL-36 | auto | `uv run pytest tests/test_no_money.py -q` |

Skipped or not checked: no test, fixture or receipt exists; the parameter lists of LT-02 and LT-10 were not counted; strace and `opusdec` availability on the CI runner was not checked; the 20 run counts come from the TRD build part and were not tuned.
