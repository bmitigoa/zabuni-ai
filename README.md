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
_One-command setup is still TBD (Day 6)._ Current manual steps (Windows; on macOS/Linux use `.venv/bin/`):
```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
# place the frozen snapshot at data/raw/full.jsonl.gz, then:
.venv/Scripts/python data/loader.py                                   # builds data/zabuni.duckdb
.venv/Scripts/python data/profile_fields.py > docs/data_profile.txt   # optional: refresh the field profile
.venv/Scripts/python -m pytest                                        # 34 tests against the real data
```

**Python version.** Deployment is pinned to **Python 3.12** (`runtime.txt`, read by Heroku/Render-style
builds). Hugging Face Spaces selects Python through `python_version` in the Space's README header; that will be set
and verified on Day 5 when the Space is created. Development so far ran on Python 3.14 (3.12 was not installed on the
build machine), so a clean install on 3.12 is still to be tested on Day 6.

## 6. Usage
_TBD_

## 7. Technology stack
Python 3.11+, DuckDB, pandas, LangGraph, FastMCP / MCP Python SDK, langchain-mcp-adapters, scikit-learn, Streamlit; open-weights LLMs (Llama 3.1 8B / Qwen 2.5 7B) via Ollama or Groq.

## 8. Agent architecture
_TBD_ (Planner → Investigator → Synthesiser, LangGraph)

## 9. MCP implementation
The custom server **`zabuni-mcp`** lives in [`mcp_server/`](mcp_server/) and is built with **FastMCP** (the Python MCP SDK), served over **stdio**:

```bash
.venv/Scripts/python -m mcp_server.server        # started by an MCP client; speaks MCP on stdin/stdout
mcp dev mcp_server/server.py                      # optional: MCP Inspector (needs Node.js)
```

| File | Role |
|---|---|
| `mcp_server/server.py` | Creates the FastMCP app, registers the tools and the two resources |
| `mcp_server/tools.py` | The tool logic (plain functions, so they are unit-testable without MCP) |
| `mcp_server/rules.py` | All thresholds, in one place, labelled provisional; overridable via `ZABUNI_RULES_FILE` |
| `mcp_server/common.py` | Read-only DuckDB access, input validation, buyer-name resolution, response envelope |

**Response contract.** Every tool returns `{result, evidence, params_used, warnings}`. `evidence` is a list of
`{ocid, award_id, field, value}` items citing the OCDS award behind each statement; a flag without evidence is never
returned. Bad input and empty results come back as an explanatory message (never a crash), so the agent can recover.
Tools are annotated read-only. The database is opened read-only and holds organisation names only (no personal data).

**Resources.** `ocds://release/{ocid}` returns the stored record (tender, awards, supplier names) for citation;
`rules://ppada` returns every threshold and rule, labelled *provisional — to be validated with a procurement practitioner*.

Tests: `tests/test_tools.py` (real-data and edge-case tests per tool) and `tests/test_server_stdio.py` (launches the
server as a subprocess and checks tool listing, a tool call, and both resources over real MCP).

## 10. MCP tools / servers
**Custom server `zabuni-mcp`** (Day 2: five read-only screening tools; Day 4 adds the write/generate tools):

| Tool | What it does |
|---|---|
| `search_awards` | Find awards by buyer / supplier / title keyword / date range; returns `ocid` + `award_id` to investigate |
| `compute_price_benchmark` | Median, IQR and ratio-to-median of an award against comparable awards; asks the agent to widen the search when < 10 comparables |
| `detect_splitting` | Clusters of awards from one buyer to one supplier inside a short window, each just below a threshold, totalling above it |
| `detect_noncompetitive_method` | Buyer's share of direct + restricted procurement vs the national baseline (replaces the single-bidder check the data cannot support) |
| `supplier_concentration` | Each supplier's share of a buyer's awards by count and value, plus HHI |
| `flag_finding`, `draft_eval_memo` | *Day 4:* persist a sourced flag; draft the evaluation memo from human-approved flags only |

Rules, thresholds, fields used and sources for each tool: [`docs/red_flags.md`](docs/red_flags.md). Checks the data
cannot support (award-vs-tender gap, single bidder, bidding window) are documented there as dropped.

**External server:** the official Filesystem MCP server (rationale documented on Day 3 when the agent is wired up).

## 11. Human-in-the-loop workflow
_TBD_ — the reviewer approves, rejects or requests clarification, with a reason, on every flag. The agent never recommends or awards a tender.

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
