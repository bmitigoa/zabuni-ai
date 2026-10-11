# Zabuni AI

Agentic AI (MCP + LangGraph) that flags value-for-money and integrity risks in public procurement using OCDS data. Human reviewers decide. Governance track, African Agentic AI Design Challenge.

> **Status:** work in progress (build window 8–14 Oct 2026). Sections below are outlined and will be filled in as each part is built.

## 1. Problem statement
_TBD_

## 2. Solution overview
_TBD_

## 3. Target users
_TBD_

## 4. Architecture
_TBD_ (see `ARCHITECTURE.md` once added)

## 5. Setup / installation
_One-command setup is still TBD (Day 6)._ Requirements: Python 3.12, Node.js with `npx` (for the Filesystem MCP server), the frozen snapshot, and a Groq API key (or Ollama). On Windows (on macOS/Linux use `.venv312/bin/` and `python3.12`):

```bash
py -3.12 -m venv .venv312
.venv312/Scripts/pip install -r requirements.txt
# place the frozen snapshot at data/raw/full.jsonl.gz, then:
.venv312/Scripts/python data/loader.py          # builds data/zabuni.duckdb (about 10 s)
cp .env.example .env                            # then put your GROQ_API_KEY in .env
.venv312/Scripts/python -m agent.models         # lists the models YOUR key can use (a documented model may be unavailable)
.venv312/Scripts/python -m pytest               # 213 tests; one live LLM smoke test needs a Groq key and skips (not fails) on a provider limit (set ZABUNI_SKIP_LIVE=1 to skip it)
```

**Python version.** Deployment is pinned to **Python 3.12** (`runtime.txt`, read by Heroku/Render-style builds); the full
suite passes on 3.12.10. Hugging Face Spaces selects Python through `python_version` in the Space's README header; that
will be set and verified on Day 5. A clean-clone install is still to be tested on Day 6.

**The Filesystem MCP server** is fetched by `npx` on first use (`@modelcontextprotocol/server-filesystem`, pinned to
version 2026.8.31 in `agent/settings.py`), so the first agent run needs network access to the npm registry.

## 6. Usage
Local development, evals and repeat runs use **Ollama** (`LLM_PROVIDER=ollama`, `MODEL=qwen2.5:7b`: free, no quota). Groq is for
the hosted demo, the demo video and the final sample run.
```bash
ollama pull qwen2.5:7b                                   # once
.venv312/Scripts/python -m agent.run "Investigate Makueni County Government 2022-2024"      # plan, investigate, pause at the gate
.venv312/Scripts/python -m agent.review <run_id> --role internal_auditor                    # approve / reject / clarify, each with a reason
```
`agent.run` prints the **plan** (built from the tools it discovered on the server), every **tool call** with timing, any
**follow-ups**, **recoveries**, **corrections**, **budget stops** and **model fallbacks**, the **flags created**, and the run's
tokens, estimated cost and estimated remaining daily quota. It then pauses for review. `agent.review` resumes the same run; when
every flag is decided the memo is written to `outputs/memo_<run_id>.md`. A recorded end-to-end run (with the model used) is in
[`docs/sample_run/`](docs/sample_run/).

| Where | What |
|---|---|
| `logs/tool_calls.jsonl` | every MCP tool call: `run_id`, timestamp, tool, inputs, output summary + output, duration, error |
| `logs/agent_events.jsonl` | the agent's decisions: plan, follow-ups, recoveries, corrections, decisions, clarifications, budget stops, model fallbacks, `llm_call` (tokens, cost, remaining quota) and `run_usage` |
| `logs/llm_usage.jsonl` | one line per LLM call: tokens, estimated cost, seconds (the quota estimate is computed from it) |
| `data/flags.duckdb` | flags, reviewer decisions (append-only), clarifications and memos |
| `data/checkpoints.sqlite` | LangGraph checkpoints: the state of paused runs |
| `outputs/summary_<run_id>.md`, `outputs/memo_<run_id>.md` | the draft summary and the approved-flags memo (the only directory the Filesystem MCP server may write to) |

Useful options: `--max-steps N` (tool-call cap), `--model`, `--provider groq|ollama`, `--no-save`. Other commands:
`python -m mcp_server.server` (the MCP server alone), `python -m evals.model_check` (model reliability check),
`python -m agent.models` (models available to your Groq key).

**Run budget.** One run may make at most `agent.max_steps` tool calls and use at most `agent.max_total_tokens` LLM tokens (both in
`rules.py`; 4,000 tokens are held back so the summary can still be written). When a limit is reached the agent stops
investigating, says so in the draft and in the memo ("the run stopped early ... absence of a finding is not evidence that none
exists"), and the flags found so far still go to review.

**Cost.** Ollama costs nothing. For Groq the log shows tokens and an *estimated* pay-as-you-go cost per call and per run, from the
price list in `rules.py` (labelled with its retrieval date, 11 Oct 2026). The free tier itself costs nothing.

**Free-tier rate limits and fallback.** Groq's free tier allows `qwen/qwen3.8-27b` 1,000 output tokens per minute and 200,000 tokens
per day (a rolling window). The agent caps each reply, honours the provider's "try again in N s" hint with exponential backoff,
and on a daily-quota error **falls back to a second open-weights model on Groq** (`openai/gpt-oss-120b`, which has its own quota); if that
is also exhausted it **stops cleanly** with a message that names `LLM_PROVIDER=ollama` as the alternative. The estimated remaining
daily quota is logged with every call. The hosted demo (`agent/demo_guard.py`) limits live runs per visitor per hour and shows a
"daily quota reached, see the recorded demo video" message instead of failing.

## 7. Technology stack
Python 3.12; **DuckDB** (embedded analytics database) and pandas/numpy; **FastMCP** (MCP Python SDK) for the custom server and
**langchain-mcp-adapters** (`MultiServerMCPClient`) for the client; the official **Filesystem MCP server** (Node/npx);
**LangGraph** (`StateGraph`) for the agent; **pydantic** for the LLM's structured outputs and **jsonschema** to validate tool
arguments; **langchain-groq** / **langchain-ollama** for open-weights models; pytest; scikit-learn and Streamlit (Day 4-5).

## 8. Agent architecture
```
                       tools discovered with list_tools (names + descriptions + schemas): none are hard-coded
  request ──> PLANNER ──────────────> INVESTIGATOR ─────────────────> SYNTHESISER ──> human_review ──> memo ──> END
              LLM builds a            runs one queued step per visit;   ranks candidate flags, applies the   INTERRUPT: approve /   approved
              structured plan         loops while steps remain          CITATION GUARD, stores each accepted   reject / clarify,      flags
              (tool + args + why)       |                               flag with flag_finding, drafts a      reason required          only
                                        +-- follow-up: add steps        summary                                    ^        |
                                        +-- recovery: widen and retry                                        clarify <--------+ (agent re-investigates,
                                        +-- correction: LLM fixes bad args (once)                                                 then pauses again)
                                        +-- budget / step cap: stop, say so
```
Files: `agent/graph.py` (the graph and checkpointer), `agent/nodes.py` (the nodes), `agent/policy.py` (pure decision logic),
`agent/discovery.py`, `agent/mcp_client.py`, `agent/llm.py`, `agent/usage.py` (token/cost ledger), `agent/budget.py`, `agent/audit.py`,
`agent/state.py`, `agent/report.py`, `agent/session.py`, `agent/run.py` (start a run), `agent/review.py` (review CLI), `agent/demo_guard.py`.

* **Planner.** One LLM call returns a structured plan (objective, scope = buyer and years, and steps of tool + arguments +
  reason) from a catalogue built from `list_tools`. Unknown tools are rejected (it re-plans once). The planned buyer and years
  are copied into any step that accepts them but omitted them (`scope_injected` event), so a model that forgets cannot silently
  widen an investigation to every buyer in Kenya.
* **Investigator.** A conditional edge loops on itself while steps remain, so the sequence of calls is decided by the run, not
  by code. After each result it can (a) **follow up**: rules in `rules://ppada` (`agent.followups`, data not code) add steps,
  e.g. a search result spawns price checks of the awards found, and a flagged price outlier spawns splitting and concentration
  checks for that buyer and supplier; (b) **recover**: a result with `insufficient_comparables` and machine-readable
  `widen_options` is widened (drop the buyer-type filter, the years, ...) up to twice, and the change is recorded; (c)
  **correct**: arguments that fail the tool's JSON schema, or a tool error such as an unknown buyer (the tool returns candidate
  names), go back to the LLM once for corrected arguments; (d) **stop** at the step cap (default 12 tool calls) and say so.
* **Synthesiser.** Detection tools return uniform `result.flags`; these are the *candidates*. The LLM orders them and writes
  short explanations. The **citation guard** then drops any finding that mentions an `ocid` that no tool returned, or that no
  tool evidence supports; accepted findings take their evidence from tool results, never from model text, and a candidate the
  model drops or mangles is kept with the tool's own wording, so a model cannot hide a flag or invent one. Each accepted finding
  is stored with `flag_finding` (status `pending_review`), which independently rejects missing or non-existent citations.
* **Human review and memo.** `human_review` is a LangGraph `interrupt` backed by a SQLite checkpointer (see section 11); `clarify`
  re-investigates a queried flag; `memo` drafts the evaluation memo from approved flags. The agent recommends and awards nothing.
* **Budget and resilience.** A run is bounded by `agent.max_steps` tool calls and `agent.max_total_tokens` LLM tokens (rules); a stop is
  recorded and repeated in the memo. LLM 429s are retried with backoff, then fall back to a second open-weights model; a dead MCP
  server is restarted once and the call retried; failures are written to the audit log, not swallowed (`agent/llm.py`, `agent/mcp_client.py`).

**Why this is an agent, not a pipeline.** The tool sequence and arguments come from a model reading the live tool catalogue;
the graph branches on what each result says (flagged, too few comparables, error); a tool added to the server is planned and
run with no agent change (tested with a throw-away server and a real new tool). The follow-up *policy* is declarative and the
safety nets (scope injection, citation guard, step cap) are deterministic by design, so what the model may and may not do is
explicit and testable.

**Model choice** (open-weights only, enforced by a family check in `agent/llm.py`). Groq: **`qwen/qwen3.8-27b`**, chosen by
measurement on this project's own planning task rather than reputation: it was the most reliable, and the only model measured
on the final schema. Groq's docs list `llama-3.3-70b-versatile`, but the key used here cannot call it (HTTP 404), so Llama could
not be compared; the gpt-oss models were less reliable on the first schema. Full record, including a check that was too
lenient and the schema change that fixed it, in [`docs/sample_run/model_check.md`](docs/sample_run/model_check.md). Local
model: `qwen2.5:7b` on Ollama, used for development and the recorded sample run (`docs/sample_run/`); it follows the plan schema but writes looser summaries. The sample is small; it supports a
default, it is not a benchmark.

## 9. MCP implementation
The custom server **`zabuni-mcp`** lives in [`mcp_server/`](mcp_server/), is built with **FastMCP** and served over **stdio**:

```bash
.venv312/Scripts/python -m mcp_server.server     # started by an MCP client; speaks MCP on stdin/stdout
mcp dev mcp_server/server.py                      # optional: MCP Inspector (needs Node.js)
```

| File | Role |
|---|---|
| `mcp_server/tools/*.py` | one module per tool; importing registers it (`@tool(...)`) |
| `mcp_server/registry.py` | the registry, the `safe` wrapper, docstring rendering from rules |
| `mcp_server/server.py` | creates the FastMCP app and registers whatever the registry holds, plus the two resources |
| `mcp_server/rules.py` | every threshold, limit, heuristic and agent policy, in one place, labelled provisional |
| `mcp_server/common.py`, `toolkit.py`, `records.py` | read-only DuckDB access, validation, buyer resolution, the response envelope, shared helpers |

**Adding a tool needs three edits:** a new module in `mcp_server/tools/`, its section in `rules.py`, and its row in
`docs/red_flags.md` (a test fails if the row is missing). The server, the agent and the tests discover it on their own.
**Docstrings are templates** (`[[price_benchmark.min_comparables]]`) rendered from the live rules when the tool is registered,
so a description the LLM reads can never disagree with the thresholds the code applies; tests enforce this and scan tool code
for stray numbers (thresholds live only in rules, served as `rules://ppada`).

**Response contract.** Every tool returns `{result, evidence, params_used, warnings}`; `evidence` lists `{ocid, award_id, field,
value}` items citing the OCDS award behind each statement. Detection tools also return `result.flags` (uniform candidate flags).
Bad input and empty results come back as an explanatory message, never a crash. Analysis tools are annotated read-only and
the analysis database is opened read-only; the only write tool, `flag_finding`, writes to a separate flags store.

**Resources.** `ocds://release/{ocid}` returns the stored record for citation; `rules://ppada` returns every threshold, limit
and agent policy (the agent reads its own settings from it).

**Environment.** An MCP stdio client does not forward its environment to the server subprocess. `agent/mcp_client.py` forwards
only `ZABUNI_*` settings (database, rules override, flags store) and nothing else, so the Groq key never reaches the server;
a test proves an override reaches the server and a database path takes effect.

Tests: `tests/test_tools.py`, `test_tool_flags.py`, `test_flag_finding.py`, `test_registry.py` (auto-registration, rendered
docstrings, no magic numbers, `norm()`), `test_server_stdio.py` (real MCP over a subprocess), and the agent tests below.

## 10. MCP tools / servers
**Custom server `zabuni-mcp`:** six read-only analysis tools and five write/review tools.

| Tool | What it does |
|---|---|
| `search_awards` | Find awards by buyer / supplier / title keyword / date range, newest or largest first; returns `ocid` + `award_id` |
| `compute_price_benchmark` | Median, IQR and ratio-to-median of an award against comparable awards; machine-readable `widen_options` when there are too few comparables |
| `detect_splitting` | Clusters of awards from one buyer to one supplier inside a short window, each just below a threshold, totalling above it |
| `detect_noncompetitive_method` | Buyer's share of direct + restricted procurement vs the national baseline (replaces the single-bidder check the data cannot support) |
| `supplier_concentration` | Each supplier's share of a buyer's awards by count and value, plus HHI |
| `detect_price_anomalies` | Isolation Forest (fixed seed) on award amounts within category and buyer-type cohorts; high-side outliers with "3.2x median of 47 comparable awards" and the nearest comparables cited |
| `flag_finding` (**write**) | Stores a flag with status `pending_review`; **rejects** a flag with no evidence, a citation that does not exist in the data, or an unknown rule/severity; a flag a reviewer rejected earlier is returned as `rejected`, not re-raised |
| `record_decision` (**write**) | Stores a reviewer's approve / reject / clarify with a required reason and a role (append-only); only the approval gate calls it |
| `attach_clarification` (**write**) | Attaches the agent's answer (and any new, verified evidence) to a flag that a reviewer queried, and returns it to review |
| `list_flags` | Lists stored flags with evidence, decisions and clarification history |
| `draft_eval_memo` (**write**) | Builds the evaluation memo from **approved** flags only, with cited ocids, decisions and reasons, data limitations and the no-award statement; the Filesystem MCP server saves it |

The planner is shown only the read-only analysis tools; the write and review tools carry a *role* in their metadata and are found
by role, so the agent cannot be talked into approving its own flags.

Rules, thresholds, fields used and sources for each tool: [`docs/red_flags.md`](docs/red_flags.md). Checks the data cannot
support (award-vs-tender gap, single bidder, bidding window) are documented there as dropped.

**External server: the official Filesystem MCP server** (`@modelcontextprotocol/server-filesystem`, pinned version, run with
`npx`), **sandboxed to `outputs/`**. Why borrowed rather than written: path sandboxing and file I/O are solved, widely used code
maintained by the MCP project; the agent gets a capability-limited way to save a draft (one directory), every write crosses the
MCP boundary and is audit-logged like any other call, and we avoid hand-rolling file permissions. Verified: a write inside
`outputs/` succeeds and a write outside it is refused ("path outside allowed directories").

## 11. Human-in-the-loop workflow
```
 synthesiser ──> human_review ──(INTERRUPT: run paused, state saved in data/checkpoints.sqlite)
                      ^   |
   still undecided ───+   +── reviewer resumes: approve | reject | clarify, each with a reason, recorded by role
   clarify: agent re-investigates ◄──┘                      |
   that flag with your question                              v
   and pauses again                          all decided ──> memo (APPROVED flags only) ──> outputs/memo_<run_id>.md
```
1. **Run.** `python -m agent.run "…"` plans, investigates, and ends at the gate: every flag is `pending_review` and the process
   prints `python -m agent.review <run_id>`. Nothing is in a memo yet. The pause is a LangGraph `interrupt` with a **SQLite
   checkpointer** (`data/checkpoints.sqlite`, thread id = run id), so the process can exit and the run survives.
2. **Review.** `python -m agent.review <run_id> --role internal_auditor` lists each pending flag with its explanation and cited
   OCDS records and asks for a decision. A **reason is required** for every decision (for *clarify*, the reason is your question).
   Reviewers are recorded by **role** (`procurement_officer`, `internal_auditor`, `committee_member`, `oversight_reviewer`), never by name.
3. **Clarify.** The flag goes back to the agent with your question; it plans up to two tool calls, answers using only their
   results (the answer may cite only records the tools returned), attaches the answer to the flag, and the run **pauses again**.
   A flag can be queried twice, then must be approved or rejected.
4. **Reject is remembered.** A flag's identity is the rule plus its cited records. A flag a reviewer rejected is not raised
   again in later runs on the same scope (`rejected_flag_suppressed` in the audit log, and a note in the memo).
5. **Memo.** When every flag is decided, `draft_eval_memo` (a `zabuni-mcp` tool) builds the memo from **approved flags only**: summary,
   each finding with its cited `ocid`s, the reviewers' decisions and reasons, the data limitations, how the run ended (budget,
   quota), and the statement that the memo **does not recommend or award any tender**. The Filesystem MCP server saves it to `outputs/`.
6. **Stored and logged.** Decisions are append-only rows in `data/flags.duckdb` (`flag_decisions`, `flag_clarifications`, `memos`)
   and `decision` events in `logs/agent_events.jsonl`. The review tools are not offered to the Planner: only the gate calls them.

The agent never recommends or awards a tender; the human decision is the only thing that moves a flag into the memo.

## 12. Limitations
* **Free-tier quota.** The hosted demo runs on Groq's free tier: about 200,000 tokens per day for the main model, shared by every
  visitor and by the builder's own testing. A run uses roughly 8-10 thousand tokens, so on a busy day the live demo will refuse new runs
  ("daily quota reached") until the rolling 24-hour window frees up; the recorded demo video shows a complete run. The remaining-quota
  figure in the log is an *estimate* from our own ledger (the provider does not report it), corrected whenever a 429 states the real one.
* **Local model speed and quality.** `qwen2.5:7b` on a laptop without a dedicated GPU generates only a few tokens per second, so a
  run takes minutes, and a 7B model sometimes invents an argument (the agent drops arguments a tool does not declare and logs it) or
  words a finding loosely (the citation guard and the tools' own wording protect against that).
* **Review is single-step per flag.** Any one reviewer's decision moves a flag; two-level approval for high-severity flags is not built.
* **Costs are estimates** from a dated list price, not an invoice.
See also "Data source & limitations" below.

## 13. Future improvements
_TBD_

---

## Data source & limitations
- **Source:** Public Procurement Regulatory Authority (PPRA) Kenya, published as Open Contracting Data Standard (OCDS) data via the Open Contracting Data Registry, publication 147 — <https://data.open-contracting.org/en/publication/147>. Frozen snapshot downloaded 8 Oct 2026 and never refreshed during the build. The raw file is not redistributed in this repo.
- **Method attribution:** the red-flag approach follows the Open Contracting Partnership's *Red Flags for Integrity* and Kenya's Public Procurement and Asset Disposal Act, 2015 (see [`docs/red_flags.md`](docs/red_flags.md)).
- **Bid documents are not published**, so the tool cannot assess bid content, evaluation scoring or bidder qualifications.
- **Gaps in this snapshot** (measured; see [`docs/data_profile.txt`](docs/data_profile.txt)): no tenderers or bidder counts; no tender-level value; no item-level data; no award decision date (contract signing date used instead); no description or status; tender-period dates unreliable (end before start in 44 %); supplier name missing on ~27 % of awards; extreme or non-positive amounts present. Organisations are matched on normalised names, not IDs.
- Only organisation-level names are used; no personal data is loaded.
- Flags are screening indicators for human review, not findings of wrongdoing.

## Evals summary
_TBD (Day 6)_

## Cost per run
_TBD (Day 6)_

## License
MIT — see [LICENSE](LICENSE).
