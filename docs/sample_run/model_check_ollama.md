# Model check: planning reliability on ollama (real run, 11 Oct 2026)

Planner prompt + the real tool catalogue (discovered over MCP), 2 requests x 2 runs per model, a **single attempt** per call (no retries). Each cell is passes / runs. Produced by `python -m evals.model_check`.

| model | ok | tool_names_valid | args_schema_valid | args_carry_scope | scope_buyer_ok | scope_years_ok | no_invented_ids | covers_requested | median s | tokens/run |
|---|---|---|---|---|---|---|---|---|---|---|
| `qwen2.5:7b` | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 | 92.3 | 1292 |

## Notes

- Model: `qwen2.5:7b` on Ollama (local, this laptop, CPU only). 2 requests x 2 runs = 4 plans: evidence that the local model follows the plan schema, not a benchmark.
- The median of about 92 s per plan is the cost of running a 7B model without a GPU; a hosted Groq call takes about 1.5 s (see `model_check.md`).
- The prompt and tool catalogue are the Day 4 compact versions (about 1,300 tokens per plan call, against about 3,100 before).
