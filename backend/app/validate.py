"""Field-level validation for the raw-dict request bodies.

Write endpoints take ``payload: dict = Body(...)`` rather than Pydantic models,
so a number arrives as whatever the client sent. The frontend turns a mistyped
box into ``null`` (``Number("abc")`` is ``NaN``, which JSON-encodes as null) and
CSV/API callers send bare strings; without a check those reach ``int()`` or the
ORM and surface as an unhandled 500. The quieter failures are worse: a quantity
of 2.7 truncated to 2, a unit cost of -500 written into a FIFO batch, a count of
10**12 accepted as stock on hand.

Three helpers cover every such field in the API:

    whole(value, "quantity")           -> int    counts, deltas, caps
    money(value, "unit_cost")          -> float  costs, prices, fees, shipping
    choice(value, "condition", CONDITIONS) -> str  canonical enums

All three raise ``HTTPException(400, "<field> must be ...")`` naming the field,
which the frontend's existing error toast surfaces as-is.

Both numeric helpers reject non-finite values (NaN/inf are valid JSON floats via
``Infinity``/``NaN`` literals but never a valid quantity or price) and bools
(``True`` is an ``int`` subclass in Python, so an unguarded ``int(value)`` would
happily read it as 1).
"""
import math

from fastapi import HTTPException

# Sanity ceilings. Not business rules — just the point past which a value is
# certainly a typo or a bad unit conversion rather than a real count/price.
MAX_QUANTITY = 10_000_000
MAX_MONEY = 10_000_000.0

# Distinguishes "no default, a missing value is an error" from an explicit
# ``default=None`` meaning "null is allowed and means null".
_MISSING = object()


def _reject(field: str, expected: str) -> None:
    raise HTTPException(400, f"{field} must be {expected}")


def _to_float(value, field: str, expected: str) -> float:
    if isinstance(value, bool):
        _reject(field, expected)
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        _reject(field, expected)
    if not math.isfinite(n):
        _reject(field, expected)
    return n


def whole(value, field: str, *, default=_MISSING, min_value: int | None = 0,
          max_value: int | None = MAX_QUANTITY) -> int | None:
    """Coerce a payload value to a whole number, or raise 400.

    Accepts ints, integral floats (``2.0``) and numeric strings (``"2"``) —
    CSV import and hand-rolled API calls both send strings. Rejects fractional
    values outright rather than truncating: a "2.5" in a quantity box is a
    mistake, and silently storing 2 hides it.

    ``min_value=None`` allows negatives (adjustment deltas); the default of 0
    covers the common case of a count that cannot go below zero.
    """
    if value is None or value == "":
        if default is _MISSING:
            _reject(field, "a whole number")
        return default
    expected = "a whole number"
    n = _to_float(value, field, expected)
    if n != int(n):
        _reject(field, f"{expected} (got {value})")
    n = int(n)
    if min_value is not None and n < min_value:
        _reject(field, f"at least {min_value} (got {n})")
    if max_value is not None and n > max_value:
        _reject(field, f"at most {max_value:,} (got {n:,})")
    return n


def money(value, field: str, *, default=_MISSING, min_value: float | None = 0.0,
          max_value: float | None = MAX_MONEY) -> float | None:
    """Coerce a payload value to a currency amount, or raise 400.

    Deliberately does not round: unit costs are stored to 4 decimals (a bulk
    pile's ``total_cost / quantity`` is routinely fractions of a cent) and
    rounding here would quietly change the numbers FIFO is built on.
    """
    if value is None or value == "":
        if default is _MISSING:
            _reject(field, "a number")
        return default
    expected = "a number"
    n = _to_float(value, field, expected)
    if min_value is not None and n < min_value:
        _reject(field, f"at least {min_value:g} (got {n:g})")
    if max_value is not None and n > max_value:
        _reject(field, f"at most {max_value:,.2f} (got {n:,.2f})")
    return n


def choice(value, field: str, options, *, default=_MISSING) -> str | None:
    """Validate a value against a canonical list, or raise 400.

    Strict on purpose. ``domain.normalize_condition`` / ``normalize_printing``
    fall back to "NM" / "normal" for anything unrecognized, which is right for
    CSV import (best-effort on messy third-party data) but wrong for a direct
    API write, where a typo'd condition silently becoming NM is a data bug the
    user never sees. Import keeps the normalizers; the API says no.
    """
    if value is None or value == "":
        if default is _MISSING:
            _reject(field, f"one of: {', '.join(options)}")
        return default
    if value not in options:
        _reject(field, f"one of: {', '.join(options)} (got {value!r})")
    return value


def mapping(value, field: str) -> dict:
    """Require a JSON object, or raise 400.

    Guards the few fields whose payload shape is itself a dict — e.g. bulk
    edit's ``quantity: {"set": n}`` — where a bare number would otherwise hit
    ``.get`` on an int and 500.
    """
    if not isinstance(value, dict):
        _reject(field, f"an object (got {type(value).__name__})")
    return value
