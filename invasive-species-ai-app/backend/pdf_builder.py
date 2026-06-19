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
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

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
        fontName="Helvetica-Bold", leading=21, keepWithNext=1,
    )
    s["h2"] = ParagraphStyle("h2",
        fontSize=13, textColor=GREEN_MID, spaceAfter=4, spaceBefore=10,
        fontName="Helvetica-Bold", leading=16, keepWithNext=1,
    )
    s["h3"] = ParagraphStyle("h3",
        fontSize=11, textColor=GREEN_HEADER, spaceAfter=3, spaceBefore=7,
        fontName="Helvetica-Bold", leading=14, keepWithNext=1,
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
    # ── Capa ──
    s["cover_kicker"] = ParagraphStyle("cover_kicker",
        fontSize=12, textColor=GREEN_MID, spaceAfter=10, spaceBefore=0,
        fontName="Helvetica-Bold", leading=16, alignment=TA_CENTER,
    )
    s["cover_title"] = ParagraphStyle("cover_title",
        fontSize=26, textColor=GREEN_DARK, spaceAfter=12, spaceBefore=0,
        fontName="Helvetica-Bold", leading=31, alignment=TA_CENTER,
    )
    s["cover_meta"] = ParagraphStyle("cover_meta",
        fontSize=11, textColor=TEXT_DARK, spaceAfter=5, spaceBefore=0,
        fontName="Helvetica", leading=16, alignment=TA_CENTER,
    )
    # ── Índice ──
    s["toc0"] = ParagraphStyle("toc0",
        fontName="Helvetica-Bold", fontSize=11, textColor=GREEN_DARK,
        leftIndent=4, firstLineIndent=-4, spaceBefore=5, leading=16,
    )
    s["toc1"] = ParagraphStyle("toc1",
        fontName="Helvetica", fontSize=10, textColor=TEXT_DARK,
        leftIndent=18, firstLineIndent=-4, spaceBefore=2, leading=14,
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


class _DocWithTOC(SimpleDocTemplate):
    """SimpleDocTemplate que alimenta o Índice (TableOfContents) com os títulos.

    Os cabeçalhos h1 (secções principais) e h2 (ex.: Análise no Município,
    Análise Estatística) são registados como entradas do índice, com o número
    de página real (resolvido em multiBuild).
    """

    def afterFlowable(self, flowable):
        if not isinstance(flowable, Paragraph):
            return
        text = flowable.getPlainText().strip()
        if not text or text.lower() == "índice":
            return
        # Secções principais (h1) e secções de topo marcadas como h2
        # (Análise no Município, Análise Estatística) entram ao mesmo nível.
        name = flowable.style.name
        if name in ("h1", "h2"):
            self.notify("TOCEntry", (0, text, self.page))


# ── Função principal ─────────────────────────────────────────────────────────
def build_pdf(
    report_text: str,
    level: str,
    summary: dict | None = None,
    filters: dict | None = None,
    species_maps: list[dict] | None = None,
    charts: list | None = None,
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

    # ── Extrair o título do relatório (linha "**Título:** ...") para a capa,
    # removendo-a do corpo para não aparecer duplicada.
    report_title = None
    mt = re.search(r'(?im)^\s*\*\*\s*T[íi]tulo\s*:?\s*\*\*\s*[:\-—]?\s*(.+)$', report_text)
    if mt:
        report_title = mt.group(1).strip()
        report_text = report_text[:mt.start()] + report_text[mt.end():]
    if not report_title:
        report_title = f"Relatório {level.capitalize()} — Espécies Invasoras"

    buf = io.BytesIO()
    doc = _DocWithTOC(
        buf, pagesize=A4,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm,
        topMargin=2.0 * cm, bottomMargin=1.6 * cm,
        title=report_title,
    )

    story: list = []

    # ── CAPA ─────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 5.0 * cm))
    story.append(Paragraph(f"RELATÓRIO {level.upper()}", styles["cover_kicker"]))
    story.append(Paragraph(_fmt(report_title), styles["cover_title"]))
    story.append(HRFlowable(width="55%", thickness=1.4, color=GREEN_HEADER,
                            spaceBefore=6, spaceAfter=18, hAlign="CENTER"))
    if filters:
        if filters.get("species"):
            names = ", ".join(f"<i>{s}</i>" for s in filters["species"])
            story.append(Paragraph(f"Espécies: {names}", styles["cover_meta"]))
        if filters.get("municipality"):
            story.append(Paragraph(f"Município: <b>{filters['municipality']}</b>",
                                   styles["cover_meta"]))
    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph(f"Gerado em {date_str}", styles["cover_meta"]))
    story.append(Paragraph("AlienSmart — Plataforma de Espécies Invasoras",
                           styles["cover_meta"]))
    story.append(PageBreak())

    # ── ÍNDICE ───────────────────────────────────────────────────────────────
    story.append(Paragraph("Índice", styles["h1"]))
    story.append(HRFlowable(width="100%", thickness=1.2, color=GREEN_MID, spaceAfter=8))
    toc = TableOfContents()
    toc.levelStyles = [styles["toc0"], styles["toc1"]]
    story.append(toc)
    story.append(PageBreak())

    # Índice: espécie (lowercase) → dict com png e flag "já inserido"
    from reportlab.lib.utils import ImageReader as _IR

    def _map_image_flowables(m: dict) -> list:
        """Gera os flowables do mapa (título + imagem) para uma entrada species_maps."""
        if m.get("title"):
            title_text = m["title"]
        else:
            period_label = "Histórico (1981–2024)" if m["period"] == "hist" else m["period"]
            sc_label = f" · {m['scenario']}" if m.get("scenario") else ""
            title_text = f"Mapa de Distribuição — {period_label}{sc_label}"
        _r = _IR(io.BytesIO(m["map_png"]))
        _pw, _ph = _r.getSize()
        _max_w = 14 * cm
        _max_h = 18 * cm  # nunca ultrapassar a altura da página
        _dh = _max_w * (_ph / _pw)
        if _dh > _max_h:
            _dh = _max_h
            _max_w = _max_h * (_pw / _ph)
        # KeepTogether garante que o título do mapa e a imagem nunca se separam
        # entre páginas.
        return [
            Spacer(1, 0.3 * cm),
            KeepTogether([
                Paragraph(title_text, styles["h3"]),
                Image(io.BytesIO(m["map_png"]), width=_max_w, height=_dh),
            ]),
            Spacer(1, 0.5 * cm),
        ]

    # Chave única por mapa; "match" = espécie a que o mapa pertence (para casar
    # com o marcador [[MAPA:espécie]] e com a heurística por título).
    map_lookup: dict[str, dict] = {}
    if species_maps:
        for m in species_maps:
            match = (m.get("match_species") or m["species"]).lower()
            map_lookup[m["species"].lower()] = {"data": m, "inserted": False, "match": match}

    # Quando o relatório usa marcadores [[MAPA:…]], desligamos a heurística por
    # título (os mapas são colocados no sítio exato do marcador).
    has_map_markers = "[[MAPA:" in report_text

    def _insert_maps_for(species_name: str) -> None:
        """Insere todos os mapas (distribuição + recorte) de uma espécie."""
        alvo = species_name.strip().lower()
        for entry in map_lookup.values():
            if not entry["inserted"] and entry["match"] == alvo:
                for fl in _map_image_flowables(entry["data"]):
                    story.append(fl)
                entry["inserted"] = True

    def _try_insert_map(heading_text: str) -> None:
        """Heurística (sem marcadores): insere o mapa cujo nome esteja no título."""
        if has_map_markers:
            return
        h_lower = heading_text.lower()
        for entry in map_lookup.values():
            if not entry["inserted"] and entry["match"] in h_lower:
                for fl in _map_image_flowables(entry["data"]):
                    story.append(fl)
                entry["inserted"] = True
                break

    # Parser de markdown
    lines = report_text.split("\n")
    i = 0
    pending_map_after_table: dict | None = None  # mapa a inserir após a próxima tabela

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            story.append(Spacer(1, 3))
            i += 1
            continue

        # Marcador de mapa: [[MAPA:Nome da espécie]] → insere aqui o(s) mapa(s)
        marker = re.match(r'^\[\[MAPA:(.+?)\]\]$', stripped)
        if marker:
            _insert_maps_for(marker.group(1))
            i += 1
            continue

        # Linha "**Título:** Texto" → título principal (h1) só com o texto,
        # sem o rótulo "Título:".
        mt = re.match(r'^\*\*\s*T[íi]tulo\s*:?\s*\*\*\s*[:\-—]?\s*(.+)$', stripped)
        if mt:
            titulo = mt.group(1).strip()
            story.append(Paragraph(_fmt(titulo), styles["h1"]))
            story.append(HRFlowable(width="100%", thickness=1.2,
                                    color=GREEN_MID, spaceAfter=4))
            _try_insert_map(titulo)
            i += 1
            continue

        # H1: linha que começa com # ou texto todo a bold curto
        if stripped.startswith("# "):
            text = stripped[2:].strip()
            story.append(Paragraph(_fmt(text), styles["h1"]))
            story.append(HRFlowable(width="100%", thickness=1.2,
                                    color=GREEN_MID, spaceAfter=4))
            _try_insert_map(text)
            i += 1
            continue

        # Título bold isolado (ex: **RELATÓRIO TÉCNICO**)
        if re.match(r'^\*\*[^*]+\*\*$', stripped) and len(stripped) < 80:
            text = stripped.strip("*")
            story.append(Paragraph(text, styles["h1"]))
            story.append(HRFlowable(width="100%", thickness=1.2,
                                    color=GREEN_MID, spaceAfter=4))
            _try_insert_map(text)
            i += 1
            continue

        # H2
        if stripped.startswith("## "):
            text = stripped[3:]
            story.append(Paragraph(_fmt(text), styles["h2"]))
            _try_insert_map(text)
            i += 1
            continue

        # H3
        if stripped.startswith("### "):
            text = stripped[4:]
            story.append(Paragraph(_fmt(text), styles["h3"]))
            _try_insert_map(text)
            i += 1
            continue

        # Tabela markdown
        if stripped.startswith("|"):
            # Sem marcadores: insere o 1.º mapa pendente antes da tabela (comportamento
            # antigo). Com marcadores, os mapas já foram colocados no sítio certo.
            if not has_map_markers:
                for entry in map_lookup.values():
                    if not entry["inserted"]:
                        for fl in _map_image_flowables(entry["data"]):
                            story.append(fl)
                        entry["inserted"] = True
                        break

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

    # Mapas ainda não inseridos (espécies não mencionadas no texto)
    for entry in map_lookup.values():
        if not entry["inserted"]:
            for fl in _map_image_flowables(entry["data"]):
                story.append(fl)
            entry["inserted"] = True

    # Gráficos estatísticos (cada um pode ser bytes ou {'caption','png'})
    if charts:
        story.append(Spacer(1, 0.4 * cm))
        story.append(Paragraph("Análise Estatística", styles["h2"]))
        story.append(HRFlowable(width="100%", thickness=1.2, color=GREEN_MID, spaceAfter=6))
        for chart in charts:
            caption = chart.get("caption") if isinstance(chart, dict) else None
            analysis = chart.get("analysis") if isinstance(chart, dict) else None
            png = chart["png"] if isinstance(chart, dict) else chart
            img = Image(io.BytesIO(png), width=14 * cm, height=7.5 * cm)
            img.hAlign = "LEFT"
            if caption:
                # título e gráfico nunca se separam entre páginas
                story.append(KeepTogether([Paragraph(caption, styles["h3"]), img]))
            else:
                story.append(img)
            story.append(Spacer(1, 0.2 * cm))
            # Análise da IA do gráfico (uma frase ou duas) logo a seguir.
            if analysis:
                story.append(Paragraph(_fmt(analysis), styles["body"]))
            story.append(Spacer(1, 0.4 * cm))

    def _page_cb(canvas, doc):
        _on_page(canvas, doc, level, date_str)

    # multiBuild: necessário para resolver os números de página do Índice.
    doc.multiBuild(story, onFirstPage=_page_cb, onLaterPages=_page_cb)
    buf.seek(0)
    return buf.read()