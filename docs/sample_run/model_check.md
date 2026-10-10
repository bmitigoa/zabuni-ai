# Model check: which open-weights model plans most reliably for this agent? (real runs, Groq)

Produced with `python -m evals.model_check`: the real Planner prompt against the real tool catalogue (discovered over MCP),
**one attempt per call, no retries**. Every number below is something the models actually returned on 10 Oct 2026. The
harness overwrites its output file on each run, so this page is the hand-assembled record of the runs, kept honest about
what changed between them. The sample is small (2 requests per model): evidence for choosing a default, not a benchmark.

## What this key can use

`python -m agent.models` lists what the API key behind this project can call: `qwen/qwen3.8-27b` (Alibaba, open-weights),
`openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `openai/gpt-oss-safeguard-20b` (open-weights, made by OpenAI), two Llama
prompt-guard classifiers, Whisper, Orpheus and `allam-2-7b`. **No Llama chat model is enabled for this key.** Groq's
documentation lists `llama-3.3-70b-versatile` with tool use, but the API answers HTTP 404 ("does not exist or you do not have
access"). Llama therefore could not be compared. The `MODEL` value that was in `.env` (`llama-3.3-70b-versatile`) was
unusable for this reason.

## Run 1: first schema (tool arguments as an open JSON object), 3 models, 2 requests x 4 runs

Transcribed from the console output of that run (the raw JSON log was overwritten by later runs). Checks in this run: valid
structure, valid tool names, schema-valid arguments, buyer and years extracted into `scope`, no invented ids, requested checks present.

| model | passes / 8 | median s | main failure |
|---|---|---|---|
| `qwen/qwen3.8-27b` | **8/8** | 1.4 | (after fixing an account limit, see below) |
| `openai/gpt-oss-120b` | 6/8 | 2.3 | HTTP 400 `Failed to parse tool call arguments as JSON` |
| `openai/gpt-oss-20b` | 0/8 | 1.5 | `Unknown tool type: 'functions.Plan'` (its tool-name format is rejected by the LangChain tool-call path) |

A first attempt at the Qwen row scored 4/8 because every failing call was HTTP 429 `Request too large ... output tokens per
minute (OTPM): Limit 1000, Requested 2048` on Groq's free tier: an account limit, not model quality. It was fixed by capping
`max_tokens` (rules `agent.llm_max_tokens`), after which Qwen passed 8/8.

## The check was too lenient (found on the first real agent run)

The first end-to-end run flagged 30 buyers that had nothing to do with the request: Qwen had extracted the scope correctly but
wrote **empty arguments** for every step, so each tool scanned all of Kenya. Empty arguments are schema-valid, so Run 1's checks
could not see it. A stricter check, `args_carry_scope` (every step whose tool accepts a buyer / years / date range must carry
the requested one), was added to the harness. Re-scored with it, the open-object schema gave **0/6** for Qwen.

Experiment on the real model (2 iterations each, `Investigate Makueni County Government 2022-2024`):

| variant | plans whose arguments carried the scope |
|---|---|
| A: open-object `args`, original prompt | 0/2 |
| B: open-object `args` + a worked example in the prompt | 0/2 |
| C: `args` as a **JSON string** (`args_json`) + the worked example | **2/2** |

Cause: an open-ended object gives the model no structure to fill, so it returns `{}`. One further detail found while applying
C: `args_json` must be a **required** field. With a default of `"{}"` the model skipped it again (0/6); required, it passed.

## Run 2: final schema (required `args_json`), Qwen only, 2 requests x 3 runs

| model | ok | tool names | args schema | **args carry scope** | scope buyer | scope years | no invented ids | covers request | median s |
|---|---|---|---|---|---|---|---|---|---|
| `qwen/qwen3.8-27b` | 6/6 | 6/6 | 6/6 | **6/6** | 6/6 | 6/6 | 6/6 | 6/6 | 1.5 |

The gpt-oss models were **not** re-run with the final schema (Groq's per-minute output limit made a three-model re-run take
over 20 minutes, and it was stopped). Their Run 1 results stand as measured on the first schema only.

## Decision

Default Groq model: **`qwen/qwen3.8-27b`**, an open-weights Qwen (the family named in the brief). It was the most reliable on
this task, and the only model measured on the final schema. The agent also keeps a deterministic safety net, `inject_scope`,
which copies the planned buyer and years into any step that omitted them (logged as a `scope_injected` event), so a weaker
model cannot silently widen an investigation. Ollama fallback: `qwen2.5:7b` (not tested here: Ollama is not installed on
the build machine).
