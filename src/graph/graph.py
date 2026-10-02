"""AgentCore Platform v1.0"""

# FIN-C2-071 - Outer graph (AgentBaseGraph; Cat 2 two-layer nested architecture)
#
# Financial Reconciliation & Discrepancy Triage Agent (Cat 2 domain workflow).
#
# Architecture (Cat 2):
#
#   Outer backbone (fixed - identical to Cat 1, do NOT override add_edges()):
#     START -> initialize -> pre_process -> main -> {route} -> post_process -> finalize -> END
#                                             |  (RETRY, max 3)
#                                             -> pre_process
#
#   `main` slot is a GraphNode subclass (ReconciliationGraphNode) that delegates
#   the full reconciliation domain workflow to DomainWorkflowGraph (inner
#   BaseGraph).
#
#   Domain complexity is fully encapsulated inside the inner graph. The outer
#   backbone is never modified.
#
# Directory layout:
#   src/graph/graph.py                 <- outer graph (this file)
#   src/graph/domain_workflow_graph.py <- inner graph (multi-step topology)
#   src/graph/context_bridge.py        <- validated-ledger hand-off across the boundary
#
# Rules enforced:
#   - FinancialReconciliationDiscrepancyAgent inherits AgentBaseGraph (L1 Base - direct inheritance)
#   - super().register_nodes() called first (fills initialize + finalize)
#   - ReconciliationGraphNode assigned to self._nodes["main"]
#   - merge_output() returns only changed keys
#   - add_edges() NOT overridden on the outer graph
#   - No Level 0 agenticstar imports

from typing import Any, ClassVar, Dict, Optional

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.trust_level import TrustLevel
from src.graph.context_bridge import set_caller_ledger
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.runtime_config import load_runtime_config
from src.schemas.state import State, from_json


class ReconciliationGraphNode(GraphNode):
    """GraphNode subclass assigned to the `main` slot of the outer agent.

    Wraps DomainWorkflowGraph (inner Cat 2 BaseGraph).
    Called by AgentBaseGraph backbone after pre_process and before post_process.

    Contracts:
      get_subgraph()    - instantiate and return DomainWorkflowGraph
      extract_input()   - stash the validated ledger for the inner graph and
                          return the free-text string passed to invoke()
      merge_output()    - map sub_result fields into outer state delta (changed keys only)
      error_strategy    - "propagate": re-raise inner errors as SubgraphError (fail-fast)
    """

    # GraphNode does not inherit FunctionNode's declaration enforcement, so the
    # trust level is stated explicitly rather than falling back to the
    # permissive BaseNode default.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    # "propagate": re-raise inner graph exceptions as SubgraphError (default - fail fast).
    # "handle": call on_subgraph_error() instead - use for graceful degradation.
    error_strategy: ClassVar[str] = "propagate"

    # False: HITL interrupts are handled inside the inner graph only.
    # True: surface inner HITL interrupt to the outer caller.
    propagate_hitl: ClassVar[bool] = False

    def get_subgraph(self) -> Any:
        """Instantiate and return the inner domain workflow graph.

        DomainWorkflowGraph is imported lazily (inside the method) to avoid
        circular-import risk at module load time and to match the pattern in
        the Cat 2 scaffold sample.

        The runtime parameters come from config/config.yaml, so a declared
        amount_tolerance or match_keys reaches the inner nodes. The inner graph
        seeds them into state in _extra_initial_state(); the nodes read them
        from state, because BaseNode.__call__ invokes execute(state) with no
        config argument and any `config=` parameter on a node is therefore dead.
        """
        from src.graph.domain_workflow_graph import DomainWorkflowGraph

        return DomainWorkflowGraph(config=load_runtime_config())

    def extract_input(self, state: AgentState) -> str:
        """Stash the validated ledger, then return the string for invoke().

        The ledger travels through the ContextVar bridge rather than through
        this return value: the SDK's GraphNode forwards nothing but the string
        and the InvocationContext, and the string channel is masked by the
        platform. See src/graph/context_bridge.py.
        """
        set_caller_ledger(from_json(state.get("validated_ledger"), {}))
        return str(state.get("validated_input", state.get("user_input", "")))

    def merge_output(self, state: AgentState, sub_result: Dict[str, Any]) -> Dict[str, Any]:
        """Map inner graph sub_result back into the outer state delta.

        sub_result is the dict returned by DomainWorkflowGraph.get_output().
        Returns ONLY changed keys - never the full state.

        Key coupling (designed together with DomainWorkflowGraph.get_output()):
          Inner get_output() emits  -> "triage_report", "status", ...
          This merge_output() reads -> sub_result.get("triage_report"),
                                       sub_result.get("status")

        reconciliation_report (str | None): final rendered Markdown triage
          report; written by GenerateTriageReportNode inside the inner graph.
        status (str | None): terminal AgentStatus value from the inner graph run.
        """
        return {
            "reconciliation_report": sub_result.get("triage_report"),
            # PostProcessNode (outer post_process slot) reads state.get("result").
            # The inner graph emits the rendered report under "triage_report", so
            # map it to "result" as well - otherwise the final output surfaced by
            # PostProcessNode (and the output gate) is always empty.
            "result": sub_result.get("triage_report"),
            "status": sub_result.get("status"),
            "intake_notes": sub_result.get("intake_notes"),
        }


class FinancialReconciliationDiscrepancyAgent(AgentBaseGraph):
    """Outer graph for FIN-C2-071 (Cat 2).

    Inherits AgentBaseGraph directly (L1 Base). Domain logic is fully
    encapsulated in ReconciliationGraphNode (main slot), which delegates to
    DomainWorkflowGraph (inner BaseGraph).

    Backbone (fixed - identical to Cat 1):
        START -> initialize -> pre_process -> main -> post_process -> finalize -> END

    register_nodes() is the ONLY override:
      - super().register_nodes() fills: initialize, finalize (framework defaults)
      - pre_process:  PreProcessNode (caller contract + injection screen)
      - main:         ReconciliationGraphNode (delegates to DomainWorkflowGraph)
      - post_process: PostProcessNode (output gate)

    add_edges() is NOT overridden - backbone wiring belongs to the framework.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Default the runtime parameters to config/config.yaml.

        The framework reads max_retry off this mapping in its own routing loop,
        so a bare Graph() built by a boundary test or by the standalone server
        must carry the same parameters an AgentRegistry-built one would. An
        explicit config argument still wins.
        """
        super().__init__(config if config is not None else load_runtime_config())

    @property
    def name(self) -> str:
        """Agent identifier registered with AgentRegistry."""
        return "FinancialReconciliationDiscrepancyAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        """Fill all 5 backbone slots.

        super().register_nodes() MUST be called first - it injects the
        framework's default InitializeNode (sets schema_version, session_id,
        trust_level) and FinalizeNode (builds response_metadata, total_time_ms).
        """
        super().register_nodes()  # fills: initialize, finalize

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = ReconciliationGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden - backbone wiring belongs to the framework.


# Back-compat alias - config/agent.yaml declares the entry class as
# src.graph.graph.FinancialReconciliationDiscrepancyAgent, and src/api/server.py
# imports `Graph`. Keep both names pointing at the agent.
Graph = FinancialReconciliationDiscrepancyAgent
