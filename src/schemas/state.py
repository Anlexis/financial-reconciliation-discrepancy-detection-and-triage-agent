"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict - never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or Pydantic models.
#
# ADR-005 (msgpack safety): structured fields (dict / list[dict]) are stored
# as JSON STRINGS, not bare Python containers - a bare dict/list in a
# checkpointed State field is a CoE gate-state-safety violation. Producers
# serialize with to_json() on write; consumers deserialize with from_json()
# on read.
#
# FIN-C2-071 - Financial Reconciliation & Discrepancy Triage Agent
# Two-layer nested Cat 2 graph: outer backbone (AgentBaseGraph) + inner
# domain workflow (BaseGraph).  Fields below cover both layers.
#
# PII / confidentiality note: account identifiers, customer names, and other
# direct identifiers in the multi-source financial input are surface-stripped
# by PreProcessNode (S-1/S-2) before any field is written to State.  Raw
# financial ledger rows are NEVER persisted to State - only matched/unmatched
# partition summaries, discrepancy descriptors, and the final triage report.

import json
from typing import Any, Optional

from framework.schemas.agent_state import AgentState


def to_json(value: Any) -> Optional[str]:
    """Serialize a dict/list State field to a JSON string (ADR-005 msgpack safety).

    None passes through unchanged so an 'unset' field stays distinguishable
    from an empty container.
    """
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def from_json(value: Optional[str], default: Any = None) -> Any:
    """Deserialize a JSON-string State field back to its dict/list.

    None / empty / malformed input -> the supplied ``default`` so a missing or
    corrupt field is non-fatal for the consuming node.
    """
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class State(AgentState):
    """Flat TypedDict for FIN-C2-071.

    All shared fields (user_input, status, session_id, node_history,
    error_log, hitl_*, etc.) are inherited from AgentState.
    """

    # ------------------------------------------------------------------
    # Outer layer - set by PreProcessNode / ReconciliationGraphNode.merge_output
    # ------------------------------------------------------------------

    # Identifier-stripped free-text description produced by PreProcessNode.
    # The RECORDS never travel here - see validated_ledger below.
    validated_input: Optional[str]

    # JSON STRING (to_json) of the validated caller contract, written by
    # PreProcessNode and carried into the inner graph by the context bridge.
    # Deserialised shape:
    # {"source_a": [{"reference": str, "amount": float, "date": str}, ...],
    #  "source_b": [...], "match_keys": [str], "amount_tolerance": float}.
    # Every value in it has already passed src/schemas/contract.py, so the
    # records are inert by the time any domain node reads them.
    validated_ledger: Optional[str]

    # JSON STRING (to_json) of the resolved runtime settings for one run, seeded
    # by DomainWorkflowGraph._extra_initial_state() from config/config.yaml with
    # the caller's validated overrides applied. Deserialised shape:
    # {"match_keys": [str], "amount_tolerance": float, "system_prompt": str}.
    # This is how declared configuration reaches the nodes: BaseNode.__call__
    # calls execute(state) with no config argument.
    domain_settings: Optional[str]

    # Final reconciliation triage report (Markdown / structured text), mapped
    # from the inner graph's triage_report output via merge_output.
    reconciliation_report: Optional[str]

    # ------------------------------------------------------------------
    # Inner layer - domain nodes (DomainWorkflowGraph)
    # ------------------------------------------------------------------

    # MatchRecordsNode outputs
    # JSON STRING (to_json) of the matched-record partition. Deserialised shape:
    # list[dict], each entry a matched pair summary
    # {"key": str, "source_a": {...}, "source_b": {...}, "amount": float, ...}.
    # Consumers (DetectDiscrepanciesNode) read it back via from_json().
    matched_records: Optional[str]

    # JSON STRING (to_json) of records present in one source but not the other.
    # Deserialised dict shape:
    # {"unmatched_in_a": [ {...}, ... ], "unmatched_in_b": [ {...}, ... ]}.
    # Consumers (DetectDiscrepanciesNode) read it back via from_json().
    unmatched_records: Optional[str]

    # DetectDiscrepanciesNode output
    # JSON STRING (to_json) of the raw discrepancy set detected across matched +
    # unmatched partitions. Deserialised shape: list[dict], each entry
    # {"id": str, "kind": str, "key": str, "detail": str,
    #  "amount_delta": float | None, ...}.
    # Consumers (ClassifyAndHypothesizeNode) read it back via from_json().
    discrepancy_set: Optional[str]

    # ClassifyAndHypothesizeNode output
    # JSON STRING (to_json) of discrepancies enriched with a classified type and
    # a root-cause hypothesis. Deserialised shape: list[dict], each entry
    # {"id": str, "kind": str, "category": str, "hypothesis": str,
    #  "severity": str, "amount_delta": float | None, ...}.
    # Consumers (GenerateTriageReportNode) read it back via from_json().
    classified_discrepancies: Optional[str]

    # GenerateTriageReportNode output
    # Final rendered triage report string. Written by GenerateTriageReportNode;
    # surfaced to the outer graph via get_output() -> merge_output().
    triage_report: Optional[str]

    # Validation / redaction notes accumulated during intake (no PII).
    # JSON STRING (to_json) of list[str].
    intake_notes: Optional[str]

    # ------------------------------------------------------------------
    # Tracing / audit - framework-managed; do NOT write from node code
    # ------------------------------------------------------------------

    trace_id: Optional[str]
    correlation_id: Optional[str]
    # node_history inherited from AgentState; listed here for clarity
    # node_history: Optional[List[str]]
