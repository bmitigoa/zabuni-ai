# User engagement — session with a procurement practitioner

**Status: TEMPLATE — to be filled in after the session.** Everything below marked `▢` or `_(answer)_` is blank on purpose.
Nothing here is a real answer yet; do not quote this file as evidence of user feedback until it is completed.

**Purpose.** Zabuni AI's thresholds (`rules://ppada`, `mcp_server/rules.py`) are all *provisional — to be validated with a
procurement practitioner*. This session checks them, finds out which red flags matter in practice, and observes whether
the reviewer workflow makes sense to someone who does this work.

## 0. Session record

| | |
|---|---|
| Date / duration | _(answer)_ |
| Practitioner's role (e.g. CPA, procurement officer, internal auditor, oversight body) | _(answer)_ |
| Type of organisation (county, state corporation, university, audit office, …) | _(answer)_ |
| Years of procurement / audit experience | _(answer)_ |
| Format (in person / call; who ran the keyboard) | _(answer)_ |
| Consent to be quoted | ▢ anonymously by role only  ▢ by name  ▢ not quoted |

**Privacy.** Record the practitioner by **role only** unless they consent to be named. Do not write down tender-sensitive
information from their own organisation; use the public PPRA data only. No personal data of third parties in this file.

## 1. Before the demo (5 min) — their workflow today

1. When your committee reviews a tender or an award, what do you check first? _(answer)_
2. What information do you wish you had at that point and usually don't? _(answer)_
3. How do you currently look for repeat suppliers, split purchases or unusual prices? Spreadsheets? IFMIS? Nothing? _(answer)_
4. Which public data sources do you already use (PPRA portal, IFMIS, Auditor-General reports)? _(answer)_

## 2. Demo (10 min) — suggested walk-through

Run the tools on **public** examples (one buyer the practitioner knows from public sources works best):
`search_awards` → `compute_price_benchmark` → `detect_splitting` → `detect_noncompetitive_method` → `supplier_concentration`.
Let them read the evidence citations (`ocid`, `award_id`) and ask them to find one on the PPRA portal.

| Observation | Notes |
|---|---|
| Did they understand what a flag means ("prompt for review", not a finding)? | _(answer)_ |
| Did the evidence citations make them trust or distrust the output? | _(answer)_ |
| Where did they hesitate or get confused? | _(answer)_ |
| What did they ask for that the tool cannot do? | _(answer)_ |

## 3. Validating the thresholds

For each provisional value, record what they would use and **why**, and the legal basis if they cite one (Act/Regulations
section, circular, entity policy). Do not accept a number without a source; write "no source" if there is none.

### 3.1 Contract splitting (`detect_splitting`)

| Parameter | Provisional default | Practitioner's value | Source / reasoning |
|---|---|---|---|
| Approval / method thresholds in KES (e.g. where quotations, restricted or open tendering start, by entity type) | 0.5M, 1M, 2M, 5M, 10M — **illustrative only, unverified** | _(answer)_ | _(answer)_ |
| Window within which awards to one supplier look like one purchase | 30 days | _(answer)_ | _(answer)_ |
| Minimum number of awards that makes a pattern suspicious | 3 | _(answer)_ | _(answer)_ |
| "Just below" a threshold means amount ≥ this fraction of it | 50 % | _(answer)_ | _(answer)_ |

Questions:
1. What does splitting look like in practice — same item bought in tranches, or different items from the same supplier? _(answer)_
2. What legitimate reasons produce several awards to one supplier on the same day or week (framework agreements, multi-site projects, emergencies)? How can a reviewer tell them apart? _(answer)_
3. Should the check consider the *procurement plan* or annual totals per category, not just a window? _(answer)_
4. Do thresholds differ by entity type (national, county, state corporation, university)? Which list should the tool use? _(answer)_
5. Is it normal for the same contract to appear twice on the portal (duplicate publication)? Why? _(answer)_

### 3.2 Price benchmark (`compute_price_benchmark`)

| Parameter | Provisional default | Practitioner's value | Source / reasoning |
|---|---|---|---|
| Ratio to cohort median that deserves a look | 3× | _(answer)_ | _(answer)_ |
| Minimum comparable awards before the result is trusted | 10 | _(answer)_ | _(answer)_ |

1. Is "title words + category + buyer type" a fair way to find comparable awards without item data? What else would you match on (location, quantity, delivery terms)? _(answer)_
2. For which kinds of purchase is a price comparison meaningful (fuel, stationery, vehicles) and for which is it misleading (works, bespoke services)? _(answer)_
3. Would you want works and goods handled by separate rules? _(answer)_

### 3.3 Non-competitive methods (`detect_noncompetitive_method`)

| Parameter | Provisional default | Practitioner's value | Source / reasoning |
|---|---|---|---|
| Which methods count as non-competitive | direct, restricted (OCDS `selective`) | _(answer)_ | _(answer)_ |
| Multiple of the national baseline that deserves a look | 2× | _(answer)_ | _(answer)_ |
| Minimum number of such awards | 10 | _(answer)_ | _(answer)_ |

1. When is direct or restricted procurement legitimate, and what justification should a reviewer expect to see? _(answer)_
2. Is the national baseline the right comparison, or should it be peers of the same type/size? _(answer)_

### 3.4 Supplier concentration (`supplier_concentration`)

| Parameter | Provisional default | Practitioner's value | Source / reasoning |
|---|---|---|---|
| Top-supplier share that deserves a look (count or value) | 25 % | _(answer)_ | _(answer)_ |
| Minimum named awards for the buyer | 30 | _(answer)_ | _(answer)_ |

1. Which buyers legitimately depend on one supplier (specialised goods, sole distributors, insurers)? _(answer)_
2. Should the tool look at concentration by category rather than across everything a buyer buys? _(answer)_

## 4. Which red flags matter most?

Ask them to rank (1 = most useful for their work), and say why.

| Rank | Flag | Why |
|---|---|---|
| _(answer)_ | Price outlier vs comparable awards | _(answer)_ |
| _(answer)_ | Contract splitting | _(answer)_ |
| _(answer)_ | Heavy use of direct / restricted procurement | _(answer)_ |
| _(answer)_ | Supplier concentration | _(answer)_ |
| _(answer)_ | Other flag they would add (e.g. repeated awards just under approval levels, related suppliers, late/early award, variation orders) | _(answer)_ |

1. Which of these can you already see in your own systems, and which would be genuinely new? _(answer)_
2. Which flag would you act on straight away, and which would you ignore? _(answer)_
3. What data is missing that would make these flags more reliable (bidder counts, estimates, item lines, contract variations)? _(answer)_

## 5. Reviewer workflow (human-in-the-loop)

The agent proposes flags; a human **approves / rejects / requests clarification, with a reason**, before anything enters the memo.

1. Is approve / reject / clarify the right set of choices? What is missing? _(answer)_
2. Who should be allowed to approve a flag in your organisation? _(answer)_
3. What would the final evaluation memo need to contain to be usable by a committee (format, length, wording)? _(answer)_
4. Would you be comfortable relying on a flag whose only source is published OCDS data? What else would you need before acting? _(answer)_
5. Do you accept that the tool never recommends or awards a tender? Any concerns about how it could be misused? _(answer)_

## 6. Close (5 min)

1. Would you use this? For what task, how often, and what would stop you? _(answer)_
2. One thing to change before anyone relies on it: _(answer)_
3. Anyone else it would be worth showing it to? _(answer)_

## 7. After the session — actions

| Finding | Change to make (rule / threshold / UI / docs) | Done? |
|---|---|---|
| _(answer)_ | _(answer)_ | ▢ |

Update `mcp_server/rules.py` and `docs/red_flags.md` for any threshold that changes, keep the "provisional" label
on every value that still has no source, and record the final stress-test feedback in README "Limitations".
