"""AgentCore Platform v1.0"""

# FIN-C2-071 — ClassifyAndHypothesizeNode
# Domain node 3: classify each detected discrepancy into a root-cause category
# (timing cutoff, FX rounding, duplicate entry, omission, data-entry error) and
# attach a concise root-cause hypothesis and a severity ranking.
#
# The shipped classifier is deterministic and rule-based, so the pipeline runs
# and tests end-to-end with no model call and no network. The governed system
# prompt a model would use is carried in config/config.yaml and reaches this
# node through domain_settings, so swapping a model in at the marked seam is a
# code change here and nowhere else.
#
# Wired by the inner graph (DomainWorkflowGraph).
# Returns only changed state keys (partial dict).

import logging
from typing import Any, ClassVar, Dict, List, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.schemas.state import from_json, to_json

logger = logging.getLogger(__name__)

# Severity threshold above which a value mismatch is escalated to critical, in
# the ledger's own currency unit.
_CRITICAL_DELTA = 1000.0

# Discrepancy kind -> (category, hypothesis, severity).
# This deterministic mapping is the testable stand-in for the model classifier.
# Both the categories and the hypotheses are CLOSED SETS: nothing in the report
# is composed from caller text.
_KIND_RULES: Dict[str, Tuple[str, str, str]] = {
    "value_mismatch": (
        "fx_rounding",
        "Amount difference is consistent with FX conversion or rounding between "
        "the two systems; verify the applied rate and rounding rule.",
        "high",
    ),
    "timing_anomaly": (
        "timing_cutoff",
        "Records posted on different value dates suggest a period cut-off / "
        "settlement-timing difference rather than a true discrepancy.",
        "medium",
    ),
    "missing_in_b": (
        "omission",
        "Entry exists in source A but not source B; likely an omission or an " "in-transit item not yet booked in B.",
        "high",
    ),
    "missing_in_a": (
        "duplicate_entry",
        "Entry exists in source B but not source A; likely a duplicate booking "
        "in B or a missing entry in A - confirm against the originating document.",
        "high",
    ),
}

_FALLBACK_RULE: Tuple[str, str, str] = (
    "data_entry_error",
    "Unclassified discrepancy; manual review recommended to confirm the root cause.",
    "medium",
)

# The closed sets the report gate re-checks after rendering.
CATEGORIES = frozenset({rule[0] for rule in _KIND_RULES.values()} | {_FALLBACK_RULE[0]})
HYPOTHESES = frozenset({rule[1] for rule in _KIND_RULES.values()} | {_FALLBACK_RULE[1]})
SEVERITIES = frozenset({"critical", "high", "medium", "low"})


def _classify(disc: Dict[str, Any]) -> Tuple[str, str, str]:
    """Return (category, hypothesis, severity) for one discrepancy."""
    kind = str(disc.get("kind", ""))
    category, hypothesis, severity = _KIND_RULES.get(kind, _FALLBACK_RULE)

    # Escalate a value mismatch to critical when the delta is large.
    delta = disc.get("amount_delta")
    if (
        kind == "value_mismatch"
        and isinstance(delta, (int, float))
        and not isinstance(delta, bool)
        and abs(delta) >= _CRITICAL_DELTA
    ):
        severity = "critical"
    return category, hypothesis, severity


class ClassifyAndHypothesizeNode(FunctionNode):
    """Classify discrepancies and attach a root-cause hypothesis + severity.

    Input state keys:
        discrepancy_set: raw discrepancies (from DetectDiscrepanciesNode)
        domain_settings: resolved system_prompt for the classification seam

    Output state keys (partial dict):
        classified_discrepancies: discrepancies enriched with category /
                                  hypothesis / severity
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        discrepancies: List[Dict[str, Any]] = from_json(state.get("discrepancy_set"), []) or []
        settings: Dict[str, Any] = from_json(state.get("domain_settings"), {}) or {}

        # The governed prompt is carried for the model seam even though the
        # shipped classification below is deterministic.
        system_prompt = str(settings.get("system_prompt", "")).strip()
        logger.debug(
            "ClassifyAndHypothesizeNode: using system_prompt (%d chars)",
            len(system_prompt),
        )

        classified: List[Dict[str, Any]] = []
        for disc in discrepancies:
            category, hypothesis, severity = _classify(disc)
            enriched = dict(disc)
            enriched["category"] = category
            enriched["hypothesis"] = hypothesis
            enriched["severity"] = severity
            classified.append(enriched)

        # S-4 domain audit: discrepancies classified.
        emit_trace_event(
            "discrepancies_classified",
            {
                "total": len(classified),
                "categories": sorted({item["category"] for item in classified}),
            },
            state,
        )

        logger.info(
            "ClassifyAndHypothesizeNode: classified %d discrepancies",
            len(classified),
        )

        return {
            "classified_discrepancies": to_json(classified),
        }
