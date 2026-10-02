"""AgentCore Platform v1.0"""

# FIN-C2-071 - runtime parameter loading.
#
# config/agent.yaml is the STATIC manifest: identity, entry class, trust level,
# compile-time requirements. It carries no runtime parameters and is not read
# here. config/config.yaml holds the runtime parameters (max_retry, timeout_s
# and the `reconciliation` block) and is what this module loads.
#
# The graph consumes these through its constructor: the framework reads
# max_retry off the graph config in its own routing loop, and the inner graph
# seeds the reconciliation defaults into state from the same mapping. Loading
# them here - rather than in one entry point - is what keeps a bare Graph()
# (the shape the boundary tests and the deployed server both use) configured
# identically to one built by the registry.
#
# An unreadable or malformed file degrades to {} so start-up falls back to the
# framework defaults instead of failing; a file that parses but declares an
# out-of-bounds value is a different case and DOES fail, in
# DomainWorkflowGraph._validate_config().

from pathlib import Path
from typing import Any, Dict

import yaml

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "config.yaml"


def load_runtime_config() -> Dict[str, Any]:
    """Load config/config.yaml; ``{}`` when it is absent or unreadable."""
    try:
        loaded = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}
