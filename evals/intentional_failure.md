# Intentional-failure case: the request "hello" (found Day 4, fixed Day 5)

This is the one eval case that is **expected to fail on the earlier code**, kept with its remediation, as the challenge asks. Both runs below are real; nothing is simulated.

## The failure (11 Oct 2026, Day 4 code, `ollama:qwen2.5:7b`)

Command: `python -m agent.run hello`

| What happened | Number |
|---|---|
| Buyer, supplier or period in the request | none |
| Scope the model extracted anyway | `years: [2022, 2023, 2024]` (invented; the request says nothing about years) |
| Tool calls | 53 (12 analysis calls, **40 `flag_finding` writes**, 1 `list_flags`) |
| Flags raised for review | **40**, from ranking tools scanning every buyer in Kenya (for example a flag on the Kenya Airport Authority for a request that named nobody) |
| Cost of the mistake | 85 s of work, and a review queue nobody can reasonably read |

Why it matters for the challenge: a procurement officer who types a vague request gets a wall of flags about institutions they never asked about. That is noise at best and an unfair signal at worst; a screening tool must refuse to guess a scope.

Root causes, both in the agent, neither in the tools:
1. The Planner is allowed to return a scope the request does not contain. A 7B model filled the empty field with the example years from its own prompt.
2. Nothing checked that the request *had* a scope before running tools, and nothing capped how many flags one run may raise.

## The remediation (12 Oct 2026, Day 5)

| Fix | Where | Test |
|---|---|---|
| **Ground the scope in the request:** a year survives only if the request states it (a range keeps its middle years); a buyer or supplier only if at least half of its significant words appear in the request | `agent/policy.py::ground_scope`, called by `planner` (logs `scope_ungrounded`) | `test_a_scope_the_request_does_not_support_is_removed`, `test_years_and_names_that_the_request_states_are_kept`, `test_the_planner_removes_an_invented_scope_and_asks_instead_of_running` |
| **Scope guard:** with no buyer, supplier or period the run ends after the planner call with a question for the user; **no tool is called** | `agent/nodes.py::planner`, `route_after_planner`, rules `agent.scope_request_message` | `test_a_request_with_no_scope_asks_for_it_and_runs_no_tool` |
| The web form passes the typed buyer, supplier and years as an explicit hint that wins over the model | `agent/policy.py::merge_scope`, `app/engine.py::build_query` | `test_the_callers_scope_wins_and_blanks_are_ignored`, `test_the_same_vague_plan_runs_when_the_caller_supplies_the_scope` |
| **Flag cap:** at most `agent.max_flags_per_run` (10) flags per run, strongest first (severity, then evidence count); the rest are named in the notes and the memo | `agent/policy.py::cap_findings`, `synthesiser` | `test_the_cap_keeps_the_strongest_findings_first_and_ties_in_order`, `test_at_most_the_configured_number_of_flags_is_raised_and_the_memo_says_so` |

## The same request after the fix (12 Oct 2026, same model)

Command: `python -m agent.run hello`

```
Request: hello
(scope_ungrounded: removed ['years']; model_scope years [2022, 2023, 2024])
SCOPE NEEDED: This request names no buyer, supplier or period, so every tool would scan all of Kenya. Add a buyer (for example a county or
agency), a supplier or years, and run it again.
(no tool was run; 1264 tokens used for planning)
```

Result: 0 tool calls, 0 flags, 1 LLM call (1,264 tokens), and a message that tells the user what to add.

## What this case does not cover
* A *vague but scoped* request (for example only "2023") still scans every buyer in that year; the flag cap bounds the review queue, the progress log shows what ran, and the memo records the cap. A period-only scope is accepted on purpose (an oversight body may legitimately review a year), so this is a documented limitation rather than a fixed one.
* The grounding rule is lexical: a buyer named by a nickname the request does not spell out ("the roads authority" for "Kenya Rural Roads Authority") is dropped and the user is asked to name it. Asking is the safe side of that error.
