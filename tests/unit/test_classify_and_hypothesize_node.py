# FIN-C2-071 - Unit Tests: ClassifyAndHypothesizeNode
#
# The classifier is deterministic, so every mapping is pinned here rather than
# sampled. The categories, hypotheses and severities are CLOSED SETS - that is
# what lets the report gate re-check the rendered table against them.
#
# Mirrors docs/03_test_spec.md section 2.4 (CLASSIFY).
# Deterministic - no LLM, no network. framework.* / src.* imports only.

import pytest

from src.nodes.classify_and_hypothesize_node import (
    CATEGORIES,
    HYPOTHESES,
    SEVERITIES,
    ClassifyAndHypothesizeNode,
)
from src.schemas.state import from_json, to_json


def _run(discrepancies, system_prompt="Classify the discrepancy."):
    out = ClassifyAndHypothesizeNode().execute(
        {
            "discrepancy_set": to_json(discrepancies),
            "domain_settings": to_json({"system_prompt": system_prompt}),
        }
    )
    return from_json(out["classified_discrepancies"], [])


def _disc(kind, **extra):
    return {"id": "D0001", "kind": kind, "key": "R1", "amount_delta": None, **extra}


class TestClassification:
    @pytest.mark.parametrize(
        "kind,category,severity",
        [
            ("value_mismatch", "fx_rounding", "high"),
            ("timing_anomaly", "timing_cutoff", "medium"),
            ("missing_in_b", "omission", "high"),
            ("missing_in_a", "duplicate_entry", "high"),
        ],
    )
    def test_known_kinds_map_to_their_rule(self, kind, category, severity):
        classified = _run([_disc(kind)])[0]
        assert classified["category"] == category
        assert classified["severity"] == severity

    def test_unknown_kind_falls_back_to_manual_review(self):
        classified = _run([_disc("something_new")])[0]
        assert classified["category"] == "data_entry_error"
        assert classified["severity"] == "medium"

    def test_large_value_mismatch_escalates_to_critical(self):
        classified = _run([_disc("value_mismatch", amount_delta=5000.0)])[0]
        assert classified["severity"] == "critical"

    def test_small_value_mismatch_does_not_escalate(self):
        assert _run([_disc("value_mismatch", amount_delta=12.5)])[0]["severity"] == "high"

    def test_negative_delta_escalates_on_magnitude(self):
        assert _run([_disc("value_mismatch", amount_delta=-5000.0)])[0]["severity"] == "critical"

    def test_boolean_delta_does_not_escalate(self):
        # isinstance(True, int) is True; a bare numeric check would read `true`
        # as 1 and, worse, a large bool-shaped value as an escalation trigger.
        assert _run([_disc("value_mismatch", amount_delta=True)])[0]["severity"] == "high"

    def test_original_fields_are_preserved(self):
        classified = _run([_disc("missing_in_b")])[0]
        assert classified["id"] == "D0001"
        assert classified["key"] == "R1"


class TestClosedSets:
    def test_every_output_comes_from_the_closed_sets(self):
        classified = _run(
            [_disc(kind) for kind in ("value_mismatch", "timing_anomaly", "missing_in_b", "missing_in_a", "other")]
        )
        for item in classified:
            assert item["category"] in CATEGORIES
            assert item["severity"] in SEVERITIES
            assert item["hypothesis"] in HYPOTHESES

    def test_empty_input_yields_empty_output(self):
        assert _run([]) == []


class TestPromptGovernance:
    def test_the_governed_prompt_is_read_from_state(self):
        # The prompt reaches the node through domain_settings, seeded from
        # config/config.yaml - not through an execute() config argument, which
        # the framework never supplies.
        assert _run([_disc("missing_in_b")], system_prompt="A custom instruction.")

    def test_absent_settings_do_not_break_classification(self):
        out = ClassifyAndHypothesizeNode().execute({"discrepancy_set": to_json([_disc("missing_in_b")])})
        assert len(from_json(out["classified_discrepancies"], [])) == 1
