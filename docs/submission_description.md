# Zabuni AI: submission description

**Theme: Value for money.** Kenya publishes its public-procurement awards as open data, but the officers who must act on it cannot read thousands of rows, and the data has no bid documents, bidder lists or item prices. The classic signals (one supplier repeatedly just under an approval threshold, an award many times its peers' price, a buyer that rarely competes) are found by hand, if at all.

**Institution and users.** Built for the **procurement units of county and national entities** and for **internal audit**: a procurement officer or auditor names a buyer, supplier or years and gets, in about a minute, a short list of leads, each tied to the published records behind it.

**Workflow it speeds up.** The first pass of a pre-approval review or audit, today done by filtering spreadsheets. A LangGraph agent running an open-weights model (Qwen) plans its own investigation, calls tools only through MCP (a custom analysis server plus the official Filesystem server), follows up on what it finds, and raises flags that each cite their OCDS `ocid`. Each card shows the flagged award apart from the awards it was compared with, explains why two checks on one award give different multiples, labels every threshold *provisional*, and warns when an amount looks like a data-entry error.

**Accountability.** A human approves, rejects or queries each flag **with a reason and a role** before it can enter the evaluation memo; a rejected flag is not raised again. The agent never recommends or awards a tender. Guards stop the model inventing a scope or a citation, a budget caps cost, and every tool call is logged.

**Evidence.** Of 21 evaluation cases on the frozen PPRA snapshot, 18 pass; the three failures are honest false alarms (road-section lots, repeat supply, direct procurement) handed to a human. A hosted run uses about 4,200 tokens: roughly half a US cent at list price, nothing on the free tier.

**Limits.** Thresholds are not yet validated by a practitioner; no bid documents; the demo shares a free-tier quota.

Live demo: https://zabuni-ai.streamlit.app · Code: https://github.com/bmitigoa/zabuni-ai
