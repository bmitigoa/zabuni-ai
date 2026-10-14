# User engagement: conversation with a procurement practitioner

This is an honest record of one informal conversation. It is **not** a formal user test, and it does not validate Zabuni AI's thresholds.

| | |
|---|---|
| Date | 8 October 2026 |
| Participant | One practitioner: a managing partner at a Kenyan audit firm (the firm where the builder works). Kept anonymous and credited by role only; no names, no firm name. |
| Format | Informal one-to-one conversation. Not a recorded interview and not a formal user test. |
| Did the participant use the app? | No. |

## What the practitioner described

When auditors review procurement, they check an award against previous benchmark records (past comparable purchases) and look at the differences. A large difference is a reason to treat the award as an anomaly and investigate further.

## How it maps to Zabuni AI

This is the method used by `compute_price_benchmark` (an award against the median of comparable past awards) and by `detect_price_anomalies`. It also supports a design choice already in the app: every flag card shows the comparison set next to the flagged award, so the reviewer can see the differences for themselves instead of trusting a score.

## What was not covered

* **No threshold figures were given.** Every threshold in `mcp_server/rules.py` therefore stays **provisional**, and every flag card says so.
* Splitting, supplier-concentration and non-competitive-method rules were **not discussed**.
* The participant did not use the app, so nothing here says whether the reviewer workflow or the cards work for a practitioner.

## Next step

Validate the thresholds, and run a hands-on review session with auditors and procurement officers.
