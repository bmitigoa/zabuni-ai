# EVALS

Real runs only: every number below was produced by the scripts in `evals/` on the frozen PPRA snapshot (8 Oct 2026) and is stored in `evals/results/`. Reproduce: `python -m evals.run_cases`, `python -m evals.variance --provider ollama --runs 5`, `python -m evals.variance --provider groq --runs 2`, `python -m evals.render_evals`. Failures are kept visible.

## 1. Cases (21): 18 pass, 3 fail, 0 error

Run on 2026-10-12 16:15:51 against the frozen snapshot with the real `zabuni-mcp` server (`python -m evals.run_cases`). Detection cases (D) test each rule on a known positive and a known negative with no model involved. Agent cases (A) test the agent's guards and the human gate with a scripted stand-in for the LLM, so they are deterministic and test **our code, not a model** (model behaviour is in section 4). False-alarm cases (F) are practitioner-style situations where a flag probably has a lawful explanation: **a raised flag is a FAIL on purpose**, because the rules cannot tell lots, frameworks or emergencies from splitting. Failures are kept, not tuned away.

| ID | Category | Case | Expectation | Result | What happened |
|---|---|---|---|---|---|
| D1 | detection | Splitting: positive | Makueni County Government 2022-2023 raises the 3-award cluster to DOUBLE KEY SYSTEMS (each just under KES 5M, 13 days) | **PASS** | 3 awards from Makueni County Government to DOUBLE KEY SYSTEMS LTD within 13 days, each just below KES 5,000,00 |
| D2 | detection | Splitting: negative | Makueni County Government 2019 raises nothing | **PASS** | 0 flags |
| D3 | detection | Price benchmark: positive | the steel-drums award (KES 8.9M) is flagged with >= 10 comparables | **PASS** | 22.53x the median of 10 comparables |
| D4 | detection | Price benchmark: negative | an award at 0.43x its comparables' median is not flagged | **PASS** | 0.43x the median of 18 comparables, flagged=False |
| D5 | detection | Price anomaly (Isolation Forest): positive | Makueni County Government 2022-2023: the staff-medical-cover award is flagged as an outlier | **PASS** | 71.0x the median of 4659 comparable awards (anomaly score 0.75) |
| D6 | detection | Price anomaly: negative | Makueni County Government 2019 raises nothing (cohorts too small to model) | **PASS** | 5 cohort(s) had fewer than 30 awards and were not analysed. |
| D7 | detection | Non-competitive method: positive | Kenya Power & Lighting Company 2023: direct+restricted share >= 2x the national baseline | **PASS** | 7.64x the baseline, 77 awards |
| D8 | detection | Non-competitive method: negative | Makueni County Government 2023 (0.11x the baseline) is not flagged | **PASS** | 0.11x the baseline |
| D9 | detection | Supplier concentration: positive | the national ranking raises at least one buyer whose top supplier holds >= 25 % | **PASS** | Kenya Industrial Research & Development Institute: top supplier Kingsley Construction Co. Ltd holds 8% of 38 named award |
| D10 | detection | Supplier concentration: negative | Makueni County Government 2023 (top supplier 0.17 % of awards) is not flagged | **PASS** | top supplier share by count 0.17% |
| A1 | agent | Citation guard | a model that cites an invented ocid, in a finding or in the summary, cannot get it into a stored flag | **PASS** | 2 drafts dropped, 0 invented ocids stored, 4 tool-proposed flags kept |
| A2 | agent | Scope guard | a request with no buyer, supplier or period runs no tool, even if the model invents a scope | **PASS** | scope requested, 0 tool calls, 1 LLM call |
| A3 | agent | Budget stop | when the token budget is spent the run stops cleanly and the memo says so | **PASS** | stopped before any tool call; memo states the stop |
| A4 | agent | Rejected-flag memory | a flag a reviewer rejected is not raised again in a later run on the same scope | **PASS** | 4 flags in run 1, 3 in run 2; the rejected one was suppressed |
| A5 | agent | Clarify loop | a reviewer's question re-investigates that flag, attaches an answer and pauses again before the memo | **PASS** | paused again with the agent's answer attached, then drafted the memo after approval |
| A6 | agent | Duplicate-flag merge | a price benchmark and a price anomaly on the SAME award become one card with two signals | **PASS** | 1 card, 2 signals |
| A7 | agent | Scope check on flagged awards | a flagged award dated outside the requested years is not raised | **PASS** | 1 finding(s) were not raised because the flagged award lies outside the requested scope (award ocds-5whusi-42686: dated 2022-12-14, outside |
| A8 | agent | Flag cap | at most max_flags_per_run flags are raised, strongest first, and the memo says so | **PASS** | 2 flags raised, 2 capped |
| F1 | false alarm | Road-section lots (KRRA) | routine-maintenance contracts for DIFFERENT road sections awarded to one contractor are lots of a programme, not splitting | **FAIL** | FLAGGED (false alarm): 3 awards with different road names ['Ndonyo Uasin - Sereolipi C605', 'DB West - Opiroi C606', 'Routine maintenance Lgoss - Jn Longeiwan C609']; card caveat: 'Several awards to one supplier can be separate lots, section...' |
| F2 | false alarm | Repeat supply under a framework (KPLC) | repeat supply of tools and equipment to one supplier may be call-offs under a framework agreement | **FAIL** | FLAGGED (false-alarm risk): 4 awards from Kenya Power & Lighting Company to Pureplus Solutions Limited within 15 days, each just below KES; the tool cannot see a framework agreement |
| F3 | false alarm | Direct procurement by a single-source buyer (Kenya Airport Authority) | a high direct-procurement share can be lawful (emergency, single source) | **FAIL** | FLAGGED (4.68x the baseline); caveat on the flag: True |

**What the failures mean.** F1-F3 show the limit of rule-based detection on this data: the awards really do match the rules (several awards to one contractor within 30 days under a threshold; a high direct-procurement share), and nothing in the published data says whether the awards are separate road sections, call-offs of a framework or an emergency. The system does what it can: it raises the flag, states on the card the lawful explanations to rule out (`flag_caveats` in `rules.py`), and leaves the decision to a human. Whether the expectation in each F case is right is itself a hypothesis for a practitioner to confirm (`docs/user_engagement.md`).

## 2. The intentional failure: "hello" produced 40 flags

The request `hello` (no buyer, supplier or period) made the Day 4 agent scan all of Kenya and raise **40 flags in 53 tool calls**; the local model had invented the years 2022-2024. Remediation (Day 5): the scope is grounded in the request text, a request with no scope ends with a question and **runs no tool**, and at most 10 flags are raised per run. Re-run for real on the same model: **0 tool calls, 0 flags, 1,264 tokens**. Full before/after, with the tests that pin it: [`evals/intentional_failure.md`](evals/intentional_failure.md) (agent test: case A2 above).

## 3. What the cases do not show

* The agent cases use a scripted model, so they cannot say how often a real model plans well; section 4 measures that on two requests only.
* There is **no ground truth** for 'a real problem': positives and negatives are *rule* positives and negatives chosen from the data, not audit findings.
* Thresholds are provisional; changing one changes these results (a test keeps `docs/red_flags.md` in step with `rules.py`).

## 4. Run-to-run variance

Two requests, each run repeatedly with the real model, no human review step:

* **Q1**: Review Makueni County Government awards 2022-2023 for splitting and price anomalies
* **Q2**: Check Kenya Rural Roads Authority for non-competitive procurement and supplier concentration in 2023

### Ollama (`qwen2.5:7b`)

| Query | Runs (failed) | Identical plan (tool set) | Distinct plans | Flagged-award overlap (mean Jaccard) | Flags min / median / max | Tokens min / median / max | Seconds min / median / max | Est. cost USD min / median / max |
|---|---|---|---|---|---|---|---|---|
| Q1 | 5 (0) | 100% | 1 | 1.0 | 8 / 8 / 8 | 3783 / 3783 / 3787 | 233.6 / 235.3 / 397.5 | 0.0000 / 0.0000 / 0.0000 |
| Q2 | 5 (0) | 100% | 1 | 1.0 | 1 / 1 / 1 | 2109 / 2109 / 2216 | 96.4 / 98 / 202.9 | 0.0000 / 0.0000 / 0.0000 |

Per run:

| Query | Run | Plan (tools) | Tool calls | Flags (high/med/low) | Tokens | Seconds | Fallbacks |
|---|---|---|---|---|---|---|---|
| Q1 | 1 | search_awards, splitting, price_anomalies | 16 | 8 (3/5/0) | 3787 | 397.5 | 0 |
| Q2 | 1 | search_awards, noncompetitive_method, supplier_concentration | 7 | 1 (0/1/0) | 2216 | 202.9 | 0 |
| Q1 | 2 | search_awards, splitting, price_anomalies | 16 | 8 (3/5/0) | 3783 | 252.5 | 0 |
| Q2 | 2 | search_awards, noncompetitive_method, supplier_concentration | 7 | 1 (0/1/0) | 2109 | 98.0 | 0 |
| Q1 | 3 | search_awards, splitting, price_anomalies | 16 | 8 (3/5/0) | 3783 | 235.3 | 0 |
| Q2 | 3 | search_awards, noncompetitive_method, supplier_concentration | 7 | 1 (0/1/0) | 2109 | 96.4 | 0 |
| Q1 | 4 | search_awards, splitting, price_anomalies | 16 | 8 (3/5/0) | 3783 | 235.1 | 0 |
| Q2 | 4 | search_awards, noncompetitive_method, supplier_concentration | 7 | 1 (0/1/0) | 2109 | 97.0 | 0 |
| Q1 | 5 | search_awards, splitting, price_anomalies | 16 | 8 (3/5/0) | 3783 | 233.6 | 0 |
| Q2 | 5 | search_awards, noncompetitive_method, supplier_concentration | 7 | 1 (0/1/0) | 2109 | 99.5 | 0 |

### Groq (`qwen/qwen3.8-27b`)

| Query | Runs (failed) | Identical plan (tool set) | Distinct plans | Flagged-award overlap (mean Jaccard) | Flags min / median / max | Tokens min / median / max | Seconds min / median / max | Est. cost USD min / median / max |
|---|---|---|---|---|---|---|---|---|
| Q1 | 2 (0) | 100% | 1 | 1.0 | 4 / 4 / 4 | 4256 / 4259 / 4262 | 89.5 / 100.15 / 110.8 | 0.0039 / 0.0039 / 0.0039 |
| Q2 | 2 (0) | 100% | 1 | 1.0 | 3 / 3 / 3 | 4155 / 4163 / 4171 | 32.2 / 33 / 33.8 | 0.0065 / 0.0066 / 0.0066 |

Per run:

| Query | Run | Plan (tools) | Tool calls | Flags (high/med/low) | Tokens | Seconds | Fallbacks |
|---|---|---|---|---|---|---|---|
| Q1 | 1 | search_awards, splitting, price_anomalies, supplier_concentration, noncompetitive_method | 16 | 4 (3/1/0) | 4256 | 89.5 | 1 |
| Q2 | 1 | search_awards, noncompetitive_method, supplier_concentration, splitting, price_anomalies | 14 | 3 (0/3/0) | 4171 | 32.2 | 0 |
| Q1 | 2 | search_awards, splitting, price_anomalies, supplier_concentration, noncompetitive_method | 16 | 4 (3/1/0) | 4262 | 110.8 | 1 |
| Q2 | 2 | search_awards, noncompetitive_method, supplier_concentration, splitting, price_anomalies | 14 | 3 (0/3/0) | 4155 | 33.8 | 0 |

Reading the tables: *identical plan* = share of runs whose set of planned tools equals the most common set; *overlap* = mean Jaccard similarity of the flagged-award sets between runs (1.0 = the same awards every time). The tools are exact queries and a seeded model, and the language models run at temperature 0, so **run-to-run spread is near zero**: the same plan, the same flagged awards, tokens within a handful. The first Ollama run of each query is slower only because the local model had to load. **The real difference is between models, not between runs:** for the same request Qwen 2.5 7B (Ollama) plans 3 tools and Qwen 3.8 27B (Groq) plans 5, so Groq finds more (for Q2, 3 flags against 1). On Groq every Q1 run logs one fallback: Qwen's summary call returned a malformed tool call five times and gpt-oss-120b answered that one call (cheaper per token, which is why Q1 costs less than Q2 for similar tokens). Samples are small (5 and 2 runs per request): they show the spread, they do not bound it, and temperature 0 does not guarantee identical output on a hosted model that changes under us.

## 5. Cost per run

Prices are the Groq pay-as-you-go list in `rules.py` (retrieved 2026-10-11 (retrieval date; the source page is undated)); the **free tier itself costs nothing**, so the figures are what the same tokens would cost on a paid plan. Ollama runs locally: cost 0 (electricity not counted). Tokens and seconds are medians of the runs in section 4 (each run = plan, investigation, summary; the review step adds about 1,000-1,500 tokens when a flag is queried).

| Provider / model | Runs | Median tokens | Median est. cost per run | Median seconds | Cost for 1,000 runs |
|---|---|---|---|---|---|
| Groq `qwen/qwen3.8-27b` | 4 | 4,214 | $0.0052 | 62 | $5.21 |
| Ollama `qwen2.5:7b` | 10 | 3,000 | $0.0000 | 218 | $0.00 |

