# PB - Output Boundary: nothing un-gated reaches the caller, on any path.
#
# Three separate properties, because passing one of them proves nothing about
# the others:
#
#  (1) THE GATE'S PATTERN SET IS A UNION (TestOutputGateCoverage).
#      The framework's own detect_credentials() is the floor: a value it catches
#      and this template misses makes the framework raise INSIDE the node
#      wrapper, which discards whatever the node cleared and returns a bare
#      error partial - and the graph's output then falls back to the un-gated
#      state["result"]. A detector gap is a containment bypass. The local
#      patterns are kept alongside it, because they catch shapes the framework
#      describes nowhere (a bare `password=` assignment).
#
#  (2) A VIOLATION CLEARS EVERY OUTPUT-BEARING FIELD (TestContainment).
#      AgentBaseGraph.get_output() returns `formatted_output or result`, and it
#      does so on the error status too. A gate that raises, or that returns
#      ERROR without overwriting result, still ships the un-gated report inside
#      the error envelope. The replacement is also deliberately TRUTHY: a falsy
#      formatted_output re-opens that same fallback.
#
#  (3) THE ERROR ENVELOPE IS CLEAN END-TO-END (TestErrorEnvelope).
#      Whatever refuses a request - this template, the platform's own input
#      gate, or the framework's credential scan - the caller must receive no
#      released report text, no traceback, and no source paths.
#
# docs/03_test_spec.md section 2.6 (POST) / section 4 (PoB).
# Deterministic - no LLM, no network. framework.* / src.* imports only.

import pytest

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.security.credential_detector import detect_credentials

from src.graph.graph import Graph
from src.nodes.post_process_node import PostProcessNode

# Simulated credential patterns - NOT real credentials.
_SIMULATED_API_KEY = "sk-TESTKEY1234567890abcdefghijklmn"
_SIMULATED_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
_SIMULATED_BEARER = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.SflKx"
_SIMULATED_CRED_STR = "password=super_secret_password_abc123"
_SIMULATED_AWS = "AKIAIOSFODNN7EXAMPLE"
_SIMULATED_STRIPE = "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc"
_SIMULATED_CONN = "postgresql://user:mock-password-value@db.internal:5432/ledger"

_FRAMEWORK_ONLY = [_SIMULATED_AWS, _SIMULATED_STRIPE, _SIMULATED_CONN]
_LOCAL_ONLY = [_SIMULATED_CRED_STR]
_ALL_SECRETS = [
    _SIMULATED_API_KEY,
    _SIMULATED_JWT,
    _SIMULATED_BEARER,
    _SIMULATED_CRED_STR,
    _SIMULATED_AWS,
    _SIMULATED_STRIPE,
    _SIMULATED_CONN,
]

_CLEAN_REPORT = "# Reconciliation Triage Report\n\n**Total discrepancies:** 0\n"
_OUTPUT_FIELDS = ("formatted_output", "result", "reconciliation_report")

_LEDGER = {
    "source_a": [{"reference": "INV-1001", "amount": 1500.0, "date": "2026-01-05"}],
    "source_b": [],
}


def _run(context=None, request="Reconcile the January extract against the ledger."):
    ctx = InvocationContext(
        session_id="pb",
        caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
        caller_id="test-suite",
    )
    return Graph().invoke(request, ctx=ctx, input_context=_LEDGER if context is None else context)


class TestOutputGateCoverage:
    @pytest.mark.parametrize("secret", _ALL_SECRETS)
    def test_every_form_is_withheld(self, secret):
        result = PostProcessNode().execute({"result": f"# Report\n\nNote: {secret}\n"})
        assert result["status"] == AgentStatus.ERROR.value
        for field in _OUTPUT_FIELDS:
            assert secret not in result[field]

    @pytest.mark.parametrize("secret", _FRAMEWORK_ONLY)
    def test_the_framework_detector_is_the_floor(self, secret):
        # These are the forms the original local pattern set missed entirely.
        assert detect_credentials(secret)
        assert PostProcessNode().execute({"result": f"x {secret}"})["status"] == AgentStatus.ERROR.value

    @pytest.mark.parametrize("secret", _LOCAL_ONLY)
    def test_the_local_patterns_are_kept_not_replaced(self, secret):
        # The framework's patterns describe credential FORMATS and match nothing
        # of this shape. Delegating to it would have made this gate narrower
        # while looking like a tightening.
        assert detect_credentials(secret) == []
        assert PostProcessNode().execute({"result": f"x {secret}"})["status"] == AgentStatus.ERROR.value

    def test_a_clean_report_is_not_withheld(self):
        result = PostProcessNode().execute({"result": _CLEAN_REPORT})
        assert result["status"] == AgentStatus.SUCCESS.value
        assert result["formatted_output"] == _CLEAN_REPORT


class TestContainment:
    def test_no_output_bearing_field_retains_the_withheld_text(self):
        report = f"# Report\n\nNote: {_SIMULATED_API_KEY}\n"
        result = PostProcessNode().execute(
            {"result": report, "reconciliation_report": report, "formatted_output": report}
        )
        for field in _OUTPUT_FIELDS:
            assert _SIMULATED_API_KEY not in result[field]
            assert result[field], f"{field} must stay truthy or the graph fallback re-opens"

    def test_the_notice_is_truthy_on_every_withholding_path(self):
        for state in ({"result": ""}, {"result": f"x {_SIMULATED_CRED_STR}"}):
            assert PostProcessNode().execute(state)["formatted_output"]

    def test_error_log_carries_a_closed_set_reason_not_content(self):
        report = f"# Report\n\n{_SIMULATED_JWT}\n"
        joined = " ".join(PostProcessNode().execute({"result": report})["error_log"])
        assert _SIMULATED_JWT not in joined


class TestErrorEnvelope:
    """Whatever refuses the request, the envelope the caller receives is clean."""

    @pytest.mark.parametrize(
        "context",
        [
            {},
            {"source_a": [{"reference": "R1", "amount": "NaN"}], "source_b": []},
            {"source_a": [{"reference": "not an inert ref", "amount": 1.0}], "source_b": []},
        ],
    )
    def test_refusals_release_no_internals(self, context):
        result = _run(context=context)
        assert result.get("status") == AgentStatus.ERROR.value
        body = str(result.get("output") or "")
        assert "Traceback" not in body
        assert "src/" not in body
        assert "/Users/" not in body
        assert "reconcile" not in body.lower()

    def test_a_credential_shaped_reference_never_ships_a_report(self):
        # An inert reference can still look like an AWS key: the shape
        # [A-Za-z0-9_-]{1,32} admits it. The request must not come back with a
        # report either way - not a gated one, not an un-gated one in an error
        # envelope.
        context = {
            "source_a": [{"reference": _SIMULATED_AWS, "amount": 1.0}],
            "source_b": [],
        }
        result = _run(context=context)
        body = str(result.get("output") or "")
        assert _SIMULATED_AWS not in body
        assert "Reconciliation Triage Report" not in body

    def test_a_successful_run_carries_no_credential_shape(self):
        body = str(_run().get("output") or "")
        assert detect_credentials(body) == []


# Simulated account identifiers embedded in the request - NOT real data.
_IBAN = "DE89370400440532013000"
_EMAIL = "holder.taro@example.com"


class TestDomainBoundary:
    """Direct identifiers supplied by the caller never surface in the report."""

    def _result(self):
        return _run(request=f"Break on IBAN {_IBAN}, contact {_EMAIL} about the open item.")

    def test_run_succeeds(self):
        assert self._result().get("status") == AgentStatus.SUCCESS.value

    def test_output_is_non_empty(self):
        assert self._result().get("output", "").strip()

    def test_iban_not_in_output(self):
        assert _IBAN not in self._result().get("output", "")

    def test_email_not_in_output(self):
        assert _EMAIL not in self._result().get("output", "")

    def test_free_text_is_never_rendered_into_the_report(self):
        marker = "zqx_echo_marker_zqx"
        output = _run(request=f"Reconcile the extract {marker} please.").get("output", "")
        assert marker not in output
