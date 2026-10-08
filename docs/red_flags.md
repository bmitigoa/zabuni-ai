# Zabuni AI — Red-flag rules

Every rule below was chosen **after** profiling the frozen PPRA Kenya OCDS snapshot
(`docs/data_profile.txt`). A rule is only included if the fields it needs are populated.
A flag is an *indicator for human review*, never a finding of wrongdoing, and the agent
never recommends or awards a tender. Every flag must cite `ocid` + `award_id` (+ field/value).

Sources: **PPADA 2015** (Kenya Public Procurement and Asset Disposal Act, 2015, including the
procurement methods in Part VIII and the provision against splitting procurements to avoid
methods or thresholds); **OCP "Red Flags for Integrity: Giving the green light to open data
solutions"** (Open Contracting Partnership, 2016).
Thresholds are *screening defaults* to be calibrated on Day 2 against the data and with a
procurement officer; they are parameters of the tools, not legal limits. Exact PPADA section
numbers will be verified against the Act text on Day 2 before being cited in tool output.

## Tools the data supports (final list)

| # | Tool (Day 2/4) | Rule | Threshold (default) | OCDS fields used | Source |
|---|---|---|---|---|---|
| 1 | `detect_splitting` | Same buyer awards ≥3 contracts to the same supplier within 30 days with combined value far above a typical single award, suggesting one purchase split to stay under approval thresholds | ≥3 awards per buyer+supplier per 30 days; flag if combined value > 2× the cohort 90th-percentile single award | `buyer.name`, `awards[].suppliers[].name`, `contracts[].dateSigned`, `awards[].value.amount` | PPADA 2015 (anti-splitting); OCP red flags |
| 2 | `supplier_concentration` | One supplier takes a disproportionate share of a buyer's awards | buyer has ≥30 supplier-named awards and top supplier ≥25 % of count or value (of 314 eligible buyers: 10 exceed 20 %, 4 exceed 30 %, 0 exceed 40 % by count — tune Day 2) | `buyer.name`, `awards[].suppliers[].name`, `awards[].value.amount` | OCP red flags (supplier dominance) |
| 3 | `compute_price_benchmark` | Compare an award with comparable awards (same category, similar title text, same buyer type); report ratio to cohort median | flag ratio ≥3× median; needs ≥10 comparables, otherwise the agent widens category/time window (recovery path) | `tender.title`, `tender.mainProcurementCategory`, `awards[].value.amount`, `contracts[].dateSigned` | OCP red flags (abnormal price); value-for-money principle in PPADA 2015 |
| 4 | `detect_noncompetitive_method` | Buyer's share of `direct` procurement versus peers | direct share > 2× peer median and ≥10 direct awards | `tender.procurementMethod` (100 % filled; of awards: open 92.7 %, selective 3.7 %, direct 3.5 %) | PPADA 2015 Part VIII (direct procurement is an exception needing justification); OCP red flags |
| 5 | `detect_price_anomalies` (Day 4 stretch) | Isolation Forest on log amount within category cohorts, fixed seed; returns an explanation like "3.2× median of 47 comparables" plus the cited comparables | contamination 1 %, `random_state=42` | `awards[].value.amount`, `tender.mainProcurementCategory` | Liu, Ting & Zhou 2008 (Isolation Forest); scikit-learn |

Substantive (write/generate) tools, Day 4: `flag_finding` (persist a sourced flag to DuckDB) and
`draft_eval_memo` (memo from **approved** flags only). Resources: `ocds://release/{ocid}`,
`rules://ppada`.

## Rules considered and DROPPED (data does not support them)

| Planned check | Why dropped |
|---|---|
| `award_vs_tender_gap` | `tender.value` is absent (0 % filled), and `contracts[].value` equals `awards[].value` in 109,216 of 109,216 matched cases, so there is no independent second value to compare. |
| Single-bidder / few-bidders | No `tenderers` and no `numberOfTenderers` in the snapshot (0 %). Replaced by the procurement-method check (#4), as CLAUDE.md allows. |
| Short bidding window | `tenderPeriod.endDate < startDate` in 44 % of releases (114,835 of 260,499): `startDate` looks like a publication/upload timestamp, not the opening of bidding. Unreliable, so not used. |
| Award-date / late-award checks | `awards[].date` is absent. We derive a date from `contracts[].dateSigned` (fallback `awards[].contractPeriod.startDate`; 98.2 % filled), but it is a signing date, not an award decision date. |
| Item-level unit-price comparison | No item data; benchmarks compare whole-award amounts within title/category cohorts only. |
| Inclusion & local economy theme (AGPO / reservations) | No AGPO or reservation fields exist in the snapshot, so the theme stays **Value for money**. |

## Data cleaning rules (applied before any statistic)

- Exclude awards with `amount <= 0` (1,153) and treat amounts above KES 10 billion (12) as probable
  data-entry errors: keep them visible in the record but exclude them from benchmarks.
- Ignore award dates outside 2018–2026 (a handful, e.g. years `24`, `204`) in time windows.
- Match organisations on **normalised names** (`buyer_norm`, `supplier_norm`); IDs are unreliable.
  Supplier names are missing on ~27 % of awards (79,924 of 109,237 have one), so concentration
  and splitting results describe only awards with a named supplier, and say so.
- Only company/organisation names are loaded. Contact points, emails, phone numbers and
  the names of individuals are never read.
