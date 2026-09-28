#!/usr/bin/env python3
"""Render the English evidence report into a two-page PDF, without service calls."""

from __future__ import annotations

import html
import re
from collections import Counter
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

BASE = Path(__file__).resolve().parent
SOURCE = BASE / "Benchmark_Findings_EN.md"
OUTPUT = BASE / "Benchmark_Findings_EN.pdf"
NAVY = colors.HexColor("#15304D")
BLUE = colors.HexColor("#2371AF")
INK = colors.HexColor("#23384D")
MUTED = colors.HexColor("#5E6F80")
RULE = colors.HexColor("#D8E2EC")
PALE = colors.HexColor("#F2F6FB")
PAGE_W, PAGE_H = A4
MARGIN = 40
TEXT_W = PAGE_W - 2 * MARGIN


def register_fonts() -> None:
    root = Path("/usr/share/fonts/truetype/dejavu")
    for name, filename in (
        ("ReportSans", "DejaVuSans.ttf"),
        ("ReportSans-Bold", "DejaVuSans-Bold.ttf"),
        ("ReportSans-Oblique", "DejaVuSans-Oblique.ttf"),
        ("ReportSans-BoldOblique", "DejaVuSans-BoldOblique.ttf"),
    ):
        pdfmetrics.registerFont(TTFont(name, str(root / filename)))
    pdfmetrics.registerFontFamily(
        "ReportSans",
        normal="ReportSans",
        bold="ReportSans-Bold",
        italic="ReportSans-Oblique",
        boldItalic="ReportSans-BoldOblique",
    )


def inline(text: str) -> str:
    """Convert the source's small Markdown subset, retaining relative links."""
    escaped = html.escape(text)
    escaped = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<link href="{m.group(2)}" color="#2371AF">{m.group(1)}</link>',
        escaped,
    )
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"\1", escaped)
    return escaped


def parse_blocks(source: str) -> list[tuple[str, str | list[list[str]]]]:
    blocks = []
    for block in re.split(r"\n\s*\n", source.strip()):
        if block.startswith("# "):
            blocks.append(("title", block[2:]))
        elif block.startswith("|"):
            rows = [
                [cell.strip() for cell in row.strip().strip("|").split("|")]
                for row in block.splitlines()
            ]
            rows = [row for row in rows if not all(re.fullmatch(r":?-+:?", cell) for cell in row)]
            blocks.append(("table", rows))
        else:
            blocks.append(("paragraph", " ".join(block.splitlines())))
    return blocks


def styles() -> dict[str, ParagraphStyle]:
    body = ParagraphStyle(
        "Body", fontName="ReportSans", fontSize=10.2, leading=14.0,
        textColor=INK, spaceAfter=8, allowWidows=0, allowOrphans=0,
    )
    return {
        "body": body,
        "title": ParagraphStyle(
            "Title", parent=body, fontName="ReportSans-Bold", fontSize=21,
            leading=26, textColor=NAVY, spaceAfter=9,
        ),
        "meta": ParagraphStyle(
            "Meta", parent=body, fontSize=9.4, leading=13, textColor=MUTED,
            spaceAfter=10,
        ),
        "table": ParagraphStyle(
            "Table", parent=body, fontSize=10.0, leading=13.2,
            spaceAfter=0, allowWidows=1, allowOrphans=1,
        ),
        "map": ParagraphStyle(
            "EvidenceCell", parent=body, fontSize=9.2, leading=12.2,
            spaceAfter=0, allowWidows=1, allowOrphans=1,
        ),
        "header": ParagraphStyle(
            "TableHeader", parent=body, fontName="ReportSans-Bold", fontSize=9.2,
            leading=12.2, spaceAfter=0, textColor=colors.white,
        ),
    }


def make_table(rows: list[list[str]], style: dict[str, ParagraphStyle]) -> Table:
    is_map = rows[0][0] == "Category"
    cell_style = style["map"] if is_map else style["table"]
    widths = (
        [108, 223, TEXT_W - 331] if is_map
        else [TEXT_W * 0.59, TEXT_W * 0.41] if len(rows[0]) == 2
        else [TEXT_W * 0.36, TEXT_W * 0.32, TEXT_W * 0.32]
    )
    data = []
    for index, row in enumerate(rows):
        data.append([
            Paragraph(inline(f"**{cell}**" if is_map and index and col == 0 else cell),
                      style["header"] if index == 0 else cell_style)
            for col, cell in enumerate(row)
        ])
    table = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [PALE, colors.white]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7 if is_map else 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7 if is_map else 5),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, RULE),
    ]))
    return table


def page_furniture(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(BLUE)
    canvas.rect(MARGIN, PAGE_H - 27, 24, 3, stroke=0, fill=1)
    canvas.setFont("ReportSans-Bold", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN + 32, PAGE_H - 28, "E2B BENCHMARK  /  PHASES 0–2")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.6)
    canvas.line(MARGIN, 30, PAGE_W - MARGIN, 30)
    canvas.setFont("ReportSans", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, 17, "LOCAL DEPLOYMENT  ·  20 SEPTEMBER 2026")
    canvas.drawRightString(PAGE_W - MARGIN, 17, f"{doc.page} / 2")
    canvas.restoreState()


def render() -> None:
    register_fonts()
    style = styles()
    blocks = parse_blocks(SOURCE.read_text(encoding="utf-8"))
    story = []
    has_metadata = False
    for kind, content in blocks:
        if kind == "title":
            story.append(Paragraph(inline(str(content)), style["title"]))
        elif kind == "table":
            if content[0][0] == "Category":
                story.extend([
                    PageBreak(),
                    Paragraph("Evidence map and configuration", style["title"]),
                    Spacer(1, 3),
                ])
            story.extend([make_table(content, style), Spacer(1, 8)])
        else:
            is_meta = not has_metadata
            story.append(Paragraph(inline(str(content)), style["meta"] if is_meta else style["body"]))
            has_metadata = True

    document = SimpleDocTemplate(
        str(OUTPUT), pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=45, bottomMargin=42,
        title="Benchmark findings: measured limits and next experiments",
        author="E2B Benchmark", subject="Local benchmark evidence, configuration, and next experiments",
        pageCompression=1,
    )
    document.build(story, onFirstPage=page_furniture, onLaterPages=page_furniture)

    import fitz

    with fitz.open(OUTPUT) as pdf:
        if len(pdf) != 2:
            raise RuntimeError(f"Expected two pages, rendered {len(pdf)}")
        for number, page in enumerate(pdf, 1):
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        if not page.rect.contains(fitz.Rect(span["bbox"])):
                            raise RuntimeError(f"Text outside page {number}: {span['text']!r}")
        source_text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", SOURCE.read_text(encoding="utf-8"))
        source_tokens = Counter(re.findall(r"\w+", source_text.lower()))
        rendered_tokens = Counter(re.findall(r"\w+", "\n".join(page.get_text() for page in pdf).lower()))
        missing = source_tokens - rendered_tokens
        if missing:
            raise RuntimeError(f"Source words missing from PDF: {dict(missing)}")
        print(
            f"Rendered {OUTPUT.name}: {len(pdf)} pages, "
            "all text bounds inside page rectangles, all source words retained"
        )


if __name__ == "__main__":
    render()
