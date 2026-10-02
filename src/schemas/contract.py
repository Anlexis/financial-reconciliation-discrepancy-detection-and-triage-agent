"""AgentCore Platform v1.0"""

# FIN-C2-071 - Caller-data contract for a reconciliation request.
#
# Every value the caller supplies is validated here before any domain node sees
# it. Three rules hold without exception:
#
#   1. FAIL CLOSED. A field outside its declared bounds refuses the request; it
#      is never coerced, clamped or ignored. NaN and Infinity parse fine through
#      float() and then compare False against every threshold, so a numeric that
#      is not finite is refused rather than compared.
#   2. NAME THE FIELD, NEVER THE VALUE. A rejected value is caller data and may
#      itself be hostile; the error text carries the field path only.
#   3. WHAT SURVIVES IS INERT. A record that passes carries exactly three
#      fields - an identifier-shaped reference, a finite amount, an ISO date.
#      Unknown keys are dropped, not copied forward, so nothing free-text can
#      reach the matcher, the checkpoint or the rendered report.

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

# Structural caps. An unbounded record list is a denial-of-service surface and
# an unbounded reference is an output-injection surface.
MAX_RECORDS_PER_SOURCE = 500
MAX_REFERENCE_LEN = 32
MAX_MATCH_KEYS = 3

# Magnitude bounds. 1e12 covers any realistic ledger line in any currency unit
# while still refusing the values that turn a float comparison into nonsense.
AMOUNT_ABS_MAX = 1e12
TOLERANCE_MAX = 1e9

# The inert alphabet every rendered caller-derived token is locked to.
REFERENCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,%d}$" % MAX_REFERENCE_LEN)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

ALLOWED_MATCH_KEYS: Tuple[str, ...] = ("reference", "amount", "date")
DEFAULT_MATCH_KEYS: List[str] = ["reference", "amount"]
DEFAULT_AMOUNT_TOLERANCE = 0.01

# The closed set of top-level keys the caller context may carry. Anything else
# is dropped by the entry point before the agent is invoked.
CONTEXT_FIELDS: Tuple[str, ...] = ("source_a", "source_b", "match_keys", "amount_tolerance")

_SOURCES: Tuple[str, ...] = ("source_a", "source_b")

# Aliases accepted for the two record sets, so a caller holding a legacy payload
# does not have to rename its keys. Each maps onto one canonical source name.
_SOURCE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "source_a": ("source_a", "ledger_a", "system_a", "a"),
    "source_b": ("source_b", "ledger_b", "system_b", "b"),
}

_REFERENCE_ALIASES: Tuple[str, ...] = (
    "reference",
    "ref",
    "reference_number",
    "txn_id",
    "transaction_id",
    "id",
)
_AMOUNT_ALIASES: Tuple[str, ...] = ("amount", "value", "total", "sum")
_DATE_ALIASES: Tuple[str, ...] = ("date", "value_date", "posted_date", "timestamp")


class ContractError(ValueError):
    """A caller field violated its declared bounds.

    ``field`` is a dotted path such as ``source_a[3].amount``. The message
    quotes that path and the rule that failed - never the offending value.
    """

    def __init__(self, field: str, rule: str) -> None:
        self.field = field
        self.rule = rule
        super().__init__(f"{field}: {rule}")


def finite_in_range(value: Any, *, field: str, lo: float, hi: float) -> float:
    """Parse a caller-supplied number, or refuse.

    Accepts int / float / a decimal string (thousands separators tolerated).
    Refuses bool (``isinstance(True, int)`` is True in Python, so a bare int
    check would let ``true`` through as 1), non-numeric text, NaN, +/-Infinity,
    and anything outside ``[lo, hi]``.
    """
    if isinstance(value, bool):
        raise ContractError(field, "must be a number, not a boolean")
    if isinstance(value, (int, float)):
        parsed = float(value)
    elif isinstance(value, str):
        cleaned = value.replace(",", "").replace("_", "").strip()
        if not cleaned:
            raise ContractError(field, "must be a number")
        try:
            parsed = float(cleaned)
        except ValueError:
            raise ContractError(field, "must be a number") from None
    else:
        raise ContractError(field, "must be a number")

    if not math.isfinite(parsed):
        raise ContractError(field, "must be a finite number (NaN and Infinity are refused)")
    if parsed < lo or parsed > hi:
        raise ContractError(field, f"must be within [{lo}, {hi}]")
    return parsed


def _first_alias(record: Dict[str, Any], aliases: Tuple[str, ...]) -> Optional[Any]:
    """Return the first present alias value from a record (case-insensitive)."""
    lowered = {str(key).lower(): val for key, val in record.items()}
    for alias in aliases:
        if alias in lowered and lowered[alias] not in (None, ""):
            return lowered[alias]
    return None


def _validate_reference(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ContractError(field, "must be a string")
    text = value.strip()
    if not text:
        raise ContractError(field, "must not be empty")
    if not REFERENCE_RE.match(text):
        raise ContractError(
            field,
            f"must match the inert reference shape [A-Za-z0-9_-]{{1,{MAX_REFERENCE_LEN}}}",
        )
    return text


def _validate_date(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ContractError(field, "must be an ISO date string (YYYY-MM-DD)")
    text = value.strip()
    if not _ISO_DATE_RE.match(text):
        raise ContractError(field, "must be an ISO date string (YYYY-MM-DD)")
    try:
        date.fromisoformat(text)
    except ValueError:
        raise ContractError(field, "must be a real calendar date") from None
    return text


def _validate_record(raw: Any, *, field: str) -> Dict[str, Any]:
    """Validate one ledger record down to the three inert fields that survive."""
    if not isinstance(raw, dict):
        raise ContractError(field, "must be an object")

    reference = _first_alias(raw, _REFERENCE_ALIASES)
    if reference is None:
        raise ContractError(f"{field}.reference", "is required")
    amount = _first_alias(raw, _AMOUNT_ALIASES)
    if amount is None:
        raise ContractError(f"{field}.amount", "is required")

    record: Dict[str, Any] = {
        "reference": _validate_reference(reference, field=f"{field}.reference"),
        "amount": finite_in_range(amount, field=f"{field}.amount", lo=-AMOUNT_ABS_MAX, hi=AMOUNT_ABS_MAX),
        "date": "",
    }
    raw_date = _first_alias(raw, _DATE_ALIASES)
    if raw_date is not None:
        record["date"] = _validate_date(raw_date, field=f"{field}.date")
    return record


def _validate_source(raw: Any, *, field: str) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ContractError(field, "must be an array of records")
    if len(raw) > MAX_RECORDS_PER_SOURCE:
        raise ContractError(field, f"must hold at most {MAX_RECORDS_PER_SOURCE} records")
    return [_validate_record(item, field=f"{field}[{index}]") for index, item in enumerate(raw)]


def _validate_match_keys(raw: Any, *, field: str) -> List[str]:
    if not isinstance(raw, list):
        raise ContractError(field, "must be an array of key names")
    if not raw or len(raw) > MAX_MATCH_KEYS:
        raise ContractError(field, f"must hold 1 to {MAX_MATCH_KEYS} key names")
    keys: List[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str) or item.strip().lower() not in ALLOWED_MATCH_KEYS:
            raise ContractError(f"{field}[{index}]", f"must be one of {list(ALLOWED_MATCH_KEYS)}")
        key = item.strip().lower()
        if key not in keys:
            keys.append(key)
    return keys


def extract_sources(payload: Any) -> Optional[Dict[str, Any]]:
    """Pull the two record sets out of a caller mapping, or None if absent.

    Returns the raw (still unvalidated) mapping so the caller can tell "no
    ledger supplied" apart from "a ledger that failed validation" - the two
    need different responses.
    """
    if not isinstance(payload, dict):
        return None
    picked: Dict[str, Any] = {}
    for canonical, aliases in _SOURCE_ALIASES.items():
        for alias in aliases:
            if alias in payload:
                picked[canonical] = payload[alias]
                break
    return picked or None


def validate_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a whole reconciliation request.

    Returns ``{"source_a": [...], "source_b": [...], "match_keys": [...],
    "amount_tolerance": float}`` built only from validated values.

    :raises ContractError: naming the first field that violated its bounds.
    """
    sources = extract_sources(payload) or {}
    validated: Dict[str, Any] = {name: _validate_source(sources.get(name), field=name) for name in _SOURCES}
    total = sum(len(validated[name]) for name in _SOURCES)
    if total == 0:
        raise ContractError("source_a/source_b", "at least one source must carry a record")

    if "match_keys" in payload and payload["match_keys"] is not None:
        validated["match_keys"] = _validate_match_keys(payload["match_keys"], field="match_keys")
    else:
        validated["match_keys"] = list(DEFAULT_MATCH_KEYS)

    if "amount_tolerance" in payload and payload["amount_tolerance"] is not None:
        validated["amount_tolerance"] = finite_in_range(
            payload["amount_tolerance"], field="amount_tolerance", lo=0.0, hi=TOLERANCE_MAX
        )
    else:
        validated["amount_tolerance"] = DEFAULT_AMOUNT_TOLERANCE
    return validated
