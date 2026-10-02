# FIN-C2-071 - Unit Tests: DetectDiscrepanciesNode
#
# Covers the three discrepancy kinds, the tolerance boundary, and the reason the
# tolerance is guarded rather than trusted: a NaN threshold compares False
# against every delta, so an unguarded tolerance would report a perfectly
# reconciled ledger over records that do not agree at all.
#
# Mirrors docs/03_test_spec.md section 2.3 (DETECT).
# Deterministic - no LLM, no network. framework.* / src.* imports only.

import pytest

from src.nodes.detect_discrepancies_node import DetectDiscrepanciesNode
from src.schemas.contract import DEFAULT_AMOUNT_TOLERANCE
from src.schemas.state import from_json, to_json


def _state(matched=None, unmatched=None, **settings):
    resolved = {"match_keys": ["reference"], "amount_tolerance": DEFAULT_AMOUNT_TOLERANCE}
    resolved.update(settings)
    return {
        "matched_records": to_json(matched or []),
        "unmatched_records": to_json(unmatched or {"unmatched_in_a": [], "unmatched_in_b": []}),
        "domain_settings": to_json(resolved),
    }


def _run(**kwargs):
    return from_json(DetectDiscrepanciesNode().execute(_state(**kwargs))["discrepancy_set"], [])


_PAIR = {
    "reference": "R1",
    "amount_a": 500.0,
    "amount_b": 500.0,
    "date_a": "2026-03-01",
    "date_b": "2026-03-01",
}


class TestDiscrepancyKinds:
    def test_matching_pair_yields_nothing(self):
        assert _run(matched=[_PAIR]) == []

    def test_value_mismatch_beyond_tolerance(self):
        found = _run(matched=[{**_PAIR, "amount_b": 400.0}])
        assert [d["kind"] for d in found] == ["value_mismatch"]
        assert found[0]["amount_delta"] == 100.0

    def test_difference_inside_tolerance_is_not_a_discrepancy(self):
        assert _run(matched=[{**_PAIR, "amount_b": 500.005}]) == []

    def test_timing_anomaly_when_amounts_agree_but_dates_differ(self):
        found = _run(matched=[{**_PAIR, "date_b": "2026-03-04"}])
        assert [d["kind"] for d in found] == ["timing_anomaly"]

    def test_missing_in_b(self):
        found = _run(unmatched={"unmatched_in_a": [{"reference": "R9"}], "unmatched_in_b": []})
        assert [d["kind"] for d in found] == ["missing_in_b"]
        assert found[0]["key"] == "R9"

    def test_missing_in_a(self):
        found = _run(unmatched={"unmatched_in_a": [], "unmatched_in_b": [{"reference": "R8"}]})
        assert [d["kind"] for d in found] == ["missing_in_a"]

    def test_ids_are_assigned_by_the_pipeline_and_unique(self):
        found = _run(
            matched=[{**_PAIR, "amount_b": 1.0}],
            unmatched={"unmatched_in_a": [{"reference": "R9"}], "unmatched_in_b": []},
        )
        ids = [d["id"] for d in found]
        assert ids == sorted(ids)
        assert len(set(ids)) == len(ids)

    def test_detail_text_carries_no_caller_values(self):
        found = _run(matched=[{**_PAIR, "amount_b": 400.0}])
        assert "400" not in found[0]["detail"]
        assert "500" not in found[0]["detail"]


class TestToleranceIsLive:
    def test_declared_tolerance_suppresses_a_mismatch(self):
        assert _run(matched=[{**_PAIR, "amount_b": 400.0}], amount_tolerance=200.0) == []

    def test_declared_tolerance_admits_a_mismatch(self):
        assert len(_run(matched=[{**_PAIR, "amount_b": 400.0}], amount_tolerance=50.0)) == 1

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, "abc", None, True, 1e30])
    def test_unusable_tolerance_falls_back_to_the_declared_default(self, bad):
        # A NaN threshold compares False against every delta; without this guard
        # the node would report a clean reconciliation over a 100-unit gap.
        found = _run(matched=[{**_PAIR, "amount_b": 400.0}], amount_tolerance=bad)
        assert [d["kind"] for d in found] == ["value_mismatch"]

    def test_absent_settings_use_the_declared_default(self):
        out = DetectDiscrepanciesNode().execute({"matched_records": to_json([{**_PAIR, "amount_b": 400.0}])})
        assert len(from_json(out["discrepancy_set"], [])) == 1
