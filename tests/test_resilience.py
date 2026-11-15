"""Refine 5: demo resilience. A Synthesiser call that keeps returning unusable output gives up after 2 malformed attempts and the run
continues with the tools' own wording; the give-up is NOT passed to the fallback model, so retries never spend its quota. Mocked only:
no live model is called."""
import asyncio

import pytest

from agent.llm import ChatLLM, FallbackLLM, LLMGaveUp, LLMFormatError, LLMQuotaError
from agent.state import FindingDraft, Plan, PlanStep, Scope, SynthOutput
from tests.fakes import FakeLLM
from tests.test_agent import BUYER, events, isolated_flags_db, make_plan, run_scenario  # noqa: F401  (fixture re-exported)
from tests.test_progressive import FakeChat, Usage

BAD = {"parsed": None, "parsing_error": "malformed tool call", "raw": Usage()}
GOOD_SYNTH = SynthOutput(summary="ok", findings=[])


class CountingChat(FakeChat):
    """Always returns the given reply and counts how often it is asked."""

    def __init__(self, reply):
        super().__init__([])
        self.reply, self.asked = reply, 0
        outer = self

        class Runnable:
            async def ainvoke(self, messages):
                outer.asked += 1
                if isinstance(outer.reply, Exception):
                    raise outer.reply
                return outer.reply
        self.runnable = Runnable()


def llm_for(chat, name="groq:primary", **kw):
    return ChatLLM(chat, name, max_retries=4, backoff_base_s=0, **kw)


def run(coro):
    return asyncio.run(coro)


def test_a_synth_call_gives_up_after_two_malformed_attempts_not_five():
    chat = CountingChat(BAD)
    llm = llm_for(chat, malformed_limits={"synth": 2})
    with pytest.raises(LLMGaveUp, match="gave up after 2 malformed"):
        run(llm.structured(SynthOutput, "s", "u", kind="synth"))
    assert chat.asked == 2 and llm.malformed == 2 and llm.attempts == 2


def test_a_provider_rejection_of_the_tool_call_counts_as_malformed_too():
    chat = CountingChat(ValueError("tool call validation failed"))
    llm = llm_for(chat, malformed_limits={"synth": 2})
    with pytest.raises(LLMGaveUp):
        run(llm.structured(SynthOutput, "s", "u", kind="synth"))
    assert chat.asked == 2


def test_a_good_second_attempt_still_succeeds_within_the_limit():
    chat = FakeChat([BAD, {"parsed": GOOD_SYNTH, "parsing_error": None, "raw": Usage()}])
    llm = llm_for(chat, malformed_limits={"synth": 2})
    assert run(llm.structured(SynthOutput, "s", "u", kind="synth")) is GOOD_SYNTH and llm.malformed == 1


def test_other_call_kinds_keep_the_full_retry_allowance():
    chat = CountingChat(BAD)
    llm = llm_for(chat, malformed_limits={"synth": 2})
    with pytest.raises(LLMFormatError) as info:
        run(llm.structured(Plan, "s", "u", kind="plan"))
    assert not isinstance(info.value, LLMGaveUp) and chat.asked == 5               # 1 + llm_max_retries (4)


def test_rate_limit_waits_are_not_malformed_attempts():
    chat = CountingChat(RuntimeError("429 rate_limit_exceeded: try again in 0.1s"))
    llm = llm_for(chat, malformed_limits={"synth": 2})
    with pytest.raises(LLMQuotaError):
        run(llm.structured(SynthOutput, "s", "u", kind="synth"))
    assert llm.malformed == 0 and chat.asked == 5                                    # retried as a rate limit, never "gave up"


def test_the_give_up_is_not_handed_to_the_fallback_model_so_its_quota_is_not_spent():
    primary, backup = CountingChat(BAD), CountingChat({"parsed": GOOD_SYNTH, "parsing_error": None, "raw": Usage()})
    chain = FallbackLLM([llm_for(primary, "groq:primary", malformed_limits={"synth": 2}),
                         llm_for(backup, "groq:backup", malformed_limits={"synth": 2})])
    with pytest.raises(LLMGaveUp):
        run(chain.structured(SynthOutput, "s", "u", kind="synth"))
    assert primary.asked == 2 and backup.asked == 0                                  # the second model was never asked
    assert chain.malformed == 2 and chain.index == 0                                 # and the primary is not demoted for the rest of the run


def test_other_kinds_still_use_the_fallback_model_after_a_format_failure():
    primary, backup = CountingChat(BAD), CountingChat({"parsed": GOOD_SYNTH, "parsing_error": None, "raw": Usage()})
    chain = FallbackLLM([llm_for(primary, "groq:primary", malformed_limits={"synth": 2}),
                         llm_for(backup, "groq:backup", malformed_limits={"synth": 2})])
    assert run(chain.structured(SynthOutput, "s", "u", kind="fix")) is GOOD_SYNTH   # a non-synth kind: the chain behaves as before
    assert backup.asked == 1


def test_the_gave_up_event_is_logged_with_the_limit():
    logged = []

    class Audit:
        def event(self, type_, **data):
            logged.append((type_, data))
    llm = llm_for(CountingChat(BAD), malformed_limits={"synth": 2})
    llm.attach(Audit())
    with pytest.raises(LLMGaveUp):
        run(llm.structured(SynthOutput, "s", "u", kind="synth"))
    gave_up = [d for t, d in logged if t == "llm_gave_up"]
    assert gave_up == [{"kind": "synth", "model": "groq:primary", "malformed_attempts": 2, "limit": 2}]
    assert [t for t, _ in logged].count("llm_malformed") == 2


def test_the_shipped_rule_limits_synth_to_two():
    from mcp_server.rules import load_rules
    assert load_rules()["agent"]["llm_malformed_limit_by_kind"] == {"synth": 2}


def test_when_the_synthesiser_gives_up_the_run_continues_with_the_tools_own_wording(tmp_path, isolated_flags_db):
    """End to end through the real MCP server: flags are still raised, with the tools' text, and the run says why."""
    def gives_up(system, user):
        raise LLMGaveUp("groq:primary: synth call gave up after 2 malformed attempt(s) (limit 2): malformed tool call")
    llm = FakeLLM({Plan: make_plan(("detect_splitting", {"buyer": BUYER, "limit": 3})), SynthOutput: gives_up})
    state, _ = run_scenario(llm, tmp_path)
    assert state["flags_created"] and all(f["status"] == "pending_review" for f in state["flags_created"])
    assert all(f["source"] == "candidate_default" for f in state["findings"])        # every explanation is the tool's own wording
    assert events(tmp_path, "synth_fallback") and "gave up" in events(tmp_path, "synth_fallback")[0]["reason"]
    assert "screening indicator" in state["summary"]                                 # the deterministic summary, not the model's
    assert len(llm.prompts["SynthOutput"]) == 1                                       # the failed synth call was made once and not repeated by the agent
