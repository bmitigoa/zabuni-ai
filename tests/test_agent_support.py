"""LLM wrapper, settings, audit log, environment pass-through, 'no tool names in agent code', report builder."""
import ast
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from agent import llm as llm_module
from agent.audit import AuditLog, summarise_output
from agent.llm import ChatLLM, LLMError, assert_open_weights, create_llm
from agent.mcp_client import McpBackend, ToolCallFailed, zabuni_connection, zabuni_env
from agent.report import BANNER, build_summary_md
from agent.settings import DEFAULT_MODELS, Settings, SettingsError, load_settings
from mcp_server.registry import TOOLS, discover_tools
from mcp_server.rules import load_rules

ROOT = Path(__file__).resolve().parent.parent


class Mini(BaseModel):
    x: int


class ScriptedChat:
    """A chat model whose structured runnable replays a list of outcomes (exceptions are raised)."""

    def __init__(self, outcomes):
        self.outcomes, self.seen = list(outcomes), []

    def with_structured_output(self, schema, include_raw=True):
        outer = self

        class Runnable:
            async def ainvoke(self, messages):
                outer.seen.append(list(messages))
                out = outer.outcomes.pop(0)
                if isinstance(out, Exception):
                    raise out
                return out
        return Runnable()


def ok(x=1, tokens=7):
    return {"raw": SimpleNamespace(usage_metadata={"total_tokens": tokens}), "parsed": Mini(x=x), "parsing_error": None}


def bad(err="missing field x"):
    return {"raw": SimpleNamespace(usage_metadata={"total_tokens": 3}), "parsed": None, "parsing_error": ValueError(err)}


@pytest.fixture
def sleeps(monkeypatch):
    waited = []

    async def fake_sleep(s):
        waited.append(s)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return waited


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------------------- ChatLLM.structured
def test_rate_limit_is_retried_with_exponential_backoff_and_tokens_are_counted(sleeps):
    chat = ScriptedChat([Exception("RateLimitError: Error code: 429 - too many"), Exception("429 again"), ok(5, tokens=11)])
    llm = ChatLLM(chat, "t", max_retries=4, backoff_base_s=5.0)
    assert run(llm.structured(Mini, "sys", "user")).x == 5
    assert sleeps == [5.0, 10.0] and llm.tokens == 11 and llm.calls == 1


def test_invalid_output_is_retried_with_the_validation_error_fed_back(sleeps):
    chat = ScriptedChat([bad("x is required"), ok(2)])
    llm = ChatLLM(chat, "t", max_retries=2, backoff_base_s=1.0)
    assert run(llm.structured(Mini, "sys", "user")).x == 2
    assert len(chat.seen[0]) == 2 and len(chat.seen[1]) == 3
    assert "x is required" in chat.seen[1][-1].content


def test_a_400_from_the_api_is_fed_back_and_retried(sleeps):
    chat = ScriptedChat([Exception("BadRequestError: Error code: 400 - Failed to parse tool call arguments"), ok(3)])
    llm = ChatLLM(chat, "t", max_retries=2, backoff_base_s=1.0)
    assert run(llm.structured(Mini, "s", "u")).x == 3 and "rejected by the API" in chat.seen[1][-1].content


def test_permanent_errors_fail_immediately_with_a_useful_message(sleeps):
    chat = ScriptedChat([Exception("NotFoundError: Error code: 404 - The model `x` does not exist"), ok()])
    llm = ChatLLM(chat, "groq:x", max_retries=4, backoff_base_s=1.0)
    with pytest.raises(LLMError, match=r"python -m agent\.models"):
        run(llm.structured(Mini, "s", "u"))
    assert len(chat.seen) == 1 and sleeps == []                           # no retries, no waiting


def test_retries_are_bounded(sleeps):
    chat = ScriptedChat([bad(), bad(), bad()])
    llm = ChatLLM(chat, "t", max_retries=2, backoff_base_s=1.0)
    with pytest.raises(LLMError, match="after 3 attempts"):
        run(llm.structured(Mini, "s", "u"))
    assert len(chat.seen) == 3


# ----------------------------------------------------------------------------- settings and the open-weights rule
def test_settings_defaults_validation_and_secret_handling():
    s = load_settings(environ={"LLM_PROVIDER": "groq", "GROQ_API_KEY": "gsk_secret_value"})
    assert s.model == DEFAULT_MODELS["groq"] and "gsk_secret_value" not in repr(s) and "gsk_secret_value" not in str(s)
    assert load_settings(environ={"LLM_PROVIDER": "ollama"}).model == DEFAULT_MODELS["ollama"]
    with pytest.raises(SettingsError, match="LLM_PROVIDER must be one of"):
        load_settings(environ={"LLM_PROVIDER": "openai"})
    with pytest.raises(SettingsError, match="GROQ_API_KEY") as exc:
        load_settings(environ={"LLM_PROVIDER": "groq"})
    assert "gsk_" not in str(exc.value)


@pytest.mark.parametrize("model", ["qwen/qwen3.8-27b", "llama-3.3-70b-versatile", "qwen2.5:7b", "llama3.1:8b"])
def test_open_weights_models_are_accepted(model):
    assert_open_weights(model, load_rules()["agent"]["open_weights_families"])


@pytest.mark.parametrize("model", ["gpt-4o", "claude-sonnet-5-5", "gemini-2.5-pro"])
def test_proprietary_models_are_refused_before_any_client_is_built(model):
    with pytest.raises(LLMError, match="open-weights"):
        create_llm(Settings(provider="groq", model=model, groq_api_key="x"), load_rules())


def test_the_default_models_build_without_network_and_respect_the_output_cap():
    rules = load_rules()
    groq = create_llm(Settings(provider="groq", model=DEFAULT_MODELS["groq"], groq_api_key="x"), rules)
    assert groq.name == f"groq:{DEFAULT_MODELS['groq']}" and groq.chat.max_tokens == rules["agent"]["llm_max_tokens"]
    local = create_llm(Settings(provider="ollama", model=DEFAULT_MODELS["ollama"]), rules)
    assert local.name == f"ollama:{DEFAULT_MODELS['ollama']}"


# ----------------------------------------------------------------------------- audit log
def test_every_tool_call_record_has_the_required_fields_and_truncation_is_flagged(tmp_path):
    audit = AuditLog("run_x", tmp_path, max_chars=60)
    big = {"result": {"flags": [1, 2], "flagged": True}, "evidence": [{"a": 1}] * 3, "params_used": {}, "warnings": ["w"] * 5}
    audit.call(server="zabuni", tool="t", inputs={"a": 1}, output=big, duration_s=0.25)
    audit.call(server="zabuni", tool="t", inputs={}, output=None, duration_s=0.1, error="ToolCallFailed: boom")
    lines = [json.loads(x) for x in (tmp_path / "tool_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all({"run_id", "timestamp", "server", "tool", "inputs", "output_summary", "output", "output_truncated",
                "duration_ms", "error", "step"} <= set(r) for r in lines)
    first, second = lines
    assert first["run_id"] == "run_x" and first["duration_ms"] == 250.0 and first["timestamp"].endswith("Z")
    assert first["output_truncated"] is True and len(first["output"]) == 60
    assert first["output_summary"] == {"evidence_count": 3, "warnings": ["w"] * 3, "flags": 2, "flagged": True}
    assert second["error"] == "ToolCallFailed: boom" and second["output"] is None
    assert summarise_output("plain text")["chars"] == 10


def test_a_failed_transport_call_is_raised_and_logged(tmp_path):
    async def go():
        settings = Settings(provider="groq", model="x", logs_dir=tmp_path, outputs_dir=tmp_path / "o")
        async with McpBackend(settings, use_filesystem=False) as backend:
            backend.audit = AuditLog("run_err", tmp_path, 1000)
            with pytest.raises(ToolCallFailed):
                await backend.call("zabuni", "no_such_tool", {"a": 1})
    run(go())
    rec = json.loads((tmp_path / "tool_calls.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["tool"] == "no_such_tool" and rec["error"] and rec["inputs"] == {"a": 1}


# ----------------------------------------------------------------------------- environment pass-through
def test_only_zabuni_settings_are_forwarded_to_the_server_subprocess(monkeypatch):
    env = {"ZABUNI_DB": "d", "ZABUNI_RULES_FILE": "r", "GROQ_API_KEY": "gsk_secret", "PATH": "p", "HOME": "h"}
    assert zabuni_env(env) == {"ZABUNI_DB": "d", "ZABUNI_RULES_FILE": "r"}
    monkeypatch.setenv("GROQ_API_KEY", "gsk_secret")
    monkeypatch.setenv("ZABUNI_FLAGS_DB", "f")
    conn = zabuni_connection()
    assert "GROQ_API_KEY" not in conn["env"] and conn["env"]["ZABUNI_FLAGS_DB"] == "f"


def test_a_rules_override_and_a_database_path_really_reach_the_server(tmp_path, monkeypatch):
    rules_file = tmp_path / "rules.json"
    rules_file.write_text(json.dumps({"agent": {"max_steps": 7}, "splitting": {"window_days": 14}}), encoding="utf-8")
    monkeypatch.setenv("ZABUNI_RULES_FILE", str(rules_file))

    async def go():
        settings = Settings(provider="groq", model="x", logs_dir=tmp_path, outputs_dir=tmp_path / "o")
        async with McpBackend(settings, use_filesystem=False) as backend:
            rules = await backend.read_resource_json("rules://ppada")
            tool_desc = next(t.description for t in await backend.tools() if t.name == "detect_splitting")
            return rules, tool_desc
    rules, desc = run(go())
    assert rules["agent"]["max_steps"] == 7 and rules["splitting"]["window_days"] == 14
    assert "default 14" in " ".join(desc.split()) or "(default 14" in " ".join(desc.split())      # description re-rendered

    monkeypatch.setenv("ZABUNI_DB", str(tmp_path / "no_such.duckdb"))

    async def bad_db():
        settings = Settings(provider="groq", model="x", logs_dir=tmp_path, outputs_dir=tmp_path / "o")
        async with McpBackend(settings, use_filesystem=False) as backend:
            return await backend.call("zabuni", "search_awards", {"buyer": "Makueni"})
    out = run(bad_db())
    assert "Database not found" in out["result"]["error"] and "no_such.duckdb" in out["result"]["error"]


# ----------------------------------------------------------------------------- no hard-coded tool names in agent code
def _string_constants_without_docstrings(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]


def test_no_zabuni_tool_name_appears_in_agent_code():
    """Tools are discovered via list_tools. Names live in rules (follow-up policy data) and the server, never here."""
    names = set(discover_tools()) | set(TOOLS)
    offenders = []
    for path in sorted((ROOT / "agent").glob("*.py")):
        for line, text in _string_constants_without_docstrings(path):
            for n in names:
                if re.search(rf"\b{re.escape(n)}\b", text):
                    offenders.append(f"{path.name}:{line} mentions {n}")
    assert not offenders, offenders


# ----------------------------------------------------------------------------- report
def test_the_draft_summary_is_deterministic_about_status_flags_and_limits():
    state = {"run_id": "run_1", "query": "Investigate X", "summary": "Neutral.", "stopped_reason": "step cap of 2 tool calls reached",
             "plan": {"objective": "obj", "steps": [{"tool": "t", "args": {"a": 1}, "reason": "why"}]},
             "results": [{"tool": "t", "args": {"a": 1}, "origin": "plan", "error": None,
                          "output": {"evidence": [{}], "result": {"flags": [{}]}}}],
             "notes": ["follow-up after t: u {}"], "dropped": [{"reason": "ocid not in tool-returned evidence", "ocids": ["ocds-9-x"]}],
             "flags_created": [{"flag_id": "flag_abc", "rule": "t", "severity": "high", "status": "pending_review",
                                "explanation": "Something happened.", "evidence": [{"ocid": f"o{i}", "award_id": "a", "field": "f", "value": i}
                                                                                     for i in range(8)]}]}
    md = build_summary_md(state, {"model": "m", "llm_calls": 3, "tokens": 100, "tool_calls": 4, "seconds": 9.4}, awards_listed=5)
    assert BANNER in md and "pending human review" in md.lower() and "flag_abc" in md and "3 more evidence item" in md
    assert "Investigation stopped early" in md and "Dropped by the citation guard" in md and "## Limitations" in md
    assert "recommend" in md and "awards a tender" in md                   # the banner states the agent recommends/awards nothing


# ----------------------------------------------------------------------------- plan schema: args travel as a JSON string
def test_the_llm_sees_args_as_a_json_string_and_the_code_still_gets_a_dict():
    from agent.state import ArgsFix, Plan, PlanStep, parse_json_object
    step_props = Plan.model_json_schema()["$defs"]["PlanStep"]["properties"]
    assert "args_json" in step_props and "args" not in step_props          # an open object made the real model return {}
    assert step_props["args_json"]["type"] == "string"
    assert PlanStep(tool="t", args={"a": [1, 2]}, reason="r").args == {"a": [1, 2]}      # convenient constructor still works
    assert PlanStep(tool="t", args_json='{"buyer": "X"}', reason="r").args == {"buyer": "X"}
    assert PlanStep(tool="t", args_json="not json", reason="r").args == {}                # bad JSON -> {} (scope gets injected)
    assert PlanStep(tool="t", args_json="[1, 2]", reason="r").args == {}                  # a list is not an argument object
    assert ArgsFix(args={"limit": 5}).args == {"limit": 5} and ArgsFix().args == {} and parse_json_object(None) == {}


# ----------------------------------------------------------------------------- rate limits: honour hints, fail fast on quotas
def test_the_providers_retry_after_hint_is_parsed_and_honoured(sleeps):
    from agent.llm import retry_after_seconds
    assert retry_after_seconds("Please try again in 6m57.744s.") == pytest.approx(417.744)
    assert retry_after_seconds("try again in 1.74s") == 1.74 and retry_after_seconds("try again in 2h3m4s") == 7384.0
    assert retry_after_seconds("no hint at all") is None
    chat = ScriptedChat([Exception("RateLimitError: 429 on tokens per minute (OTPM). Please try again in 3.5s."), ok(4)])
    llm = ChatLLM(chat, "t", max_retries=2, backoff_base_s=1.0)
    assert run(llm.structured(Mini, "s", "u")).x == 4 and sleeps == [4.0]              # hint + 0.5 s margin, not the 1 s base


def test_a_daily_quota_fails_fast_with_a_clear_message_and_hides_the_organisation(sleeps):
    msg = ("RateLimitError: Error code: 429 - Rate limit reached for model `m` in organization `org_abc123` on tokens per day "
           "(TPD): Limit 200000, Used 196883, Requested 4084. Please try again in 6m57.744s.")
    chat = ScriptedChat([Exception(msg), ok()])
    llm = ChatLLM(chat, "groq:m", max_retries=4, backoff_base_s=5.0)
    with pytest.raises(LLMError, match="quota exhausted") as exc:
        run(llm.structured(Mini, "s", "u"))
    assert "org_abc123" not in str(exc.value) and "org_<hidden>" in str(exc.value)
    assert len(chat.seen) == 1 and sleeps == []                                        # no pointless retries


def test_a_wait_longer_than_the_cap_is_not_attempted(sleeps):
    chat = ScriptedChat([Exception("RateLimitError: 429 tokens per minute. Please try again in 5m0s."), ok()])
    llm = ChatLLM(chat, "t", max_retries=3, backoff_base_s=1.0, max_wait_s=90.0)
    with pytest.raises(LLMError, match="quota exhausted"):
        run(llm.structured(Mini, "s", "u"))
    assert sleeps == []
