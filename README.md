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
_TBD — one-command setup._ Current manual steps (Windows):
```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
# place the frozen snapshot at data/raw/full.jsonl.gz, then:
.venv/Scripts/python data/loader.py
.venv/Scripts/python data/profile_fields.py > docs/data_profile.txt
```

## 6. Usage
_TBD_

## 7. Technology stack
Python 3.11+, DuckDB, pandas, LangGraph, FastMCP / MCP Python SDK, langchain-mcp-adapters, scikit-learn, Streamlit; open-weights LLMs (Llama 3.1 8B / Qwen 2.5 7B) via Ollama or Groq.

## 8. Agent architecture
_TBD_ (Planner → Investigator → Synthesiser, LangGraph)

## 9. MCP implementation
_TBD_

## 10. MCP tools / servers
_TBD_ — custom `zabuni-mcp` plus the official Filesystem MCP server (rationale to be documented). Planned tools and rules: see [`docs/red_flags.md`](docs/red_flags.md).

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
