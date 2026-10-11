# Sample run (Day 4): plan, tools, flags, human decisions, memo

One real end-to-end run of the final Day 4 code, in [`final_ollama_run_20261011_160025_2ff7/`](final_ollama_run_20261011_160025_2ff7/).
It replaces the two mixed Day 3 runs (their lessons are in the git history and in `model_check.md`).

**Model: `ollama:qwen2.5:7b` (open-weights, local, cost 0).** Groq's free-tier quota was kept for the hosted demo and the demo video.
**The reviewer decisions are demo inputs**: they were typed by the developer's coding assistant under the role `demo_reviewer`, not by a
procurement professional. Their reasons describe what the cited records show; they are not audit findings.

Request: `Review Makueni County Government awards 2022-2023 for splitting and price anomalies` (`--max-steps 8`).

| File | What it is |
|---|---|
| `console_run.txt` | `python -m agent.run ...`: the plan, every tool call, follow-ups, the 8 flags, usage, and the pause |
| `console_review.txt` | `python -m agent.review <run_id> --role demo_reviewer`: flags with evidence, the decisions, the clarify round, the memo |
| `memo.md` | the evaluation memo: **approved flags only** (4 of 8 considered), reasons, limitations, the no-award statement |
| `draft_summary.md` | the pre-review draft summary (every flag still `pending_review`) |
| `tool_calls.jsonl`, `agent_events.jsonl`, `llm_usage.jsonl` | the audit log: every MCP call, every agent decision (plan, follow-ups, `decision`, `clarification`), every LLM call with tokens |

What happened, in order:

1. **Plan** (1 LLM call, 1,269 tokens; the Day 3 planner call was about 3,087): 3 steps built from the six tools discovered with
   `list_tools`. The newly added `detect_price_anomalies` was used with no agent change.
2. **Investigation**: 14 more calls, including follow-ups added by the policy rules (price checks of the awards found, then splitting and
   concentration checks after a price outlier). 16 tool calls in total.
3. **Synthesis** (1 LLM call) and the **pause**: 8 flags stored as `pending_review`; nothing in a memo.
4. **Review**: 1 flag sent back with a question (**clarify**); the agent planned tool calls, answered, and the run paused again; then it was approved.
   4 approved (one of them after the clarify round), 4 rejected (two-award clusters). A reason was required for each.
5. **Memo**: drafted from the 4 approved flags only and saved to `outputs/` through the Filesystem MCP server.

Usage (from the log): run phase 3,787 tokens in about 390 s; review phase 1,437 tokens in about 120 s (5,224 in all, against roughly 6-8 thousand for a Day 3 run); cost 0 (local).

**Honest notes on quality (7B local model).**
* The model's summary says "possible irregularities ... further investigation is needed": looser than the tools' own text. That is why every flag
  keeps the tool's wording and cited records, and why a human reads the evidence, not the summary.
* The clarification answer is thin: the 7B model had only the tool results to go on. The reviewer saw it and decided anyway; the system does
  not pretend the answer is more than it is.
* The "steel drums" price flag compares one award with ten "comparables" that include unrelated items (the data has no item-level detail), and the
  "staff medical cover" price anomaly (71x the cohort median) may be a data-entry error. Both are prompts for a human to check, as the memo says.
* Latency on a laptop CPU is minutes per LLM call; a Groq run takes seconds.

`model_check.md` is the Day 3 record of how the Groq model was chosen. A local-model reliability check can be re-run with
`python -m evals.model_check --provider ollama` (results go to `model_check_ollama.md`).
