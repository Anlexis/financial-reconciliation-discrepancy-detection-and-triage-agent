"""AgentCore Platform v1.0"""

# FIN-C2-071 - PostProcessNode (outer post_process slot; output gate)
#
# Reads the rendered reconciliation triage report from state["result"], which is
# populated by ReconciliationGraphNode.merge_output() (mapped from the inner
# graph's triage_report output), and surfaces it as the finalized output AFTER
# running the output content-safety gate.
#
# Two properties of this gate are load-bearing and easy to get wrong:
#
#   1. THE PATTERN SET IS A UNION, NEVER A REPLACEMENT. The framework's own
#      detect_credentials() is the floor: a value it catches and this node
#      misses makes the framework raise INSIDE the node wrapper, which discards
#      whatever this node cleared and returns a bare error partial - and the
#      graph's output then falls back to the un-gated state["result"]. A gap in
#      the local detector is therefore a containment bypass, not a lesser
#      catch. The local patterns are kept ALONGSIDE it because they cover shapes
#      the framework does not describe at all (a bare `password=...`
#      assignment); delegating to the framework alone would make this gate
#      narrower while looking like a tightening.
#   2. A VIOLATION CLEARS THE OUTPUT-BEARING FIELDS. AgentBaseGraph.get_output()
#      returns `formatted_output or result`, and it does so on the error status
#      too - so a gate that merely raises, or that returns ERROR without
#      overwriting result, still ships the un-gated report inside the error
#      envelope. Both fields are replaced with the same fixed notice, and the
#      notice is deliberately TRUTHY: a falsy replacement re-opens the fallback.

import logging
import re
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from framework.security.credential_detector import detect_credentials
from shared.utils.audit_logger import emit_trace_event
from src.services.llm_factory import resolve_llm
from src.services.llm_review import render_review, review_result

logger = logging.getLogger(__name__)

# Local patterns, kept for the shapes the framework's format-based detector does
# not describe. Each tuple: (name, compiled regex) - order matters (most
# specific first). See the module docstring: these are additive to
# detect_credentials(), never a substitute for it.
_LOCAL_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    # Short-form API keys: sk-/pk-/ak- with 16+ characters. The framework's
    # openai_key pattern only starts at 20, and carries no pk-/ak- form.
    ("api_key", re.compile(r"\b(?:sk|pk|ak)-[A-Za-z0-9]{16,}", re.IGNORECASE)),
    # Bearer token with a wider character class than the framework's.
    ("bearer_token", re.compile(r"Bearer\s+[A-Za-z0-9._~+/]{20,}", re.IGNORECASE)),
    # Credential ASSIGNMENTS. The framework describes credential FORMATS and
    # matches nothing of this shape - this is the clearest example of why the
    # two sets are unioned rather than swapped.
    (
        "credential_assignment",
        re.compile(
            r"\b(?:password|passwd|secret|api_key|token|access_key|private_key)\s*[:=]\s*\S{8,}",
            re.IGNORECASE,
        ),
    ),
]

_BLOCKED_NOTICE = (
    "[OUTPUT WITHHELD] The generated reconciliation triage report did not pass "
    "the output content gate and has been withheld in full. Remove "
    "credential-like strings from the source records and retry."
)

_EMPTY_NOTICE = (
    "[NO REPORT PRODUCED] The reconciliation workflow returned no triage report. "
    "No output is available for this request."
)


def _security_gate_output(content: str) -> Optional[str]:
    """Run the output content gate over the report string.

    Returns the name of the first matched violation, or None if clean. The
    framework detector runs first so that its findings are always caught here,
    before the wrapper would raise on them.
    """
    framework_findings = detect_credentials(content)
    if framework_findings:
        return str(framework_findings[0]["type"])
    for name, pattern in _LOCAL_PATTERNS:
        if pattern.search(content):
            return name
    return None


class PostProcessNode(FunctionNode):
    """Output gate: scan the triage report for disallowed content.

    Outer backbone post_process slot. Reads state["result"] (the merged
    triage_report from ReconciliationGraphNode.merge_output()) and applies the
    output content-safety gate before the response is returned to the caller.

    Input state keys:
        result: rendered reconciliation triage report (from merge_output)

    Output state keys (partial dict):
        formatted_output:      gated output - the report when clean, a fixed
                               notice on a violation or on an absent report
        result:                cleared to the same notice on those paths
        reconciliation_report: cleared alongside, so no output-bearing field
                               retains the withheld text
        status:                AgentStatus.SUCCESS or AgentStatus.ERROR
        error_log:             (on error) a closed-set reason, never content
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    @staticmethod
    def _withhold(notice: str, reason: str) -> Dict[str, Any]:
        """Return an error partial with every output-bearing field cleared."""
        return {
            "formatted_output": notice,
            "result": notice,
            "reconciliation_report": notice,
            "status": AgentStatus.ERROR.value,
            "error_log": [f"PostProcessNode: {reason}"],
        }

    def execute(self, state: AgentState) -> Dict[str, Any]:
        result = state.get("result") or ""

        if not isinstance(result, str) or not result.strip():
            # No report reached this slot. Fail closed: an empty success would
            # read as "the sources reconcile", which is the one answer this
            # agent must never give by accident.
            logger.error("PostProcessNode: no triage report reached the output gate")
            return self._withhold(_EMPTY_NOTICE, "no triage report was produced")

        _llm, _ = resolve_llm(None, state)
        _remarks = review_result(
            _llm,
            user_input=str(state.get("user_input") or ""),
            result=result,
            domain="FIN FinancialReconciliationDiscrepancyAgent",
        )
        _review = render_review(_remarks)
        # Remarks are LLM text derived from the caller's raw words, so they pass through the
        # same gate the answer does -- appending after the gate would put unscanned text past
        # it. A tripped review is dropped on its own: withholding a correct answer because an
        # advisory remark quoted an identifier would let the review change the outcome, and
        # the whole design rests on it being unable to.
        if _review and isinstance(result, str) and not _security_gate_output(result + _review):
            result = result + _review

        violation = _security_gate_output(result)
        if violation:
            logger.error(
                "PostProcessNode: output withheld - violation type: %s",
                violation,
            )
            return self._withhold(_BLOCKED_NOTICE, f"output withheld - disallowed content detected ({violation})")

        # Clean - S-4 domain audit: record that a finalized report was emitted.
        emit_trace_event(
            "reconciliation_report_emitted",
            {"report_chars": len(result)},
            state,
        )

        return {
            "formatted_output": result,
            "status": AgentStatus.SUCCESS.value,
        }
