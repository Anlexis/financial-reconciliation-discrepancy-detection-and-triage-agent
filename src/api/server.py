"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for the agent.
# Entry points are adapters only — no business logic here.
# For platform-level routing, AgentGateway calls agent.invoke() directly.

import json
import os
import secrets
from typing import Any, Dict, cast
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from framework.security.credential_detector import detect_credentials_in_value
from shared.secrets import factory as secrets_factory
from src.graph.graph import Graph
from src.schemas.contract import CONTEXT_FIELDS

app = FastAPI(title="Agent")

# Graph() defaults its runtime parameters to config/config.yaml, so the
# standalone server and an AgentRegistry-built agent carry the same settings.
agent = Graph()
agent.compile()
# namespace/agent_name match the manifest's `namespace` / `name` values.
agent.provision_secrets(secrets_factory(namespace="fin", agent_name="FinancialReconciliationDiscrepancyAgent"))

# Adapter-level size cap on the caller-supplied context (bytes of its JSON
# serialization). Field-level validation happens in the pre_process node; this
# cap only stops oversized envelopes at the door.
_INPUT_CONTEXT_MAX_BYTES = 256 * 1024


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""
    # The two record sets and the optional matching overrides. Validated field
    # by field by the pre_process node; malformed values are refused by field
    # name and never echoed back.
    input_context: Dict[str, Any] = Field(default_factory=dict)


def _filter_context(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only the declared context fields; drop everything else.

    Declaring a narrow contract is not the same as enforcing one. A validator
    that ignores undeclared keys leaves them in state["input_context"], where
    the framework's first node returns them verbatim into its own result and the
    output gate then scans them — so an undeclared key carrying a
    credential-shaped string kills the request inside the framework's first
    node, before any template code runs. Dropping them here is what makes the
    declared contract the real one.
    """
    return {key: raw[key] for key in CONTEXT_FIELDS if key in raw}


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> Dict[str, Any]:
    trust = getattr(request.state, "trust_level", TrustLevel.ANONYMOUS)

    # Standalone caller auth: when INVOKE_AUTH_TOKEN is set on the server
    # environment, a caller no upstream middleware vouched for (still ANONYMOUS)
    # must present it as a Bearer token and then runs at VERIFIED_EXTERNAL —
    # the entry trust level the manifest declares. Middleware-established trust
    # is never demoted. This is a deployment-level caller credential, not an
    # agent secret, so ctx.secrets does not apply: no InvocationContext exists
    # before auth.
    expected = os.environ.get("INVOKE_AUTH_TOKEN")
    if expected and trust is TrustLevel.ANONYMOUS:
        supplied = request.headers.get("authorization", "")
        # Compare bytes: compare_digest raises TypeError on non-ASCII str input
        # (headers decode as latin-1), which would 500 instead of a clean 401.
        if not secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
            # Generic body on purpose — do not leak whether the token was
            # absent, malformed, or wrong.
            raise HTTPException(status_code=401, detail="Token is invalid or expired.")
        trust = TrustLevel.VERIFIED_EXTERNAL

    context = _filter_context(req.input_context)
    if len(json.dumps(context, ensure_ascii=False).encode("utf-8")) > _INPUT_CONTEXT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="input_context too large.")

    # Credential screen on the context channel, before invoke(). The framework's
    # output gate scans every value of every node result, and the first node
    # returns input_context verbatim — so a credential-shaped value here fails
    # the run inside the framework with a traceback the caller cannot act on.
    # The request cannot succeed either way; refusing it here converts an opaque
    # failure into an actionable one. detect_credentials_in_value is the
    # framework's own function, so this refusal set matches its block set
    # exactly. 400, not 422: pydantic owns 422 and returns error objects there.
    for field in CONTEXT_FIELDS:
        if field in context and detect_credentials_in_value(context[field]):
            raise HTTPException(
                status_code=400,
                detail=f"input_context.{field} carries a credential-shaped value.",
            )

    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        return cast(Dict[str, Any], agent.invoke(req.input, ctx=ctx, input_context=context))


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "agent": "FinancialReconciliationDiscrepancyAgent"}
