# FIN-C2-071 - Unit Tests: nested Cat-2 graph composition (end-to-end)
#
# Drives the REAL outer agent (FinancialReconciliationDiscrepancyAgent / Graph)
# end-to-end through invoke(). The inner DomainWorkflowGraph runs all 4 domain
# nodes; GenerateTriageReportNode sets status=SUCCESS, which the outer
# merge_output maps to the outer state so the backbone routes
# main -> post_process -> finalize.
#
# Two composition properties are asserted here that unit-level node tests cannot
# reach:
#   - the caller's records CROSS THE GRAPH BOUNDARY. The SDK's GraphNode
#     forwards only a string and the InvocationContext to the inner graph, so
#     without the context bridge the matcher would run over nothing while every
#     node test stayed green.
#   - a value declared in config/config.yaml CHANGES BEHAVIOUR through the whole
#     stack, rather than being read into a mapping nothing consults.
#
# Mirrors docs/03_test_spec.md section 3 (INT-01 .. INT-05).
# Deterministic - no LLM, no network. framework.* / src.* imports only.

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.context_bridge import get_caller_ledger, set_caller_ledger
from src.graph.domain_workflow_graph import DomainWorkflowGraph
from src.graph.graph import (
    FinancialReconciliationDiscrepancyAgent,
    Graph,
    ReconciliationGraphNode,
)
from src.schemas.state import State, from_json, to_json

# Two record sets that produce a known mix of discrepancies under the default
# composite key (reference, amount):
#   R1: same reference + amount            -> matched, no discrepancy
#   R2: same reference, amount 100 vs 130  -> unmatched on both sides
#   R3: only in source A                   -> missing_in_b
#   R4: only in source B                   -> missing_in_a
_LEDGER = {
    "source_a": [
        {"reference": "R1", "amount": 500.0, "date": "2026-03-01"},
        {"reference": "R2", "amount": 100.0, "date": "2026-03-02"},
        {"reference": "R3", "amount": 75.0, "date": "2026-03-03"},
    ],
    "source_b": [
        {"reference": "R1", "amount": 500.0, "date": "2026-03-01"},
        {"reference": "R2", "amount": 130.0, "date": "2026-03-02"},
        {"reference": "R4", "amount": 60.0, "date": "2026-03-04"},
    ],
}

_REQUEST = "Reconcile the March extract against the ledger and triage the differences."


def _run(context=None, request=_REQUEST, trust=TrustLevel.VERIFIED_EXTERNAL):
    # VERIFIED_EXTERNAL is the entry trust level config/agent.yaml declares, so
    # it is the level the tests must prove the agent works at. Asserting only at
    # INTERNAL would hide an agent that no real caller can use.
    ctx = InvocationContext(session_id="test", caller_trust_level=trust, caller_id="test-suite")
    return Graph().invoke(request, ctx=ctx, input_context=_LEDGER if context is None else context)


class TestOuterGraphConstruction:
    def test_int_02_inherits_agent_base_graph(self):
        from framework.graph.agent_base_graph import AgentBaseGraph

        assert issubclass(FinancialReconciliationDiscrepancyAgent, AgentBaseGraph)

    def test_state_schema_is_state(self):
        assert FinancialReconciliationDiscrepancyAgent().state_schema is State

    def test_int_02_main_slot_is_reconciliation_graph_node(self):
        agent = FinancialReconciliationDiscrepancyAgent()
        agent.compile()
        assert isinstance(agent._nodes.get("main"), ReconciliationGraphNode)

    def test_compile_fills_all_backbone_slots(self):
        agent = FinancialReconciliationDiscrepancyAgent()
        agent.compile()
        for slot in ("initialize", "pre_process", "main", "post_process", "finalize"):
            assert agent._nodes.get(slot) is not None, f"backbone slot not filled: {slot}"

    def test_int_05_graph_alias(self):
        assert Graph is FinancialReconciliationDiscrepancyAgent

    def test_runtime_config_is_loaded_by_default(self):
        # A bare Graph() - the shape the deployed server and the boundary tests
        # both use - must carry the same parameters a registry-built one does.
        assert FinancialReconciliationDiscrepancyAgent().config.get("max_retry") == 3

    def test_explicit_config_wins(self):
        assert FinancialReconciliationDiscrepancyAgent(config={"max_retry": 1}).config == {"max_retry": 1}


class TestInnerGraphConstruction:
    def test_int_01_inner_graph_registers_four_domain_nodes(self):
        inner = DomainWorkflowGraph()
        inner.register_nodes()
        assert set(inner._nodes.keys()) == {
            "match_records",
            "detect_discrepancies",
            "classify_and_hypothesize",
            "generate_triage_report",
        }

    def test_inner_graph_name_and_schema(self):
        inner = DomainWorkflowGraph()
        assert inner.name == "fin_c2_071_reconciliation_triage_workflow"
        assert inner.state_schema is State

    def test_inner_graph_get_output_shape(self):
        inner = DomainWorkflowGraph()
        out = inner.get_output({"triage_report": "R", "status": AgentStatus.SUCCESS.value})
        assert out["triage_report"] == "R"
        assert out["status"] == AgentStatus.SUCCESS.value

    @pytest.mark.parametrize(
        "block",
        [
            {"match_keys": []},
            {"match_keys": ["customer_name"]},
            {"match_keys": "reference"},
            {"amount_tolerance": -1.0},
            {"amount_tolerance": "loose"},
            {"amount_tolerance": True},
            {"system_prompt": 42},
        ],
    )
    def test_out_of_bounds_operator_config_fails_at_compile(self, block):
        from framework.errors import ConfigError

        # Operator config is a deployment statement, not caller data: a value
        # outside its bounds must stop start-up rather than silently revert to a
        # default the operator believes they overrode.
        with pytest.raises(ConfigError):
            DomainWorkflowGraph(config={"reconciliation": block}).compile()

    def test_valid_operator_config_compiles(self):
        DomainWorkflowGraph(config={"reconciliation": {"match_keys": ["reference"], "amount_tolerance": 1.0}}).compile()


class TestContextBridge:
    def test_extract_input_stashes_the_validated_ledger(self):
        set_caller_ledger(None)
        node = ReconciliationGraphNode()
        node.extract_input({"validated_ledger": to_json(_LEDGER), "validated_input": "text"})
        assert get_caller_ledger()["source_a"] == _LEDGER["source_a"]

    def test_extract_input_returns_the_free_text_not_the_records(self):
        node = ReconciliationGraphNode()
        assert node.extract_input({"validated_input": "VI", "user_input": "UI"}) == "VI"
        assert node.extract_input({"user_input": "UI"}) == "UI"

    def test_inner_graph_seeds_the_stashed_ledger_into_state(self):
        set_caller_ledger({**_LEDGER, "match_keys": ["reference"], "amount_tolerance": 9.0})
        seeded = DomainWorkflowGraph()._extra_initial_state()
        assert from_json(seeded["validated_ledger"], {})["source_a"] == _LEDGER["source_a"]
        settings = from_json(seeded["domain_settings"], {})
        assert settings["match_keys"] == ["reference"]
        assert settings["amount_tolerance"] == 9.0

    def test_operator_defaults_apply_when_the_caller_overrides_nothing(self):
        set_caller_ledger(_LEDGER)
        seeded = DomainWorkflowGraph(
            config={"reconciliation": {"match_keys": ["date"], "amount_tolerance": 3.0}}
        )._extra_initial_state()
        settings = from_json(seeded["domain_settings"], {})
        assert settings["match_keys"] == ["date"]
        assert settings["amount_tolerance"] == 3.0


class TestMergeOutputMapping:
    def test_int_03_merge_output_maps_triage_report(self):
        node = ReconciliationGraphNode()
        delta = node.merge_output({}, {"triage_report": "RPT", "status": AgentStatus.SUCCESS.value})
        assert delta["reconciliation_report"] == "RPT"
        assert delta["result"] == "RPT"
        assert delta["status"] == AgentStatus.SUCCESS.value


class TestEndToEndInvoke:
    """Full agent run at the DECLARED entry trust level."""

    def test_invoke_returns_success(self):
        result = _run()
        assert (
            result.get("status") == AgentStatus.SUCCESS.value
        ), f"Expected success, got {result.get('status')}. result={result!r}"

    def test_int_04_invoke_output_is_populated_report(self):
        output = _run().get("output")
        assert isinstance(output, str) and output.strip()
        assert "Reconciliation Triage Report" in output

    def test_int_04_report_reflects_expected_discrepancies(self):
        output = _run().get("output", "")
        assert "omission" in output
        assert "duplicate_entry" in output
        # R2 / R3 / R4 are the affected keys; R1 reconciles and is not a row.
        for key in ("R2", "R3", "R4"):
            assert key in output

    def test_e2e_traverses_post_process_gate(self):
        """E2E ROUTING ASSERTION: node_history records each node's class name,
        so PreProcessNode, ReconciliationGraphNode AND PostProcessNode must all
        appear - the output gate is traversed, not bypassed."""
        history = _run().get("node_history", [])
        for cls_name in ("PreProcessNode", "ReconciliationGraphNode", "PostProcessNode"):
            assert cls_name in history, f"node_history missing {cls_name}: {history}"

    def test_agreeing_sources_report_a_clean_reconciliation(self):
        agreeing = {"source_a": _LEDGER["source_a"], "source_b": _LEDGER["source_a"]}
        result = _run(context=agreeing)
        assert result.get("status") == AgentStatus.SUCCESS.value
        assert "reconcile" in result.get("output", "").lower()

    def test_an_unreadable_request_is_refused_not_reconciled(self):
        # The defect this pins: before the caller contract existed, a request
        # that carried no ledger at all was answered "the two sources
        # reconcile" with status success.
        result = _run(context={})
        assert result.get("status") == AgentStatus.ERROR.value
        assert "reconcile" not in (result.get("output") or "").lower()

    def test_a_caller_override_changes_the_result_end_to_end(self):
        strict = _run(context={**_LEDGER, "match_keys": ["reference"]})
        loose = _run(context={**_LEDGER, "match_keys": ["reference"], "amount_tolerance": 100.0})
        assert "**Total discrepancies:** 3" in strict.get("output", "")
        assert "**Total discrepancies:** 2" in loose.get("output", "")

    def test_anonymous_caller_is_denied(self):
        result = _run(trust=TrustLevel.ANONYMOUS)
        assert result.get("status") == AgentStatus.ERROR.value


class TestStateRoundTrip:
    """State helper round-trip: producers to_json() on write, consumers
    from_json() on read (msgpack-safe checkpointing)."""

    def test_to_from_json_list_round_trip(self):
        original = [{"id": "D1", "kind": "value_mismatch", "amount_delta": 12.5}]
        assert from_json(to_json(original)) == original

    def test_to_from_json_dict_round_trip(self):
        original = {"unmatched_in_a": [{"reference": "A1"}], "unmatched_in_b": []}
        assert from_json(to_json(original)) == original

    def test_to_json_none_passes_through(self):
        assert to_json(None) is None

    def test_from_json_malformed_returns_default(self):
        assert from_json("{not valid json", default=[]) == []
        assert from_json(None, default={}) == {}
        assert from_json("", default=[]) == []
