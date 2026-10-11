"""Day 4 support code: model fallback on 429, usage ledger and cost, run budget, demo guard, MCP reconnect, argument
pruning and the compact tool catalogue. No network: chat models are scripted; the MCP server in the reconnect test is a
tiny local script that really exits."""
import asyncio
import json
import sys
import textwrap
import time
from types import SimpleNamespace

import anyio
import pytest

from agent.audit import AuditLog
from agent.budget import Budget
from agent.demo_guard import DemoGuard
from agent.discovery import ToolInfo, brief, catalogue, compact_args
from agent.llm import ChatLLM, FallbackLLM, LLMError, LLMQuotaExhausted
from agent.mcp_client import McpBackend, ToolCallFailed
from agent.policy import compact_result, prune_unknown_args
from agent.settings import Settings
from agent.usage import UsageLedger, parse_quota_report, price_usd
from mcp_server.rules import load_rules
from tests.test_agent_support import Mini, ScriptedChat, bad, ok, run, sleeps  # noqa: F401  (sleeps is a fixture)

RULES = load_rules()
QUOTA_429 = ("RateLimitError: Error code: 429 - Rate limit reached for model `m` in organization `org_abc123` on tokens per day "
             "(TPD): Limit 200000, Used 199000, Requested 3000. Please try again in 6m57.744s.")


def chain(*outcome_lists, ledger=None, run_id="run_x"):
    models = []
    for i, outs in enumerate(outcome_lists):
        models.append(ChatLLM(ScriptedChat(outs), f"groq:model{i}", max_retries=2, backoff_base_s=0.01, rules=RULES, ledger=ledger))
    llm = FallbackLLM(models)
    llm.attach(None, ledger, run_id)
    return llm


# ----------------------------------------------------------------------------- 429 -> retry -> fallback -> stop cleanly
def test_a_transient_429_is_retried_with_backoff_on_the_same_model(sleeps):
    llm = chain([Exception("RateLimitError: 429 tokens per minute. Please try again in 2s."), ok(5)], [ok(9)])
    assert run(llm.structured(Mini, "s", "u")).x == 5 and sleeps == [2.5] and llm.index == 0


def test_a_daily_quota_429_falls_back_to_the_second_model_and_stays_there(sleeps):
    audit_events = []
    llm = chain([Exception(QUOTA_429)], [ok(7), ok(8)])
    llm.chain[0].audit = SimpleNamespace(event=lambda t, **kw: audit_events.append((t, kw)))
    assert run(llm.structured(Mini, "s", "u")).x == 7
    assert llm.name == "groq:model1" and llm.index == 1                                        # sticky for the rest of the run
    assert run(llm.structured(Mini, "s", "u")).x == 8 and len(llm.chain[0].chat.seen) == 1     # the first model is not retried
    fb = [kw for t, kw in audit_events if t == "llm_fallback"]
    assert fb and fb[0]["from_model"] == "groq:model0" and fb[0]["to_model"] == "groq:model1" and "org_abc123" not in fb[0]["reason"]


def test_when_every_model_is_out_of_quota_the_run_stops_with_a_clear_message(sleeps):
    llm = chain([Exception(QUOTA_429)], [Exception(QUOTA_429.replace("model `m`", "model `n`"))])
    with pytest.raises(LLMQuotaExhausted) as exc:
        run(llm.structured(Mini, "s", "u"))
    text = str(exc.value)
    assert "Every configured model is out of quota" in text and "LLM_PROVIDER=ollama" in text and "org_abc123" not in text
    assert isinstance(exc.value, LLMError)                                                      # callers can catch LLMError


def test_a_non_quota_failure_does_not_trigger_the_fallback(sleeps):
    llm = chain([Exception("NotFoundError: model_not_found")], [ok(1)])
    with pytest.raises(LLMError, match="model_not_found"):
        run(llm.structured(Mini, "s", "u"))
    assert llm.index == 0


def test_the_fallback_chain_totals_tokens_across_models(sleeps):
    llm = chain([Exception(QUOTA_429)], [ok(1, tokens=11)])
    run(llm.structured(Mini, "s", "u"))
    assert llm.tokens == 11 and llm.calls == 1


# ----------------------------------------------------------------------------- usage ledger, cost and remaining quota
def test_tokens_and_cost_are_logged_per_call_and_per_run(tmp_path):
    ledger = UsageLedger(tmp_path / "u.jsonl")
    chat = ScriptedChat([{"raw": SimpleNamespace(usage_metadata={"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200}),
                          "parsed": Mini(x=1), "parsing_error": None}])
    llm = ChatLLM(chat, "groq:qwen/qwen3.8-27b", 1, 0.01, rules=RULES, ledger=ledger)
    llm.attach(None, ledger, "run_a")
    run(llm.structured(Mini, "s", "u", kind="plan"))
    price = RULES["agent"]["pricing"]["groq"]["qwen/qwen3.8-27b"]
    expected = (1000 * price["input_per_mtok"] + 200 * price["output_per_mtok"]) / 1_000_000
    assert llm.cost_usd == pytest.approx(expected) and llm.tokens == 1200
    totals = ledger.run_totals("run_a")
    assert totals["total_tokens"] == 1200 and totals["cost_usd"] == pytest.approx(expected) and totals["llm_calls"] == 1
    line = json.loads((tmp_path / "u.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert line["kind"] == "plan" and line["prompt_tokens"] == 1000 and line["model"] == "qwen/qwen3.8-27b"


def test_a_local_model_costs_nothing_and_has_no_quota_estimate(tmp_path):
    ledger = UsageLedger(tmp_path / "u.jsonl")
    assert price_usd(RULES, "ollama", "qwen2.5:7b", 5000, 500) == 0.0
    assert ledger.remaining(RULES, "ollama", "qwen2.5:7b") is None
    assert price_usd(RULES, "groq", "unknown/model", 1, 1) is None                              # unknown price is not invented
    assert RULES["agent"]["pricing"]["as_of"].startswith("2026-10-11")                          # the price list is dated


def test_remaining_daily_quota_is_estimated_from_the_ledger_and_corrected_by_the_provider(tmp_path):
    ledger = UsageLedger(tmp_path / "u.jsonl")
    limit = RULES["agent"]["groq_daily_token_limits"]["qwen/qwen3.8-27b"]
    now = time.time()
    ledger.record_call(run_id="r", provider="groq", model="qwen/qwen3.8-27b", kind="plan", prompt_tokens=900, completion_tokens=100,
                       seconds=1, cost_usd=0.0, now=now - 60)
    ledger.record_call(run_id="r", provider="groq", model="qwen/qwen3.8-27b", kind="plan", prompt_tokens=10, completion_tokens=0,
                       seconds=1, cost_usd=0.0, now=now - 48 * 3600)                             # outside the 24 h window
    assert ledger.remaining(RULES, "groq", "qwen/qwen3.8-27b", now) == limit - 1000
    ledger.record_quota_report(provider="groq", model="qwen/qwen3.8-27b", limit=limit, used=190000, now=now - 30)
    assert ledger.remaining(RULES, "groq", "qwen/qwen3.8-27b", now) == limit - 190000          # the provider's figure wins
    assert parse_quota_report(QUOTA_429) == {"limit": 200000, "used": 199000, "requested": 3000}
    assert parse_quota_report("some other error") is None


def test_a_torn_ledger_line_does_not_break_accounting(tmp_path):
    path = tmp_path / "u.jsonl"
    ledger = UsageLedger(path)
    ledger.record_call(run_id="r", provider="groq", model="m", kind="k", prompt_tokens=5, completion_tokens=5, seconds=1, cost_usd=None)
    path.write_text(path.read_text(encoding="utf-8") + '{"type": "call", "ts"\n', encoding="utf-8")
    assert ledger.run_totals("r")["total_tokens"] == 10


# ----------------------------------------------------------------------------- run budget
def test_the_budget_stops_on_tool_calls_or_tokens_and_keeps_a_reserve_for_the_summary():
    state = {"calls": 0, "tokens": 0}
    b = Budget(max_calls=3, max_tokens=1000, synth_reserve=200, calls=lambda: state["calls"], tokens=lambda: state["tokens"])
    assert b.stop_reason() is None
    state["tokens"] = 800
    assert "token budget" in b.stop_reason() and b.stop_reason(keep_reserve=False) is None     # the summary may still run
    state["tokens"] = 1000
    assert b.stop_reason(keep_reserve=False) is not None
    state.update(tokens=0, calls=3)
    assert "tool-call budget" in b.stop_reason()
    assert b.stop_reason(count_calls=False) is None                                             # the summary ignores the call cap
    assert "not evidence that none exists" in b.memo_notice("x") and b.memo_notice(None) is None


def test_the_budget_limits_come_from_rules_not_from_code():
    b = Budget.from_rules(RULES["agent"], lambda: 0, lambda: 0)
    assert (b.max_calls, b.max_tokens, b.synth_reserve) == (RULES["agent"]["max_steps"], RULES["agent"]["max_total_tokens"],
                                                             RULES["agent"]["synth_reserve_tokens"])


# ----------------------------------------------------------------------------- hosted demo guard
def test_the_demo_guard_limits_runs_per_visitor_per_hour_and_resets():
    clock = [1000.0]
    guard = DemoGuard(RULES, UsageLedger(__import__("pathlib").Path(__import__("tempfile").mkdtemp()) / "u.jsonl"), "ollama", "m",
                      clock=lambda: clock[0])
    n = RULES["demo"]["runs_per_visitor_per_hour"]
    assert all(guard.check("a").allowed for _ in range(n))
    blocked = guard.check("a")
    assert not blocked.allowed and blocked.reason == "rate" and "recorded demo video" in blocked.message
    assert guard.check("b").allowed                                                              # another visitor is unaffected
    clock[0] += 3601
    assert guard.check("a").allowed                                                              # the hour passed


def test_the_demo_guard_shows_a_friendly_message_when_the_daily_quota_is_used(tmp_path):
    ledger = UsageLedger(tmp_path / "u.jsonl")
    limit = RULES["agent"]["groq_daily_token_limits"]["qwen/qwen3.8-27b"]
    ledger.record_quota_report(provider="groq", model="qwen/qwen3.8-27b", limit=limit, used=limit - 100)
    guard = DemoGuard(RULES, ledger, "groq", "qwen/qwen3.8-27b")
    decision = guard.check("v")
    assert not decision.allowed and decision.reason == "quota"
    assert "daily free-tier quota" in decision.message and "recorded demo video" in decision.message
    assert DemoGuard(RULES, ledger, "ollama", "qwen2.5:7b").check("v").allowed                  # local models have no daily quota


# ----------------------------------------------------------------------------- MCP reconnect
class FlakyRunner:
    """A runner whose session fails with a transport error on its first call (mock for the reconnect logic)."""
    starts = 0

    def __init__(self, client, name):
        self.name, self.session = name, None

    async def start(self):
        FlakyRunner.starts += 1
        n = FlakyRunner.starts
        async def call_tool(tool, args):
            if n == 1:
                raise anyio.ClosedResourceError()
            return SimpleNamespace(structuredContent={"result": {"ok": True}}, content=[], isError=False)
        self.session = SimpleNamespace(call_tool=call_tool)

    async def stop(self):
        self.session = None


def test_a_dead_connection_restarts_the_server_once_and_the_call_succeeds(tmp_path, monkeypatch):
    FlakyRunner.starts = 0
    monkeypatch.setattr("agent.mcp_client.MultiServerMCPClient", lambda conns: None)
    audit = AuditLog("run_r", tmp_path, 1000)
    settings = Settings(provider="ollama", model="m", logs_dir=tmp_path, outputs_dir=tmp_path / "o")

    async def go():
        async with McpBackend(settings, audit, use_filesystem=False, connections={"zabuni": {}}, reconnects=1,
                              runner_factory=FlakyRunner) as backend:
            return await backend.call("zabuni", "t", {"a": 1})
    assert run(go()) == {"result": {"ok": True}} and FlakyRunner.starts == 2
    ev = [json.loads(x) for x in (tmp_path / "agent_events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert ev[0]["type"] == "mcp_reconnect" and ev[0]["server"] == "zabuni" and "ClosedResourceError" in ev[0]["reason"]


def test_a_server_that_stays_down_gives_a_clear_error_after_one_reconnect(tmp_path, monkeypatch):
    class AlwaysDead(FlakyRunner):
        async def start(self):
            FlakyRunner.starts += 1
            async def call_tool(tool, args):
                raise anyio.BrokenResourceError()
            self.session = SimpleNamespace(call_tool=call_tool)

    FlakyRunner.starts = 0
    monkeypatch.setattr("agent.mcp_client.MultiServerMCPClient", lambda conns: None)
    audit = AuditLog("run_r", tmp_path, 1000)
    settings = Settings(provider="ollama", model="m", logs_dir=tmp_path, outputs_dir=tmp_path / "o")

    async def go():
        async with McpBackend(settings, audit, use_filesystem=False, connections={"zabuni": {}}, reconnects=1,
                              runner_factory=AlwaysDead) as backend:
            await backend.call("zabuni", "t", {})
    with pytest.raises(ToolCallFailed, match="unavailable after 1 reconnect"):
        run(go())
    assert FlakyRunner.starts == 2                                                               # one restart, not a loop
    rec = json.loads((tmp_path / "tool_calls.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["error"] and "unavailable" in rec["error"]                                        # the failure is in the audit log


DYING_SERVER = textwrap.dedent('''
    import os, sys
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("dying")
    marker = sys.argv[1]

    @mcp.tool()
    def ping() -> dict:
        if not os.path.exists(marker):
            open(marker, "w").close()
            os._exit(1)                      # the process dies in the middle of the first call
        return {"result": {"pong": True}}

    mcp.run(transport="stdio")
''')


def test_a_real_server_process_that_dies_mid_call_is_restarted_and_the_call_retried(tmp_path):
    script = tmp_path / "dying.py"
    script.write_text(DYING_SERVER, encoding="utf-8")
    marker = tmp_path / "marker"
    conns = {"zabuni": {"transport": "stdio", "command": sys.executable, "args": [str(script), str(marker)], "env": None}}
    audit = AuditLog("run_real", tmp_path, 1000)
    settings = Settings(provider="ollama", model="m", logs_dir=tmp_path, outputs_dir=tmp_path / "o")

    async def go():
        async with McpBackend(settings, audit, use_filesystem=False, connections=conns, reconnects=1) as backend:
            return await backend.call("zabuni", "ping", {})
    assert run(asyncio.wait_for(go(), 60)) == {"result": {"pong": True}}
    types = [json.loads(x)["type"] for x in (tmp_path / "agent_events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "mcp_reconnect" in types


# ----------------------------------------------------------------------------- argument pruning and the compact catalogue
SCHEMA = {"properties": {"buyer": {"type": "string", "description": "organisation name"}, "limit": {"type": "integer"},
                         "kind": {"enum": ["a", "b"]}, "years": {"anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "null"}]}},
          "required": ["buyer"]}


def test_arguments_the_tool_does_not_declare_are_dropped_and_reported():
    args, dropped = prune_unknown_args({"buyer": "X", "limit": 3, "invented": 1, "also_invented": 2}, SCHEMA)
    assert args == {"buyer": "X", "limit": 3} and dropped == ["also_invented", "invented"]
    assert prune_unknown_args({"buyer": "X"}, {}) == ({"buyer": "X"}, [])                         # no schema, nothing to prune


def test_the_compact_catalogue_is_short_and_keeps_names_types_and_required_marks():
    assert compact_args(SCHEMA) == "buyer*: str (organisation name); limit: int; kind: a|b; years: int[]"
    long = "Finds things. " + "Detail sentence number one. " * 30
    assert len(brief(long, 100)) <= 104 and brief("Short one.", 100) == "Short one."
    tool = ToolInfo("t1", long, SCHEMA, True)
    text = catalogue([tool], 100)
    assert text.startswith("- t1: ") and "args: buyer*: str" in text and len(text) < 260


def test_the_real_catalogue_is_far_smaller_than_the_full_descriptions():
    from mcp_server.registry import TOOLS, discover_tools
    discover_tools()
    infos = [ToolInfo(t.name, t.fn.__doc__ or "", {}, True) for t in TOOLS.values() if getattr(t, "read_only", True)]
    full = sum(len(t.description) for t in infos)
    assert len(catalogue(infos, RULES["agent"]["catalogue_brief_chars"])) < full / 2


def test_tool_results_are_compacted_before_an_llm_sees_them():
    out = {"result": {"explanation": "3.2x median", "flags": [{"summary": f"flag {i}"} for i in range(9)], "awards": [{"x": 1}] * 500},
           "evidence": [{"ocid": f"ocds-{i % 3}", "award_id": "1", "field": "f", "value": 1} for i in range(40)]}
    c = compact_result(out)
    assert c["n_evidence"] == 40 and len(c["flags"]) == 5 and c["ocids"] == ["ocds-0", "ocds-1", "ocds-2"]
    assert "awards" not in json.dumps(c) and len(json.dumps(c)) < 300


def test_a_per_minute_limit_is_not_recorded_as_the_daily_quota(tmp_path, sleeps):
    """Found in the web app: a 429 about tokens per MINUTE (Limit 7000) must not pass for the daily quota."""
    minute = "RateLimitError: 429 on tokens per minute (TPM): Limit 7000, Used 5775, Requested 2147. Please try again in 3s."
    ledger = UsageLedger(tmp_path / "u.jsonl")
    llm = ChatLLM(ScriptedChat([Exception(minute), ok(2)]), "groq:qwen/qwen3.8-27b", max_retries=2, backoff_base_s=0.01, rules=RULES, ledger=ledger)
    assert run(llm.structured(Mini, "s", "u")).x == 2
    assert ledger.remaining(RULES, "groq", "qwen/qwen3.8-27b") == RULES["agent"]["groq_daily_token_limits"]["qwen/qwen3.8-27b"] - 7
    assert not [r for r in ledger._read() if r["type"] == "quota_report"]


# ----------------------------------------------------------------------------- unusable output falls back for that call only
def test_unusable_output_tries_the_next_model_for_that_call_without_demoting_the_primary(sleeps):
    from agent.llm import LLMFormatError
    llm = chain([bad(), bad(), bad()], [ok(5)])                 # model0: three invalid replies (max_retries=2 -> 3 attempts); model1 is fine
    assert run(llm.structured(Mini, "s", "u")).x == 5
    assert llm.index == 0                                         # not sticky: the next call goes to the primary again
    llm.chain[0].chat.outcomes = [ok(9)]
    assert run(llm.structured(Mini, "s", "u")).x == 9


def test_when_no_model_produces_usable_output_the_error_says_so(sleeps):
    from agent.llm import LLMFormatError
    llm = chain([bad()] * 3, [bad()] * 3)
    with pytest.raises(LLMFormatError, match="No configured model produced usable output"):
        run(llm.structured(Mini, "s", "u"))
