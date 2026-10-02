# FIN-C2-071 - Unit Tests: MatchRecordsNode
#
# The node reads the VALIDATED ledger and the resolved settings out of state -
# both seeded by DomainWorkflowGraph._extra_initial_state(). It does no parsing
# and no coercion: a value it could not use never reached it.
#
# The settings are read from state rather than from an execute() `config`
# argument. BaseNode.__call__ calls execute(state) with one argument, so a node
# parameter named `config` is never populated in a real run - only in a unit
# test that passes it directly, which is how a whole configuration surface can
# be "covered" by green tests while being dead in production.
#
# Mirrors docs/03_test_spec.md section 2.2 (MATCH).
# Deterministic - no LLM, no network. framework.* / src.* imports only.

from src.nodes.match_records_node import MatchRecordsNode
from src.schemas.state import from_json, to_json


def _state(source_a, source_b, **settings):
    resolved = {"match_keys": ["reference", "amount"], "amount_tolerance": 0.01}
    resolved.update(settings)
    return {
        "validated_ledger": to_json({"source_a": source_a, "source_b": source_b}),
        "domain_settings": to_json(resolved),
    }


def _run(source_a, source_b, **settings):
    out = MatchRecordsNode().execute(_state(source_a, source_b, **settings))
    return from_json(out["matched_records"], []), from_json(out["unmatched_records"], {})


_A1 = {"reference": "R1", "amount": 500.0, "date": "2026-03-01"}
_B1 = {"reference": "R1", "amount": 500.0, "date": "2026-03-01"}


class TestMatching:
    def test_identical_records_match(self):
        matched, unmatched = _run([_A1], [_B1])
        assert len(matched) == 1
        assert matched[0]["reference"] == "R1"
        assert unmatched == {"unmatched_in_a": [], "unmatched_in_b": []}

    def test_amount_difference_splits_under_the_default_composite_key(self):
        # The default key is (reference, amount), so a same-reference pair with
        # different amounts does not match - it is unmatched on both sides.
        matched, unmatched = _run([_A1], [{**_B1, "amount": 530.0}])
        assert matched == []
        assert len(unmatched["unmatched_in_a"]) == 1
        assert len(unmatched["unmatched_in_b"]) == 1

    def test_reference_only_key_matches_across_an_amount_difference(self):
        matched, unmatched = _run([_A1], [{**_B1, "amount": 530.0}], match_keys=["reference"])
        assert len(matched) == 1
        assert matched[0]["amount_a"] == 500.0
        assert matched[0]["amount_b"] == 530.0

    def test_date_in_the_key_separates_records_posted_on_different_days(self):
        matched, unmatched = _run(
            [_A1],
            [{**_B1, "date": "2026-03-02"}],
            match_keys=["reference", "amount", "date"],
        )
        assert matched == []
        assert len(unmatched["unmatched_in_a"]) == 1

    def test_duplicate_keys_consume_one_partner_each(self):
        matched, unmatched = _run([_A1, dict(_A1)], [_B1])
        assert len(matched) == 1
        assert len(unmatched["unmatched_in_a"]) == 1

    def test_unmatched_in_b_is_reported(self):
        matched, unmatched = _run([], [_B1])
        assert matched == []
        assert unmatched["unmatched_in_b"] == [_B1]

    def test_float_noise_does_not_break_a_match(self):
        matched, _ = _run([{**_A1, "amount": 500.001}], [_B1])
        assert len(matched) == 1


class TestSettingsAreLive:
    def test_match_keys_from_state_change_the_partition(self):
        # The same two records partition differently under two settings - which
        # is the whole point of the setting being live.
        default_matched, _ = _run([_A1], [{**_B1, "amount": 530.0}])
        override_matched, _ = _run([_A1], [{**_B1, "amount": 530.0}], match_keys=["reference"])
        assert len(default_matched) == 0
        assert len(override_matched) == 1

    def test_absent_settings_fall_back_to_the_declared_defaults(self):
        out = MatchRecordsNode().execute({"validated_ledger": to_json({"source_a": [_A1], "source_b": [_B1]})})
        assert len(from_json(out["matched_records"], [])) == 1

    def test_absent_ledger_yields_an_empty_partition(self):
        out = MatchRecordsNode().execute({})
        assert from_json(out["matched_records"], []) == []
        assert from_json(out["unmatched_records"], {}) == {
            "unmatched_in_a": [],
            "unmatched_in_b": [],
        }


class TestStateSafety:
    def test_outputs_are_json_strings(self):
        out = MatchRecordsNode().execute(_state([_A1], [_B1]))
        assert isinstance(out["matched_records"], str)
        assert isinstance(out["unmatched_records"], str)
