"""Refine 3: context budget and progressive disclosure.

The Planner first sees a one-line-per-tool INDEX and names the tools it needs; only those tools' full argument lists are put in the
planning prompt. Unusable choices fall back to the full catalogue. Malformed model output is counted per model attempt.
"""
import asyncio
import re

import pytest

from agent.llm import ChatLLM, LLMError
from agent.state import Plan, PlanStep, Scope, ToolChoice
from agent.state import SynthOutput
from tests.test_agent import BUYER, events, isolated_flags_db, make_plan, run_scenario, synth_from_candidates  # noqa: F401  (fixture re-exported)


def FakeLLM(script):  # noqa: N802 - a FakeLLM that can also word the flags the tools propose
    from tests.fakes import FakeLLM as Base
    return Base({SynthOutput: lambda system, user: synth_from_candidates(user), **script})

SC = Scope(buyer=BUYER, years=[2022, 2023])
PLAN = make_plan(("search_awards", {"buyer": BUYER, "limit": 1}), ("detect_splitting", {"buyer": BUYER, "limit": 1}))


def lines(prompt):
    return re.findall(r"^- (\w+):", prompt, re.M)


def test_the_first_step_shows_an_index_without_arguments_and_the_second_only_the_chosen_tools(tmp_path):
    llm = FakeLLM({ToolChoice: ToolChoice(tools=["search_awards", "detect_splitting"], scope=SC), Plan: PLAN})
    state, ctx = run_scenario(llm, tmp_path, rules_edit=lambda r: r["agent"].update(followups=[]))
    index = llm.prompts["ToolChoice"][0][1]
    assert set(lines(index)) == {t.name for t in ctx.plannable} and "args:" not in index          # every tool, no schemas
    plan_prompt = llm.prompts["Plan"][0][1]
    assert lines(plan_prompt) == ["search_awards", "detect_splitting"] and plan_prompt.count("args:") == 2
    assert "supplier_concentration" not in plan_prompt                                                # not chosen: not disclosed
    assert [r["tool"] for r in state["results"]] == ["search_awards", "detect_splitting"]
    chosen = events(tmp_path, "tools_chosen")[0]
    assert chosen["chosen"] == ["search_awards", "detect_splitting"] and chosen["offered"] == len(ctx.plannable)


def test_the_two_step_prompts_are_smaller_than_the_single_full_catalogue_prompt(tmp_path):
    full = FakeLLM({Plan: PLAN})
    run_scenario(full, tmp_path / "full", rules_edit=lambda r: r["agent"].update(progressive_disclosure=False, followups=[]))
    prog = FakeLLM({ToolChoice: ToolChoice(tools=["search_awards", "detect_splitting"], scope=SC), Plan: PLAN})
    run_scenario(prog, tmp_path / "prog", rules_edit=lambda r: r["agent"].update(followups=[]))
    assert "ToolChoice" not in full.prompts and len(full.prompts["Plan"]) == 1                       # off = the v1.0 behaviour
    size = lambda llm, kinds: sum(len(s) + len(u) for k in kinds for s, u in llm.prompts[k])           # noqa: E731
    assert len(prog.prompts["Plan"][0][1]) < len(full.prompts["Plan"][0][1]) * 0.7                    # the planning prompt itself shrinks a lot
    assert size(prog, ["ToolChoice"]) < size(prog, ["Plan"])                                          # and the index step is cheap


def test_a_tool_that_was_not_chosen_cannot_be_planned(tmp_path):
    bad = make_plan(("supplier_concentration", {"buyer": BUYER}))
    llm = FakeLLM({ToolChoice: ToolChoice(tools=["search_awards"], scope=SC), Plan: [bad, make_plan(("search_awards", {"buyer": BUYER, "limit": 1}))]})
    state, _ = run_scenario(llm, tmp_path, rules_edit=lambda r: r["agent"].update(followups=[]))
    assert events(tmp_path, "plan_rejected_steps")[0]["tools"] == ["supplier_concentration"]
    assert "Valid tool names: ['search_awards']" in llm.prompts["Plan"][1][1]
    assert [r["tool"] for r in state["results"]] == ["search_awards"]


@pytest.mark.parametrize("choice", [ToolChoice(tools=[], scope=SC), ToolChoice(tools=["no_such_tool", "also_fake"], scope=SC)])
def test_an_empty_or_invented_choice_falls_back_to_the_full_catalogue(tmp_path, choice):
    llm = FakeLLM({ToolChoice: choice, Plan: PLAN})
    state, ctx = run_scenario(llm, tmp_path, rules_edit=lambda r: r["agent"].update(followups=[]))
    assert set(lines(llm.prompts["Plan"][0][1])) == {t.name for t in ctx.plannable}
    assert events(tmp_path, "tools_choice_fallback") and len(state["results"]) == 2


def test_a_failing_choice_step_falls_back_instead_of_failing_the_run(tmp_path):
    def boom(system, user):
        raise LLMError("the model could not choose")
    llm = FakeLLM({ToolChoice: boom, Plan: PLAN})
    state, ctx = run_scenario(llm, tmp_path, rules_edit=lambda r: r["agent"].update(followups=[]))
    assert set(lines(llm.prompts["Plan"][0][1])) == {t.name for t in ctx.plannable} and len(state["results"]) == 2
    assert "could not choose" in events(tmp_path, "tools_choice_fallback")[0]["reason"]


def test_the_number_of_chosen_tools_is_capped_by_rules(tmp_path):
    first = lambda system, user: make_plan((lines(user)[0], {"buyer": BUYER}))                          # noqa: E731 - plan with a tool that was disclosed
    llm = FakeLLM({ToolChoice: lambda system, user: ToolChoice(tools=lines(user), scope=SC), Plan: first})                                                                       # default fake choice = every tool
    run_scenario(llm, tmp_path, rules_edit=lambda r: r["agent"].update(max_chosen_tools=2, followups=[]))
    assert len(lines(llm.prompts["Plan"][0][1])) == 2


# ============================================================================= malformed-output accounting
class FakeRunnable:
    def __init__(self, replies):
        self.replies = list(replies)

    async def ainvoke(self, messages):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeChat:
    def __init__(self, replies):
        self.runnable = FakeRunnable(replies)

    def with_structured_output(self, schema, include_raw=True):
        return self.runnable


class Usage:
    usage_metadata = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}


def test_malformed_attempts_are_counted_and_logged(tmp_path):
    good = Plan(objective="x", scope=Scope(), steps=[PlanStep(tool="t", args={}, reason="r")])
    chat = FakeChat([{"parsed": None, "parsing_error": "bad tool call", "raw": Usage()},
                     ValueError("API rejected the tool call"),
                     {"parsed": good, "parsing_error": None, "raw": Usage()}])
    llm = ChatLLM(chat, "ollama:fake", max_retries=3, backoff_base_s=0)
    logged = []

    class Audit:
        def event(self, type_, **data):
            logged.append((type_, data))
    llm.attach(Audit())
    assert asyncio.run(llm.structured(Plan, "s", "u", kind="plan")) is good
    assert (llm.attempts, llm.malformed) == (3, 2)                                                    # 2 of 3 attempts were unusable
    assert [t for t, _ in logged].count("llm_malformed") == 2 and [t for t, _ in logged].count("llm_call") == 2


def test_a_request_with_no_scope_is_stopped_after_the_first_model_call(tmp_path):
    """The tool-choice step also reports the scope, so the scope guard still costs ONE model call (eval case A2)."""
    llm = FakeLLM({ToolChoice: ToolChoice(tools=["search_awards"]), Plan: PLAN})
    state, _ = run_scenario(llm, tmp_path)
    assert state["scope_request"] and state["results"] == [] and llm.calls == 1 and "Plan" not in llm.prompts
