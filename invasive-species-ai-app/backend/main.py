from collections import Counter
from pathlib import Path
import io
import json
import textwrap
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from agent_report import generate_agent_report, normalize_report_level

# Define o caminho para o ficheiro de dados
DATA_FILE = Path(__file__).parent / "records.json"

app = FastAPI(
    title="Invasive Species AI API",
    description="API para explorar registos georreferenciados de espécies invasoras.",
    version="0.1.0",
)

# Configuração de CORS para permitir que o frontend aceda à API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def load_records() -> list[dict]:
    """Carrega os registos a partir do ficheiro JSON."""
    if not DATA_FILE.exists():
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as file:
        return json.load(file)

@app.get("/")
def root():
    return {
        "message": "API de espécies invasoras operacional.",
        "docs": "/docs",
    }

@app.get("/records")
def get_records():
    """Devolve todos os registos."""
    return load_records()

@app.get("/species")
def get_species():
    """Devolve a lista de espécies presentes nos dados."""
    records = load_records()
    return sorted({record["species"] for record in records})

@app.get("/municipalities")
def get_municipalities():
    """Devolve a lista de municípios presentes nos dados."""
    records = load_records()
    return sorted({record["municipality"] for record in records})

@app.get("/records/by-species/{species_name}")
def get_records_by_species(species_name: str):
    """Devolve os registos de uma espécie específica."""
    records = load_records()
    return [
        record for record in records
        if record["species"].lower() == species_name.lower()
    ]

@app.get("/summary")
def get_summary(species: str = None, municipality: str = None):
    """Calcula indicadores simples de prevalência."""
    records = load_records()
    
    # Filtrar por espécie se selecionada
    if species:
        records = [r for r in records if r["species"].lower() == species.lower()]
    
    # Filtrar por município se selecionado
    if municipality:
        records = [r for r in records if r["municipality"].lower() == municipality.lower()]
    
    total = len(records)
    species_count = Counter(record["species"] for record in records)
    municipality_count = Counter(record["municipality"] for record in records)
    species_percentages = {
        sp: round((count / total) * 100, 1)
        for sp, count in species_count.items()
    } if total > 0 else {}
    hotspots = [
        {
            "municipality": mun,
            "records": count,
        }
        for mun, count in municipality_count.most_common()
        if count >= 3
    ]
    most_common_species = species_count.most_common(1)[0][0] if species_count else None
    most_common_municipality = municipality_count.most_common(1)[0][0] if municipality_count else None
    return {
        "total_records": total,
        "species_count": dict(species_count),
        "species_percentages": species_percentages,
        "municipality_count": dict(municipality_count),
        "most_common_species": most_common_species,
        "most_common_municipality": most_common_municipality,
    }


def validate_report_text(report: str, summary: dict) -> dict:
    problems = []
    # Verifica presença do total de registos (com segurança caso falte a chave)
    total = str(summary.get("total_records", ""))
    if total and total not in report:
        problems.append("O número total de registos pode estar ausente ou incorreto.")

    # Verifica espécie e município dominantes quando disponíveis
    most_common_species = summary.get("most_common_species")
    if most_common_species and most_common_species not in report:
        problems.append("A espécie dominante não foi mencionada.")

    most_common_municipality = summary.get("most_common_municipality")
    if most_common_municipality and most_common_municipality not in report:
        problems.append("O município dominante não foi mencionado.")

    lower_report = report.lower()
    if "limita" not in lower_report:
        problems.append("O relatório pode não incluir limitações.")

    return {
        "valid": len(problems) == 0,
        "problems": problems,
    }


def build_template_report(summary: dict, level: str) -> str:
    total = summary.get("total_records", 0)
    species = summary.get("most_common_species", "N/A")
    municipality = summary.get("most_common_municipality", "N/A")
    if level == "executivo":
        return f"""## 1. Titulo
**Relatorio Executivo: Especies Invasoras**

## 2. Resumo executivo
Foram analisados {total} registos. A especie dominante e *{species}* e o municipio com mais registos e *{municipality}*.

## 3. Limitações
- Relatorio baseado em dados disponiveis no sistema.
- Analise preliminar sem validacao externa.
"""
    if level == "publico":
        return f"""## 1. Titulo
**Relatorio Publico: Especies Invasoras**

## 2. Resumo
Este relatorio usa {total} registos. A especie mais observada foi *{species}* e o municipio com mais registos foi *{municipality}*.

## 3. Limitações
- Os dados podem ter falhas e lacunas.
- O texto e apenas preliminar.
"""
    return f"""## 1. Titulo
**Relatorio Tecnico: Prevalencia de Especies Invasoras**

## 2. Resumo executivo
Foram analisados {total} registos. A especie dominante e *{species}* e o municipio com mais registos e *{municipality}*.

## 3. Limitações
- Relatorio gerado automaticamente.
- Pode existir enviesamento de amostragem.
"""


def build_report_payload(level: str, species: str = None, municipality: str = None) -> dict:
    summary = get_summary(species=species, municipality=municipality)
    try:
        report = generate_agent_report(level, species=species, municipality=municipality)
        source = "langchain_agent"
    except Exception as error:
        report = build_template_report(summary, level)
        report = f"""{report}

Motivo tecnico: {str(error)}
""".strip()
        source = "template_fallback"
    validation = validate_report_text(report, summary)
    return {
        "source": source,
        "report": report,
        "validation": validation,
        "summary": summary,
        "level": level,
    }


def build_charts(summary: dict) -> list[bytes]:
    charts: list[bytes] = []
    for title, data in (
        ("Registos por especie", summary.get("species_count", {})),
        ("Registos por municipio", summary.get("municipality_count", {})),
    ):
        if not data:
            continue
        items = sorted(data.items(), key=lambda item: item[1], reverse=True)[:8]
        labels = [item[0] for item in items]
        values = [item[1] for item in items]
        fig, ax = plt.subplots(figsize=(6.5, 3.5))
        ax.bar(labels, values, color="#2d6a4f")
        ax.set_title(title)
        ax.set_ylabel("Registos")
        ax.tick_params(axis="x", rotation=30)
        fig.tight_layout()
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=150)
        plt.close(fig)
        buffer.seek(0)
        charts.append(buffer.read())
    return charts


def build_pdf(report_text: str, level: str, summary: dict) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 2 * cm
    y = height - margin

    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(margin, y, f"Relatorio ({level})")
    y -= 1.2 * cm

    pdf.setFont("Helvetica", 10)
    clean_text = report_text.replace("**", "").replace("## ", "")
    for paragraph in clean_text.split("\n"):
        lines = textwrap.wrap(paragraph, width=95) if paragraph else [""]
        for line in lines:
            if y <= margin:
                pdf.showPage()
                pdf.setFont("Helvetica", 10)
                y = height - margin
            pdf.drawString(margin, y, line)
            y -= 0.5 * cm
        y -= 0.2 * cm

    chart_images = build_charts(summary)
    if chart_images:
        pdf.showPage()
        y = height - margin
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(margin, y, "Graficos")
        y -= 1 * cm
        for chart in chart_images:
            if y <= 8 * cm:
                pdf.showPage()
                y = height - margin
            image = ImageReader(io.BytesIO(chart))
            pdf.drawImage(image, margin, y - 7 * cm, width=16 * cm, height=7 * cm, preserveAspectRatio=True)
            y -= 8 * cm

    pdf.save()
    buffer.seek(0)
    return buffer.read()


# Garante que tens este endpoint no backend/main.py

@app.post("/report-template")
def get_report_template(level: str = "tecnico", species: str = None, municipality: str = None):
    """
    Gera um relatório estático com base num template predefinido (Fallback).
    Não consome créditos da OpenAI.
    """
    try:
        normalized_level = normalize_report_level(level)
        summary_data = get_summary(species=species, municipality=municipality)
        report_text = build_template_report(summary_data, normalized_level)
        return {
            "source": "template_fallback",
            "report": report_text,
            "validation": {
                "valid": True,
                "problems": []
            },
            "level": normalized_level,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar template: {str(e)}")
    

@app.post("/report")
def generate_report(level: str = "tecnico", species: str = None, municipality: str = None):
    normalized_level = normalize_report_level(level)
    payload = build_report_payload(normalized_level, species=species, municipality=municipality)
    return {
        "source": payload["source"],
        "report": payload["report"],
        "validation": payload["validation"],
        "level": payload["level"],
    }


@app.get("/report/pdf")
def export_report_pdf(level: str = "tecnico", species: str = None, municipality: str = None):
    normalized_level = normalize_report_level(level)
    payload = build_report_payload(normalized_level, species=species, municipality=municipality)
    pdf_bytes = build_pdf(payload["report"], normalized_level, payload["summary"])
    filename = f"relatorio_{normalized_level}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )