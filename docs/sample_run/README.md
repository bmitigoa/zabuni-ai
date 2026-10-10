# Sample runs (real)

Two real runs of the agent on the same request, `python -m agent.run "Investigate Makueni County Government 2022-2024"`, with the
live open-weights model `groq:qwen/qwen3.8-27b`, on 10 Oct 2026 (UTC). Nothing here is scripted or edited: the files are the
agent's own audit-log lines for each run (filtered by `run_id`), the draft summary it saved through the Filesystem MCP server,
and the flags it stored with `flag_finding` (exported from the flags database).

| | Run A: `run_20261010_204919_4a7e` | Run B: `run_20261010_220028_7932` |
|---|---|---|
| Code | earlier version: arguments as a JSON string but **without** the worked example in the planner prompt | the final Day 3 code |
| Plan | 4 steps; the model left the arguments empty, so **scope injection** added the buyer and years to all four (4 `scope_injected` events) | 5 steps; the model carried the buyer and years itself (no injection) |
| Follow-ups | 3 price checks after the search; none flagged | 3 price checks after the search; **all three flagged** (7.0x, 6.6x, 8.7x), each triggering a splitting check for that supplier |
| Synthesiser | **the LLM ranked and worded the findings** (see `summary.md`) | the provider's daily token quota ran out: `synth_fallback` event, the tools' own wording was used (graceful degradation) |
| Flags stored | 4 (splitting), all `pending_review` | 7 (3 price outliers + 4 splitting), all `pending_review` |
| Console output | not saved to a file for this run | `console.txt` |

Each folder holds `tool_calls.jsonl` (every MCP tool call: run id, timestamp, tool, inputs, output summary, output, duration, error),
`agent_events.jsonl` (the agent's decisions: plan, follow-ups, scope injection, synthesiser fallback), `summary.md` (the draft
summary, labelled pending human review), and `flags.json` (the stored flags with their cited OCDS records).

How to read them honestly:
- **Flags are screening indicators, not findings.** The four splitting clusters are three awards each to one supplier just below a
  threshold within weeks; without item-level data they may be different works. The three price outliers are "staff medical
  insurance cover" awards compared with other awards whose titles share the words "medical" and "staff"; a large insurance premium
  can be entirely legitimate. A human decides (the approval gate is built on Day 4).
- **A real LLM varies.** The same request produced different plans in the two runs; the splitting flags are identical because they come
  from a deterministic tool.
- **There was a third, failed first run** (before the fixes: empty arguments, 30 flags about unrelated buyers). It is described in the
  Day 3 notes and in `model_check.md`, and is deliberately not presented as a sample.
- The model-selection evidence is in [`model_check.md`](model_check.md).
- Company and buyer names are from the public PPRA data; no personal data is loaded anywhere in the pipeline.

Reproduce: see the README (Usage). A run costs roughly 6-8k tokens; the free tier's daily quota for this model is 200,000 tokens.
