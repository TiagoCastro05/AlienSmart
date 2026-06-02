from collections import Counter
from pathlib import Path
import io
import json
import textwrap
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from agent_report import generate_agent_report, normalize_report_level
from raster_tools import (
    get_available_species as get_raster_species_list,
    get_raster_files,
    parse_raster_filename,
    compute_suitable_area,
    get_raster_stats,
    compare_periods,
    overlap_two_species,
    scenario_matrix,
    get_raster_bounds,
    get_raster_data_samples,
    get_raster_legend,
)

# Router para servir overlays PNG e bounds para o Leaflet
from raster_tiles import router as tiles_router


# Define o caminho para o ficheiro de dados
DATA_FILE = Path(__file__).parent / "records.json"
RASTERS_DIR = Path(__file__).parent.parent / "Dados rasters"

app = FastAPI(

    title="Invasive Species AI API",
    description="API para explorar registos georreferenciados de espécies invasoras.",
    version="0.1.0",
)

# Mount router that serves /raster/overlay-info, /raster/tile, /raster/bounds
app.include_router(tiles_router)


# Configuração de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# [NOVO] Montar a pasta dos rasters para que o Leaflet consiga descarregar os .tif diretamente
if RASTERS_DIR.exists():
    app.mount("/rasters_data", StaticFiles(directory=RASTERS_DIR), name="rasters_data")

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
    """Devolve TODAS as espécies: do Records.json + dos rasters."""
    records = load_records()
    historical_species = set(record["species"] for record in records)
    raster_species = set(get_raster_species_list())
    all_species = sorted(historical_species | raster_species)
    return all_species

@app.get("/species-with-data-types")
def get_species_with_data_types():
    records = load_records()
    historical_species = set(record["species"] for record in records)
    raster_species = set(get_raster_species_list())
    
    result = []
    for sp in sorted(historical_species - raster_species):
        result.append({"species": sp, "data_types": ["historical"]})
    for sp in sorted(raster_species - historical_species):
        result.append({"species": sp, "data_types": ["raster"]})
    for sp in sorted(historical_species & raster_species):
        result.append({"species": sp, "data_types": ["historical", "raster"]})
    return result

@app.get("/municipalities")
def get_municipalities():
    records = load_records()
    return sorted({record["municipality"] for record in records})

@app.get("/records/by-species/{species_name}")
def get_records_by_species(species_name: str):
    records = load_records()
    return [
        record for record in records
        if record["species"].lower() == species_name.lower()
    ]

@app.get("/summary")
def get_summary(species: str = None, municipality: str = None):
    records = load_records()
    
    if species:
        records = [r for r in records if r["species"].lower() == species.lower()]
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
        "hotspots": hotspots,
    }


def validate_report_text(report: str, summary: dict) -> dict:
    problems = []
    total = str(summary.get("total_records", ""))
    if total and total not in report:
        problems.append("O número total de registos pode estar ausente ou incorreto.")

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
        return f"""## 1. Titulo\n**Relatorio Executivo: Especies Invasoras**\n\n## 2. Resumo executivo\nForam analisados {total} registos. A especie dominante e *{species}* e o municipio com mais registos e *{municipality}*.\n\n## 3. Limitações\n- Relatorio baseado em dados disponiveis no sistema.\n- Analise preliminar sem validacao externa.\n"""
    if level == "publico":
        return f"""## 1. Titulo\n**Relatorio Publico: Especies Invasoras**\n\n## 2. Resumo\nEste relatorio usa {total} registos. A especie mais observada foi *{species}* e o municipio com mais registos foi *{municipality}*.\n\n## 3. Limitações\n- Os dados podem ter falhas e lacunas.\n- O texto e apenas preliminar.\n"""
    return f"""## 1. Titulo\n**Relatorio Tecnico: Prevalencia de Especies Invasoras**\n\n## 2. Resumo executivo\nForam analisados {total} registos. A especie dominante e *{species}* e o municipio com mais registos e *{municipality}*.\n\n## 3. Limitações\n- Relatorio gerado automaticamente.\n- Pode existir enviesamento de amostragem.\n"""


def build_report_payload(level: str, species: str = None, municipality: str = None) -> dict:
    summary = get_summary(species=species, municipality=municipality)
    try:
        report = generate_agent_report(level, species=species, municipality=municipality)
        source = "Llama3.2_agent"
    except Exception as error:
        report = build_template_report(summary, level)
        report = f"""{report}\nMotivo tecnico: {str(error)}\n""".strip()
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

@app.post("/report-template")
def get_report_template(level: str = "tecnico", species: str = None, municipality: str = None):
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

@app.get("/raster/species")
def get_raster_species():
    return {"species": get_raster_species_list()}

@app.get("/raster/files")
def get_raster_files_endpoint(
    species: str = None,
    period: str = None,
    scenario: str = None,
    binary: bool = None
):
    files = get_raster_files(
        species=species,
        period=period,
        scenario=scenario,
        binary=binary
    )
    return {
        "count": len(files),
        "files": [Path(f).name for f in files],
    }

@app.get("/raster/parse/{filename}")
def parse_raster_filename_endpoint(filename: str):
    result = parse_raster_filename(filename)
    if result is None:
        raise HTTPException(status_code=400, detail="Nome de ficheiro inválido")
    return result

@app.get("/raster/stats/{species}")
def get_raster_stats_endpoint(species: str, period: str = "hist", scenario: str = None):
    files = get_raster_files(species=species, period=period, scenario=scenario, binary=True)
    if not files:
        raise HTTPException(status_code=404, detail=f"Nenhum raster encontrado para {species}")
    return {
        "species": species,
        "period": period,
        "scenario": scenario,
        "stats": get_raster_stats(files[0]),
    }

@app.get("/raster/suitable-area/{species}")
def get_suitable_area(species: str, period: str = "hist", scenario: str = None, threshold: float = 0.5):
    files = get_raster_files(species=species, period=period, scenario=scenario, binary=True)
    if not files:
        raise HTTPException(status_code=404, detail=f"Nenhum raster encontrado para {species}")
    return {
        "species": species,
        "period": period,
        "scenario": scenario,
        "threshold": threshold,
        "data": compute_suitable_area(files[0], threshold=threshold),
    }

@app.get("/raster/compare-periods/{species}")
def compare_periods_endpoint(species: str, scenario: str = "ssp370"):
    return compare_periods(species=species, scenario=scenario)

@app.get("/raster/overlap")
def get_overlap(species1: str, species2: str, period: str = "hist", operation: str = "intersection"):
    if operation not in ["intersection", "union", "difference"]:
        raise HTTPException(status_code=400, detail="Operação inválida")
    return overlap_two_species(species1=species1, species2=species2, period=period, operation=operation)

@app.get("/raster/scenarios/{species}")
def get_scenarios(species: str):
    return scenario_matrix(species=species)

@app.get("/raster/summary/{species}")
def get_raster_summary(species: str):
    species_list = get_raster_species_list()
    if species not in species_list:
        raise HTTPException(status_code=404, detail=f"Espécie não encontrada: {species}")
    return {
        "species": species,
        "available_periods": {
            "hist": bool(get_raster_files(species=species, period="hist")),
            "2041-2070": bool(get_raster_files(species=species, period="2041-2070")),
            "2071-2100": bool(get_raster_files(species=species, period="2071-2100")),
        },
        "scenario_analysis": scenario_matrix(species=species),
        "temporal_comparison": compare_periods(species=species),
    }

@app.get("/raster/bounds/{species}")
def get_raster_bounds_endpoint(species: str, period: str = "hist"):
    files = get_raster_files(species=species, period=period, binary=True)
    if not files:
        raise HTTPException(status_code=404, detail=f"Raster não encontrado: {species}/{period}")
    bounds_data = get_raster_bounds(files[0])
    if "error" in bounds_data:
        raise HTTPException(status_code=500, detail=bounds_data["error"])
    return bounds_data

@app.get("/raster/legend/{species}")
def get_raster_legend_endpoint(species: str, period: str = "hist"):
    files = get_raster_files(species=species, period=period, binary=True)
    if not files:
        raise HTTPException(status_code=404, detail=f"Raster não encontrado: {species}/{period}")
    legend_data = get_raster_legend(files[0])
    if "error" in legend_data:
        raise HTTPException(status_code=500, detail=legend_data["error"])
    return legend_data

@app.get("/raster/data/{species}")
def get_raster_data_endpoint(species: str, period: str = "hist", max_samples: int = 1000):
    files = get_raster_files(species=species, period=period, binary=True)
    if not files:
        raise HTTPException(status_code=404, detail=f"Raster não encontrado: {species}/{period}")
    data = get_raster_data_samples(files[0], max_samples=max_samples)
    if "error" in data:
        raise HTTPException(status_code=500, detail=data["error"])
    return data