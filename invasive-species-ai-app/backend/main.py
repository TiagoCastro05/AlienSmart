import logging
import io
import textwrap
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Optional
from pdf_builder import build_pdf

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
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
    compute_combined_invasive_raster,
)
from raster_tiles import router as tiles_router
from services.observation_service import get_observations
from services.report_service import save_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

RASTERS_DIR = Path(__file__).parent.parent / "Dados rasters"

app = FastAPI(
    title="Invasive Species AI API",
    description="API para explorar registos georreferenciados de espécies invasoras.",
    version="0.2.0",
)

app.include_router(tiles_router)

# ============================================================================
# HUB CENTRAL DE PLUGINS / APIs EXTENSÍVEIS
# ============================================================================
try:
    # Quando criares novos plugins na pasta plugins/, importa-os aqui:
    from plugins import weather_plugin  # , gdal_plugin, biodiversity_plugin

    ACTIVE_PLUGINS = [
        weather_plugin,
    ]
except ImportError:
    ACTIVE_PLUGINS = []
    print("⚠️ Pasta 'plugins' ou 'weather_plugin.py' ainda não foram criados.")

# 1. Registar automaticamente as rotas de todos os plugins ativos no FastAPI
for plugin in ACTIVE_PLUGINS:
    if hasattr(plugin, "router"):
        app.include_router(plugin.router)

# 2. Função para o 'agent_report.py' recolher as ferramentas de IA automaticamente
def get_plugin_ai_tools():
    ai_tools = []
    for plugin in ACTIVE_PLUGINS:
        for attr_name in dir(plugin):
            attr = getattr(plugin, attr_name)
            # Deteta funções decoradas com @tool do LangChain
            if hasattr(attr, "is_agent_tool") or type(attr).__name__ == "WrappedTool":
                ai_tools.append(attr)
    return ai_tools
# ============================================================================



app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if RASTERS_DIR.exists():
    app.mount("/rasters_data", StaticFiles(directory=RASTERS_DIR), name="rasters_data")


# ---------------------------------------------------------------------------
# Endpoints de dados
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {"message": "API de espécies invasoras operacional.", "docs": "/docs"}


@app.get("/records")
def get_records():
    return get_observations()


@app.get("/species")
def get_species():
    records = get_observations()
    historical_species = {r["species"] for r in records}
    raster_species = set(get_raster_species_list())
    return sorted(historical_species | raster_species)


@app.get("/species-with-data-types")
def get_species_with_data_types():
    records = get_observations()
    historical_species = {r["species"] for r in records}
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
    records = get_observations()
    return sorted({r["municipality"] for r in records if r.get("municipality")})


@app.get("/records/by-species/{species_name}")
def get_records_by_species(species_name: str):
    return get_observations(species=species_name)


@app.get("/summary")
def get_summary(species: str = None, municipality: str = None):
    records = get_observations(species=species, municipality=municipality)

    total = len(records)
    species_count = Counter(r["species"] for r in records)
    municipality_count = Counter(r["municipality"] for r in records if r.get("municipality"))
    species_percentages = (
        {sp: round((cnt / total) * 100, 1) for sp, cnt in species_count.items()}
        if total > 0 else {}
    )
    hotspots = [
        {"municipality": mun, "records": cnt}
        for mun, cnt in municipality_count.most_common()
        if cnt >= 3
    ]
    return {
        "total_records": total,
        "species_count": dict(species_count),
        "species_percentages": species_percentages,
        "municipality_count": dict(municipality_count),
        "most_common_species": species_count.most_common(1)[0][0] if species_count else None,
        "most_common_municipality": municipality_count.most_common(1)[0][0] if municipality_count else None,
        "hotspots": hotspots,
    }


# ---------------------------------------------------------------------------
# Relatórios
# ---------------------------------------------------------------------------

COLORMAP_STOPS: dict[str, list[str]] = {
    "Greens5": ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
    "YlOrRd":  ["#ffffcc", "#fed976", "#fd8d3c", "#e31a1c", "#800026"],
    "Blues":   ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"],
    "RdPu":    ["#feebe2", "#fbb4b9", "#f768a1", "#ae017e", "#49006a"],
    "BurntYellow": ["#fff7d4", "#ffe27a", "#e8b339", "#c9892a", "#9c5e0a"],
}


class SpeciesConfigItem(BaseModel):
    species: str
    period: str = "hist"
    scenario: str = "ssp370"
    binary: bool = True
    colormap: str = "Greens5"


class ReportRequestBody(BaseModel):
    species_configs: list[SpeciesConfigItem] = []


def _normalize_text(value: str) -> str:
    """Minúsculas e sem acentos, para comparações tolerantes."""
    nfkd = unicodedata.normalize("NFKD", value or "")
    sem_acentos = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return sem_acentos.lower()


def _mentions(norm_report: str, name: str) -> bool:
    """Verifica se `name` é referido no relatório, tolerando acentos, maiúsculas
    e o autor do nome científico (ex.: 'Acacia longifolia (Andrews) Willd.')."""
    norm_name = _normalize_text(name).strip()
    if not norm_name:
        return False
    if norm_name in norm_report:
        return True
    # match parcial por género + epíteto (as duas primeiras palavras)
    parts = norm_name.split()
    if len(parts) >= 2 and " ".join(parts[:2]) in norm_report:
        return True
    return False


def validate_report_text(report: str, summary: dict, species_configs: list = None) -> dict:
    problems = []
    norm_report = _normalize_text(report)

    total = str(summary.get("total_records", ""))
    if total and total not in report:
        problems.append("O número total de registos pode estar ausente ou incorreto.")

    # As espécies relevantes são as dos modelos SDM usadas no relatório;
    # se não houver, recai sobre a espécie dominante dos registos de campo.
    sdm_species = [c.get("species") for c in (species_configs or []) if c.get("species")]
    expected_species = sdm_species or (
        [summary["most_common_species"]] if summary.get("most_common_species") else []
    )
    missing_species = [s for s in expected_species if not _mentions(norm_report, s)]
    if missing_species:
        nomes = ", ".join(missing_species)
        problems.append(f"Espécie(s) não mencionada(s) no relatório: {nomes}.")

    most_common_municipality = summary.get("most_common_municipality")
    if most_common_municipality and not _mentions(norm_report, most_common_municipality):
        problems.append("O município dominante não foi mencionado.")

    if "limita" not in norm_report:
        problems.append("O relatório pode não incluir limitações.")

    return {"valid": len(problems) == 0, "problems": problems}


def build_template_report(summary: dict, level: str) -> str:
    total        = summary.get("total_records", 0)
    species      = summary.get("most_common_species", "N/A")
    municipality = summary.get("most_common_municipality", "N/A")
    if level == "executivo":
        return (
            f"## 1. Titulo\n**Relatorio Executivo: Especies Invasoras**\n\n"
            f"## 2. Resumo executivo\nForam analisados {total} registos. "
            f"A especie dominante e *{species}* e o municipio com mais registos e *{municipality}*.\n\n"
            f"## 3. Limitações\n- Relatorio baseado em dados disponiveis no sistema.\n"
            f"- Analise preliminar sem validacao externa.\n"
        )
    if level == "publico":
        return (
            f"## 1. Titulo\n**Relatorio Publico: Especies Invasoras**\n\n"
            f"## 2. Resumo\nEste relatorio usa {total} registos. "
            f"A especie mais observada foi *{species}* e o municipio com mais registos foi *{municipality}*.\n\n"
            f"## 3. Limitações\n- Os dados podem ter falhas e lacunas.\n"
            f"- O texto e apenas preliminar.\n"
        )
    return (
        f"## 1. Titulo\n**Relatorio Tecnico: Prevalencia de Especies Invasoras**\n\n"
        f"## 2. Resumo executivo\nForam analisados {total} registos. "
        f"A especie dominante e *{species}* e o municipio com mais registos e *{municipality}*.\n\n"
        f"## 3. Limitações\n- Relatorio gerado automaticamente.\n"
        f"- Pode existir enviesamento de amostragem.\n"
    )


def build_report_payload(
    level: str,
    species: str = None,
    municipality: str = None,
    species_configs: list = None,
) -> dict:
    summary = get_summary(municipality=municipality)
    try:
        report = generate_agent_report(
            level,
            species=species,
            municipality=municipality,
            species_configs=species_configs or [],
        )
        source = "Llama3.1_agent"
        logger.info("Relatório gerado pelo agente — nível=%s", level)
    except Exception as error:
        logger.warning("Agente falhou, usando template: %s", error)
        report = build_template_report(summary, level)
        report = f"{report}\nMotivo tecnico: {str(error)}".strip()
        source = "template_fallback"

    validation = validate_report_text(report, summary, species_configs=species_configs or [])
    save_report(
        level=level,
        report_text=report,
        source=source,
        validation=validation,
        species=species,
        municipality=municipality,
    )
    return {"source": source, "report": report, "validation": validation, "summary": summary, "level": level}


QGIS_PYTHON = Path(r"C:\Program Files\QGIS 3.44.11\bin\python3.exe")
QGIS_ENV = {
    "OSGEO4W_ROOT":    r"C:\Program Files\QGIS 3.44.11",
    "QGIS_PREFIX_PATH": r"C:/Program Files/QGIS 3.44.11/apps/qgis-ltr",
    "PATH": (
        r"C:\Program Files\QGIS 3.44.11\bin"
        r";C:\Program Files\QGIS 3.44.11\apps\qgis-ltr\bin"
        r";C:\Program Files\QGIS 3.44.11\apps\Qt5\bin"
        r";C:\Windows\system32;C:\Windows"
    ),
    "QT_PLUGIN_PATH": (
        r"C:\Program Files\QGIS 3.44.11\apps\qgis-ltr\qtplugins"
        r";C:\Program Files\QGIS 3.44.11\apps\qt5\plugins"
    ),
    "PYTHONPATH":  r"C:\Program Files\QGIS 3.44.11\apps\qgis-ltr\python",
    "PYTHONHOME":  r"C:\Program Files\QGIS 3.44.11\apps\Python312",
    "GDAL_DATA":   r"C:\Program Files\QGIS 3.44.11\share\gdal",
    "PROJ_LIB":    r"C:\Program Files\QGIS 3.44.11\share\proj",
}
_QGIS_RENDER_SCRIPT = Path(__file__).parent / "qgis_render.py"


def build_raster_map_image(
    species: str,
    period: str = "hist",
    scenario: str = None,
    colormap_name: str = "Greens5",
) -> bytes | None:
    """Renderiza o raster SDM via PyQGIS (subprocesso) — basemap OSM + cores do utilizador."""
    files = get_raster_files(species=species, period=period, scenario=scenario, binary=True)
    logger.info("[raster map] species=%r period=%r scenario=%r cmap=%r → %d ficheiro(s)",
                species, period, scenario, colormap_name, len(files))
    if not files:
        logger.warning("[raster map] Nenhum TIF encontrado para %r / %r / %r", species, period, scenario)
        return None

    import subprocess, tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        out_png = tmp.name

    args = [
        str(QGIS_PYTHON),
        str(_QGIS_RENDER_SCRIPT),
        files[0],
        out_png,
        colormap_name,
        species,
        period,
    ]
    if scenario:
        args.append(scenario)

    try:
        result = subprocess.run(
            args, env=QGIS_ENV, capture_output=True, text=True, timeout=120,
            cwd=tempfile.gettempdir(),
        )
        if result.stderr:
            logger.debug("[qgis_render stderr] %s", result.stderr[:500])
        out_path = Path(out_png)
        if out_path.exists() and out_path.stat().st_size > 1000:
            data = out_path.read_bytes()
            out_path.unlink(missing_ok=True)
            logger.info("[raster map] PNG gerado via PyQGIS: %d bytes", len(data))
            return data
        logger.warning("[raster map] PNG não gerado ou vazio para %r", species)
        return None
    except Exception as exc:
        logger.warning("[raster map] Erro no subprocesso PyQGIS: %s", exc, exc_info=True)
        return None


def build_combined_map_image(species_configs: list, threshold: int = 2) -> bytes | None:
    """
    Soma os rasters binários das espécies selecionadas e renderiza as zonas onde
    `threshold` ou mais espécies coexistem ("Muito invasivo").
    """
    if len(species_configs) < 2:
        return None

    combined = compute_combined_invasive_raster(species_configs, threshold=threshold)
    if "error" in combined:
        logger.warning("[combined map] %s", combined["error"])
        return None

    combined_tif = combined["path"]
    import subprocess, tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        out_png = tmp.name

    args = [
        str(QGIS_PYTHON),
        str(_QGIS_RENDER_SCRIPT),
        combined_tif,
        out_png,
        "BurntYellow",
        "Sobreposição",
        "hist",
        "--label-suitable=Muito invasivo",
    ]

    try:
        result = subprocess.run(
            args, env=QGIS_ENV, capture_output=True, text=True, timeout=120,
            cwd=tempfile.gettempdir(),
        )
        if result.stderr:
            logger.debug("[qgis_render stderr] %s", result.stderr[:500])
        out_path = Path(out_png)
        if out_path.exists() and out_path.stat().st_size > 1000:
            data = out_path.read_bytes()
            out_path.unlink(missing_ok=True)
            logger.info("[combined map] PNG gerado via PyQGIS: %d bytes", len(data))
            return data
        logger.warning("[combined map] PNG não gerado ou vazio")
        return None
    except Exception as exc:
        logger.warning("[combined map] Erro no subprocesso PyQGIS: %s", exc, exc_info=True)
        return None
    finally:
        Path(combined_tif).unlink(missing_ok=True)


def build_charts(summary: dict) -> list[bytes]:
    charts: list[bytes] = []
    for title, data in (
        ("Registos por especie", summary.get("species_count", {})),
        ("Registos por municipio", summary.get("municipality_count", {})),
    ):
        if not data:
            continue
        items = sorted(data.items(), key=lambda x: x[1], reverse=True)[:8]
        labels = [x[0] for x in items]
        values = [x[1] for x in items]
        fig, ax = plt.subplots(figsize=(6.5, 3.5))
        ax.bar(labels, values, color="#2d6a4f")
        ax.set_title(title)
        ax.set_ylabel("Registos")
        ax.tick_params(axis="x", rotation=30)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150)
        plt.close(fig)
        buf.seek(0)
        charts.append(buf.read())
    return charts



def _build_species_maps(configs: list) -> list[dict]:
    maps = []
    for cfg in configs:
        sp            = cfg["species"]
        period        = cfg.get("period", "hist")
        scenario      = cfg.get("scenario") if period != "hist" else None
        colormap_name = cfg.get("colormap", "Greens5")
        logger.info("[PDF maps] A gerar mapa: especie=%r period=%r scenario=%r cmap=%r", sp, period, scenario, colormap_name)
        png = build_raster_map_image(sp, period, scenario, colormap_name=colormap_name)
        if png:
            logger.info("[PDF maps] Mapa gerado: %d bytes", len(png))
            maps.append({"species": sp, "period": period, "scenario": scenario, "map_png": png})
        else:
            logger.warning("[PDF maps] Mapa devolveu None para %r / %r / %r", sp, period, scenario)

    if len(configs) >= 2:
        combined_png = build_combined_map_image(configs)
        if combined_png:
            maps.append({
                "species": "__combined__",
                "period": "hist",
                "scenario": None,
                "map_png": combined_png,
                "title": "Mapa de Sobreposição — Zonas Muito Invasivas (2+ espécies)",
            })
        else:
            logger.warning("[PDF maps] Mapa combinado devolveu None")

    logger.info("[PDF maps] Total mapas gerados: %d", len(maps))
    return maps


@app.post("/report-template")
def get_report_template(level: str = "tecnico", species: str = None, municipality: str = None):
    try:
        normalized_level = normalize_report_level(level)
        summary_data = get_summary(species=species, municipality=municipality)
        report_text = build_template_report(summary_data, normalized_level)
        return {
            "source": "template_fallback",
            "report": report_text,
            "validation": {"valid": True, "problems": []},
            "level": normalized_level,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar template: {str(exc)}")


@app.post("/report")
def generate_report(
    level: str = "tecnico",
    municipality: str = None,
    body: Optional[ReportRequestBody] = None,
):
    normalized_level = normalize_report_level(level)
    configs = [cfg.model_dump() for cfg in body.species_configs] if body else []
    species = configs[0]["species"] if len(configs) == 1 else None
    payload = build_report_payload(
        normalized_level,
        species=species,
        municipality=municipality,
        species_configs=configs,
    )
    return {
        "source": payload["source"],
        "report": payload["report"],
        "validation": payload["validation"],
        "level": payload["level"],
    }


@app.get("/report/pdf")
def export_report_pdf(
    level: str = "tecnico",
    species: str = None,
    municipality: str = None,
    period: str = "hist",
    scenario: str = "ssp370",
):
    normalized_level = normalize_report_level(level)
    configs = [{"species": species, "period": period, "scenario": scenario, "binary": True}] if species else []
    payload = build_report_payload(normalized_level, species=species, municipality=municipality, species_configs=configs)
    filters = {"species": [species] if species else [], "municipality": municipality}
    species_maps = _build_species_maps(configs)
    charts = build_charts(payload["summary"])
    pdf_bytes = build_pdf(payload["report"], normalized_level, filters=filters, species_maps=species_maps, charts=charts)
    filename = f"relatorio_{normalized_level}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.post("/report/pdf")
def export_report_pdf_post(
    level: str = "tecnico",
    municipality: str = None,
    body: Optional[ReportRequestBody] = None,
):
    normalized_level = normalize_report_level(level)
    configs = [cfg.model_dump() for cfg in body.species_configs] if body and body.species_configs else []
    species = configs[0]["species"] if len(configs) == 1 else None
    payload = build_report_payload(normalized_level, species=species, municipality=municipality, species_configs=configs)
    filters = {"species": [c["species"] for c in configs], "municipality": municipality}
    species_maps = _build_species_maps(configs)
    charts = build_charts(payload["summary"])
    pdf_bytes = build_pdf(payload["report"], normalized_level, filters=filters, species_maps=species_maps, charts=charts)
    filename = f"relatorio_{normalized_level}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Endpoints raster
# ---------------------------------------------------------------------------

@app.post("/raster/combined")
def get_combined_raster_map(body: ReportRequestBody):
    """Gera (on-demand) o mapa de sobreposição entre as espécies selecionadas."""
    configs = [cfg.model_dump() for cfg in body.species_configs] if body.species_configs else []
    if len(configs) < 2:
        raise HTTPException(status_code=400, detail="Seleciona pelo menos 2 espécies para gerar o mapa combinado.")
    png = build_combined_map_image(configs)
    if not png:
        raise HTTPException(status_code=500, detail="Não foi possível gerar o mapa combinado.")
    return Response(content=png, media_type="image/png")


@app.get("/raster/species")
def get_raster_species():
    return {"species": get_raster_species_list()}


@app.get("/raster/files")
def get_raster_files_endpoint(species: str = None, period: str = None, scenario: str = None, binary: bool = None):
    files = get_raster_files(species=species, period=period, scenario=scenario, binary=binary)
    return {"count": len(files), "files": [Path(f).name for f in files]}


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
    return {"species": species, "period": period, "scenario": scenario, "stats": get_raster_stats(files[0])}


@app.get("/raster/suitable-area/{species}")
def get_suitable_area(species: str, period: str = "hist", scenario: str = None, threshold: float = 0.5):
    files = get_raster_files(species=species, period=period, scenario=scenario, binary=True)
    if not files:
        raise HTTPException(status_code=404, detail=f"Nenhum raster encontrado para {species}")
    return {
        "species": species, "period": period, "scenario": scenario,
        "threshold": threshold, "data": compute_suitable_area(files[0], threshold=threshold),
    }


@app.get("/raster/compare-periods/{species}")
def compare_periods_endpoint(species: str, scenario: str = "ssp370"):
    return compare_periods(species=species, scenario=scenario)


@app.get("/raster/overlap")
def get_overlap(species1: str, species2: str, period: str = "hist", operation: str = "intersection"):
    if operation not in {"intersection", "union", "difference"}:
        raise HTTPException(status_code=400, detail="Operação inválida")
    return overlap_two_species(species1=species1, species2=species2, period=period, operation=operation)


@app.get("/raster/scenarios/{species}")
def get_scenarios(species: str):
    return scenario_matrix(species=species)


@app.get("/raster/summary/{species}")
def get_raster_summary(species: str):
    if species not in get_raster_species_list():
        raise HTTPException(status_code=404, detail=f"Espécie não encontrada: {species}")
    return {
        "species": species,
        "available_periods": {
            "hist":      bool(get_raster_files(species=species, period="hist")),
            "2041-2070": bool(get_raster_files(species=species, period="2041-2070")),
            "2071-2100": bool(get_raster_files(species=species, period="2071-2100")),
        },
        "scenario_analysis":   scenario_matrix(species=species),
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


@app.get("/dashboard-stats")
def get_dashboard_stats():
    all_raster_species = get_raster_species_list()
    species_file_counts = {}
    for sp in all_raster_species:
        files = get_raster_files(species=sp, period="hist", binary=True)
        if files:
            species_file_counts[sp] = get_raster_stats(files[0]).get("count", 0)

    top5_species = sorted(species_file_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    records = get_observations()
    municipality_count = Counter(r["municipality"] for r in records if r.get("municipality"))
    top5_municipalities = municipality_count.most_common(5)

    return {
        "total_raster_species":  len(all_raster_species),
        "total_records":         len(records),
        "top5_species":          [{"species": sp, "pixel_count": cnt} for sp, cnt in top5_species],
        "top5_municipalities":   [{"municipality": m,  "records": c}  for m,  c  in top5_municipalities],
    }
