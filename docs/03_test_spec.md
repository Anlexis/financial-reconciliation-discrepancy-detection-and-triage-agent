# Test Specification — FIN-C2-071

**Template ID:** FIN-C2-071
**Template Name:** FinancialReconciliationDiscrepancyAgent
**Category:** Cat 2 (nested)

This document is the contract the shipped tests implement. Every row below
corresponds to a test that exists in the repository; nothing is listed that does
not ship.

## 1. Scope

- Unit tests for the caller-data contract, the 4 inner domain nodes, and the 2
  outer gate nodes (`tests/unit/`).
- Composition and end-to-end tests for the inner and outer graphs, including the
  caller-context bridge and live runtime configuration
  (`tests/unit/test_graph_composition.py`).
- End-to-end tests through the real ASGI `/invoke` entry point
  (`tests/integration/test_invoke_contract.py`).
- Boundary tests: input contract, output containment, invoke order, import
  isolation, state safety (`tests/proof_of_boundary/`).

Two properties are asserted throughout rather than in one place, because each
has silently failed in this template before:

1. **The agent works at the trust level its manifest declares.** Every
   end-to-end assertion runs at `VERIFIED_EXTERNAL`. Asserting only at INTERNAL
   hides an agent no real caller can use.
2. **Refusal is enforced by this template, not only by the platform.** The gate
   nodes are driven through `execute()` directly, with no framework wrapper in
   front, so a guarantee that would evaporate wherever a platform gate is
   configured off is visible as a failure here.

## 2. Unit Test Cases

### 2.1 Caller-data contract (`tests/unit/test_contract.py`)

| ID | Case | Expected |
|----|------|----------|
| CON-01 | `NaN`, `Infinity`, `-Infinity` — as strings and as raw floats | refused, every field |
| CON-02 | booleans as numbers | refused (`isinstance(True, int)` is True) |
| CON-03 | non-numeric text, empty, `None`, list, dict | refused |
| CON-04 | out-of-range magnitude | refused |
| CON-05 | valid numeric forms incl. thousands separators | accepted |
| CON-06 | the error message | names the field path, never the value |
| CON-07 | valid request | survives as exactly `reference` / `amount` / `date` |
| CON-08 | unknown record keys (description, note) | dropped, not carried forward |
| CON-09 | non-inert reference (space, pipe, control token, over-length, empty, non-string) | refused |
| CON-10 | malformed or impossible date | refused |
| CON-11 | missing required field | refused, named |
| CON-12 | more than 500 records in a source | refused |
| CON-13 | request with no records at all | refused |
| CON-14 | `match_keys` outside the closed set / empty | refused |
| CON-15 | `match_keys` deduplicated and lower-cased | accepted, normalised |
| CON-16 | `amount_tolerance` non-finite / negative / boolean | refused, named |
| CON-17 | legacy source and field aliases | accepted, normalised |

### 2.2 PreProcessNode (`tests/unit/test_pre_post_process_nodes.py`)

| ID | Case | Expected |
|----|------|----------|
| PRE-01 | valid request | `status=SUCCESS`, `validated_ledger` set |
| PRE-02 | `validated_ledger` content | the inert contract, with resolved match keys |
| PRE-03 | records not written into the masked free-text field | `validated_input` carries no record data |
| PRE-04 | legacy single-blob caller | accepted, with an intake note naming the masked channel |
| PRE-05 | empty / whitespace / non-string free text | `status=ERROR`, `error_log` non-empty |
| PRE-06 | no records supplied | refused — never answered with a clean reconciliation |
| PRE-07 | refusal payload | `formatted_output` is truthy (the error route skips post_process) |
| PRE-08 | contract violation | names the field, never the value |
| PRE-09 | free text over 8000 characters | refused |
| PRE-10 | control tokens `<|im_start|>`, `[INST]`, `<<SYS>>`, directive phrasing | refused |
| PRE-11 | markup-spliced directive (`ig<b>nore all previous instructions`) | refused after the strip |
| PRE-12 | real domain sentences ("Transact as a settlement agent…") | accepted |
| PRE-13 | hostile context field NAME | refused |
| PRE-14 | `\u`-escaped control token | refused (screen runs post-parse) |
| PRE-15 | IBAN / e-mail in the free text | redacted before it is written to state |

### 2.3 MatchRecordsNode (`tests/unit/test_match_records_node.py`)

| ID | Case | Expected |
|----|------|----------|
| MATCH-01 | identical records | 1 matched, 0 unmatched each side |
| MATCH-02 | same reference, different amount, default composite key | unmatched on both sides |
| MATCH-03 | `match_keys=["reference"]` | matches across the amount difference |
| MATCH-04 | `date` in the key, different dates | separated |
| MATCH-05 | duplicate keys | one partner consumed each |
| MATCH-06 | record only in source B | reported in `unmatched_in_b` |
| MATCH-07 | float noise (500.001 vs 500.0) | still matches (rounded to 2dp) |
| MATCH-08 | settings read from state change the partition | two settings, two partitions |
| MATCH-09 | settings absent | declared defaults applied |
| MATCH-10 | ledger absent | empty partition, no crash |
| MATCH-11 | outputs | JSON strings (msgpack-safe checkpointing) |

### 2.4 DetectDiscrepanciesNode (`tests/unit/test_detect_discrepancies_node.py`)

| ID | Case | Expected |
|----|------|----------|
| DET-01 | agreeing pair | no discrepancy |
| DET-02 | amount delta beyond tolerance | one `value_mismatch` with `amount_delta` |
| DET-03 | delta inside tolerance | not flagged |
| DET-04 | amounts agree, dates differ | one `timing_anomaly` |
| DET-05 | `unmatched_in_a` | `missing_in_b` |
| DET-06 | `unmatched_in_b` | `missing_in_a` |
| DET-07 | discrepancy ids | assigned by the pipeline, ordered, unique |
| DET-08 | detail text | carries no caller values |
| DET-09 | declared tolerance | suppresses / admits a mismatch as declared |
| DET-10 | unusable tolerance (NaN, Inf, negative, text, boolean, over-range) | falls back to the declared default rather than comparing against it |

### 2.5 ClassifyAndHypothesizeNode (`tests/unit/test_classify_and_hypothesize_node.py`)

| ID | Case | Expected |
|----|------|----------|
| CLS-01 | `value_mismatch` | `fx_rounding`, high |
| CLS-02 | `timing_anomaly` | `timing_cutoff`, medium |
| CLS-03 | `missing_in_b` | `omission`, high |
| CLS-04 | `missing_in_a` | `duplicate_entry`, high |
| CLS-05 | unknown kind | `data_entry_error`, medium |
| CLS-06 | `|delta| >= 1000` on a value mismatch | escalated to critical |
| CLS-07 | small / negative / boolean delta | escalation only on real magnitude |
| CLS-08 | every output | drawn from the closed category / severity / hypothesis sets |
| CLS-09 | governed prompt | read from state, not from an `execute()` config argument |

### 2.6 GenerateTriageReportNode (`tests/unit/test_generate_triage_report_node.py`)

| ID | Case | Expected |
|----|------|----------|
| GEN-01 | empty set | report states the sources reconcile, `status=SUCCESS` |
| GEN-02 | mixed severities | ranked critical-first |
| GEN-03 | counts | reflect the input |
| GEN-04 | amounts, dates, detail text | absent from the report |
| GEN-05 | render invariant, clean report | satisfied |
| GEN-06 | a row with a non-inert key / free-text hypothesis / unknown severity or category / non-pipeline id / wrong cell count | refused |
| GEN-07 | a discrepancy carrying a non-inert key | no report produced at all |
| GEN-08 | narrative sections | not read as table rows |

### 2.7 PostProcessNode (`tests/unit/test_pre_post_process_nodes.py`, `tests/proof_of_boundary/test_s3_output_gate.py`)

| ID | Case | Expected |
|----|------|----------|
| POST-01 | clean report | `formatted_output=result`, `status=SUCCESS` |
| POST-02 | `sk-`, JWT, Bearer, `password=`, `AKIA…`, `sk_live_…`, a database URI | withheld, `status=ERROR` |
| POST-03 | every output-bearing field on a violation | cleared, and the replacement is truthy |
| POST-04 | a form only the framework detector carries | withheld (the framework detector is the floor) |
| POST-05 | a form only the local patterns carry (`password=`) | withheld (the local set is kept, not replaced) |
| POST-06 | empty / absent report | fails closed with a truthy notice, `status=ERROR` |
| POST-07 | error log | a closed-set reason, never the matched content |

## 3. Integration (`tests/unit/test_graph_composition.py`, `tests/integration/test_invoke_contract.py`)

| ID | Case | Expected |
|----|------|----------|
| INT-01 | inner graph composition | 4 domain nodes, linear START→…→END |
| INT-02 | outer graph composition | inherits `AgentBaseGraph`; `main` is `ReconciliationGraphNode`; `add_edges()` not overridden |
| INT-03 | `merge_output` mapping | inner `triage_report` → outer `result` and `reconciliation_report` |
| INT-04 | end-to-end happy path at VERIFIED_EXTERNAL | triage report with the expected discrepancies |
| INT-05 | `Graph` alias | `Graph is FinancialReconciliationDiscrepancyAgent` |
| INT-06 | bare `Graph()` | loads `config/config.yaml`; an explicit config wins |
| INT-07 | out-of-bounds operator config | `ConfigError` at compile, not a silent default |
| INT-08 | context bridge | `extract_input` stashes the ledger, `_extra_initial_state` seeds it |
| INT-09 | records cross the graph boundary | a caller reference appears in the report |
| INT-10 | unreadable request | refused, never "the sources reconcile" |
| INT-11 | a caller override | changes the discrepancy count end to end |
| INT-12 | ANONYMOUS caller | denied |
| INT-13 | `/health` | served |
| INT-14 | missing / wrong bearer token | 401 with a generic body |
| INT-15 | the committed staging payload | answered, and the answer is computed from it |
| INT-16 | 1 record vs 40 records | the reported numbers move |
| INT-17 | a pure-numeric reference | crosses the whole stack byte-identical |
| INT-18 | every severity path | reachable end to end |
| INT-19 | non-finite amount through the endpoint | refused, value not echoed |
| INT-20 | injection through the endpoint | refused, no report |
| INT-21 | oversized `input_context` | 413 at the adapter |
| INT-22 | credential-shaped `input_context` | 400 naming the field, value not echoed |
| INT-23 | refusal set vs the framework's block set | identical over the filtered context (anti-drift property) |
| INT-24 | undeclared context key | dropped, request still succeeds |
| INT-25 | response body | no credential shape on success, no traceback or source path on refusal |

## 4. Proof-of-Boundary

| ID | Case | Expected |
|----|------|----------|
| PB-IMPORT | `test_import_isolation.py` | no platform-SDK import anywhere under `src/` |
| PB-STATE | `test_state_safety.py` | `State` has no credential-named fields, no `BaseModel` / `InvocationContext` annotations |
| PB-INPUT | `test_s1_input_boundary.py` | malformed, non-finite, non-inert, oversized and hostile input refused; real domain sentences accepted; values never echoed |
| PB-OUTPUT | `test_s3_output_gate.py` | union detector, containment on every output-bearing field, clean error envelope on every refusal path, caller identifiers never in the report |
| PB-6 | `test_pb_invoke_order.py` | the backbone runs initialize → pre_process → main → post_process → finalize at VERIFIED_EXTERNAL, over the committed staging payload |
| PB-7 | `test_pb7_hitl_interrupt_propagation.py` | skipped — this template does not declare `hitl.enabled` |

## 5. Staging Quality Gate

- All unit, integration and boundary tests green in CI (`run-tests`).
- All gate jobs green (scaffold-integrity, import-isolation, composition,
  invoke-chain, credential-scan, forbidden-strings, trust-level, audit-trace,
  cat-consistency, manifest-schema, stub-check, dep-pinning, oss-license).
- First staging invoke over `deploy/invoke_payload.json` returns
  `status: success` with a populated triage report.
