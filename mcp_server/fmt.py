"""Display formatting shared by the tools, the memo, the command line and the web app (one place, so a number looks the same everywhere)."""
from __future__ import annotations

import re
import unicodedata
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


# ----------------------------------------------------------------------------- text that came from data
# Unicode categories removed from data text: control (Cc), format incl. zero-width and bidirectional marks (Cf), surrogate (Cs),
# private use (Co), unassigned (Cn). They hide text from a human reviewer but not from a model or a renderer.
STRIP_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})
_LOOKALIKE_OPEN, _LOOKALIKE_CLOSE = re.compile(r"<{3,}"), re.compile(r">{3,}")
# Markdown/HTML constructs that can turn data into a link, an image (a request to a third-party server), a heading or markup.
_MD_TABLE = str.maketrans({"[": "(", "]": ")", "<": "\u2039", ">": "\u203a", "`": "'", "|": "/", "\\": "/", "\n": " ", "\r": " "})


def scrub(text: Any, max_chars: int | None = None) -> str:
    """Data text made inert: invisible and control characters removed, whitespace collapsed, '<<<'/'>>>' defused and, when
    `max_chars` is given, cut to that length with an ellipsis."""
    text = unicodedata.normalize("NFC", str(text)).replace("\t", " ").replace("\r", " ").replace("\n", " ")
    text = "".join(ch for ch in text if unicodedata.category(ch) not in STRIP_CATEGORIES)
    text = _LOOKALIKE_CLOSE.sub("\u00bb", _LOOKALIKE_OPEN.sub("\u00ab", " ".join(text.split())))
    if max_chars is None or len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "\u2026"


def md_safe(text: Any, max_chars: int | None = None) -> str:
    """Data text safe to put in Markdown shown to a reviewer (the web app, the memo): scrubbed, and `[ ] < > ` | \\` replaced so a
    name cannot become a link, an image, a heading, HTML or a table cell. The stored record is untouched; this is display only."""
    return scrub(text, max_chars).translate(_MD_TABLE)


def fold(text: str) -> str:
    """The text as a matcher should see it: compatibility-normalised (full-width 'ＩＧＮＯＲＥ' -> 'IGNORE'), invisible characters removed."""
    text = unicodedata.normalize("NFKC", str(text))
    text = "".join(" " if ch in "\t\r\n" else ch for ch in text if unicodedata.category(ch) not in STRIP_CATEGORIES or ch in "\t\r\n")
    return " ".join(text.split())


def compile_patterns(cfg: dict[str, Any]) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in cfg["injection_patterns"]]


def find_injection(text: str, patterns: list[re.Pattern[str]]) -> str | None:
    """The first matched snippet if `text` reads like an instruction to an AI, else None."""
    folded = fold(text)
    for pat in patterns:
        m = pat.search(folded)
        if m:
            return m.group(0)[:80]
    return None


def strip_invisible(text: str) -> str:
    """Multi-line text with the characters removed that a terminal or a reader cannot see or that move the cursor (ESC sequences,
    bidirectional marks, zero-width characters); newlines and ordinary spacing are kept. For the command line and plain-text reports."""
    return "".join(ch for ch in str(text) if ch == "\n" or unicodedata.category(ch) not in STRIP_CATEGORIES)
