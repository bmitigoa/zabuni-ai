# Zabuni AI — Red-flag rules

Every rule below was chosen **after** profiling the frozen PPRA Kenya OCDS snapshot
(`docs/data_profile.txt`). A rule is only included if the fields it needs are populated.
A flag is an *indicator for human review*, never a finding of wrongdoing, and the agent
never recommends or awards a tender. Every flag must cite `ocid` + `award_id` (+ field/value).

Sources: **PPADA 2015** (Kenya Public Procurement and Asset Disposal Act, 2015, including the
procurement methods in Part VIII and the provision against splitting procurements to avoid
methods or thresholds); **OCP "Red Flags for Integrity: Giving the green light to open data
solutions"** (Open Contracting Partnership, 2016).

> **All thresholds are provisional — to be validated with a procurement practitioner.**
> They are screening defaults, not legal limits. The machine-readable copy lives in
> `mcp_server/rules.py`, is served to the agent as the MCP resource `rules://ppada`, and can be
> overridden with a JSON file (`ZABUNI_RULES_FILE`) or per tool call. Exact PPADA section numbers have
> not been verified against the Act text and are therefore not quoted by the tools.

## Tools (implemented Day 2, `mcp_server/tools.py`)

| # | Tool | Rule | Threshold (provisional default) | OCDS fields used | Source |
|---|---|---|---|---|---|
| 0 | `search_awards` | Exploration, not a flag: find awards by buyer / supplier / title / date and obtain `ocid` + `award_id` | `limit` 1–50 | `buyer.name`, `awards[].suppliers[].name`, `awards[].title`, `contracts[].dateSigned`, `awards[].value.amount` | — |
| 1 | `compute_price_benchmark` | Compare an award with comparable awards (same category, title words, optional buyer type and years); report median, IQR and ratio to median | flag if ratio ≥ **3×** median **and** ≥ **10** comparables; with fewer, return `insufficient_comparables` so the agent widens the search | `tender.title`, `tender.mainProcurementCategory`, `awards[].value.amount`, `contracts[].dateSigned` | OCP red flags (abnormal price); value-for-money principle |
| 2 | `detect_splitting` | Same buyer awards the same supplier ≥ **3** contracts within **30** days, each *just below* an approval threshold T (0.5·T ≤ amount < T), with combined value ≥ T | T ∈ {KES 0.5M, 1M, 2M, 5M, 10M} (**illustrative, unverified**); exact duplicate records (same buyer, supplier, title, amount, date) are collapsed first | `buyer.name`, `awards[].suppliers[].name`, `awards[].title`, `contracts[].dateSigned`, `awards[].value.amount` | PPADA 2015 (anti-splitting provision); OCP red flags |
| 3 | `detect_noncompetitive_method` | Buyer's share of **direct + restricted (`selective`)** awards versus the national baseline (all buyers, same years) | flag if share ≥ **2×** baseline **and** ≥ **10** such awards; ranking mode needs ≥ 20 awards | `tender.procurementMethod` (100 % filled; of awards: open 92.7 %, selective 3.7 %, direct 3.5 %) | PPADA 2015 Part VIII (direct and restricted procurement are exceptions needing justification); OCP red flags |
| 4a | `flag_finding` (write tool, Day 3) | Persists a flag for human review; not a detection rule: it enforces that every flag is sourced | evidence required; every cited award must exist; severity in {low, medium, high}; explanation ≥ 20 characters | `ocid`, `award_id` (from tool evidence) | Hard rule: every finding sourced |
| 4 | `supplier_concentration` | One supplier takes a disproportionate share of a buyer's awards, by count and by value; HHI reported | flag if top supplier ≥ **25 %** by count or value among ≥ **30** supplier-named awards (of 314 eligible buyers: 10 exceed 20 % by count, 4 exceed 30 %, 0 exceed 40 %) | `buyer.name`, `awards[].suppliers[].name`, `awards[].value.amount` | OCP red flags (supplier dominance) |
| 5 | `detect_price_anomalies` (Day 4 stretch) | Isolation Forest on log amount within category cohorts, fixed seed; explanation like "3.2× median of 47 comparables" plus cited comparables | contamination 1 %, `random_state=42` | `awards[].value.amount`, `tender.mainProcurementCategory` | Liu, Ting & Zhou 2008; scikit-learn |

Substantive (write/generate) tools: **`flag_finding`** (Day 3, the first non-read-only tool) persists a sourced flag in a
separate writable `flags` store with status `pending_review`; it **rejects** a flag with no evidence, an evidence item
that is malformed, a cited award that does not exist in the OCDS data, an `ocid` not among the cited evidence, an
unknown `rule` or `severity`, and an explanation that is too short; an identical flag is returned, not duplicated.
`draft_eval_memo` (Day 4) will write the memo from **approved** flags only. Resources: `ocds://release/{ocid}` (the stored
record for citation, rebuilt from the database; no personal fields) and `rules://ppada` (every threshold, limit and
agent policy; the agent reads its own settings from it).

### What a tool returns
Every tool returns `{result, evidence, params_used, warnings}`. `evidence` is a list of
`{ocid, award_id, field, value}` items that point at the OCDS award behind each statement. Bad input
or an empty result never raises: `result` carries an `error` message (with candidate names for unknown or
ambiguous buyers) and `warnings` explains what to try. A flag without evidence is never emitted.

Detection tools also put a uniform `flags` list in `result` (each `{rule, summary, severity_hint, ocids, evidence}`), so the
agent can collect candidate flags without knowing each tool's result shape; the evidence in a flag is a subset of the
evidence the tool returned. `compute_price_benchmark` additionally returns machine-readable `widen_options`
(`{op: drop|set, arg, [value]}`) when it has too few comparables, which the agent applies generically to recover.

## Rules considered and DROPPED (data does not support them)

| Planned check | Why dropped |
|---|---|
| `award_vs_tender_gap` | `tender.value` is absent (0 % filled), and `contracts[].value` equals `awards[].value` in 109,216 of 109,216 matched cases, so there is no independent second value to compare. |
| Single-bidder / few-bidders | No `tenderers` and no `numberOfTenderers` in the snapshot (0 %). Replaced by the procurement-method check (#3), as CLAUDE.md allows. |
| Short bidding window | `tenderPeriod.endDate < startDate` in 44 % of releases (114,835 of 260,499): `startDate` looks like a publication/upload timestamp, not the opening of bidding. Unreliable, so not used. |
| Award-date / late-award checks | `awards[].date` is absent. We derive a date from `contracts[].dateSigned` (fallback `awards[].contractPeriod.startDate`), but it is a signing date, not an award decision date. |
| Item-level unit-price comparison | No item data; benchmarks compare whole-award amounts within title/category cohorts only. |
| Inclusion & local economy theme (AGPO / reservations) | No AGPO or reservation fields exist in the snapshot, so the theme stays **Value for money**. |

## Data cleaning rules (applied before any statistic)

- **Dates:** `award_date` outside 2018–2026 (32 awards, e.g. years `24`, `204`) is set to NULL in the
  `award_facts` view; the award is kept, but excluded from any year/date filter. About 1.8 % of awards have no date.
- **Amounts:** amounts `<= 0` (1,153) or above KES 10 billion (12) are probable data-entry errors. They stay visible
  in `search_awards` (marked `amount_valid=false`) but are excluded from every statistic. Tiny positive amounts
  (e.g. KES 1) are *not* removed; medians and quartiles are used because they tolerate them.
- **Names:** organisations are matched on **normalised names** (`buyer_norm`, `supplier_norm`); IDs are unreliable.
  A hyphenated `Co-` prefix is kept inside its word (`Co-operative` becomes `COOPERATIVE`, `Co-ordination` becomes
  `COORDINATION`; previously `OPERATIVE` / `ORDINATION`, which changed 4 buyer and 11 supplier names), while a standalone
  `Co` / `& Co.` is still dropped as a company-form word.
  A buyer must resolve to exactly one normalised name (exact match, else a unique substring match); otherwise the tool
  returns candidates. Supplier names are missing on ~27 % of awards (79,924 of 109,237 have one), so concentration and
  splitting results describe only awards with a named supplier, and every result says so in `warnings`.
- **Duplicate publications:** the same contract is sometimes published under several `ocid`s. `detect_splitting`
  collapses exact duplicates (same buyer, supplier, title, amount, date) before clustering (846 records across the
  whole dataset) so they are not mistaken for split awards.
- **Buyer type:** `buyer_type` (education, health, county_government, state_agency, state_corporation, other) is a
  keyword heuristic on the raw buyer name, used only to form comparable cohorts in price benchmarks. The ordered keyword
  lists live in `mcp_server/rules.py` (`buyer_types`), the loader builds the column from them, and changing them needs
  `python data/loader.py` to be re-run. The valid year window (`data_cleaning.min_year` / `max_year`) has the same single
  source.
- Only company/organisation names are loaded. Contact points, emails, phone numbers and the names of individuals are
  never read.

## Known limitations of the rules
- Splitting cannot confirm the awards were for the same goods (no item data) and a same-day batch of awards to one
  supplier (e.g. several boreholes) can be legitimate. Reviewers must read the titles.
- Value-share concentration can be driven by a single very large contract; the result lists the award so a reviewer can see it.
- The national baseline for non-competitive methods includes the buyer being tested.
- Thresholds have not been calibrated against ground truth; there are no labelled corruption cases in the data.
