# Draft investigation summary: Investigate Makueni County Government 2022-2024

**DRAFT: pending human review.** Everything below is a screening indicator produced from published OCDS data. No flag is a finding of wrongdoing, and nothing here recommends or awards a tender; the committee decides.

Run `run_20261010_204919_4a7e` | model `groq:qwen/qwen3.8-27b` | 2 LLM calls, 5965 tokens | 11 tool calls | 30 s

## Summary

The screening flagged four sets of multiple Makueni County Government awards to the same supplier, each cluster consisting of three awards just below a procurement threshold and totalling roughly 2x that threshold. The clusters span 2022/2023 to 2024/2025 and involve different suppliers and thresholds. These are timing and value patterns only; the data contains no item-level detail, so each cluster may have a legitimate explanation. Price-benchmark follow-ups were run on separate awards and,

## Plan

Objective: Screen Makueni County Government procurement awards from 2022 to 2024 for value-for-money and integrity risks.

1. `search_awards` {"buyer": "Makueni County Government", "date_from": "2022-01-01", "date_to": "2024-12-31"} — Explore the buyer's award universe and surface the largest awards to anchor later price checks.
2. `supplier_concentration` {"buyer": "Makueni County Government", "years": [2022, 2023, 2024]} — Check whether awards are concentrated among a few suppliers, a common integrity red flag.
3. `detect_noncompetitive_method` {"buyer": "Makueni County Government", "years": [2022, 2023, 2024]} — Compare the buyer's reliance on non-open (direct/restricted) procurement against the national baseline.
4. `detect_splitting` {"buyer": "Makueni County Government", "years": [2022, 2023, 2024]} — Look for possible contract splitting: repeated awards to the same supplier just below approval thresholds.

## Investigation log

- `search_awards` {"buyer": "Makueni County Government", "date_from": "2022-01-01", "date_to": "2024-12-31"} (plan) → ok; 20 evidence items, 0 flag(s)
- `compute_price_benchmark` {"ocid": "ocds-5whusi-220180-1683083", "award_id": "20250512144404-123719"} (follow-up of search_awards) → ok; 11 evidence items, 0 flag(s)
- `compute_price_benchmark` {"ocid": "ocds-5whusi-220180-1683083", "award_id": "20250116110051-113051"} (follow-up of search_awards) → ok; 11 evidence items, 0 flag(s)
- `compute_price_benchmark` {"ocid": "ocds-5whusi-220183-1683074", "award_id": "20250115120241-112897"} (follow-up of search_awards) → ok; 11 evidence items, 0 flag(s)
- `supplier_concentration` {"buyer": "Makueni County Government", "years": [2022, 2023, 2024]} (plan) → ok; 15 evidence items, 0 flag(s)
- `detect_noncompetitive_method` {"buyer": "Makueni County Government", "years": [2022, 2023, 2024]} (plan) → ok; 7 evidence items, 0 flag(s)
- `detect_splitting` {"buyer": "Makueni County Government", "years": [2022, 2023, 2024]} (plan) → ok; 24 evidence items, 4 flag(s)

### Follow-ups, recoveries and problems

- follow-up after search_awards: compute_price_benchmark {"ocid": "ocds-5whusi-220180-1683083", "award_id": "20250512144404-123719"} (price-check one of the awards just found)
- follow-up after search_awards: compute_price_benchmark {"ocid": "ocds-5whusi-220180-1683083", "award_id": "20250116110051-113051"} (price-check one of the awards just found)
- follow-up after search_awards: compute_price_benchmark {"ocid": "ocds-5whusi-220183-1683074", "award_id": "20250115120241-112897"} (price-check one of the awards just found)

## Flags (all pending human review)

### flag_a6b46397c8ba — detect_splitting — severity high — status pending_review

Three Makueni County awards to DOUBLE KEY SYSTEMS LTD within 13 days, each just under KES 5,000,000, totalling KES 11,859,448, about 2.37x the threshold.

Cited OCDS records:
- ocid `ocds-5whusi-1177969`, award `20250205150309-43082`, award_amount = 4761539.0
- ocid `ocds-5whusi-1177969`, award `20250205150309-43082`, contract_date_signed = 2023-03-23
- ocid `ocds-5whusi-1212735`, award `20250205150330-52396`, award_amount = 3499126.0
- ocid `ocds-5whusi-1212735`, award `20250205150330-52396`, contract_date_signed = 2023-04-03
- ocid `ocds-5whusi-1223916-2022/2023`, award `20250205150317-42727`, award_amount = 3598783.2
- … and 1 more evidence item(s) stored with the flag

### flag_c6452ce7831e — detect_splitting — severity high — status pending_review

Three Makueni County awards to JUMUKA TRADERS within 4 days, each just under KES 500,000, totalling KES 1,052,120, about 2.1x the threshold.

Cited OCDS records:
- ocid `ocds-5whusi-220647-1655874/2024/2025`, award `20250114112111-112725`, award_amount = 313820.0
- ocid `ocds-5whusi-220647-1655874/2024/2025`, award `20250114112111-112725`, contract_date_signed = 2024-10-14
- ocid `ocds-5whusi-220629-1656108/2024/2025`, award `20250114101846-112699`, award_amount = 368500.0
- ocid `ocds-5whusi-220629-1656108/2024/2025`, award `20250114101846-112699`, contract_date_signed = 2024-10-18
- ocid `ocds-5whusi-220666-1656109/2024/2025`, award `20250114120130-112738`, award_amount = 369800.0
- … and 1 more evidence item(s) stored with the flag

### flag_c2f973ceb60c — detect_splitting — severity medium — status pending_review

Three Makueni County awards to VIVIFY1 SPACE LIMITED on the same day, each just under KES 5,000,000, totalling KES 9,812,108, about 1.96x the threshold.

Cited OCDS records:
- ocid `ocds-5whusi-1370851`, award `20240701114937-90406`, award_amount = 3274497.68
- ocid `ocds-5whusi-1370851`, award `20240701114937-90406`, contract_date_signed = 2024-01-08
- ocid `ocds-5whusi-1376731-2023/2024`, award `20240704151103-91339`, award_amount = 3274497.68
- ocid `ocds-5whusi-1376731-2023/2024`, award `20240704151103-91339`, contract_date_signed = 2024-01-08
- ocid `ocds-5whusi-1376731-2023/2024`, award `20240704154013-91351`, award_amount = 3263112.28
- … and 1 more evidence item(s) stored with the flag

### flag_f549f8370d3f — detect_splitting — severity medium — status pending_review

Three Makueni County awards to WAVECREST LIMITED within 23 days, each just under KES 500,000, totalling KES 987,070, about 1.97x the threshold.

Cited OCDS records:
- ocid `ocds-5whusi-043/2022/2023`, award `20250205150331-52488`, award_amount = 385058.14
- ocid `ocds-5whusi-043/2022/2023`, award `20250205150331-52488`, contract_date_signed = 2023-01-31
- ocid `ocds-5whusi-307A(1)/2022/2023`, award `20250205150331-52493`, award_amount = 296087.25
- ocid `ocds-5whusi-307A(1)/2022/2023`, award `20250205150331-52493`, contract_date_signed = 2023-02-07
- ocid `ocds-5whusi-GMC/GVN/Q/022/2022/2023`, award `20250205150330-52114`, award_amount = 305924.76
- … and 1 more evidence item(s) stored with the flag

## Limitations

- Bid documents are not published, so bid content, scoring and bidder qualifications cannot be assessed.
- The data has no tenderers, tender estimates or item-level detail; price comparisons use title words, category and buyer type only, and a cluster of awards may be genuinely different goods.
- Award dates are contract signing dates; amounts outside the valid range are excluded from statistics.
- Supplier-based checks cover only awards that name a supplier. All thresholds are provisional and unvalidated.
