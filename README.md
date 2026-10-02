# FIN-C2-071 — Financial Reconciliation & Discrepancy Triage Agent

> **Category**: Cat 2 (domain-specific multi-step pipeline)
> **Industry**: FIN (financial services)

## Overview

Reconciles two financial record sets against each other and hands an accountant a ranked triage
report of everything that does not agree. The caller supplies the two ledgers — a bank statement
and a general-ledger extract, a payment file and its settlement report, two subsidiary systems —
as structured records alongside the request. The pipeline matches them on a configurable composite
key (reference and amount by default, optionally value date), then separates three kinds of
disagreement: an entry present in one source and absent from the other, a matched pair whose
amounts differ by more than the configured tolerance, and a matched pair booked on different value
dates. Each finding is classified into a root-cause category — timing cut-off, FX or rounding
difference, duplicate booking, omission, data-entry error — given a one-line hypothesis and a
severity, and the findings are ranked severity-first into a Markdown report.

The caller contract is deliberately narrow. Record fields are validated one by one against explicit
bounds before anything is matched: reference keys are restricted to an inert identifier shape,
amounts must parse as finite numbers inside a fixed magnitude range (`NaN` and `Infinity` are
rejected, not compared), value dates must be ISO calendar dates, and the number of records per
source is capped. Anything outside those bounds is refused by field name — the offending value is
never echoed back, and never reaches the report. The rendered report therefore contains only
validated reference keys, closed-set categories and severities, and counts; free caller text is
never interpolated into it.

Classification is rule-based and deterministic, so the whole pipeline runs and tests end-to-end
with no model call and no network. A deployment that wants a model in the loop replaces the
classification step; the governed prompt it would use is already carried in `config/config.yaml`.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent fails at graph
compile / start-up preflight rather than starting in a partially working state. This is
intentional — a half-running agent is worse than one that refuses to start.

## Calling the agent

`POST /invoke` takes the free-text request in `input` and the two record sets in `input_context`:

```json
{
  "input": "Reconcile the January settlement file against the ledger extract.",
  "session_id": "recon-2026-01",
  "input_context": {
    "source_a": [{"reference": "INV-1001", "amount": 1500.0, "date": "2026-01-05"}],
    "source_b": [{"reference": "INV-1001", "amount": 1400.0, "date": "2026-01-05"}],
    "match_keys": ["reference"],
    "amount_tolerance": 0.01
  }
}
```

Records travel on `input_context` rather than inside `input` on purpose: the platform's input
filter masks personal-data shapes in free-text fields before any template code runs, which would
silently rewrite ledger values. `input_context` is not masked, so the records arrive intact — and
because it is not masked, the entry point screens it for credential-shaped values and refuses the
request, naming the field, rather than letting it fail opaquely further in.

Callers still holding a single JSON blob can pass the same object as the `input` string; the
pipeline accepts it, validates it identically, and records a note that the masked channel was
used.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, graphs, schemas)
tests/        unit and boundary tests
config/       agent manifest (agent.yaml) and runtime parameters (config.yaml)
deploy/       local staging compose file and the standard invoke payload
docs/         design and operational documentation
```

See `docs/` for the design and the test specification.

## Customising

1. Adjust `config/config.yaml` for your own matching keys, amount tolerance and record caps.
2. Replace the sample records in `deploy/invoke_payload.json` with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic — the
   classification rules in `classify_and_hypothesize_node.py` are the usual first change.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
