"""AgentCore Platform v1.0 - caller-context bridge across the graph boundary."""

# Why this exists: GraphNode.execute() invokes the inner graph as
# `subgraph.invoke(user_input, session_id=..., ctx=...)`. It forwards neither
# the outer state nor the caller's input_context, so the validated ledger that
# PreProcessNode assembled would never reach the matcher on its own. The two
# sanctioned subclass hooks bridge it:
#
#   ReconciliationGraphNode.extract_input(state)  [runs BEFORE subgraph.invoke]
#       -> set_caller_ledger({"source_a": [...], "source_b": [...], ...})
#   DomainWorkflowGraph._extra_initial_state()    [runs INSIDE subgraph.invoke]
#       -> seeds {"validated_ledger": <JSON>, "domain_settings": <JSON>}
#
# What crosses is the VALIDATED contract produced by PreProcessNode - every
# reference already matched the inert shape and every amount already passed the
# finite/bounded parser - never the raw request body.
#
# Smuggling the records inside the free-text channel instead is not an option:
# the platform masks user_input / validated_input at every node boundary, and
# ordinary ledger content trips those heuristics. A Title Case description
# ("Wire Transfer Settlement") is rewritten to "[MASKED]", and a bare 12-digit
# account number is rewritten mid-JSON so the payload no longer parses at all -
# at which point the agent reports "sources reconcile" over records it never
# read. This channel is not masked, which is exactly why it is used.
#
# A ContextVar keeps the hand-off correct per thread/task, so concurrent
# invocations inside one process cannot see each other's ledger.

from contextvars import ContextVar
from typing import Any, Dict, Optional

_CALLER_LEDGER: ContextVar[Optional[Dict[str, Any]]] = ContextVar("fin_c2_071_caller_ledger", default=None)


def set_caller_ledger(ledger: Optional[Dict[str, Any]]) -> None:
    """Stash the validated caller ledger for the imminent inner-graph invoke."""
    _CALLER_LEDGER.set(dict(ledger) if ledger else {})


def get_caller_ledger() -> Dict[str, Any]:
    """Read (without consuming) the stashed ledger; ``{}`` when none was set."""
    return _CALLER_LEDGER.get() or {}
