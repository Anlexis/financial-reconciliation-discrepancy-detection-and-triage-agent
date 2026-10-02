"""AgentCore Platform v1.0"""

# FIN-C2-071 — DomainWorkflowGraph (inner BaseGraph)
#
# This is the INNER graph for the Cat 2 two-layer nested architecture.
# It encapsulates the full reconciliation discrepancy-triage domain workflow:
#
#   START -> match_records -> detect_discrepancies -> classify_and_hypothesize
#         -> generate_triage_report -> END
#
# Called by ReconciliationGraphNode.get_subgraph() (graph.py).
# get_output() shapes the sub_result dict consumed by merge_output() there.
#
# Rules enforced:
#   - Inherits BaseGraph (fully custom topology - no forced backbone)
#   - Implements all 7 BaseGraph ABC methods
#   - register_nodes() does NOT call super() (abstract in BaseGraph)
#   - register_nodes() instantiates every domain node with NO ctor args
#   - Does NOT register initialize / finalize (outer backbone concerns)
#   - get_output() designed together with ReconciliationGraphNode.merge_output()
#   - No agenticstar imports
#   - Not placed under src/subagents/

from typing import Any, Dict, List

from langgraph.graph import END, START

from framework.errors import ConfigError
from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from src.graph.context_bridge import get_caller_ledger
from src.nodes.classify_and_hypothesize_node import ClassifyAndHypothesizeNode
from src.nodes.detect_discrepancies_node import DetectDiscrepanciesNode
from src.nodes.generate_triage_report_node import GenerateTriageReportNode
from src.nodes.match_records_node import MatchRecordsNode
from src.schemas.contract import (
    ALLOWED_MATCH_KEYS,
    DEFAULT_AMOUNT_TOLERANCE,
    DEFAULT_MATCH_KEYS,
    MAX_MATCH_KEYS,
    TOLERANCE_MAX,
)
from src.schemas.state import State, to_json


class DomainWorkflowGraph(BaseGraph):
    """Inner domain workflow graph for FIN-C2-071.

    Inherits BaseGraph directly for a fully custom node topology.
    Called by ReconciliationGraphNode.get_subgraph() in graph.py.

    Pipeline (linear):
        START
          -> match_records            (MatchRecordsNode)            - partition sources
          -> detect_discrepancies     (DetectDiscrepanciesNode)     - find deltas / gaps
          -> classify_and_hypothesize (ClassifyAndHypothesizeNode)  - root-cause + severity
          -> generate_triage_report   (GenerateTriageReportNode)    - compose triage report
          -> END

    All nodes are FunctionNode subclasses returning partial-dict state updates.
    initialize / finalize are outer backbone concerns - not registered here.
    """

    # -- Identity --------------------------------------------------------------

    @property
    def name(self) -> str:
        """Unique identifier for this inner graph."""
        return "fin_c2_071_reconciliation_triage_workflow"

    @property
    def state_schema(self) -> type:
        """TypedDict subclass shared across inner and outer graph."""
        return State

    # -- Config validation -----------------------------------------------------

    def _reconciliation_config(self) -> Dict[str, Any]:
        block = self.config.get("reconciliation")
        return block if isinstance(block, dict) else {}

    def _validate_config(self) -> None:
        """Validate the operator-supplied reconciliation block before compiling.

        Operator config is not caller data, so the failure mode is different: a
        value outside its bounds is a deployment mistake and must stop start-up
        loudly rather than silently reverting to a default that the operator
        then believes they overrode.
        """
        block = self._reconciliation_config()

        keys = block.get("match_keys")
        if keys is not None:
            if not isinstance(keys, list) or not keys or len(keys) > MAX_MATCH_KEYS:
                raise ConfigError(
                    f"[{self.__class__.__name__}] 'reconciliation.match_keys' must be a list of "
                    f"1 to {MAX_MATCH_KEYS} names, got: {keys!r}"
                )
            for key in keys:
                if not isinstance(key, str) or key.strip().lower() not in ALLOWED_MATCH_KEYS:
                    raise ConfigError(
                        f"[{self.__class__.__name__}] 'reconciliation.match_keys' entries must be "
                        f"one of {list(ALLOWED_MATCH_KEYS)}, got: {key!r}"
                    )

        tolerance = block.get("amount_tolerance")
        if tolerance is not None:
            if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)):
                raise ConfigError(
                    f"[{self.__class__.__name__}] 'reconciliation.amount_tolerance' must be a "
                    f"number, got: {tolerance!r}"
                )
            if not 0.0 <= float(tolerance) <= TOLERANCE_MAX:
                raise ConfigError(
                    f"[{self.__class__.__name__}] 'reconciliation.amount_tolerance' must be within "
                    f"[0.0, {TOLERANCE_MAX}], got: {tolerance!r}"
                )

        prompt = block.get("system_prompt")
        if prompt is not None and not isinstance(prompt, str):
            raise ConfigError(
                f"[{self.__class__.__name__}] 'reconciliation.system_prompt' must be a string, "
                f"got: {type(prompt).__name__}"
            )

    # -- Initial state ---------------------------------------------------------

    def _extra_initial_state(self) -> Dict[str, Any]:
        """Seed the validated ledger and the resolved domain settings.

        This is the inner half of the context bridge. The ledger comes from the
        ContextVar that ReconciliationGraphNode.extract_input() set moments ago;
        the settings are the operator defaults from config/config.yaml with the
        caller's validated per-request overrides applied on top.

        Seeding into state - rather than passing a config argument to the nodes -
        is what makes the settings live at all: BaseNode.__call__ calls
        execute(state) with no config argument, so a node parameter named
        `config` is never populated in a real run.
        """
        ledger = get_caller_ledger()
        block = self._reconciliation_config()

        match_keys: List[str] = ledger.get("match_keys") or block.get("match_keys") or list(DEFAULT_MATCH_KEYS)
        tolerance = ledger.get("amount_tolerance")
        if tolerance is None:
            tolerance = block.get("amount_tolerance", DEFAULT_AMOUNT_TOLERANCE)

        settings = {
            "match_keys": [str(key).strip().lower() for key in match_keys],
            "amount_tolerance": float(tolerance),
            "system_prompt": str(block.get("system_prompt", "")).strip(),
        }
        return {
            "validated_ledger": to_json(
                {
                    "source_a": ledger.get("source_a", []),
                    "source_b": ledger.get("source_b", []),
                }
            ),
            "domain_settings": to_json(settings),
        }

    # -- Node registration -----------------------------------------------------

    def register_nodes(self) -> None:
        """Register all 4 domain nodes.

        No super() call - BaseGraph.register_nodes() is abstract.
        Do NOT register initialize or finalize; those are outer backbone
        concerns handled by AgentBaseGraph in graph.py.

        Every node is instantiated with NO constructor arguments - SDK v1.0
        FunctionNode subclasses take no __init__; runtime settings reach them
        through state, seeded by _extra_initial_state(). Every key registered
        here is referenced in add_edges().
        """
        self._nodes["match_records"] = MatchRecordsNode()
        self._nodes["detect_discrepancies"] = DetectDiscrepanciesNode()
        self._nodes["classify_and_hypothesize"] = ClassifyAndHypothesizeNode()
        self._nodes["generate_triage_report"] = GenerateTriageReportNode()

    # -- Edge wiring -----------------------------------------------------------

    def add_edges(self) -> None:
        """Wire the linear reconciliation triage domain topology.

        Each step passes its partial-dict output into the shared State.
        For this template the topology is intentionally linear - no conditional
        branching between domain nodes. route() is implemented as required by
        the ABC but add_conditional_edges() is not used.
        """
        self._sg.add_edge(START, "match_records")
        self._sg.add_edge("match_records", "detect_discrepancies")
        self._sg.add_edge("detect_discrepancies", "classify_and_hypothesize")
        self._sg.add_edge("classify_and_hypothesize", "generate_triage_report")
        self._sg.add_edge("generate_triage_report", END)

    # -- Routing ---------------------------------------------------------------

    def route(self, state: State) -> str:
        """Conditional routing - required by BaseGraph ABC.

        For this linear topology add_conditional_edges() is not used, so this
        method is never called at runtime. It is implemented to satisfy the ABC
        contract. Returns END on error so an unexpected call does not re-enter a
        processing node.

        The parameter is annotated with this graph's OWN State: LangGraph reads
        a path callable's annotation as its input schema and projects away every
        field the annotation does not carry, so annotating a route with the base
        AgentState silently hides the very fields it routes on.
        """
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return "generate_triage_report"

    # -- Output shape ----------------------------------------------------------

    def get_output(self, state: AgentState) -> Dict[str, Any]:
        """Shape the output dict returned to the outer graph as sub_result.

        This dict is received by ReconciliationGraphNode.merge_output() in
        graph.py as the `sub_result` argument. Both methods are designed
        together to guarantee field-name consistency:

            Inner get_output()  emits: "triage_report", "status", ...
            Outer merge_output() reads: sub_result.get("triage_report"),
                                        sub_result.get("status")

        Additional fields (discrepancy_set, intake_notes, trace_id,
        correlation_id, node_history) are surfaced for observability /
        downstream extension.
        """
        return {
            "triage_report": state.get("triage_report"),
            "status": state.get("status"),
            "discrepancy_set": state.get("discrepancy_set"),
            "intake_notes": state.get("intake_notes"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
