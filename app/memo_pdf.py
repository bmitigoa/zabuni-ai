"""Render the evaluation memo (Markdown from `draft_eval_memo`) as a PDF for download.

Only the Markdown subset the memo uses is handled: #/##/### headings, "- " bullets, "> " quotes, `code`, **bold**. The built-in
Helvetica font covers Latin-1 only, so a few typographic characters are mapped to ASCII and anything else becomes "?".
"""
from __future__ import annotations

import io
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

ASCII_MAP = {"≥": ">=", "≤": "<=", "–": "-", "—": "-", "…": "...", "→": "->", "‘": "'", "’": "'", "“": '"', "”": '"', "·": "-", "≈": "~"}
STYLES = {
    "p": ParagraphStyle("p", fontName="Helvetica", fontSize=9.5, leading=13, spaceAfter=4),
    "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=16, leading=20, spaceAfter=8, textColor=colors.HexColor("#14213d")),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12.5, leading=16, spaceBefore=8, spaceAfter=4, textColor=colors.HexColor("#14213d")),
    "h3": ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=10.5, leading=14, spaceBefore=6, spaceAfter=3),
    "q": ParagraphStyle("q", fontName="Helvetica-Oblique", fontSize=9.5, leading=13, leftIndent=8, spaceAfter=6,
                        textColor=colors.HexColor("#7a1f1f"), borderPadding=4),
}


def to_latin1(text: str) -> str:
    for src, dst in ASCII_MAP.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", "replace").decode("latin-1")


def inline(text: str) -> str:
    """Escape for reportlab's mini-HTML, then turn `code` and **bold** into tags."""
    out = escape(to_latin1(text))
    out = re.sub(r"`([^`]+)`", r'<font name="Courier" size="8.5">\1</font>', out)
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", out)


def memo_to_pdf(markdown: str) -> bytes:
    """The memo as PDF bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title="Evaluation memo - Zabuni AI")
    story: list = []
    bullets: list = []

    def flush() -> None:
        nonlocal bullets
        if bullets:
            story.append(ListFlowable([ListItem(Paragraph(b, STYLES["p"]), leftIndent=10) for b in bullets], bulletType="bullet",
                                      start="-", leftIndent=12))
            bullets = []

    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("- "):
            bullets.append(inline(line[2:]))
            continue
        flush()
        if not line.strip():
            story.append(Spacer(1, 2 * mm))
        elif line.startswith("### "):
            story.append(Paragraph(inline(line[4:]), STYLES["h3"]))
        elif line.startswith("## "):
            story.append(Paragraph(inline(line[3:]), STYLES["h2"]))
            story.append(HRFlowable(width="100%", thickness=0.4, color=colors.HexColor("#b0b7c3")))
        elif line.startswith("# "):
            story.append(Paragraph(inline(line[2:]), STYLES["h1"]))
        elif line.startswith("> "):
            story.append(Paragraph(inline(line[2:]), STYLES["q"]))
        else:
            story.append(Paragraph(inline(line), STYLES["p"]))
    flush()
    doc.build(story)
    return buf.getvalue()
