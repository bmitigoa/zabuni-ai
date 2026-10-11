"""Pure decision logic for the agent: follow-up rules, recovery (widening), argument checks, candidate extraction and the
citation guard. No LLM, no MCP, no I/O: everything here is deterministic and unit-tested.

Tool *names* never appear in this file. Follow-up rules are DATA in rules.py (agent.followups) and are applied only to
tools that were discovered on the server; recovery works on any result carrying `insufficient_comparables` and
`widen_options`; flags are read from the uniform `result.flags` every detection tool returns.
"""
from __future__ import annotations

import json
import re
from typing import Any

import jsonschema

OCID_RE = re.compile(r"ocds-[A-Za-z0-9]+-[A-Za-z0-9\-_/().]+")
TRAILING = ".,;:"


# ----------------------------------------------------------------------------- templates ($result.x.y)
def resolve_path(obj: Any, path: str) -> Any:
    """Follow a dotted path through dicts and lists (`target.buyer`, `awards.0.ocid`); None if anything is missing."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
        if cur is None:
            return None
    return cur


def resolve_template(value: Any, ctx: dict[str, Any]) -> Any:
    """`"$result.target.buyer"` -> the value in ctx; other values are returned unchanged; unresolved -> None."""
    if isinstance(value, str) and value.startswith("$"):
        root, _, rest = value[1:].partition(".")
        base = ctx.get(root)
        return base if not rest else resolve_path(base, rest)
    return value


def render_args(template: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Resolve every template value; an argument that resolves to None/empty is left out (the tool then uses its default)."""
    out = {}
    for key, value in template.items():
        resolved = resolve_template(value, ctx)
        if resolved is None or resolved == "" or resolved == []:
            continue
        out[key] = resolved.strip() if isinstance(resolved, str) else resolved
    return out


def step_key(tool: str, args: dict[str, Any]) -> str:
    """Canonical identity of a step, to avoid running or queuing the same call twice."""
    return tool + json.dumps(args, sort_keys=True, default=str)


# ----------------------------------------------------------------------------- follow-ups
def followups_for(step: dict[str, Any], output: dict[str, Any], scope: dict[str, Any], policy: list[dict[str, Any]],
                  known_tools: set[str]) -> list[dict[str, Any]]:
    """Steps to add after `step` returned `output`, according to the declarative policy.

    A policy entry: {when_tool, [when: "result.flagged"], [foreach: "result.awards", limit: N], then: [{tool, args, reason}]}.
    Entries for tools that were not discovered are ignored, so removing a tool never breaks the agent."""
    result = output.get("result") if isinstance(output, dict) else None
    if not isinstance(result, dict) or "error" in result:
        return []
    base_ctx = {"result": result, "args": step.get("args", {}), "scope": scope}
    added: list[dict[str, Any]] = []
    for rule in policy:
        if rule["when_tool"] != step["tool"]:
            continue
        if "when" in rule and not resolve_template("$" + rule["when"], {"result": result}):
            continue
        items: list[Any] = [None]
        if "foreach" in rule:
            found = resolve_template("$" + rule["foreach"], {"result": result})
            items = (found or [])[: int(rule.get("limit", len(found or [])))]
        for item in items:
            ctx = {**base_ctx, "item": item}
            for then in rule["then"]:
                if then["tool"] not in known_tools:
                    continue
                args = render_args(then.get("args", {}), ctx)
                added.append({"tool": then["tool"], "args": args, "reason": then.get("reason", "follow-up"),
                              "origin": f"follow-up of {step['tool']}"})
    return added


def dedupe_steps(new: list[dict[str, Any]], seen: set[str]) -> list[dict[str, Any]]:
    """Drop steps whose (tool, args) was already run or queued; updates `seen`."""
    out = []
    for s in new:
        key = step_key(s["tool"], s["args"])
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


# ----------------------------------------------------------------------------- recovery
def needs_widening(output: Any) -> bool:
    """A result that says it has too few comparables and offers machine-readable ways to widen the search."""
    res = output.get("result") if isinstance(output, dict) else None
    return bool(isinstance(res, dict) and res.get("insufficient_comparables") and res.get("widen_options"))


def apply_widen_op(args: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    """Apply one {op: drop|set, arg, [value]} operation to a copy of the call arguments."""
    new = dict(args)
    if op["op"] == "drop":
        new.pop(op["arg"], None)
    elif op["op"] == "set":
        new[op["arg"]] = op["value"]
    return new


def next_widening(args: dict[str, Any], options: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The first option that actually changes the arguments -> (new_args, op), or None if nothing is left to relax."""
    for op in options:
        new = apply_widen_op(args, op)
        if new != args:
            return new, op
    return None


# ----------------------------------------------------------------------------- scope injection
def inject_scope(args: dict[str, Any], schema: dict[str, Any], scope: dict[str, Any],
                 names: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    """Fill the request's scope into a step's arguments when the tool accepts it and the plan left it out.

    Models extract the scope correctly but sometimes forget to put it in the arguments, and a step without its buyer
    would scan every buyer in Kenya. `names` maps scope concepts to the shared argument names (rules agent.scope_args).
    Returns (new_args, list of injected argument names). Existing arguments are never overwritten."""
    props = schema.get("properties") or {}
    out, injected = dict(args), []

    def put(concept: str, value: Any) -> None:
        arg = names.get(concept)
        if arg and arg in props and value not in (None, "", []) and arg not in out:
            out[arg] = value
            injected.append(arg)

    put("buyer", (scope.get("buyer") or "").strip() or None)
    put("supplier", (scope.get("supplier") or "").strip() or None)
    years = sorted(scope.get("years") or [])
    put("years", years or None)
    if years:
        put("date_from", f"{years[0]}-01-01")
        put("date_to", f"{years[-1]}-12-31")
    return out, injected


# ----------------------------------------------------------------------------- scope guard and flag cap
def merge_scope(plan_scope: dict[str, Any], hint: dict[str, Any] | None) -> dict[str, Any]:
    """The caller's explicit scope (the UI's fields) wins over what the model extracted; blanks are ignored."""
    out = dict(plan_scope)
    for key, value in (hint or {}).items():
        if value not in (None, "", []):
            out[key] = value
    return out


NAME_TOKEN_MIN_LEN = 3          # shorter words ("of", "co") say nothing about which organisation was meant
NAME_MATCH_SHARE = 0.5          # at least this share of a scope name's words must appear in the request


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) >= NAME_TOKEN_MIN_LEN]


def ground_scope(scope: dict[str, Any], query: str) -> tuple[dict[str, Any], list[str]]:
    """Keep only the parts of a model-extracted scope that the request itself supports.

    A small model asked for the scope of "hello" invented the years 2022-2024 and the agent then scanned all of Kenya. A year is
    kept only if it appears in the request (a range such as 2022-2024 keeps the years between its ends); a buyer or supplier only
    if at least half of its significant words appear in the request. Returns (grounded scope, names of the parts removed)."""
    text = (query or "").lower()
    out, removed = dict(scope), []
    literal = sorted({int(y) for y in re.findall(r"(?<![0-9])((?:19|20)[0-9]{2})(?![0-9])", text)})
    years = [y for y in (scope.get("years") or []) if literal and literal[0] <= y <= literal[-1]]
    if (scope.get("years") or []) and years != list(scope.get("years") or []):
        removed.append("years")
    out["years"] = years or None
    for key in ("buyer", "supplier"):
        words = _words(scope.get(key) or "")
        if words and sum(w in text for w in words) / len(words) < NAME_MATCH_SHARE:
            removed.append(key)
            out[key] = None
    return out, removed


def has_scope(scope: dict[str, Any]) -> bool:
    """True when the request names a buyer, a supplier or a period. Without any of them every tool would scan all of Kenya."""
    return bool((scope.get("buyer") or "").strip() or (scope.get("supplier") or "").strip() or scope.get("years"))


def cap_findings(findings: list[dict[str, Any]], severities: list[str], limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep the `limit` strongest findings: higher severity first, then more cited evidence; ties keep their order.

    Returns (kept in strength order, capped). A request that matches thousands of awards can otherwise create dozens of flags
    nobody can review."""
    rank = {s: i for i, s in enumerate(severities)}
    order = sorted(range(len(findings)), key=lambda i: (-rank.get(findings[i]["severity"], 0), -len(findings[i]["evidence"]), i))
    kept = [findings[i] for i in order[:limit]]
    capped = [findings[i] for i in order[limit:]]
    return kept, capped


# ----------------------------------------------------------------------------- unknown arguments
def prune_unknown_args(args: dict[str, Any], schema: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Drop arguments the tool's schema does not declare. A small model sometimes invents one (a `buyer` on a tool that has
    none); the server would ignore it silently and the step would run unscoped while the plan says otherwise, so the agent
    removes it and records that it did. Returns (kept args, dropped names)."""
    props = schema.get("properties")
    if not props:
        return dict(args), []
    kept = {k: v for k, v in args.items() if k in props}
    return kept, sorted(set(args) - set(kept))


# ----------------------------------------------------------------------------- argument checks
def schema_problems(args: Any, schema: dict[str, Any]) -> list[str]:
    """Human-readable problems of `args` against a tool's JSON input schema (empty list = valid)."""
    if not isinstance(args, dict):
        return [f"arguments must be a JSON object, got {type(args).__name__}"]
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{'.'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in validator.iter_errors(args)]


def error_text(output: Any) -> str | None:
    """The tool-reported error message of a result, if any."""
    res = output.get("result") if isinstance(output, dict) else None
    return res.get("error") if isinstance(res, dict) else None


COMPACT_FLAGS_SHOWN = 5
COMPACT_OCIDS_SHOWN = 6


def compact_result(output: Any) -> dict[str, Any]:
    """A tool output reduced to what an LLM needs: its explanation, flag summaries and a few cited ocids (never full rows)."""
    res = output.get("result") if isinstance(output, dict) else None
    res = res if isinstance(res, dict) else {}
    evidence = output.get("evidence") or [] if isinstance(output, dict) else []
    return {"explanation": res.get("explanation"), "flags": [f.get("summary") for f in res.get("flags", [])][:COMPACT_FLAGS_SHOWN],
            "n_evidence": len(evidence), "ocids": sorted({e["ocid"] for e in evidence})[:COMPACT_OCIDS_SHOWN]}


# ----------------------------------------------------------------------------- candidates and citation guard
def extract_candidates(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every flag proposed by a tool (`result.flags`), with a stable id. The agent needs no per-tool knowledge."""
    out = []
    for r in results:
        res = (r.get("output") or {}).get("result") if isinstance(r.get("output"), dict) else None
        for f in (res or {}).get("flags", []) if isinstance(res, dict) else []:
            out.append({"id": f"c{len(out) + 1}", "tool": r["tool"], **f})
    return out


def evidence_pool(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """All evidence items returned by tools in this run, tagged with the tool that returned them."""
    pool, seen = [], set()
    for r in results:
        out = r.get("output")
        if not isinstance(out, dict):
            continue
        for e in out.get("evidence") or []:
            key = (r["tool"], e["ocid"], e["award_id"], e["field"], json.dumps(e["value"], default=str))
            if key not in seen:
                seen.add(key)
                pool.append({**e, "source": r["tool"]})
    return pool


def _trim(token: str) -> str:
    token = token.rstrip(TRAILING)
    while token.endswith(")") and token.count(")") > token.count("("):
        token = token[:-1].rstrip(TRAILING)
    return token


def ocids_in(text: str) -> set[str]:
    """ocid-like strings mentioned in free text."""
    return {_trim(m) for m in OCID_RE.findall(text or "")}


def guard_findings(drafts: list[dict[str, Any]], candidates: list[dict[str, Any]], pool: list[dict[str, Any]],
                   severities: list[str], min_chars: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The citation guard. Returns (accepted findings, dropped findings with reasons).

    A finding is DROPPED if any ocid it mentions (its ocid list or its explanation text) is not among the ocids of
    tool-returned evidence, or if no tool-returned evidence supports it. Accepted findings take their evidence from
    tool results, never from the model: a candidate's own evidence, or the pool items for the cited ocids and rule.
    Candidates the model did not mention are appended unchanged, so the model can re-order and re-word findings
    but cannot make a tool-proposed flag disappear."""
    pool_ocids = {e["ocid"] for e in pool}
    by_id = {c["id"]: c for c in candidates}
    accepted, dropped, used = [], [], set()
    for d in drafts:
        cand = by_id.get(d.get("candidate_id") or "")
        mentioned = set(d.get("ocids") or []) | ocids_in(d.get("explanation", ""))
        unknown = sorted(mentioned - pool_ocids)
        if unknown:
            dropped.append({"draft": d, "reason": "ocid not in tool-returned evidence", "ocids": unknown})
            continue
        if cand is not None:
            if cand["id"] in used:
                dropped.append({"draft": d, "reason": "duplicate of an earlier finding for the same candidate"})
                continue
            used.add(cand["id"])
            rule, evidence = cand["tool"], cand["evidence"]
        else:
            rule = d.get("rule") or ""
            wanted = set(d.get("ocids") or [])
            evidence = [{k: e[k] for k in ("ocid", "award_id", "field", "value")}
                        for e in pool if e["ocid"] in wanted and e["source"] == rule]
            if not wanted or not evidence:
                dropped.append({"draft": d, "reason": "no tool-returned evidence supports this finding", "ocids": sorted(wanted)})
                continue
        text = (d.get("explanation") or "").strip()
        severity = (d.get("severity") or "").strip().lower()
        accepted.append({
            "rule": rule, "evidence": evidence, "ocid": evidence[0]["ocid"], "ocids": sorted({e["ocid"] for e in evidence}),
            "severity": severity if severity in severities else (cand or {}).get("severity_hint", severities[len(severities) // 2]),
            "explanation": text if len(text) >= min_chars else (cand or {}).get("summary", text),
            "candidate_id": cand["id"] if cand else None, "source": "candidate" if cand else "model_extra"})
    for c in candidates:
        if c["id"] not in used:
            accepted.append({"rule": c["tool"], "evidence": c["evidence"], "ocid": c["evidence"][0]["ocid"], "ocids": c["ocids"],
                             "severity": c["severity_hint"], "explanation": c["summary"], "candidate_id": c["id"],
                             "source": "candidate_default"})
    return accepted, dropped
