"""AgentCore Platform v1.0"""

# FIN-C2-071 — MatchRecordsNode
# Domain node 1: config-driven multi-source record matching. Reads the validated
# ledger seeded by the inner graph and partitions it into matched pairs,
# unmatched-in-A, and unmatched-in-B using the resolved composite key
# (reference / amount / date).
#
# Every record reaching this node has already passed src/schemas/contract.py, so
# it carries exactly three inert fields. There is no parsing and no coercion
# here: a value this node cannot use never got this far.
#
# Wired by the inner graph (DomainWorkflowGraph).
# Returns only changed state keys (partial dict).

import logging
from typing import Any, ClassVar, Dict, List, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.contract import DEFAULT_MATCH_KEYS
from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)


def _record_key(record: Dict[str, Any], match_keys: List[str]) -> Tuple[Any, ...]:
    """Build the composite match key tuple for a single validated record."""
    parts: List[Any] = []
    for key in match_keys:
        if key == "reference":
            parts.append(str(record.get("reference", "")).lower())
        elif key == "amount":
            amount = record.get("amount")
            # Round to 2 dp so trivial float noise does not break a match.
            parts.append(round(float(amount), 2) if isinstance(amount, (int, float)) else None)
        elif key == "date":
            parts.append(str(record.get("date", "")))
    return tuple(parts)


class MatchRecordsNode(FunctionNode):
    """Partition two record sets into matched / unmatched-in-A / unmatched-in-B.

    Input state keys:
        validated_ledger: validated caller contract (seeded by the inner graph)
        domain_settings:  resolved match_keys / amount_tolerance for this run

    Output state keys (partial dict):
        matched_records:   list of matched pair summaries
        unmatched_records: {"unmatched_in_a": [...], "unmatched_in_b": [...]}
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        ledger: Dict[str, Any] = from_json(state.get("validated_ledger"), {}) or {}
        settings: Dict[str, Any] = from_json(state.get("domain_settings"), {}) or {}

        match_keys: List[str] = settings.get("match_keys") or list(DEFAULT_MATCH_KEYS)
        source_a: List[Dict[str, Any]] = ledger.get("source_a") or []
        source_b: List[Dict[str, Any]] = ledger.get("source_b") or []

        # Index source B by composite key (allowing duplicates per key).
        b_index: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
        for rec in source_b:
            b_index.setdefault(_record_key(rec, match_keys), []).append(rec)

        matched: List[Dict[str, Any]] = []
        unmatched_in_a: List[Dict[str, Any]] = []

        for rec_a in source_a:
            bucket = b_index.get(_record_key(rec_a, match_keys))
            if bucket:
                rec_b = bucket.pop(0)
                matched.append(
                    {
                        "reference": rec_a.get("reference", ""),
                        "amount_a": rec_a.get("amount"),
                        "amount_b": rec_b.get("amount"),
                        "date_a": rec_a.get("date", ""),
                        "date_b": rec_b.get("date", ""),
                    }
                )
            else:
                unmatched_in_a.append(rec_a)

        # Anything left in the B index was never matched.
        unmatched_in_b: List[Dict[str, Any]] = []
        for bucket in b_index.values():
            unmatched_in_b.extend(bucket)

        unmatched_records = {
            "unmatched_in_a": unmatched_in_a,
            "unmatched_in_b": unmatched_in_b,
        }

        # S-4 domain audit: source record sets partitioned.
        emit_trace_event(
            "records_matched",
            {
                "matched": len(matched),
                "unmatched_in_a": len(unmatched_in_a),
                "unmatched_in_b": len(unmatched_in_b),
                "match_keys": match_keys,
            },
            state,
        )

        logger.info(
            "MatchRecordsNode: %d matched, %d unmatched-in-A, %d unmatched-in-B",
            len(matched),
            len(unmatched_in_a),
            len(unmatched_in_b),
        )

        return {
            "matched_records": to_json(matched),
            "unmatched_records": to_json(unmatched_records),
        }
