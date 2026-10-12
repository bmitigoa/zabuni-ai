# Zabuni AI: architecture (one page)

![Zabuni AI system architecture](docs/architecture.png)

**What it is.** An agentic *decision-support* tool. A reviewer names a buyer, a supplier or years; a LangGraph agent plans an investigation of published Kenyan tender awards (PPRA, Open Contracting Data Standard), calls tools **only through MCP**, and raises flags that each cite their OCDS record. A human approves, rejects or queries every flag with a reason; only approved flags enter the evaluation memo. **The agent never recommends or awards a tender.**

## Agent design
```
request ─► PLANNER ─(scope guard)─► INVESTIGATOR ⟲ ─► SYNTHESISER ─► HUMAN GATE ⟷ CLARIFY ─► MEMO
```
* **Planner** (1 LLM call): builds a structured plan from the tool catalogue discovered with `list_tools` (no tool name is hard-coded). The scope it extracts is *grounded* in the request text; a request with no buyer, supplier or period ends here with a question.
* **Investigator** (loops on a conditional edge): runs one step per visit, then follows declarative follow-up rules (a price outlier triggers splitting and concentration checks), widens a search that found too few comparables, and gives a failing call one LLM correction. Stops at a tool-call or token budget and says so.
* **Synthesiser** (1 LLM call): ranks and words the flags the *tools* proposed. A citation guard drops any finding that mentions an ocid no tool returned; checks on the same award are merged into one card; a flagged award outside the requested scope is dropped; at most 10 flags per run.
* **Human gate:** a LangGraph `interrupt` saved by a SQLite checkpointer. Approve / reject / clarify per flag, a reason required, the reviewer recorded by *role*. Clarify re-investigates that flag and pauses again; a rejected flag is remembered and not raised again.
* **Memo:** drafted from approved flags only, saved through the Filesystem MCP server.

## Custom vs borrowed MCP servers
| Server | Why |
|---|---|
| **`zabuni-mcp`** (custom, FastMCP, stdio): 6 analysis tools (`search_awards`, `compute_price_benchmark`, `detect_splitting`, `detect_noncompetitive_method`, `supplier_concentration`, `detect_price_anomalies`), 5 write/review tools (`flag_finding`, `record_decision`, `attach_clarification`, `list_flags`, `draft_eval_memo`), resources `ocds://release/{ocid}` and `rules://ppada` | The domain logic (red-flag rules, thresholds, citations, the flag store) is ours and must be testable, bounded and cited. Every result carries `evidence: [{ocid, award_id, field, value}]`; every threshold lives in `rules.py`. |
| **Filesystem MCP** (official `@modelcontextprotocol/server-filesystem`, pinned, sandboxed to `outputs/`) | File I/O and path sandboxing are solved, widely used code; the agent gets a capability-limited way to save memos, and every write is an audited MCP call. We do not hand-roll file permissions. |

The planner sees only read-only tools without a role; the write/review tools carry a *role* in their metadata and are called only by the gate, so the model cannot approve its own flags.

## Data flow
UI/CLI → `open_session` (MCP client, rules, audit log, LLM chain, checkpointer) → graph → MCP tools → read-only `data/zabuni.duckdb` (frozen PPRA snapshot of 8 Oct 2026) and the writable `flags.duckdb` (flags, decisions, clarifications, memos). Every tool call, agent decision and LLM call is logged (`logs/*.jsonl`) with tokens and estimated cost.

## Failure handling
| Failure | Response |
|---|---|
| No scope / a scope the model invented | `ground_scope` + scope guard: ask, run nothing (the "hello → 40 flags" case, `evals/intentional_failure.md`) |
| Run too long or too costly | tool-call and token budget in `rules.py`, carried across run and review; the memo states the stop |
| Groq 429 / daily quota | backoff → second open-weights model → clean stop; a per-minute limit is never mistaken for the daily one |
| Model returns unusable output | next model for that call only |
| MCP server dies | restart once and retry; a clear error in the log |
| Bad tool arguments | schema check before the call, unknown arguments dropped, one LLM correction |
| Hallucinated ocid | citation guard + `flag_finding` re-checks every cited award exists |

## Deliberately left out
No automated decision, scoring or ranking of tenders or suppliers; no personal data (company names only); no bid documents (not published); no live data (frozen snapshot); no ML beyond one Isolation Forest outlier check (fixed seed); no second-level approval in the product (designed and verified as a practice requirement); no validated thresholds yet (all labelled *provisional* until a practitioner session).
