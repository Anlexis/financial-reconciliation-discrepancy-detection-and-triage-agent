"""AgentCore Platform v1.0"""

# FIN-C2-071 — DetectDiscrepanciesNode
# Domain node 2: detect reconciliation discrepancies across the matched and
# unmatched partitions:
#   - missing entries  (present in one source, absent in the other)
#   - value mismatches (matched key but amount differs beyond tolerance)
#   - timing anomalies (matched key + amount but differing value dates)
# Accumulates a structured discrepancy set in State.
#
# Wired by the inner graph (DomainWorkflowGraph).
# Returns only changed state keys (partial dict).

import logging
import math
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.contract import DEFAULT_AMOUNT_TOLERANCE, TOLERANCE_MAX
from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)


def _resolve_tolerance(settings: Dict[str, Any]) -> float:
    """Return the run's amount tolerance, falling back only on a missing value.

    A non-finite or out-of-range tolerance cannot reach this point - the caller
    contract and the graph config validator both refuse it - but the guard is
    kept because a comparison against NaN is False for every operand, which
    would silently report a perfectly reconciled ledger.
    """
    value = settings.get("amount_tolerance", DEFAULT_AMOUNT_TOLERANCE)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_AMOUNT_TOLERANCE
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > TOLERANCE_MAX:
        return DEFAULT_AMOUNT_TOLERANCE
    return number


class DetectDiscrepanciesNode(FunctionNode):
    """Detect missing entries, value mismatches, and timing anomalies.

    Input state keys:
        matched_records:   matched pair summaries (from MatchRecordsNode)
        unmatched_records: {"unmatched_in_a": [...], "unmatched_in_b": [...]}
        domain_settings:   resolved amount_tolerance for this run

    Output state keys (partial dict):
        discrepancy_set: list of discrepancy descriptors
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        matched: List[Dict[str, Any]] = from_json(state.get("matched_records"), []) or []
        unmatched: Dict[str, Any] = from_json(state.get("unmatched_records"), {}) or {}
        settings: Dict[str, Any] = from_json(state.get("domain_settings"), {}) or {}

        tolerance = _resolve_tolerance(settings)

        discrepancies: List[Dict[str, Any]] = []
        seq = 0

        # 1. Value mismatches + timing anomalies within matched pairs.
        for pair in matched:
            seq += 1
            ref = pair.get("reference", "")
            amount_a = pair.get("amount_a")
            amount_b = pair.get("amount_b")
            if isinstance(amount_a, (int, float)) and isinstance(amount_b, (int, float)):
                delta = round(float(amount_a) - float(amount_b), 4)
                if abs(delta) > tolerance:
                    discrepancies.append(
                        {
                            "id": f"D{seq:04d}",
                            "kind": "value_mismatch",
                            "key": ref,
                            "detail": "Amount differs beyond the configured tolerance.",
                            "amount_delta": delta,
                        }
                    )
                    continue
            # Timing anomaly: same key + amount within tolerance, but the value
            # dates differ between the two sources.
            date_a = str(pair.get("date_a", ""))
            date_b = str(pair.get("date_b", ""))
            if date_a and date_b and date_a != date_b:
                discrepancies.append(
                    {
                        "id": f"D{seq:04d}",
                        "kind": "timing_anomaly",
                        "key": ref,
                        "detail": "Value dates differ between the two sources.",
                        "amount_delta": None,
                    }
                )

        # 2. Missing entries (present in A, absent in B).
        for rec in unmatched.get("unmatched_in_a", []):
            seq += 1
            discrepancies.append(
                {
                    "id": f"D{seq:04d}",
                    "kind": "missing_in_b",
                    "key": str(rec.get("reference", "")),
                    "detail": "Record present in source A but absent from source B.",
                    "amount_delta": None,
                }
            )

        # 3. Missing entries (present in B, absent in A).
        for rec in unmatched.get("unmatched_in_b", []):
            seq += 1
            discrepancies.append(
                {
                    "id": f"D{seq:04d}",
                    "kind": "missing_in_a",
                    "key": str(rec.get("reference", "")),
                    "detail": "Record present in source B but absent from source A.",
                    "amount_delta": None,
                }
            )

        # S-4 domain audit: discrepancies detected.
        emit_trace_event(
            "discrepancies_detected",
            {
                "total": len(discrepancies),
                "tolerance": tolerance,
            },
            state,
        )

        logger.info(
            "DetectDiscrepanciesNode: %d discrepancies (tolerance=%s)",
            len(discrepancies),
            tolerance,
        )

        return {
            "discrepancy_set": to_json(discrepancies),
        }
