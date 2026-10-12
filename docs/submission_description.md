# Zabuni AI: submission description (about 300 words)

**Theme: Value for money.** Kenya publishes its public-procurement awards as open data, but the officers who must act on it have no time to read thousands of rows, and no bid documents, bidder lists or item prices to help. The usual value-for-money and integrity signals (one supplier repeatedly just under an approval threshold, an award many times its peers' price, a buyer that rarely competes, one supplier dominating a buyer) are found by hand, if at all.

**Institution and users.** Zabuni AI is built for the **procurement units of county governments and national entities** and for **internal audit units**. A procurement officer or auditor names a buyer, a supplier or years and gets, in about a minute, a short list of leads to question, each tied to the published records behind it.

**Workflow it speeds up.** Today an audit or pre-approval review starts with filtering spreadsheets for suspect awards. Zabuni replaces that first pass: a LangGraph agent running an open-weights model (Qwen) plans its own investigation, calls tools only through MCP (a custom server of analysis tools plus the official Filesystem server), follows up on what it finds, and raises flags that each cite their OCDS `ocid`. The card shows the flagged award apart from the set of awards it was compared with, says why two checks on one award give different multiples, labels every threshold *provisional*, and warns when an amount looks like a data-entry error.

**Accountability by design.** A human reviewer approves, rejects or asks a question on each flag, **with a reason and a role**, before it can enter the evaluation memo; a rejected flag is not raised again. The agent never recommends or awards a tender; the committee decides. Guards stop the model inventing a scope or a citation, a run budget stops runaway cost, and every tool call is logged.

**Evidence.** On the frozen PPRA snapshot, 18 of 21 evaluation cases pass; the three failures are honest false alarms (road-section lots, repeat supply, direct procurement) that the cards flag for a human to rule out. A typical hosted run costs well under one US cent on the free tier; the local model costs nothing.

**Honest limits.** Thresholds are not yet validated by a practitioner; there are no bid documents in the data; the hosted demo shares a free-tier quota.

Live demo: https://zabuni-ai.streamlit.app · Code: https://github.com/bmitigoa/zabuni-ai
