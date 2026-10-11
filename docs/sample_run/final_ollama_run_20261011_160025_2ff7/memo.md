# Evaluation memo: Review Makueni County Government awards 2022-2023 for splitting and price anomalies

> This memo does not recommend or award any tender. Every finding is a screening indicator from published OCDS data, approved by a human reviewer, and may have a legitimate explanation.

Date: 2026-10-11 | Run: `run_20261011_160025_2ff7`

## Summary

4 finding(s) were approved by human reviewers out of 8 flag(s) considered (4 rejected, 0 not yet decided; those are not part of this memo). Approved findings by check: compute_price_benchmark x1, detect_price_anomalies x1, detect_splitting x2.

## Approved findings

### 1. compute_price_benchmark (severity high), flag `flag_a2a26a526bd7`

SUPPLY AND DELIVERY OF STEEL DRUMS (ocds-5whusi-GMC/K/D/056/2023/2024): 22.5x median of 10 comparable awards (median KES 395,860)

Cited OCDS records:
- ocid `ocds-5whusi-GMC/K/D/056/2023/2024`, award `20240703171733-91111`, award_amount = 8916920.0
- ocid `ocds-5whusi-063761`, award `20250205150148-18687`, award_amount = 362520.0
- ocid `ocds-5whusi-KMC/RTO/012057/2022`, award `20250205150254-31542`, award_amount = 429200.0
- ocid `ocds-5whusi-225224-KLB/RFQ/08/2024-2025`, award `20250209122932-115709`, award_amount = 333499.95
- ocid `ocds-5whusi-225224-KLB/RFQ/08/2024-2025`, award `20250209123808-115711`, award_amount = 190000.0
- ocid `ocds-5whusi-HBCA/PROC/RFQ/G9/2022-20243`, award `20240219101205-72990`, award_amount = 620000.0
- ocid `ocds-5whusi-HBCA/PROC/RFQ/G232022-2023`, award `20240215150152-72748`, award_amount = 700000.0
- ocid `ocds-5whusi-HBCA/PROC/RFQ/G23/2022-2023`, award `20240118160931-70303`, award_amount = 700000.0
- ocid `ocds-5whusi-225224-KLB/RFQ/08/2024-2025`, award `20250209123247-115710`, award_amount = 84000.0
- ocid `ocds-5whusi-KAFU/GRAD/PROTOCOL/067/2022-2023`, award `20250205150325-48230`, award_amount = 21000.0
- ... and 1 more cited record(s) stored with the flag

Reviewer decisions:
- 2026-10-11 16:07:31 UTC, demo_reviewer: **clarify**. Reason: Are other awards by this buyer for drums or similar containers in the same years priced closer to the median?
- 2026-10-11 16:09:16 UTC, demo_reviewer: **approve**. Reason: Demo decision: after reading the agent's answer I still see one award far above the median of its comparables; sent to the committee to check what was bought.
- Clarification round 1: question "Are other awards by this buyer for drums or similar containers in the same years priced closer to the median?"; answer: No other awards for drums or similar containers were found in the same years. The available data only includes one award (ocid: ocds-5whusi-GMC/K/D/056/2023/2024) priced at 0.0x median of comparable awards.

### 2. detect_splitting (severity high), flag `flag_a68bbc6710ef`

3 awards from Makueni County Government to DOUBLE KEY SYSTEMS LTD within 13 days, each just below KES 5,000,000, totalling KES 11,859,448 (2.37x the threshold)

Cited OCDS records:
- ocid `ocds-5whusi-1177969`, award `20250205150309-43082`, award_amount = 4761539.0
- ocid `ocds-5whusi-1177969`, award `20250205150309-43082`, contract_date_signed = 2023-03-23
- ocid `ocds-5whusi-1212735`, award `20250205150330-52396`, award_amount = 3499126.0
- ocid `ocds-5whusi-1212735`, award `20250205150330-52396`, contract_date_signed = 2023-04-03
- ocid `ocds-5whusi-1223916-2022/2023`, award `20250205150317-42727`, award_amount = 3598783.2
- ocid `ocds-5whusi-1223916-2022/2023`, award `20250205150317-42727`, contract_date_signed = 2023-04-05

Reviewer decisions:
- 2026-10-11 16:07:31 UTC, demo_reviewer: **approve**. Reason: Demo decision: three awards to one supplier within 13 days, each below KES 5,000,000 and together 2.37 times that figure, as the cited records show.

### 3. detect_splitting (severity medium), flag `flag_d55995e58fe4`

3 awards from Makueni County Government to WAVECREST LIMITED within 23 days, each just below KES 500,000, totalling KES 987,070 (1.97x the threshold)

Cited OCDS records:
- ocid `ocds-5whusi-043/2022/2023`, award `20250205150331-52488`, award_amount = 385058.14
- ocid `ocds-5whusi-043/2022/2023`, award `20250205150331-52488`, contract_date_signed = 2023-01-31
- ocid `ocds-5whusi-307A(1)/2022/2023`, award `20250205150331-52493`, award_amount = 296087.25
- ocid `ocds-5whusi-307A(1)/2022/2023`, award `20250205150331-52493`, contract_date_signed = 2023-02-07
- ocid `ocds-5whusi-GMC/GVN/Q/022/2022/2023`, award `20250205150330-52114`, award_amount = 305924.76
- ocid `ocds-5whusi-GMC/GVN/Q/022/2022/2023`, award `20250205150330-52114`, contract_date_signed = 2023-02-23

Reviewer decisions:
- 2026-10-11 16:07:31 UTC, demo_reviewer: **approve**. Reason: Demo decision: three awards to one supplier within 23 days, each below KES 500,000 and together 1.97 times that figure, as the cited records show.

### 4. detect_price_anomalies (severity high), flag `flag_131b8ecc7b41`

PROVISION OF STAFF MEDICAL COVER (ocds-5whusi-42686): 71.0x median of 4659 comparable awards (median KES 2,780,390)

Cited OCDS records:
- ocid `ocds-5whusi-42686`, award `20250205150132-43031`, award_amount = 197527657.0
- ocid `ocds-5whusi-BGM/OT/1166912/2022-2023`, award `20250205150306-41545`, award_amount = 2781500.0
- ocid `ocds-5whusi-CGOKTI/ETSD/Q012/2023-2024`, award `20240118111023-70239`, award_amount = 2779280.0
- ocid `ocds-5whusi-CGN/EVIE/ONT/048/2022-2023`, award `20250205150308-57375`, award_amount = 2779040.0
- ocid `ocds-5whusi-CGN/MOH/ONT/012/2022-2023`, award `20240215161426-72777`, award_amount = 2783570.8
- ocid `ocds-5whusi-CGN/MOH/ONT/017/2022-2023`, award `20240215153526-72762`, award_amount = 2776958.0

Reviewer decisions:
- 2026-10-11 16:07:32 UTC, demo_reviewer: **approve**. Reason: Demo decision: 71 times the cohort median is extreme; the cohort mixes different works and services, so a human should check what was bought and whether the amount is a data-entry error.

## Data limitations

- Bid documents are not published, so bid content, scoring and bidder qualifications cannot be assessed.
- The data has no tenderers, tender estimates or item-level detail; price comparisons use title words, category and buyer type only, and a cluster of awards may be genuinely different goods.
- Award dates are contract signing dates; amounts outside the valid range are excluded from statistics.
- Supplier-based checks cover only awards that name a supplier. All thresholds are provisional and unvalidated.

## Statement

This memo does not recommend or award any tender. Every finding is a screening indicator from published OCDS data, approved by a human reviewer, and may have a legitimate explanation.
