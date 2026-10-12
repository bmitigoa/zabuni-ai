"""The example queries on the form. Each one is checked against the frozen snapshot by tests/test_app.py: it must return awards
and at least one flag, so a visitor's first click never ends in an empty result."""

EXAMPLES = [
    {"label": "Makueni County: splitting and price anomalies, 2022-2023", "buyer": "Makueni County Government", "supplier": "",
     "years": [2022, 2023], "text": "Review awards for contract splitting and price anomalies.", "check": "detect_splitting"},
    {"label": "Kenya Rural Roads Authority: non-competitive procurement, 2023", "buyer": "Kenya Rural Roads Authority", "supplier": "",
     "years": [2023], "text": "Check supplier concentration and non-competitive procurement.", "check": "detect_noncompetitive_method"},
    {"label": "Supplier name 'Wavecrest' across all buyers", "buyer": "", "supplier": "Wavecrest Limited",
     "years": [], "text": "List this supplier's awards and look for splitting. Spelling variants of the name are grouped.", "check": "detect_splitting"},
]
