# CN_A Fundamental Fresh/Final Acceptance Template

This template defines the evidence slots required for a CN_A fundamental
fresh/final acceptance sample. It is an evidence checklist, not proof that a
fresh run has completed.

## Required Evidence Fields

| Field | Required content |
| --- | --- |
| visible_tools | Provider-captured model-visible tool schema for the worker dispatch. |
| provider_request | Final OpenClaw LLM provider request payload for the dispatch. |
| tool_calls | Model tool calls recorded for the dispatch. |
| data_pack_raw_output | Raw domain-pack tool output captured before worker prose use. |
| provider_attempts | OpenBB provider attempt records for all data-provider calls. |
| field_sources | Field-level source mapping for normalized fundamental values. |
| missing_fields | Explicit missing-field record, including unsupported PE/PB/ROE slots when absent. |
| openviking_receipt | OpenViking material receipt and lineage reference. |
| guard_result | Guard result proving truthfulness checks did not accept fake success. |
| report | Final worker/report artifact tied back to the evidence chain. |

## Sample Row Template

| sample_id | visible_tools | provider_request | tool_calls | data_pack_raw_output | provider_attempts | field_sources | missing_fields | openviking_receipt | guard_result | report |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sample_1 | pending_t_fnd_047_real_run | pending_t_fnd_047_real_run | pending_t_fnd_047_real_run | pending_t_fnd_047_real_run | pending_t_fnd_047_real_run | pending_t_fnd_047_real_run | pending_t_fnd_047_real_run | pending_t_fnd_047_real_run | pending_t_fnd_047_real_run | pending_t_fnd_047_real_run |

