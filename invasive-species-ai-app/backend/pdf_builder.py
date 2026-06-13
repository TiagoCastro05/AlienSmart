"""
pdf_builder.py — Gerador de PDF profissional para relatórios AlienSmart.
Importar em main.py:  from pdf_builder import build_pdf
"""
from __future__ import annotations

import io
import re
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── Paleta ───────────────────────────────────────────────────────────────────
GREEN_DARK   = colors.HexColor("#1b4332")
GREEN_MID    = colors.HexColor("#2d6a4f")
GREEN_LIGHT  = colors.HexColor("#d8f3dc")
GREEN_HEADER = colors.HexColor("#40916c")
GREY_LIGHT   = colors.HexColor("#f8f9fa")
GREY_MID     = colors.HexColor("#dee2e6")
TEXT_DARK    = colors.HexColor("#212529")
TEXT_GREY    = colors.HexColor("#6c757d")
TABLE_ALT    = colors.HexColor("#f0f7f4")


# ── Estilos ──────────────────────────────────────────────────────────────────
def _make_styles() -> dict:
    s = {}
    s["h1"] = ParagraphStyle("h1",
        fontSize=17, textColor=GREEN_DARK, spaceAfter=4, spaceBefore=14,
        fontName="Helvetica-Bold", leading=21,
    )
    s["h2"] = ParagraphStyle("h2",
        fontSize=13, textColor=GREEN_MID, spaceAfter=4, spaceBefore=10,
        fontName="Helvetica-Bold", leading=16,
    )
    s["h3"] = ParagraphStyle("h3",
        fontSize=11, textColor=GREEN_HEADER, spaceAfter=3, spaceBefore=7,
        fontName="Helvetica-Bold", leading=14,
    )
    s["body"] = ParagraphStyle("body",
        fontSize=10, textColor=TEXT_DARK, spaceAfter=4, spaceBefore=2,
        fontName="Helvetica", leading=14, alignment=TA_JUSTIFY,
    )
    s["bullet"] = ParagraphStyle("bullet",
        fontSize=10, textColor=TEXT_DARK, spaceAfter=2, spaceBefore=1,
        fontName="Helvetica", leading=13, leftIndent=14, bulletIndent=4,
    )
    s["meta"] = ParagraphStyle("meta",
        fontSize=9, textColor=TEXT_GREY, spaceAfter=8, spaceBefore=0,
        fontName="Helvetica", leading=12,
    )
    s["th"] = ParagraphStyle("th",
        fontSize=9, fontName="Helvetica-Bold",
        textColor=colors.white, leading=12,
    )
    s["td"] = ParagraphStyle("td",
        fontSize=9, fontName="Helvetica",
        textColor=TEXT_DARK, leading=12,
    )
    return s


# ── Helpers ──────────────────────────────────────────────────────────────────
def _fmt(text: str) -> str:
    """Converte **bold** e *italic* para tags ReportLab."""
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.+?)\*',     r'<i>\1</i>', text)
    # Escapar & que não sejam já entidades
    text = re.sub(r'&(?!(?:amp|lt|gt|nbsp|b|i|/b|/i);)', '&amp;', text)
    return text


def _is_separator(line: str) -> bool:
    return bool(re.match(r'^\s*\|[\s\-:]+\|\s*$', line.strip()))


def _parse_table(table_lines: list[str], styles: dict) -> Table | None:
    rows = []
    for line in table_lines:
        if _is_separator(line):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        rows.append(cells)

    if not rows:
        return None

    col_count = max(len(r) for r in rows)
    page_w = A4[0] - 3.6 * cm
    col_w = page_w / col_count

    table_data = []
    for i, row in enumerate(rows):
        while len(row) < col_count:
            row.append("")
        st = styles["th"] if i == 0 else styles["td"]
        table_data.append([Paragraph(_fmt(c), st) for c in row])

    zebra = [
        ("BACKGROUND", (0, r), (-1, r), TABLE_ALT if r % 2 == 0 else colors.white)
        for r in range(1, len(table_data))
    ]

    t = Table(table_data, colWidths=[col_w] * col_count, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  GREEN_MID),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0),  9),
        ("TOPPADDING",    (0, 0), (-1, 0),  7),
        ("BOTTOMPADDING", (0, 0), (-1, 0),  7),
        ("LINEBELOW",     (0, 0), (-1, 0),  1.5, GREEN_DARK),
        *zebra,
        ("GRID",          (0, 0), (-1, -1), 0.4, GREY_MID),
        ("TOPPADDING",    (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _on_page(canvas, doc, level: str, date_str: str) -> None:
    canvas.saveState()
    w, h = A4

    # Cabeçalho
    canvas.setFillColor(GREEN_DARK)
    canvas.rect(0, h - 1.15 * cm, w, 1.15 * cm, fill=True, stroke=False)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(1.8 * cm, h - 0.78 * cm, "AlienSmart — Espécies Invasoras")
    canvas.setFont("Helvetica", 9)
    canvas.drawRightString(w - 1.8 * cm, h - 0.78 * cm, f"Relatório {level.capitalize()}")

    # Rodapé
    canvas.setFillColor(GREY_MID)
    canvas.rect(0, 0, w, 0.85 * cm, fill=True, stroke=False)
    canvas.setFillColor(TEXT_GREY)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(1.8 * cm, 0.3 * cm, f"Gerado em {date_str}")
    canvas.drawRightString(w - 1.8 * cm, 0.3 * cm, f"Página {doc.page}")

    canvas.restoreState()


# ── Função principal ─────────────────────────────────────────────────────────
def build_pdf(
    report_text: str,
    level: str,
    summary: dict | None = None,
    filters: dict | None = None,
    species_maps: list[dict] | None = None,
) -> bytes:
    """
    Gera PDF profissional a partir do markdown gerado pelo Ollama.

    Args:
        report_text: Texto em markdown do relatório.
        level:       "tecnico" | "executivo" | "publico"
        summary:     Não utilizado (mantido por compatibilidade com main.py).
        filters:     {'species': [...], 'municipality': str}  — mostrado no topo.
    """
    date_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    styles = _make_styles()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        topMargin=2.0 * cm, bottomMargin=1.6 * cm,
    )

    story: list = []

    # Linha de filtros
    if filters:
        parts = []
        if filters.get("species"):
            names = ", ".join(f"<i>{s}</i>" for s in filters["species"])
            parts.append(f"Espécies: {names}")
        if filters.get("municipality"):
            parts.append(f"Município: <b>{filters['municipality']}</b>")
        if parts:
            story.append(Paragraph(" &nbsp;|&nbsp; ".join(parts), styles["meta"]))
            story.append(HRFlowable(width="100%", thickness=0.8,
                                    color=GREEN_LIGHT, spaceAfter=6))

    # Parser de markdown
    lines = report_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            story.append(Spacer(1, 3))
            i += 1
            continue

        # H1: linha que começa com # ou texto todo a bold curto
        if stripped.startswith("# "):
            text = stripped[2:].strip()
            story.append(Paragraph(_fmt(text), styles["h1"]))
            story.append(HRFlowable(width="100%", thickness=1.2,
                                    color=GREEN_MID, spaceAfter=4))
            i += 1
            continue

        # Título bold isolado (ex: **RELATÓRIO TÉCNICO**)
        if re.match(r'^\*\*[^*]+\*\*$', stripped) and len(stripped) < 80:
            text = stripped.strip("*")
            story.append(Paragraph(text, styles["h1"]))
            story.append(HRFlowable(width="100%", thickness=1.2,
                                    color=GREEN_MID, spaceAfter=4))
            i += 1
            continue

        # H2
        if stripped.startswith("## "):
            story.append(Paragraph(_fmt(stripped[3:]), styles["h2"]))
            i += 1
            continue

        # H3
        if stripped.startswith("### "):
            story.append(Paragraph(_fmt(stripped[4:]), styles["h3"]))
            i += 1
            continue

        # Tabela markdown
        if stripped.startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            tbl = _parse_table(table_lines, styles)
            if tbl:
                story.append(Spacer(1, 4))
                story.append(tbl)
                story.append(Spacer(1, 10))
            continue

        # Bullet
        if stripped.startswith(("- ", "* ")):
            story.append(Paragraph(f"• &nbsp;{_fmt(stripped[2:])}", styles["bullet"]))
            i += 1
            continue

        # Parágrafo normal
        story.append(Paragraph(_fmt(stripped), styles["body"]))
        i += 1

    # Mapas raster (PNG pré-renderizados em main.py)
    if species_maps:
        story.append(Spacer(1, 0.4 * cm))
        story.append(Paragraph("Mapas de Distribuição Potencial", styles["h2"]))
        story.append(HRFlowable(width="100%", thickness=1.2, color=GREEN_MID, spaceAfter=6))
        for m in species_maps:
            period_label = "Histórico (1981–2024)" if m["period"] == "hist" else m["period"]
            sc_label = f" · {m['scenario']}" if m.get("scenario") else ""
            story.append(Paragraph(f"{m['species']} — {period_label}{sc_label}", styles["h3"]))
            img = Image(io.BytesIO(m["map_png"]), width=14 * cm, height=12.25 * cm)
            img.hAlign = "LEFT"
            story.append(img)
            story.append(Spacer(1, 0.6 * cm))

    def _page_cb(canvas, doc):
        _on_page(canvas, doc, level, date_str)

    doc.build(story, onFirstPage=_page_cb, onLaterPages=_page_cb)
    buf.seek(0)
    return buf.read()