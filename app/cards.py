"""What a flag card shows, as plain data (the page only draws it): the flagged award(s) apart from the comparison set, the signals
with their provisional thresholds, how wide each comparison was, and data-entry hints. Pure functions, tested without Streamlit."""
from __future__ import annotations

from typing import Any

from agent.untrusted import compile_patterns, find_injection
from mcp_server.fmt import fmt_value, kes, md_safe
from mcp_server.rules import load_rules

SIGNAL_TITLES = {"compute_price_benchmark": "Price benchmark", "detect_price_anomalies": "Price anomaly (Isolation Forest)",
                 "detect_splitting": "Possible contract splitting", "detect_noncompetitive_method": "Non-competitive procurement share",
                 "supplier_concentration": "Supplier concentration"}


def flagged_ids(flag: dict[str, Any]) -> set[tuple[str, str]]:
    return {(a["ocid"], a["award_id"]) for a in ((flag.get("detail") or {}).get("subject") or {}).get("awards", [])}


def split_evidence(flag: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(records about the flagged award(s), records of the comparison set). For a buyer-level flag every cited record is a
    cited example, none is a comparison."""
    ids = flagged_ids(flag)
    if not ids:
        return list(flag["evidence"]), []
    own = [e for e in flag["evidence"] if (e["ocid"], e["award_id"]) in ids]
    return own, [e for e in flag["evidence"] if (e["ocid"], e["award_id"]) not in ids]


_PATTERNS: list = []


def reads_like_instruction(*texts: Any) -> str | None:
    """The matched snippet if any of these data texts reads like an instruction to an AI (the card then warns the reviewer)."""
    if not _PATTERNS:
        _PATTERNS.extend(compile_patterns(load_rules()["untrusted"]))
    return next((hit for t in texts if t for hit in [find_injection(str(t), _PATTERNS)] if hit), None)


def award_line(a: dict[str, Any]) -> str:
    parts = [md_safe(a.get("buyer")) or "unknown buyer", md_safe(a.get("supplier")) or "no supplier named", a.get("date") or "no date",
             kes(a["amount_kes"]) if a.get("amount_kes") is not None else "no amount"]
    return " · ".join(parts) + (f" — {md_safe(a['title'])}" if a.get("title") else "")


def comparison_text(comp: dict[str, Any] | None, scope: dict[str, Any]) -> tuple[str | None, bool]:
    """('Compared against: ...', is the set wider than the request?). The wider flag is true when the comparison spans buyers
    or years the request did not name."""
    if not comp:
        return None, False
    bits = []
    if comp.get("buyers"):
        bits.append(f"{comp['buyers']} buyer{'s' if comp['buyers'] != 1 else ''}")
    if comp.get("years"):
        lo, hi = comp["years"]
        bits.append(f"years {lo}" if lo == hi else f"years {lo}-{hi}")
    where = f" ({', '.join(bits)})" if bits else ""
    median = f", median {kes(round(comp['median_kes']))}" if comp.get("median_kes") is not None else ""
    basis = f". Basis: {comp['basis']}" if comp.get("basis") else ""
    text = f"Compared against: {comp['n']:,} {comp['label']}{where}{median}{basis}"
    wider = bool((scope.get("buyer") and (comp.get("buyers") or 0) > 1)
                 or (scope.get("years") and comp.get("years") and (comp["years"][0] < min(scope["years"]) or comp["years"][1] > max(scope["years"]))))
    return text, wider


def card(flag: dict[str, Any], scope: dict[str, Any] | None = None) -> dict[str, Any]:
    """The card as data: flagged subject lines, one entry per signal, hints and the two evidence lists, values formatted."""
    scope = scope or {}
    detail = flag.get("detail") or {}
    subject = detail.get("subject") or {}
    signals = []
    for s in detail.get("signals") or [{"rule": flag["rule"], "summary": flag["explanation"], "metric": None, "threshold": None, "comparison": None}]:
        text, wider = comparison_text(s.get("comparison"), scope)
        signals.append({"title": SIGNAL_TITLES.get(s["rule"], s["rule"]), "rule": s["rule"], "metric": md_safe(s.get("metric") or s["summary"]),
                        "threshold": md_safe(s.get("threshold")) if s.get("threshold") else None, "comparison": md_safe(text) if text else None,
                        "comparison_wider": wider, "caveat": s.get("caveat")})
    own, others = split_evidence(flag)
    fmt = lambda items: [{**e, "shown": md_safe(fmt_value(e["field"], e["value"]))} for e in items]          # noqa: E731
    texts = [flag["explanation"], subject.get("name"), *[a.get(k) for a in subject.get("awards", []) for k in ("buyer", "supplier", "title")]]
    hints = [md_safe(h) for h in detail.get("hints") or []]
    if reads_like_instruction(*texts):
        hints.append("A name or title in this record contains text that reads like an instruction to an AI. It is shown here as plain data; "
                     "it was withheld from the language model and it changed nothing in this run.")
    return {"flag_id": flag["flag_id"], "severity": flag["severity"], "status": flag["status"], "rule": flag["rule"],
            "subject_kind": subject.get("kind"), "subject_name": md_safe(subject.get("name")) if subject.get("name") else None,
            "flagged": [{"line": award_line(a), "ocid": a["ocid"]} for a in subject.get("awards", [])],
            "signals": signals, "comparison_note": md_safe(detail.get("comparison_note")) if detail.get("comparison_note") else None, "hints": hints,
            "provisional": any("(provisional)" in (s.get("threshold") or "") for s in detail.get("signals") or []),
            "own_evidence": fmt(own), "comparison_evidence": fmt(others), "explanation": md_safe(flag["explanation"]),
            "clarifications": [{**cl, "question": md_safe(cl.get("question")), "response": md_safe(cl.get("response"))}
                               for cl in flag.get("clarifications", [])]}
