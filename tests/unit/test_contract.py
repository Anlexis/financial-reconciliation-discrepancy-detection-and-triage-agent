# FIN-C2-071 - Unit Tests: the caller-data contract (src/schemas/contract.py)
#
# Every caller value is refused or made inert here, so this is where the bounds
# are pinned. Two properties matter more than the individual cases:
#
#   - FAIL CLOSED on every numeric. NaN and Infinity parse fine through float()
#     and then compare False against every threshold, which turns "is this
#     difference beyond tolerance?" into a silent no. They are refused, not
#     compared.
#   - THE VALUE IS NEVER ECHOED. A rejected value is caller data; the error
#     names the field path and the rule only.
#
# docs/03_test_spec.md section 2.1 (PRE).
# Deterministic - no LLM, no network. framework.* / src.* imports only.

import pytest

from src.schemas.contract import (
    AMOUNT_ABS_MAX,
    MAX_RECORDS_PER_SOURCE,
    ContractError,
    finite_in_range,
    validate_request,
)

_GOOD = {
    "source_a": [{"reference": "INV-1001", "amount": 1500.0, "date": "2026-01-05"}],
    "source_b": [{"reference": "INV-1001", "amount": 1400.0, "date": "2026-01-05"}],
}

# Every non-finite / non-numeric form a caller can actually send, including the
# raw JSON floats that arrive as Python objects rather than as strings.
NON_FINITE = [
    "NaN",
    "nan",
    "Infinity",
    "-Infinity",
    "inf",
    float("nan"),
    float("inf"),
    float("-inf"),
]


class TestFiniteInRange:
    @pytest.mark.parametrize("value", NON_FINITE)
    def test_non_finite_is_refused(self, value):
        with pytest.raises(ContractError):
            finite_in_range(value, field="amount", lo=-10.0, hi=10.0)

    @pytest.mark.parametrize("value", [True, False])
    def test_bool_is_refused(self, value):
        # isinstance(True, int) is True in Python, so a bare int check accepts
        # `true` as 1 - the exact confusion this rejects.
        with pytest.raises(ContractError):
            finite_in_range(value, field="amount", lo=-10.0, hi=10.0)

    @pytest.mark.parametrize("value", ["", "  ", "abc", None, [], {}, "1.2.3"])
    def test_non_numeric_is_refused(self, value):
        with pytest.raises(ContractError):
            finite_in_range(value, field="amount", lo=-10.0, hi=10.0)

    @pytest.mark.parametrize("value", [10.1, -10.1, 1e30])
    def test_out_of_range_is_refused(self, value):
        with pytest.raises(ContractError):
            finite_in_range(value, field="amount", lo=-10.0, hi=10.0)

    def test_in_range_values_pass(self):
        assert finite_in_range("1,500.25", field="amount", lo=-1e6, hi=1e6) == 1500.25
        assert finite_in_range(7, field="amount", lo=0.0, hi=10.0) == 7.0

    def test_error_names_the_field_not_the_value(self):
        with pytest.raises(ContractError) as excinfo:
            finite_in_range("swordfish-9000", field="source_a[2].amount", lo=0.0, hi=1.0)
        message = str(excinfo.value)
        assert "source_a[2].amount" in message
        assert "swordfish" not in message


class TestRecordValidation:
    def test_valid_request_survives_as_three_inert_fields(self):
        out = validate_request(_GOOD)
        assert out["source_a"] == [{"reference": "INV-1001", "amount": 1500.0, "date": "2026-01-05"}]
        assert out["match_keys"] == ["reference", "amount"]
        assert out["amount_tolerance"] == 0.01

    def test_unknown_record_keys_are_dropped_not_carried(self):
        payload = {
            "source_a": [
                {
                    "reference": "INV-1",
                    "amount": 1.0,
                    "description": "Wire Transfer Settlement",
                    "note": "<script>alert(1)</script>",
                }
            ],
            "source_b": [],
        }
        record = validate_request(payload)["source_a"][0]
        assert set(record) == {"reference", "amount", "date"}

    @pytest.mark.parametrize(
        "reference",
        [
            "has space",
            "pipe|injection",
            "<|im_start|>",
            "x" * 33,
            "",
            "   ",
            123,
            None,
        ],
    )
    def test_non_inert_reference_is_refused(self, reference):
        with pytest.raises(ContractError):
            validate_request({"source_a": [{"reference": reference, "amount": 1.0}], "source_b": []})

    @pytest.mark.parametrize("value", NON_FINITE)
    def test_non_finite_amount_is_refused_by_field(self, value):
        with pytest.raises(ContractError) as excinfo:
            validate_request({"source_a": [{"reference": "R1", "amount": value}], "source_b": []})
        assert excinfo.value.field == "source_a[0].amount"

    def test_over_magnitude_amount_is_refused(self):
        with pytest.raises(ContractError):
            validate_request({"source_a": [{"reference": "R1", "amount": AMOUNT_ABS_MAX * 10}], "source_b": []})

    @pytest.mark.parametrize("bad_date", ["2026-13-01", "2026/01/05", "05-01-2026", "2026-02-30"])
    def test_malformed_date_is_refused(self, bad_date):
        with pytest.raises(ContractError):
            validate_request({"source_a": [{"reference": "R1", "amount": 1.0, "date": bad_date}], "source_b": []})

    def test_missing_required_field_is_refused_by_name(self):
        with pytest.raises(ContractError) as excinfo:
            validate_request({"source_a": [{"amount": 1.0}], "source_b": []})
        assert excinfo.value.field == "source_a[0].reference"

    def test_record_count_cap(self):
        records = [{"reference": f"R{i}", "amount": 1.0} for i in range(MAX_RECORDS_PER_SOURCE + 1)]
        with pytest.raises(ContractError) as excinfo:
            validate_request({"source_a": records, "source_b": []})
        assert excinfo.value.field == "source_a"

    def test_empty_request_is_refused(self):
        with pytest.raises(ContractError):
            validate_request({"source_a": [], "source_b": []})

    def test_source_must_be_a_list(self):
        with pytest.raises(ContractError):
            validate_request({"source_a": {"reference": "R1"}, "source_b": []})


class TestOverrides:
    def test_match_keys_restricted_to_the_closed_set(self):
        with pytest.raises(ContractError):
            validate_request({**_GOOD, "match_keys": ["reference", "customer_name"]})

    def test_match_keys_must_not_be_empty(self):
        with pytest.raises(ContractError):
            validate_request({**_GOOD, "match_keys": []})

    def test_match_keys_deduplicated_and_lowercased(self):
        assert validate_request({**_GOOD, "match_keys": ["Reference", "reference"]})["match_keys"] == ["reference"]

    @pytest.mark.parametrize("value", NON_FINITE + [-1.0, True])
    def test_tolerance_bounds(self, value):
        with pytest.raises(ContractError) as excinfo:
            validate_request({**_GOOD, "amount_tolerance": value})
        assert excinfo.value.field == "amount_tolerance"

    def test_tolerance_override_is_applied(self):
        assert validate_request({**_GOOD, "amount_tolerance": 5.5})["amount_tolerance"] == 5.5


class TestAliases:
    def test_legacy_source_and_field_aliases_are_accepted(self):
        payload = {
            "ledger_a": [{"ref": "R1", "value": "1,000", "value_date": "2026-01-05"}],
            "ledger_b": [],
        }
        out = validate_request(payload)
        assert out["source_a"] == [{"reference": "R1", "amount": 1000.0, "date": "2026-01-05"}]
