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
.venv312/Scripts/python -m pytest               # 141 tests; the last is a live LLM smoke test that needs a Groq key and skips (not fails) on a provider limit
```

**Python version.** Deployment is pinned to **Python 3.12** (`runtime.txt`, read by Heroku/Render-style builds); the full
suite passes on 3.12.10. Hugging Face Spaces selects Python through `python_version` in the Space's README header; that
will be set and verified on Day 5. A clean-clone install is still to be tested on Day 6.

**The Filesystem MCP server** is fetched by `npx` on first use (`@modelcontextprotocol/server-filesystem`, pinned to
version 2026.8.31 in `agent/settings.py`), so the first agent run needs network access to the npm registry.

## 6. Usage
```bash
.venv312/Scripts/python -m agent.run "Investigate Makueni County Government 2022-2024"
```
It prints the **plan** (built from the tools it discovered on the server), every **tool call** with timing, any
**follow-ups**, **recoveries** and **corrections**, and the **flags created**, then saves a draft summary to `outputs/`
through the Filesystem MCP server. A real run is recorded in [`docs/sample_run/`](docs/sample_run/) (console output,
the audit log lines of that run, the draft summary and the flags it created).

| Where | What |
|---|---|
| `logs/tool_calls.jsonl` | every MCP tool call: `run_id`, timestamp, tool, inputs, output summary + output, duration, error |
| `logs/agent_events.jsonl` | the agent's decisions: plan, follow-ups, recoveries, corrections, scope injections, step cap, citation guard |
| `data/flags.duckdb` (table `flags`) | flags stored by `flag_finding`, status `pending_review` until a human decides (Day 4) |
| `outputs/summary_<run_id>.md` | the draft summary (the only directory the Filesystem MCP server may write to) |

Useful options: `--max-steps N` (step cap), `--model`, `--provider groq|ollama`, `--no-save`. Other commands:
`python -m mcp_server.server` (the MCP server alone), `python -m evals.model_check` (model reliability check),
`python -m agent.models` (models available to your key).

**Rate limits.** Groq's free tier allows `qwen/qwen3.8-27b` 1,000 output tokens per minute and 200,000 tokens per day. A run
uses roughly 6-8 thousand tokens. The agent caps each reply, honours the provider's "try again in N s" hint, and **fails
fast with a clear message on a daily quota** instead of retrying pointlessly.

## 7. Technology stack
Python 3.12; **DuckDB** (embedded analytics database) and pandas/numpy; **FastMCP** (MCP Python SDK) for the custom server and
**langchain-mcp-adapters** (`MultiServerMCPClient`) for the client; the official **Filesystem MCP server** (Node/npx);
**LangGraph** (`StateGraph`) for the agent; **pydantic** for the LLM's structured outputs and **jsonschema** to validate tool
arguments; **langchain-groq** / **langchain-ollama** for open-weights models; pytest; scikit-learn and Streamlit (Day 4-5).

## 8. Agent architecture
```
                       tools discovered with list_tools (names + descriptions + schemas): none are hard-coded
  request ──> PLANNER ──────────────> INVESTIGATOR ─────────────────> SYNTHESISER ──> human_review ──> END
              LLM builds a            runs one queued step per visit;   ranks candidate flags, applies the   (Day 4: interrupt,
              structured plan         loops while steps remain          CITATION GUARD, stores each accepted   approve / reject /
              (tool + args + why)       |                               flag with flag_finding, drafts a      clarify + reason)
                                        +-- follow-up: add steps        summary
                                        +-- recovery: widen and retry
                                        +-- correction: LLM fixes bad args (once)
                                        +-- step cap: stop, say so
```
Files: `agent/graph.py` (the graph), `agent/nodes.py` (the nodes), `agent/policy.py` (pure decision logic),
`agent/discovery.py`, `agent/mcp_client.py`, `agent/llm.py`, `agent/audit.py`, `agent/state.py`, `agent/report.py`, `agent/run.py`.

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
* **Human review (placeholder).** `human_review` is where Day 4's approve / reject / clarify interrupt goes; until then every
  flag stays pending and the draft is labelled "pending human review". The agent recommends and awards nothing.

**Why this is an agent, not a pipeline.** The tool sequence and arguments come from a model reading the live tool catalogue;
the graph branches on what each result says (flagged, too few comparables, error); a tool added to the server is planned and
run with no agent change (tested with a throw-away server and a real new tool). The follow-up *policy* is declarative and the
safety nets (scope injection, citation guard, step cap) are deterministic by design, so what the model may and may not do is
explicit and testable.

**Model choice** (open-weights only, enforced by a family check in `agent/llm.py`). Groq: **`qwen/qwen3.8-27b`**, chosen by
measurement on this project's own planning task rather than reputation: it was the most reliable, and the only model measured
on the final schema. Groq's docs list `llama-3.3-70b-versatile`, but the key used here cannot call it (HTTP 404), so Llama could
not be compared; the gpt-oss models were less reliable on the first schema. Full record, including a check that was too
lenient and the schema change that fixed it, in [`docs/sample_run/model_check.md`](docs/sample_run/model_check.md). Ollama
fallback: `qwen2.5:7b` (not tested here: Ollama is not installed on the build machine). The sample is small; it supports a
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
**Custom server `zabuni-mcp`:** five read-only analysis tools and one write tool.

| Tool | What it does |
|---|---|
| `search_awards` | Find awards by buyer / supplier / title keyword / date range, newest or largest first; returns `ocid` + `award_id` |
| `compute_price_benchmark` | Median, IQR and ratio-to-median of an award against comparable awards; machine-readable `widen_options` when there are too few comparables |
| `detect_splitting` | Clusters of awards from one buyer to one supplier inside a short window, each just below a threshold, totalling above it |
| `detect_noncompetitive_method` | Buyer's share of direct + restricted procurement vs the national baseline (replaces the single-bidder check the data cannot support) |
| `supplier_concentration` | Each supplier's share of a buyer's awards by count and value, plus HHI |
| `flag_finding` (**write**) | Stores a flag with status `pending_review`; **rejects** a flag with no evidence, a citation that does not exist in the data, or an unknown rule/severity |
| `draft_eval_memo` | *Day 4:* draft the evaluation memo from human-approved flags only |

Rules, thresholds, fields used and sources for each tool: [`docs/red_flags.md`](docs/red_flags.md). Checks the data cannot
support (award-vs-tender gap, single bidder, bidding window) are documented there as dropped.

**External server: the official Filesystem MCP server** (`@modelcontextprotocol/server-filesystem`, pinned version, run with
`npx`), **sandboxed to `outputs/`**. Why borrowed rather than written: path sandboxing and file I/O are solved, widely used code
maintained by the MCP project; the agent gets a capability-limited way to save a draft (one directory), every write crosses the
MCP boundary and is audit-logged like any other call, and we avoid hand-rolling file permissions. Verified: a write inside
`outputs/` succeeds and a write outside it is refused ("path outside allowed directories").

## 11. Human-in-the-loop workflow
Flags are stored as `pending_review`; the Day 4 gate (a LangGraph `interrupt`) lets the reviewer approve, reject or request clarification with a reason before a flag can enter the memo. Today the graph has a clearly marked `human_review` placeholder and every draft is labelled pending human review. The agent never recommends or awards a tender.

## 12. Limitations
See "Data source & limitations" below; more to be added.

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
