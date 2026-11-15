"""Refine 4: OpenTelemetry spans (run -> LLM call -> tool call), exported to a local file, with the JSONL audit log unchanged."""
import asyncio
import json

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent import telemetry, traces
from agent.audit import AuditLog
from agent.llm import ChatLLM
from agent.mcp_client import McpBackend
from agent.session import open_session
from agent.settings import Settings
from agent.state import Plan, PlanStep, Scope
from agent.telemetry import NullTracer, RunTracer
from mcp_server.rules import load_rules
from tests.test_progressive import FakeChat, Usage

CFG = load_rules()["telemetry"]


def make(tmp_path, primary="groq:qwen/qwen3.8-27b", cfg=None):
    mem = InMemorySpanExporter()
    tracer = RunTracer.create(tmp_path, cfg or CFG, primary_model=primary, extra_exporters=[mem])
    audit = AuditLog("run_t", tmp_path, 5000)
    audit.tracer = tracer
    tracer.start_run("run_t", "run", model=primary)
    return tracer, audit, mem


def by_name(mem, prefix):
    return [s for s in mem.get_finished_spans() if s.name.startswith(prefix)]


def test_a_tool_call_becomes_a_child_span_with_its_duration_server_tool_and_evidence_count(tmp_path):
    tracer, audit, mem = make(tmp_path)
    audit.call(server="zabuni", tool="detect_splitting", inputs={"buyer": "B"}, output={"result": {"flags": [1]}, "evidence": [1, 2, 3]},
               duration_s=1.5, step={"reason": "check splitting", "origin": "plan"})
    tracer.end_run()
    tool, run = by_name(mem, "tool.call")[0], by_name(mem, "run")[0]
    a = tool.attributes
    assert tool.parent.span_id == run.context.span_id and tool.context.trace_id == run.context.trace_id
    assert a["mcp.tool.name"] == "detect_splitting" and a["mcp.server"] == "zabuni" and a["zabuni.tool.evidence_count"] == 3
    assert a["zabuni.step.reason"] == "check splitting"
    assert 1.45e9 < tool.end_time - tool.start_time < 1.55e9                    # duration = the logged 1.5 s
    assert run.attributes["zabuni.tool_calls"] == 1


def test_an_llm_call_carries_tokens_cost_provider_model_kind_and_fallback_flag(tmp_path):
    tracer, audit, mem = make(tmp_path)
    audit.event("llm_call", kind="plan", model="groq:qwen/qwen3.8-27b", prompt_tokens=1000, completion_tokens=200, total_tokens=1200,
                cost_usd=0.0016, seconds=2.0, est_remaining_daily_tokens=150000)
    audit.event("llm_fallback", from_model="groq:qwen/qwen3.8-27b", to_model="groq:openai/gpt-oss-120b", reason="malformed", cause="unusable output", sticky=False)
    audit.event("llm_call", kind="synth", model="groq:openai/gpt-oss-120b", prompt_tokens=500, completion_tokens=100, total_tokens=600,
                cost_usd=0.0001, seconds=1.0)
    audit.event("llm_malformed", kind="synth", model="groq:qwen/qwen3.8-27b", reason="bad tool call")
    tracer.end_run()
    first, second = by_name(mem, "llm.call plan")[0], by_name(mem, "llm.call synth")[0]
    a = first.attributes
    assert a["gen_ai.system"] == "groq" and a["gen_ai.request.model"] == "qwen/qwen3.8-27b" and a["gen_ai.usage.input_tokens"] == 1000
    assert a["gen_ai.usage.output_tokens"] == 200 and a["zabuni.cost_usd"] == 0.0016 and a["zabuni.llm.kind"] == "plan"
    assert a["zabuni.llm.fallback"] is False and second.attributes["zabuni.llm.fallback"] is True      # a different model than the primary
    assert a["zabuni.est_remaining_daily_tokens"] == 150000
    run = by_name(mem, "run")[0]
    assert run.attributes["zabuni.llm_calls"] == 2 and run.attributes["gen_ai.usage.input_tokens"] == 1500
    assert run.attributes["zabuni.fallbacks"] == 1 and run.attributes["zabuni.malformed_attempts"] == 1
    assert abs(run.attributes["zabuni.cost_usd"] - 0.0017) < 1e-9
    assert {e.name for e in run.events} == {"llm_fallback", "llm_malformed"}
    fb = next(e for e in run.events if e.name == "llm_fallback")
    assert fb.attributes["to_model"] == "groq:openai/gpt-oss-120b" and fb.attributes["cause"] == "unusable output"


def test_a_failed_tool_call_marks_its_span_as_an_error(tmp_path):
    tracer, audit, mem = make(tmp_path)
    audit.call(server="zabuni", tool="x", inputs={}, output=None, duration_s=0.1, error="tool call failed: boom")
    tracer.end_run()
    span = by_name(mem, "tool.call")[0]
    assert span.status.status_code.name == "ERROR" and "boom" in span.status.description
    assert by_name(mem, "run")[0].attributes["zabuni.tool_errors"] == 1


def test_the_jsonl_audit_log_is_unchanged_by_tracing(tmp_path):
    tracer, audit, _ = make(tmp_path)
    audit.call(server="zabuni", tool="search_awards", inputs={"buyer": "B"}, output={"result": {}, "evidence": []}, duration_s=0.2)
    audit.event("plan", objective="o")
    tracer.end_run()
    rec = json.loads((tmp_path / "tool_calls.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["tool"] == "search_awards" and rec["inputs"] == {"buyer": "B"} and rec["duration_ms"] == 200.0
    assert json.loads((tmp_path / "agent_events.jsonl").read_text(encoding="utf-8").splitlines()[0])["type"] == "plan"


def test_spans_are_written_to_the_trace_file_as_otel_json_and_the_viewer_prints_a_tree(tmp_path, capsys):
    tracer, audit, _ = make(tmp_path)
    audit.event("llm_call", kind="plan", model="groq:qwen/qwen3.8-27b", prompt_tokens=10, completion_tokens=5, total_tokens=15, cost_usd=0.0, seconds=0.5)
    audit.call(server="zabuni", tool="search_awards", inputs={}, output={"result": {}, "evidence": [1]}, duration_s=0.3)
    tracer.end_run()
    path = tmp_path / "traces.jsonl"
    spans = traces.load(path)
    assert {s["name"] for s in spans} == {"run", "llm.call plan", "tool.call search_awards"}
    assert all(s["context"]["trace_id"] == spans[0]["context"]["trace_id"] for s in spans)
    lines = traces.render(next(iter(traces.group(spans).values())))
    assert lines[0].startswith("run  [run run_t]") and any("llm.call plan" in l and "10 in / 5 out" in l for l in lines)
    assert any("tool.call search_awards" in l and "1 evidence" in l for l in lines) and lines[-1].startswith("  totals: 1 LLM calls, 1 tool calls")


def test_tracing_off_or_unavailable_is_a_silent_no_op(tmp_path, monkeypatch):
    off = RunTracer.create(tmp_path, {**CFG, "enabled": False})
    assert isinstance(off, NullTracer)
    monkeypatch.setattr(telemetry, "AVAILABLE", False)
    assert isinstance(RunTracer.create(tmp_path, CFG), NullTracer)
    audit = AuditLog("r", tmp_path, 100)
    audit.tracer = off
    audit.call(server="s", tool="t", inputs={}, output=None, duration_s=0.0)             # does not raise
    off.start_run("r", "run"); off.annotate(a=1); off.end_run()
    assert not (tmp_path / "traces.jsonl").exists()


def test_the_console_exporter_is_added_only_when_asked(tmp_path, capsys):
    tracer = RunTracer.create(tmp_path, {**CFG, "console": True})
    tracer.start_run("r", "run")
    tracer.end_run()
    assert '"name": "run"' in capsys.readouterr().err                                    # one span per line on stderr, stdout stays clean


def test_a_real_chat_llm_call_and_a_real_mcp_tool_call_are_traced(tmp_path):
    good = Plan(objective="x", scope=Scope(), steps=[PlanStep(tool="t", args={}, reason="r")])
    llm = ChatLLM(FakeChat([{"parsed": None, "parsing_error": "bad", "raw": Usage()}, {"parsed": good, "parsing_error": None, "raw": Usage()}]),
                  "ollama:fake", 2, 0)
    tracer, audit, mem = make(tmp_path, primary="ollama:fake")
    llm.attach(audit)

    async def go():
        await llm.structured(Plan, "s", "u", kind="plan")
        settings = Settings(provider="groq", model="fake", logs_dir=tmp_path, outputs_dir=tmp_path / "out")
        async with McpBackend(settings, use_filesystem=False, audit=audit) as backend:
            await backend.call("zabuni", "search_awards", {"buyer": "Makueni County Government", "limit": 1})
    asyncio.run(go())
    tracer.end_run()
    assert len(by_name(mem, "llm.call plan")) == 2 and by_name(mem, "run")[0].attributes["zabuni.malformed_attempts"] == 1       # both attempts are spans
    tool = by_name(mem, "tool.call search_awards")[0]
    assert tool.attributes["zabuni.tool.evidence_count"] == 1 and tool.attributes["mcp.server"] == "zabuni"


def test_open_session_starts_a_run_span_and_closes_it_with_totals(tmp_path, monkeypatch):
    monkeypatch.setenv("ZABUNI_FLAGS_DB", str(tmp_path / "flags.duckdb"))
    monkeypatch.setenv("ZABUNI_CHECKPOINT_DB", str(tmp_path / "cp.sqlite"))
    settings = Settings(provider="ollama", model="qwen2.5:7b", logs_dir=tmp_path / "logs", outputs_dir=tmp_path / "out")

    async def go():
        async with open_session(settings, "run_sess", use_filesystem=False) as s:
            assert s.audit.tracer.enabled
            await s.backend.call("zabuni", "search_awards", {"buyer": "Makueni County Government", "limit": 1})
    asyncio.run(go())
    spans = traces.load(tmp_path / "logs" / "traces.jsonl")
    run = next(s for s in spans if s["name"] == "run")
    assert run["attributes"]["zabuni.run_id"] == "run_sess" and run["attributes"]["zabuni.phase"] == "run" and run["attributes"]["zabuni.tool_calls"] == 1
    assert any(s["name"] == "tool.call search_awards" and s["parent_id"] == run["context"]["span_id"] for s in spans)
