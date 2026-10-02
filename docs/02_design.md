# Template Design Specification — FIN-C2-071

**Template ID:** FIN-C2-071
**Template Name:** FinancialReconciliationDiscrepancyAgent
**Category:** Cat 2 (multi-step domain workflow)
**Industry:** FIN

## Position in AgentCore Architecture

| Layer | Value |
|-------|-------|
| Agent Class | `FinancialReconciliationDiscrepancyAgent` (alias `Graph`) |
| L1 Base (framework base class) | `AgentBaseGraph` — direct framework inheritance |
| Inner graph base | `BaseGraph` — `DomainWorkflowGraph` |
| Pattern | Cat 2 two-layer nested architecture (outer fixed 5-node backbone + `GraphNode` in the `main` slot wrapping an inner `BaseGraph` domain workflow) |

- **Three-Layer Separation:**
  - State: flat `TypedDict` composition (no Pydantic — msgpack incompatible);
    structured fields stored as JSON strings via `to_json()` / `from_json()`
  - Node: framework inheritance via `FunctionNode` (override `execute(self, state) -> dict` only —
    `BaseNode.__call__` supplies the state and nothing else, so a node parameter
    named `config` is never populated at runtime)
  - Graph: composition (`register_nodes()` for node substitution); outer
    `add_edges()` is NOT overridden

## Architecture Overview

### Outer backbone (AgentBaseGraph)

```
START → initialize → pre_process → main → {route} → post_process → finalize → END
                                     ↓ (retry, max 3)
                                   pre_process
```

| Slot | Class | Responsibility |
|------|-------|----------------|
| initialize | InitializeNode (framework default) | session_id, trust_level, schema_version |
| pre_process | `PreProcessNode` | caller-data contract validation; injection screen; identifier redaction of the free-text channel → `validated_ledger`, `validated_input` |
| main | `ReconciliationGraphNode` (`GraphNode`) | stashes the validated ledger on the context bridge, delegates to inner `DomainWorkflowGraph`, maps inner `triage_report` → outer `result` |
| post_process | `PostProcessNode` | output content gate over `result`; on a violation returns ERROR and clears every output-bearing field |
| finalize | FinalizeNode (framework default) | response_metadata, total_time_ms |

### Inner graph (DomainWorkflowGraph — BaseGraph, linear)

```
START → match_records → detect_discrepancies → classify_and_hypothesize → generate_triage_report → END
```

| Node | Responsibility | Input State | Output State |
|------|----------------|-------------|--------------|
| `MatchRecordsNode` | Composite-key matching (reference / amount / date) of the two validated record sets; partition matched / unmatched-in-A / unmatched-in-B | `validated_ledger`, `domain_settings` | `matched_records`, `unmatched_records` |
| `DetectDiscrepanciesNode` | Detect missing entries, value mismatches beyond `amount_tolerance`, value-date anomalies | `matched_records`, `unmatched_records`, `domain_settings` | `discrepancy_set` |
| `ClassifyAndHypothesizeNode` | Classify each discrepancy (timing_cutoff / fx_rounding / duplicate_entry / omission / data_entry_error) + hypothesis + severity; deterministic, with a governed prompt carried for the model seam | `discrepancy_set`, `domain_settings` | `classified_discrepancies` |
| `GenerateTriageReportNode` | Severity-rank, compose the Markdown triage report, and enforce the render invariant over the rendered table | `classified_discrepancies` | `triage_report`, `status` |

### Data Flow

```
input (free text)  +  input_context (records)
  → PreProcessNode                        → validated_input (redacted), validated_ledger
  → ReconciliationGraphNode.extract_input → set_caller_ledger(...)  [context bridge]
      → DomainWorkflowGraph._extra_initial_state() seeds validated_ledger + domain_settings
        → match_records                   → matched_records / unmatched_records
        → detect_discrepancies            → discrepancy_set
        → classify_and_hypothesize        → classified_discrepancies
        → generate_triage_report          → triage_report  (render invariant enforced)
     get_output() → {triage_report, status, ...}
  → ReconciliationGraphNode.merge_output  → result, reconciliation_report
  → PostProcessNode                       → formatted_output (gated)
```

**Why the records travel on `input_context`.** The framework masks personal-data
shapes in `user_input` and `validated_input` at every node boundary, before any
template code runs. Measured on the shipped wheel: an ordinary Title Case
description ("Wire Transfer Settlement") is rewritten to `[MASKED]`, and a bare
12-digit account number is rewritten unquoted inside the JSON so the payload no
longer parses — at which point the matcher sees no records and the report says
the two sources reconcile. `input_context` is not masked. The SDK's `GraphNode`
does not forward it to the inner graph, so the validated contract crosses the
boundary through the ContextVar bridge in `src/graph/context_bridge.py`.

### State Definition

| Field | Type | Purpose | Layer |
|-------|------|---------|-------|
| `validated_input` | `Optional[str]` | redacted free-text description (never the records) | outer |
| `validated_ledger` | `Optional[str]` (JSON) | the validated caller contract | outer → inner (bridged) |
| `domain_settings` | `Optional[str]` (JSON) | resolved `match_keys` / `amount_tolerance` / `system_prompt` for the run | inner |
| `reconciliation_report` | `Optional[str]` | final report, mapped from inner `triage_report` | outer |
| `matched_records` | `Optional[str]` (JSON) | matched pair summaries | inner |
| `unmatched_records` | `Optional[str]` (JSON) | `{unmatched_in_a, unmatched_in_b}` | inner |
| `discrepancy_set` | `Optional[str]` (JSON) | raw detected discrepancies | inner |
| `classified_discrepancies` | `Optional[str]` (JSON) | discrepancies + category / hypothesis / severity | inner |
| `triage_report` | `Optional[str]` | rendered triage report | inner |
| `intake_notes` | `Optional[str]` (JSON) | closed-set intake notes (no caller data) | both |
| `trace_id` / `correlation_id` | `Optional[str]` | framework-managed tracing | both |

**State Constraints (mandatory):**
- Flat `TypedDict` only (primitives + JSON-serialisable types).
- Structured fields stored as JSON STRINGS (msgpack safety in the checkpoint).
- No credentials and no raw account-holder identifiers in State.
- `InvocationContext` via `config["configurable"]` only (never in State).
- No Pydantic models / dataclasses / arbitrary Python objects.

## Runtime configuration

`config/agent.yaml` is the static manifest and holds root-level keys only.
Runtime parameters live in `config/config.yaml` and reach the nodes by one route:

```
config/config.yaml → Graph(config=...) → DomainWorkflowGraph(config=...)
                   → _extra_initial_state() → state["domain_settings"] → nodes
```

Seeding through state is what makes the settings live. `BaseNode.__call__` calls
`execute(state)` with no config argument, so a node signature carrying
`config=None` is never populated in a real run — only in a unit test that passes
it directly, which is how an entire configuration surface can be green in tests
and dead in production.

An out-of-bounds value in the operator block fails at compile
(`DomainWorkflowGraph._validate_config`), rather than reverting to a default the
operator believes they overrode.

## Security design

| Layer | Where | Behaviour |
|-------|-------|-----------|
| Trust | manifest + every node | `VERIFIED_EXTERNAL` throughout. The adapter promotes a caller presenting `INVOKE_AUTH_TOKEN` from ANONYMOUS to VERIFIED_EXTERNAL; middleware-established trust is never demoted. |
| Input contract | `src/schemas/contract.py`, applied by `PreProcessNode` | Every field validated against explicit bounds; fail closed; the error names the field, never the value; unknown record keys dropped so nothing free-text reaches the pipeline. |
| Injection screen | `PreProcessNode` | Chat-template control tokens as a class (`<|…|>`, `[INST]`, `<<SYS>>`) plus anchored directive phrasing, screened over the raw text AND the post-redaction text, and depth-first over the context structure including field names. |
| Context-channel credential screen | `src/api/server.py` | Refuses (400, naming the field) an `input_context` carrying a credential-shaped value, using the framework's own detector so the refusal set cannot drift from the framework's block set. Undeclared keys are dropped before `invoke()`. |
| Render invariant | `GenerateTriageReportNode` | The rendered table is re-read and refused unless every cell is an inert reference or a closed-set value. |
| Output gate | `PostProcessNode` | The union of the framework's credential detector and this template's local patterns; on a violation, ERROR plus every output-bearing field cleared with a truthy notice. |
| Audit | every node | One domain trace event per node, carrying counts and closed-set names only. |

### Output invariant

The monetary-precision grid that a template rendering aggregate amounts carries
is **not applicable here**: this report renders no monetary values at all.
Amounts, value dates and the free-text detail that drove each classification stay
in state for downstream consumers and are never rendered.

The invariant enforced in its place is this template's own:

> Every caller-derived token in the report is an inert reference identifier
> (`[A-Za-z0-9_-]{1,32}`). Every other cell comes from a closed set — the
> discrepancy id the pipeline assigned, one of five categories, one of four
> severities, one of five fixed hypotheses. No amount, no date and no caller free
> text is rendered.

`_enforce_render_invariant()` re-reads the rendered table and refuses a report
that breaks it — the string that ships is the representation that is checked.

### Containment

`AgentBaseGraph.get_output()` returns `formatted_output or result`, and it does so
on the error status too, so an output gate that merely raises still ships the
un-gated report inside the error envelope. Two consequences shaped this design:

- The output gate clears `formatted_output`, `result` **and**
  `reconciliation_report`, with a truthy replacement (a falsy one re-opens the
  fallback).
- The gate's pattern set is the UNION of the framework's detector and the local
  patterns. A value the framework catches and the template misses makes the
  framework raise inside the node wrapper, which discards the clearing — so a
  detector gap is a containment bypass. The local patterns are kept because they
  catch shapes the framework describes nowhere (a bare `password=` assignment);
  replacing them with the framework's set would have made the gate narrower.
- On a refusal the backbone routes `main → finalize`, skipping post_process, so
  `PreProcessNode` carries its own fixed refusal notice in `formatted_output`.
  Nothing caller-derived rides on that path.

## Composition Pattern

- **Pattern:** `GraphNode` (subgraph) in the outer `main` slot.
- **Composition target:** `DomainWorkflowGraph` (inner `BaseGraph`).
- **Error propagation strategy:** `propagate` (inner errors re-raised as `SubgraphError`).
- Every node — outer and inner — requires `TrustLevel.VERIFIED_EXTERNAL`, matching
  the entry level the manifest declares.
- `route()` on the inner graph is annotated with the graph's OWN `State`:
  LangGraph reads a path callable's annotation as its input schema and projects
  away every field the annotation does not carry.

## Import Isolation Confirmation
- [x] Template does not import the platform SDK.
- [x] Import targets: `framework/` and `shared/` only.
- [x] No intermediate-tier class names in any base position.

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | Fixed multi-step workflow, no autonomous loop |
| Composition pattern | Standalone Cat 1 slots | GraphNode → inner BaseGraph | **GraphNode → inner BaseGraph** | 4-step domain workflow exceeds a single `main` node; nested keeps the outer backbone untouched |
| Caller data channel | free-text `input` | `input_context` + context bridge | **`input_context`** | The free-text channel is masked before template code runs, which corrupts ledger data silently; the legacy channel is still accepted and validated identically |
| Classification | Hard-coded rules | Model call | **Deterministic rules with a governed prompt at the seam** | The pipeline runs and tests with no model and no network; the prompt a model would use is already configuration |
| Output invariant | Monetary precision grid | Inert-token + closed-set render invariant | **Render invariant** | No monetary value is rendered, so the grid has nothing to enforce; the real risk here is caller text reaching a cited rendering |
| Amounts in the report | Render rounded aggregates | Omit | **Omit** | Keeps the report free of every representation of a caller number, which is what makes the invariant checkable in one pass |
