"""Display formatting shared by the tools, the memo, the command line and the web app (one place, so a number looks the same everywhere)."""
from __future__ import annotations

from typing import Any

MONEY_FIELD_WORDS = ("amount", "value_kes", "total_kes")


def kes(value: Any) -> str:
    """Kenyan shillings with thousands separators and no trailing '.0': 8916920.0 -> 'KES 8,916,920'; 330.5 -> 'KES 330.50'."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number):
        return f"KES {int(number):,}"
    return f"KES {number:,.2f}"


def is_money_field(field: str | None) -> bool:
    return bool(field) and any(word in field.lower() for word in MONEY_FIELD_WORDS)


def fmt_value(field: str | None, value: Any) -> str:
    """A cited value for display: money fields as KES, everything else as text (a whole float loses its '.0')."""
    if is_money_field(field):
        return kes(value)
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)
