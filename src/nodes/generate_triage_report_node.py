"""AgentCore Platform v1.0"""

# FIN-C2-071 — GenerateTriageReportNode
# Domain node 4 (terminal): severity-rank the classified discrepancies,
# summarise the affected reference keys, and compose a structured Markdown
# triage report for the reconciliation analyst.
#
# OUTPUT INVARIANT (this template's own; it replaces the monetary-precision grid
# that a template rendering aggregate amounts would carry — this report renders
# no monetary values at all, so that grid is not applicable here):
#
#     Every caller-derived token that reaches the report is an inert reference
#     identifier. Every other cell comes from a closed set — the discrepancy id
#     the pipeline assigned, one of five categories, one of four severities, one
#     of five fixed hypotheses. No amount, no date, and no caller free text is
#     rendered.
#
# The invariant is ENFORCED, not just documented: _enforce_render_invariant()
# re-reads the rendered table and refuses to return a report whose cells fall
# outside those sets. A report that cannot satisfy it is not shipped in any
# form.
#
# Wired by the inner graph (DomainWorkflowGraph). get_output() of the inner
# graph surfaces triage_report + status to the outer merge_output().
# Returns only changed state keys (partial dict).

import logging
import re
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.nodes.classify_and_hypothesize_node import CATEGORIES, HYPOTHESES, SEVERITIES
from src.schemas.contract import REFERENCE_RE
from src.schemas.state import from_json

logger = logging.getLogger(__name__)

# Severity ordering used to rank the triage table (highest first).
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# Discrepancy ids are assigned by this pipeline, never by the caller.
_DISCREPANCY_ID_RE = re.compile(r"^D\d{4}$")

_TABLE_HEADER = "| ID | Severity | Category | Key | Hypothesis |"
_TABLE_RULE = "|----|----------|----------|-----|------------|"


class RenderInvariantError(RuntimeError):
    """The rendered report carried a cell outside the declared closed sets."""


def _severity_rank(disc: Dict[str, Any]) -> int:
    return _SEVERITY_ORDER.get(str(disc.get("severity", "medium")).lower(), 2)


def _enforce_render_invariant(report: str) -> None:
    """Re-read the rendered table and refuse anything outside the closed sets.

    Reading the rendered text back — rather than re-checking the structures the
    renderer was given — is the point: it is the string that ships, and it is
    the only representation a reader ever sees.
    """
    in_table = False
    for line in report.splitlines():
        if line == _TABLE_HEADER:
            in_table = True
            continue
        if line == _TABLE_RULE:
            continue
        if in_table and not line.startswith("|"):
            in_table = False
            continue
        if not in_table or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            raise RenderInvariantError("triage table row does not carry exactly five cells")
        disc_id, severity, category, key, hypothesis = cells
        if not _DISCREPANCY_ID_RE.match(disc_id):
            raise RenderInvariantError("triage table row carried a non-pipeline discrepancy id")
        if severity not in SEVERITIES:
            raise RenderInvariantError("triage table row carried a severity outside the closed set")
        if category not in CATEGORIES:
            raise RenderInvariantError("triage table row carried a category outside the closed set")
        if not REFERENCE_RE.match(key):
            raise RenderInvariantError("triage table row carried a non-inert reference key")
        if hypothesis not in HYPOTHESES:
            raise RenderInvariantError("triage table row carried a hypothesis outside the closed set")


def _render_report(
    classified: List[Dict[str, Any]],
    affected_keys: List[str],
    severity_counts: Dict[str, int],
) -> str:
    """Render the Markdown reconciliation triage report."""
    generated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: List[str] = []
    lines.append("# Reconciliation Triage Report")
    lines.append("")
    lines.append(f"**Generated:** {generated_at}")
    lines.append(f"**Total discrepancies:** {len(classified)}")
    lines.append(f"**Affected reference keys:** {len(affected_keys)}")
    lines.append("")
    lines.append("## Severity Summary")
    lines.append("")
    if severity_counts:
        for sev in ("critical", "high", "medium", "low"):
            if severity_counts.get(sev):
                lines.append(f"- {sev.title()}: {severity_counts[sev]}")
    else:
        lines.append("- No discrepancies detected - the two sources reconcile.")
    lines.append("")
    lines.append("## Discrepancies (ranked)")
    lines.append("")
    if classified:
        lines.append(_TABLE_HEADER)
        lines.append(_TABLE_RULE)
        for disc in classified:
            lines.append(
                "| {id} | {sev} | {cat} | {key} | {hyp} |".format(
                    id=disc.get("id", ""),
                    sev=disc.get("severity", ""),
                    cat=disc.get("category", ""),
                    key=disc.get("key", ""),
                    hyp=str(disc.get("hypothesis", "")),
                )
            )
    else:
        lines.append("No discrepancies require triage for this reconciliation run.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "*Amounts and value dates are deliberately not reproduced in this report; "
        "it carries reference keys, categories, severities and counts only.*"
    )
    return "\n".join(lines)


class GenerateTriageReportNode(FunctionNode):
    """Severity-rank discrepancies and compose the structured triage report.

    Input state keys:
        classified_discrepancies: enriched discrepancies (from
                                  ClassifyAndHypothesizeNode)

    Output state keys (partial dict):
        triage_report: rendered Markdown triage report string
        status:        AgentStatus.SUCCESS
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def execute(self, state: AgentState) -> Dict[str, Any]:
        classified: List[Dict[str, Any]] = from_json(state.get("classified_discrepancies"), []) or []

        # Rank by severity (critical first), then by id for stable ordering.
        ranked = sorted(
            classified,
            key=lambda d: (_severity_rank(d), str(d.get("id", ""))),
        )

        affected_keys = sorted({str(d.get("key", "")) for d in ranked if d.get("key")})

        severity_counts: Dict[str, int] = {}
        for disc in ranked:
            sev = str(disc.get("severity", "medium")).lower()
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        triage_report = _render_report(ranked, affected_keys, severity_counts)
        # Refuses rather than degrades: a report that breaks the declared
        # invariant is not shipped in a partial or annotated form.
        _enforce_render_invariant(triage_report)

        # S-4 domain audit: triage report composed.
        emit_trace_event(
            "triage_report_generated",
            {
                "discrepancy_count": len(ranked),
                "affected_keys": len(affected_keys),
                "report_chars": len(triage_report),
            },
            state,
        )

        logger.info(
            "GenerateTriageReportNode: report for %d discrepancies (%d affected keys)",
            len(ranked),
            len(affected_keys),
        )

        return {
            "triage_report": triage_report,
            "status": AgentStatus.SUCCESS.value,
        }
