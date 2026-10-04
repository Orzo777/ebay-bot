"""docs/*.md → PDF (reportlab, шрифт DejaVu з кирилицею).

Підтримує заголовки #/##/###, абзаци, списки «- » (з вкладеністю), таблиці «| … |», блоки ```.
    python tools/make_overview_pdf.py                 # docs/PROJECT.md → docs/ebay-bot-overview.pdf
    python tools/make_overview_pdf.py --all           # плюс шпаргалки docs/guide-*.md → docs/guide-*.pdf
    python tools/make_overview_pdf.py docs/guide-ram.md docs/guide-ram.pdf
"""
import os
import re
import sys
from html import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "docs", "PROJECT.md")
OUT = os.path.join(ROOT, "docs", "ebay-bot-overview.pdf")
FONT_DIRS = [r"C:\Windows\Fonts", "/usr/share/fonts/truetype/dejavu", "/Library/Fonts"]


def _font(name):
    for d in FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    sys.exit(f"немає шрифту {name} (потрібен DejaVu з кирилицею)")


pdfmetrics.registerFont(TTFont("DV", _font("DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DV-B", _font("DejaVuSans-Bold.ttf")))
pdfmetrics.registerFont(TTFont("DV-M", _font("DejaVuSansMono.ttf")))
pdfmetrics.registerFontFamily("DV", normal="DV", bold="DV-B", italic="DV", boldItalic="DV-B")

ACCENT = colors.HexColor("#1f5f8b")
S = {
    "title": ParagraphStyle("title", fontName="DV-B", fontSize=24, leading=30, alignment=TA_CENTER, textColor=ACCENT,
                            spaceAfter=8),
    "sub": ParagraphStyle("sub", fontName="DV", fontSize=11, leading=15, alignment=TA_CENTER, textColor=colors.grey),
    "h2": ParagraphStyle("h2", fontName="DV-B", fontSize=15, leading=19, textColor=ACCENT, spaceBefore=12, spaceAfter=6),
    "h3": ParagraphStyle("h3", fontName="DV-B", fontSize=11.5, leading=15, spaceBefore=8, spaceAfter=3),
    "p": ParagraphStyle("p", fontName="DV", fontSize=9.5, leading=13.5, spaceAfter=5),
    "li": ParagraphStyle("li", fontName="DV", fontSize=9.5, leading=13.2, leftIndent=12, bulletIndent=3, spaceAfter=2),
    "li2": ParagraphStyle("li2", fontName="DV", fontSize=9.2, leading=12.8, leftIndent=24, bulletIndent=15, spaceAfter=1),
    "cell": ParagraphStyle("cell", fontName="DV", fontSize=8.3, leading=10.8),
    "cellh": ParagraphStyle("cellh", fontName="DV-B", fontSize=8.3, leading=10.8, textColor=colors.white),
    "code": ParagraphStyle("code", fontName="DV-M", fontSize=6.3, leading=8.4, backColor=colors.HexColor("#f3f5f7"),
                           borderPadding=5, spaceBefore=4, spaceAfter=8),
}


def inline(s: str) -> str:
    s = escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r'<font name="DV-M">\1</font>', s)
    return s


def table(rows):
    data = [[Paragraph(inline(c), S["cellh"] if i == 0 else S["cell"]) for c in r] for i, r in enumerate(rows)]
    n = len(rows[0])
    width = A4[0] - 36 * mm
    widths = {2: [0.42, 0.58], 3: [0.34, 0.2, 0.46] if "Коли" in rows[0][1] else [0.28, 0.3, 0.42]}.get(n, [1 / n] * n)
    t = Table(data, colWidths=[w * width for w in widths], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef3f7")]),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#c8d3dc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def build(src: str = SRC, out: str = OUT):
    lines = open(src, encoding="utf-8").read().split("\n")
    story, i, para = [], 0, []
    title = lines[0].lstrip("# ").strip()
    updated = next((ln for ln in lines if ln.startswith("Останнє оновлення")), "")
    story += [Spacer(1, 70 * mm), Paragraph(escape(title), S["title"]), Paragraph(escape(updated), S["sub"]),
              Spacer(1, 6 * mm), Paragraph("Купівля на Kleinanzeigen / eBay.de → перепродаж на eBay.de", S["sub"]),
              PageBreak()]
    i = 1

    def flush():
        if para:
            story.append(Paragraph(inline(" ".join(para)), S["p"]))
            para.clear()

    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            flush()
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            story.append(Preformatted("\n".join(lines[i + 1:j]), S["code"]))
            i = j + 1
            continue
        if ln.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            story += [table(rows), Spacer(1, 4)]
            continue
        if ln.startswith("## "):
            flush()
            if ln.startswith("## 12."):
                story.append(PageBreak())
            story.append(Paragraph(inline(ln[3:]), S["h2"]))
        elif ln.startswith("### "):
            flush()
            story.append(KeepTogether([Paragraph(inline(ln[4:]), S["h3"])]))
        elif re.match(r"^  +- ", ln):
            flush()
            story.append(Paragraph(inline(ln.strip()[2:]), S["li2"], bulletText="–"))
        elif ln.startswith("- "):
            flush()
            text = ln[2:]
            while i + 1 < len(lines) and re.match(r"^  +[^\s-]", lines[i + 1]):   # продовження пункту з відступом
                i += 1
                text += " " + lines[i].strip()
            story.append(Paragraph(inline(text), S["li"], bulletText="•"))
        elif not ln.strip():
            flush()
        elif ln.startswith("# "):
            pass
        else:
            para.append(ln.strip())
        i += 1
    flush()

    def page(c, d):
        c.saveState()
        c.setFont("DV", 7.5)
        c.setFillColor(colors.grey)
        c.drawString(18 * mm, 10 * mm, "ebay-bot — " + title[:70])
        c.drawRightString(A4[0] - 18 * mm, 10 * mm, str(d.page))
        c.restoreState()

    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=title, author="ebay-bot")
    doc.build(story, onFirstPage=lambda c, d: None, onLaterPages=page)
    print(out)


if __name__ == "__main__":
    import glob
    if len(sys.argv) == 3:
        build(sys.argv[1], sys.argv[2])
    else:
        build()
        if "--all" in sys.argv:
            for md in sorted(glob.glob(os.path.join(ROOT, "docs", "guide-*.md"))):
                build(md, md[:-3] + ".pdf")
