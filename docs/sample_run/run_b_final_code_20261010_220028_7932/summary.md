# Draft investigation summary: Investigate Makueni County Government 2022-2024

**DRAFT: pending human review.** Everything below is a screening indicator produced from published OCDS data. No flag is a finding of wrongdoing, and nothing here recommends or awards a tender; the committee decides.

Run `run_20261010_220028_7932` | model `groq:qwen/qwen3.8-27b` | 1 LLM calls, 3730 tokens | 19 tool calls | 14 s

## Summary

12 check(s) were run and 7 flag(s) were raised for human review. Each is a screening indicator that may have a legitimate explanation; none is a finding of wrongdoing.

## Plan

Objective: Screen Makueni County Government's 2022-2024 procurement awards for value-for-money and integrity risks (supplier concentration, non-competitive methods, contract splitting, and price outliers).

1. `search_awards` {"buyer": "Makueni County Government", "date_from": "2022-01-01", "date_to": "2024-12-31", "sort_by": "largest", "limit": 20} — Start by surfacing the buyer's largest 2022-2024 awards to establish the award population and identify the highest-value contracts worth deeper checks.
2. `supplier_concentration` {"buyer": "Makueni County Government", "years": [2022, 2023, 2024], "limit": 10} — Check how concentrated the buyer's awards are among suppliers to flag over-reliance on one or a few vendors.
3. `detect_noncompetitive_method` {"buyer": "Makueni County Government", "years": [2022, 2023, 2024], "limit": 10} — Compare the buyer's use of direct/restricted (non-open) procurement against the national baseline to flag unusually high non-competitive sourcing.
4. `detect_splitting` {"buyer": "Makueni County Government", "years": [2022, 2023, 2024], "limit": 10} — Scan for clusters of same-buyer/same-supplier awards just below approval thresholds that may indicate contract splitting.
5. `compute_price_benchmark` {"buyer_type": "county_government", "years": [2022, 2023, 2024], "category": "works"} — Pull the price distribution for county-government works awards to benchmark the largest works contracts found in the search for value-for-money.

## Investigation log

- `search_awards` {"buyer": "Makueni County Government", "date_from": "2022-01-01", "date_to": "2024-12-31", "sort_by": "largest", "limit": 20} (plan) → ok; 20 evidence items, 0 flag(s)
- `compute_price_benchmark` {"ocid": "ocds-5whusi-1678926/2024/2025", "award_id": "20250117124905-113221"} (follow-up of search_awards) → ok; 11 evidence items, 1 flag(s)
- `detect_splitting` {"buyer": "Makueni County Government", "supplier": "MADISON GENERAL INSURANCE KENYA LIMITED", "years": [2022, 2023, 2024]} (follow-up of compute_price_benchmark) → ok; 0 evidence items, 0 flag(s)
- `supplier_concentration` {"buyer": "Makueni County Government", "years": [2022, 2023, 2024]} (follow-up of compute_price_benchmark) → ok; 15 evidence items, 0 flag(s)
- `compute_price_benchmark` {"ocid": "ocds-5whusi-1110244-2-2022/2023", "award_id": "20250205150258-53204"} (follow-up of search_awards) → ok; 11 evidence items, 1 flag(s)
- `detect_splitting` {"buyer": "Makueni County Government", "supplier": "AAR Insurance Kenya Limited", "years": [2022, 2023, 2024]} (follow-up of compute_price_benchmark) → ok; 0 evidence items, 0 flag(s)
- `compute_price_benchmark` {"ocid": "ocds-5whusi-42686", "award_id": "20250205150132-43031"} (follow-up of search_awards) → ok; 11 evidence items, 1 flag(s)
- `detect_splitting` {"buyer": "Makueni County Government", "supplier": "FIRST ASSURANCE COMPANY", "years": [2022, 2023, 2024]} (follow-up of compute_price_benchmark) → ok; 0 evidence items, 0 flag(s)
- `supplier_concentration` {"buyer": "Makueni County Government", "years": [2022, 2023, 2024], "limit": 10} (plan) → ok; 15 evidence items, 0 flag(s)
- `detect_noncompetitive_method` {"buyer": "Makueni County Government", "years": [2022, 2023, 2024], "limit": 10} (plan) → ok; 7 evidence items, 0 flag(s)
- `detect_splitting` {"buyer": "Makueni County Government", "years": [2022, 2023, 2024], "limit": 10} (plan) → ok; 24 evidence items, 4 flag(s)
- `compute_price_benchmark` {"buyer_type": "county_government", "years": [2022, 2023, 2024], "category": "works"} (plan) → ok; 10 evidence items, 0 flag(s)

### Follow-ups, recoveries and problems

- follow-up after search_awards: compute_price_benchmark {"ocid": "ocds-5whusi-1678926/2024/2025", "award_id": "20250117124905-113221"} (price-check one of the awards just found)
- follow-up after search_awards: compute_price_benchmark {"ocid": "ocds-5whusi-1110244-2-2022/2023", "award_id": "20250205150258-53204"} (price-check one of the awards just found)
- follow-up after search_awards: compute_price_benchmark {"ocid": "ocds-5whusi-42686", "award_id": "20250205150132-43031"} (price-check one of the awards just found)
- follow-up after compute_price_benchmark: detect_splitting {"buyer": "Makueni County Government", "supplier": "MADISON GENERAL INSURANCE KENYA LIMITED", "years": [2022, 2023, 2024]} (price outlier: check whether this buyer split awards to this supplier)
- follow-up after compute_price_benchmark: supplier_concentration {"buyer": "Makueni County Government", "years": [2022, 2023, 2024]} (price outlier: check this buyer's dependence on suppliers)
- follow-up after compute_price_benchmark: detect_splitting {"buyer": "Makueni County Government", "supplier": "AAR Insurance Kenya Limited", "years": [2022, 2023, 2024]} (price outlier: check whether this buyer split awards to this supplier)
- follow-up after compute_price_benchmark: detect_splitting {"buyer": "Makueni County Government", "supplier": "FIRST ASSURANCE COMPANY", "years": [2022, 2023, 2024]} (price outlier: check whether this buyer split awards to this supplier)

## Flags (all pending human review)

### flag_cb344cc2cfcd — compute_price_benchmark — severity high — status pending_review

PROVISION OF STAFF MEDICAL INSURANCE COVER (ocds-5whusi-1678926/2024/2025): 7.0x median of 289 comparable awards (median KES 30,137,312)

Cited OCDS records:
- ocid `ocds-5whusi-1678926/2024/2025`, award `20250117124905-113221`, award_amount = 210000000.0
- ocid `ocds-5whusi-CA/BGM/OT/01/2024/ 2025`, award `20250109110600-112043`, award_amount = 30137312.0
- ocid `ocds-5whusi-ENNDA/CONTR/002/2022-2023`, award `20250205150305-38139`, award_amount = 30358527.0
- ocid `ocds-5whusi-068022`, award `20250205150202-21524`, award_amount = 29539901.0
- ocid `ocds-5whusi-7541`, award `20190423104958-3266`, award_amount = 29258798.0
- … and 6 more evidence item(s) stored with the flag

### flag_3575640e7be4 — compute_price_benchmark — severity high — status pending_review

PROVISION OF STAFF MEDICAL INSURANCE COVER  (ocds-5whusi-1110244-2-2022/2023): 6.6x median of 289 comparable awards (median KES 30,137,312)

Cited OCDS records:
- ocid `ocds-5whusi-1110244-2-2022/2023`, award `20250205150258-53204`, award_amount = 198999999.0
- ocid `ocds-5whusi-CA/BGM/OT/01/2024/ 2025`, award `20250109110600-112043`, award_amount = 30137312.0
- ocid `ocds-5whusi-ENNDA/CONTR/002/2022-2023`, award `20250205150305-38139`, award_amount = 30358527.0
- ocid `ocds-5whusi-068022`, award `20250205150202-21524`, award_amount = 29539901.0
- ocid `ocds-5whusi-7541`, award `20190423104958-3266`, award_amount = 29258798.0
- … and 6 more evidence item(s) stored with the flag

### flag_b35ee0827bce — compute_price_benchmark — severity high — status pending_review

PROVISION OF STAFF MEDICAL COVER (ocds-5whusi-42686): 8.7x median of 96 comparable awards (median KES 22,669,244)

Cited OCDS records:
- ocid `ocds-5whusi-42686`, award `20250205150132-43031`, award_amount = 197527657.0
- ocid `ocds-5whusi-23550`, award `20250205150113-39880`, award_amount = 22595837.8
- ocid `ocds-5whusi-46281`, award `20201221125518-15148`, award_amount = 22742650.0
- ocid `ocds-5whusi-8498`, award `20190129160449-3563`, award_amount = 22590956.0
- ocid `ocds-5whusi-45365`, award `20250205150129-41374`, award_amount = 23766338.0
- … and 6 more evidence item(s) stored with the flag

### flag_1869396bbf8e — detect_splitting — severity high — status pending_review

3 awards from Makueni County Government to DOUBLE KEY SYSTEMS LTD within 13 days, each just below KES 5,000,000, totalling KES 11,859,448 (2.37x the threshold)

Cited OCDS records:
- ocid `ocds-5whusi-1177969`, award `20250205150309-43082`, award_amount = 4761539.0
- ocid `ocds-5whusi-1177969`, award `20250205150309-43082`, contract_date_signed = 2023-03-23
- ocid `ocds-5whusi-1212735`, award `20250205150330-52396`, award_amount = 3499126.0
- ocid `ocds-5whusi-1212735`, award `20250205150330-52396`, contract_date_signed = 2023-04-03
- ocid `ocds-5whusi-1223916-2022/2023`, award `20250205150317-42727`, award_amount = 3598783.2
- … and 1 more evidence item(s) stored with the flag

### flag_68b151e133f8 — detect_splitting — severity medium — status pending_review

3 awards from Makueni County Government to VIVIFY1 SPACE LIMITED within 0 days, each just below KES 5,000,000, totalling KES 9,812,108 (1.96x the threshold)

Cited OCDS records:
- ocid `ocds-5whusi-1370851`, award `20240701114937-90406`, award_amount = 3274497.68
- ocid `ocds-5whusi-1370851`, award `20240701114937-90406`, contract_date_signed = 2024-01-08
- ocid `ocds-5whusi-1376731-2023/2024`, award `20240704151103-91339`, award_amount = 3274497.68
- ocid `ocds-5whusi-1376731-2023/2024`, award `20240704151103-91339`, contract_date_signed = 2024-01-08
- ocid `ocds-5whusi-1376731-2023/2024`, award `20240704154013-91351`, award_amount = 3263112.28
- … and 1 more evidence item(s) stored with the flag

### flag_58d1084e7cdb — detect_splitting — severity high — status pending_review

3 awards from Makueni County Government to JUMUKA TRADERS within 4 days, each just below KES 500,000, totalling KES 1,052,120 (2.1x the threshold)

Cited OCDS records:
- ocid `ocds-5whusi-220647-1655874/2024/2025`, award `20250114112111-112725`, award_amount = 313820.0
- ocid `ocds-5whusi-220647-1655874/2024/2025`, award `20250114112111-112725`, contract_date_signed = 2024-10-14
- ocid `ocds-5whusi-220629-1656108/2024/2025`, award `20250114101846-112699`, award_amount = 368500.0
- ocid `ocds-5whusi-220629-1656108/2024/2025`, award `20250114101846-112699`, contract_date_signed = 2024-10-18
- ocid `ocds-5whusi-220666-1656109/2024/2025`, award `20250114120130-112738`, award_amount = 369800.0
- … and 1 more evidence item(s) stored with the flag

### flag_37f23b738b1d — detect_splitting — severity medium — status pending_review

3 awards from Makueni County Government to WAVECREST LIMITED within 23 days, each just below KES 500,000, totalling KES 987,070 (1.97x the threshold)

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
