# Draft investigation summary: Review Makueni County Government awards 2022-2023 for splitting and price anomalies

**DRAFT: pending human review.** Everything below is a screening indicator produced from published OCDS data. No flag is a finding of wrongdoing, and nothing here recommends or awards a tender; the committee decides.

Run `run_20261011_160025_2ff7` | model `ollama:qwen2.5:7b` | 2 LLM calls, 3787 tokens | 16 tool calls | 390 s

## Summary

The data shows multiple instances of potential overpricing and splitting of contracts by Makueni County Government, with some awards significantly exceeding the median price. These findings suggest possible irregularities in the procurement process, but further investigation is needed to confirm any impropriety. The severity of the issues varies, with some being more severe than others, but all

## Plan

Objective: Review Makueni County Government awards 2022-2023 for splitting and price anomalies

1. `search_awards` {"buyer": "Makueni County Government", "date_from": "2022-01-01", "date_to": "2023-12-31", "limit": 100, "sort_by": "newest"} — Find relevant awards
2. `detect_splitting` {"buyer": "Makueni County Government", "window_days": 30, "min_awards": 2, "thresholds_kes": [1000000], "years": [2022, 2023], "limit": 50} — Check for possible contract splitting
3. `detect_price_anomalies` {"buyer": "Makueni County Government", "category": "works", "buyer_type": "county_government", "years": [2022, 2023], "limit": 20} — Identify price anomalies in works

## Investigation log

- `search_awards` {"buyer": "Makueni County Government", "date_from": "2022-01-01", "date_to": "2023-12-31", "limit": 100, "sort_by": "newest"} (plan) → ok; 50 evidence items, 0 flag(s)
- `compute_price_benchmark` {"ocid": "ocds-5whusi-GMC/K/D/056/2023/2024", "award_id": "20240703171733-91111"} (follow-up of search_awards) → ok; 11 evidence items, 1 flag(s)
- `detect_splitting` {"buyer": "Makueni County Government", "years": [2022, 2023]} (follow-up of compute_price_benchmark) → ok; 12 evidence items, 2 flag(s)
- `supplier_concentration` {"buyer": "Makueni County Government", "years": [2022, 2023]} (follow-up of compute_price_benchmark) → ok; 14 evidence items, 0 flag(s)
- `compute_price_benchmark` {"ocid": "ocds-5whusi-1366280-23/24", "award_id": "20240708123046-91956"} (follow-up of search_awards) → ok; 11 evidence items, 0 flag(s)
- `compute_price_benchmark` {"ocid": "ocds-5whusi-1366287-2023/24", "award_id": "20240709214629-92543"} (follow-up of search_awards) → ok; 11 evidence items, 0 flag(s)
- `detect_splitting` {"buyer": "Makueni County Government", "window_days": 30, "min_awards": 2, "thresholds_kes": [1000000], "years": [2022, 2023], "limit": 50} (plan) → ok; 16 evidence items, 4 flag(s)
- `detect_price_anomalies` {"buyer": "Makueni County Government", "category": "works", "buyer_type": "county_government", "years": [2022, 2023], "limit": 20} (plan) → ok; 6 evidence items, 1 flag(s)

### Follow-ups, recoveries and problems

- follow-up after search_awards: compute_price_benchmark {"ocid": "ocds-5whusi-GMC/K/D/056/2023/2024", "award_id": "20240703171733-91111"} (price-check one of the awards just found)
- follow-up after search_awards: compute_price_benchmark {"ocid": "ocds-5whusi-1366280-23/24", "award_id": "20240708123046-91956"} (price-check one of the awards just found)
- follow-up after search_awards: compute_price_benchmark {"ocid": "ocds-5whusi-1366287-2023/24", "award_id": "20240709214629-92543"} (price-check one of the awards just found)
- follow-up after compute_price_benchmark: detect_splitting {"buyer": "Makueni County Government", "years": [2022, 2023]} (price outlier: check whether this buyer split awards to this supplier)
- follow-up after compute_price_benchmark: supplier_concentration {"buyer": "Makueni County Government", "years": [2022, 2023]} (price outlier: check this buyer's dependence on suppliers)

## Flags (all pending human review)

### flag_a2a26a526bd7 — compute_price_benchmark — severity high — status pending_review

SUPPLY AND DELIVERY OF STEEL DRUMS (ocds-5whusi-GMC/K/D/056/2023/2024): 22.5x median of 10 comparable awards (median KES 395,860)

Cited OCDS records:
- ocid `ocds-5whusi-GMC/K/D/056/2023/2024`, award `20240703171733-91111`, award_amount = 8916920.0
- ocid `ocds-5whusi-063761`, award `20250205150148-18687`, award_amount = 362520.0
- ocid `ocds-5whusi-KMC/RTO/012057/2022`, award `20250205150254-31542`, award_amount = 429200.0
- ocid `ocds-5whusi-225224-KLB/RFQ/08/2024-2025`, award `20250209122932-115709`, award_amount = 333499.95
- ocid `ocds-5whusi-225224-KLB/RFQ/08/2024-2025`, award `20250209123808-115711`, award_amount = 190000.0
- … and 6 more evidence item(s) stored with the flag

### flag_a68bbc6710ef — detect_splitting — severity high — status pending_review

3 awards from Makueni County Government to DOUBLE KEY SYSTEMS LTD within 13 days, each just below KES 5,000,000, totalling KES 11,859,448 (2.37x the threshold)

Cited OCDS records:
- ocid `ocds-5whusi-1177969`, award `20250205150309-43082`, award_amount = 4761539.0
- ocid `ocds-5whusi-1177969`, award `20250205150309-43082`, contract_date_signed = 2023-03-23
- ocid `ocds-5whusi-1212735`, award `20250205150330-52396`, award_amount = 3499126.0
- ocid `ocds-5whusi-1212735`, award `20250205150330-52396`, contract_date_signed = 2023-04-03
- ocid `ocds-5whusi-1223916-2022/2023`, award `20250205150317-42727`, award_amount = 3598783.2
- … and 1 more evidence item(s) stored with the flag

### flag_d55995e58fe4 — detect_splitting — severity medium — status pending_review

3 awards from Makueni County Government to WAVECREST LIMITED within 23 days, each just below KES 500,000, totalling KES 987,070 (1.97x the threshold)

Cited OCDS records:
- ocid `ocds-5whusi-043/2022/2023`, award `20250205150331-52488`, award_amount = 385058.14
- ocid `ocds-5whusi-043/2022/2023`, award `20250205150331-52488`, contract_date_signed = 2023-01-31
- ocid `ocds-5whusi-307A(1)/2022/2023`, award `20250205150331-52493`, award_amount = 296087.25
- ocid `ocds-5whusi-307A(1)/2022/2023`, award `20250205150331-52493`, contract_date_signed = 2023-02-07
- ocid `ocds-5whusi-GMC/GVN/Q/022/2022/2023`, award `20250205150330-52114`, award_amount = 305924.76
- … and 1 more evidence item(s) stored with the flag

### flag_dc42d94372fe — detect_splitting — severity medium — status pending_review

2 awards from Makueni County Government to SEMA PROPERTIES K LIMITED within 20 days, each just below KES 1,000,000, totalling KES 1,932,200 (1.93x the threshold)

Cited OCDS records:
- ocid `ocds-5whusi-1166469-3`, award `20250205150330-52920`, award_amount = 934000.0
- ocid `ocds-5whusi-1166469-3`, award `20250205150330-52920`, contract_date_signed = 2023-03-14
- ocid `ocds-5whusi-1222048`, award `20250205150330-52301`, award_amount = 998200.0
- ocid `ocds-5whusi-1222048`, award `20250205150330-52301`, contract_date_signed = 2023-04-03

### flag_6761cb47d476 — detect_splitting — severity medium — status pending_review

2 awards from Makueni County Government to STANMAX ENTERPRISES within 16 days, each just below KES 1,000,000, totalling KES 1,877,991 (1.88x the threshold)

Cited OCDS records:
- ocid `ocds-5whusi-239923-2022/2023`, award `20250205150329-51767`, award_amount = 999991.0
- ocid `ocds-5whusi-239923-2022/2023`, award `20250205150329-51767`, contract_date_signed = 2023-05-24
- ocid `ocds-5whusi-GMC/W/Q/139/2022/2023`, award `20250205150330-52275`, award_amount = 878000.0
- ocid `ocds-5whusi-GMC/W/Q/139/2022/2023`, award `20250205150330-52275`, contract_date_signed = 2023-06-09

### flag_73b9760e095e — detect_splitting — severity medium — status pending_review

2 awards from Makueni County Government to VEMCO ENTERPRISES LIMITED within 10 days, each just below KES 1,000,000, totalling KES 1,493,143 (1.49x the threshold)

Cited OCDS records:
- ocid `ocds-5whusi-1294861-2022/2023`, award `20250205150323-50936`, award_amount = 878347.52
- ocid `ocds-5whusi-1294861-2022/2023`, award `20250205150323-50936`, contract_date_signed = 2023-06-19
- ocid `ocds-5whusi-GMC/H/Q/86/1310033/2022-2023`, award `20250205150328-51139`, award_amount = 614795.0
- ocid `ocds-5whusi-GMC/H/Q/86/1310033/2022-2023`, award `20250205150328-51139`, contract_date_signed = 2023-06-29

### flag_e9bfd99f8818 — detect_splitting — severity medium — status pending_review

2 awards from Makueni County Government to TROLLEX GENERAL SUPPLIERS within 27 days, each just below KES 1,000,000, totalling KES 1,408,930 (1.41x the threshold)

Cited OCDS records:
- ocid `ocds-5whusi-GMC/K/Q/080/2023/2024`, award `20240213144738-72478`, award_amount = 749000.0
- ocid `ocds-5whusi-GMC/K/Q/080/2023/2024`, award `20240213144738-72478`, contract_date_signed = 2023-11-17
- ocid `ocds-5whusi-GMC/K/Q/151/2023/2024`, award `20240206125130-71738`, award_amount = 659930.0
- ocid `ocds-5whusi-GMC/K/Q/151/2023/2024`, award `20240206125130-71738`, contract_date_signed = 2023-12-14

### flag_131b8ecc7b41 — detect_price_anomalies — severity high — status pending_review

PROVISION OF STAFF MEDICAL COVER (ocds-5whusi-42686): 71.0x median of 4659 comparable awards (median KES 2,780,390)

Cited OCDS records:
- ocid `ocds-5whusi-42686`, award `20250205150132-43031`, award_amount = 197527657.0
- ocid `ocds-5whusi-BGM/OT/1166912/2022-2023`, award `20250205150306-41545`, award_amount = 2781500.0
- ocid `ocds-5whusi-CGOKTI/ETSD/Q012/2023-2024`, award `20240118111023-70239`, award_amount = 2779280.0
- ocid `ocds-5whusi-CGN/EVIE/ONT/048/2022-2023`, award `20250205150308-57375`, award_amount = 2779040.0
- ocid `ocds-5whusi-CGN/MOH/ONT/012/2022-2023`, award `20240215161426-72777`, award_amount = 2783570.8
- … and 1 more evidence item(s) stored with the flag

## Dropped by the citation guard

- no tool-returned evidence supports this finding (ocds-5whusi-GMC/K/D/056/2023/2024)
- no tool-returned evidence supports this finding (ocds-5whusi-1177969, ocds-5whusi-1212735, ocds-5whusi-1223916-2022/2023)
- no tool-returned evidence supports this finding (ocds-5whusi-42686, ocds-5whusi-BGM/OT/1166912/2022-2023, ocds-5whusi-CGN/EVIE/ONT/048/2022-2023, ocds-5whusi-CGN/MOH/ONT/012/2022-2023)
- no tool-returned evidence supports this finding (ocds-5whusi-043/2022/2023, ocds-5whusi-307A(1)/2022/2023, ocds-5whusi-GMC/GVN/Q/022/2022/2023)
- no tool-returned evidence supports this finding (ocds-5whusi-1166469-3, ocds-5whusi-1222048)
- no tool-returned evidence supports this finding (ocds-5whusi-239923-2022/2023, ocds-5whusi-GMC/W/Q/139/2022/2023)
- no tool-returned evidence supports this finding (ocds-5whusi-1294861-2022/2023, ocds-5whusi-GMC/H/Q/86/1310033/2022-2023)
- no tool-returned evidence supports this finding (ocds-5whusi-GMC/K/Q/080/2023/2024, ocds-5whusi-GMC/K/Q/151/2023/2024)

## Limitations

- Bid documents are not published, so bid content, scoring and bidder qualifications cannot be assessed.
- The data has no tenderers, tender estimates or item-level detail; price comparisons use title words, category and buyer type only, and a cluster of awards may be genuinely different goods.
- Award dates are contract signing dates; amounts outside the valid range are excluded from statistics.
- Supplier-based checks cover only awards that name a supplier. All thresholds are provisional and unvalidated.
