from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
import os
import json
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage

from services.observation_service import get_observations

load_dotenv()

logger = logging.getLogger(__name__)

# Cache em memória — carregada uma vez do Supabase na primeira utilização
_raster_cache: list[dict] | None = None


def _get_raster_cache() -> list[dict]:
    global _raster_cache
    if _raster_cache is None:
        try:
            from raster_db import get_raster_stats
            _raster_cache = get_raster_stats().data or []
            logger.info("Cache raster carregada: %d registos", len(_raster_cache))
        except Exception as exc:
            logger.warning("Não foi possível carregar cache raster: %s", exc)
            _raster_cache = []
    return _raster_cache


def _cache_lookup(species: str, period: str, scenario: str | None) -> dict | None:
    for row in _get_raster_cache():
        if (
            row.get("species_name") == species
            and row.get("period") == period
            and row.get("scenario") == scenario
        ):
            return row
    return None


def _cache_by_period(period: str, scenario: str | None) -> list[dict]:
    return [
        r for r in _get_raster_cache()
        if r.get("period") == period and r.get("scenario") == scenario
    ]

# Filtros globais — definidos por generate_agent_report antes de invocar o agente
_selected_species: str | None = None
_selected_municipality: str | None = None


def _get_records() -> list[dict]:
    return get_observations(species=_selected_species, municipality=_selected_municipality)


def calculate_summary() -> dict:
    records = _get_records()
    total = len(records)
    species_count = Counter(r.get("species") for r in records if r.get("species"))
    municipality_count = Counter(r.get("municipality") for r in records if r.get("municipality"))
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


# ── Tools de registos de campo ───────────────────────────────────────────────

@tool
def get_summary_tool() -> str:
    """Devolve um resumo estatístico dos registos de campo em JSON."""
    return json.dumps(calculate_summary(), ensure_ascii=False, indent=2)


@tool
def get_species_list_tool() -> str:
    """Devolve a lista de espécies presentes nos registos de campo."""
    records = _get_records()
    species = sorted({r.get("species") for r in records if r.get("species")})
    return json.dumps(species, ensure_ascii=False)


@tool
def get_records_by_species_tool(species_name: str) -> str:
    """Devolve todos os registos detalhados associados a uma espécie específica."""
    records = get_observations(species=species_name)
    return json.dumps(records, ensure_ascii=False, indent=2)


# ── Tools raster (SDM) ───────────────────────────────────────────────────────

@tool
def get_raster_species_list_tool() -> str:
    """
    Devolve a lista completa de espécies invasoras disponíveis nos modelos SDM (rasters).
    Estas são as espécies com dados de distribuição modelada, podendo ser mais do que
    as espécies nos registos de campo.
    """
    try:
        from raster_tools import get_available_species
        species = get_available_species()
        return json.dumps(species, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
def get_raster_suitable_area_tool(species_name: str, period: str = "hist", scenario: str = "ssp370") -> str:
    """
    Devolve a área adequada (km²) para uma espécie invasora com base no modelo SDM (raster).
    Usa o raster binário histórico ou futuro.

    Parâmetros:
        species_name: nome científico da espécie (ex: "Acacia dealbata")
        period: "hist" (histórico 1981-2024), "2041-2070" ou "2071-2100"
        scenario: "ssp126", "ssp370" ou "ssp585" (só para períodos futuros)

    Devolve área em km², percentagem adequada e número de píxeis.
    """
    try:
        sc = None if period == "hist" else scenario
        cached = _cache_lookup(species_name, period, sc)
        if cached:
            return json.dumps({
                "species": species_name,
                "period": period,
                "scenario": scenario if period != "hist" else "N/A",
                "suitable_area_km2": cached.get("suitable_area_km2"),
                "suitable_pct": None,
                "n_suitable_pixels": None,
            }, ensure_ascii=False)
        # Fallback: calcular dos TIFs
        from raster_tools import get_raster_files, compute_suitable_area
        files = get_raster_files(species=species_name, period=period, scenario=sc, binary=True)
        if not files:
            return json.dumps({"error": f"Sem raster para {species_name} / {period}"})
        result = compute_suitable_area(files[0])
        return json.dumps({
            "species": species_name,
            "period": period,
            "scenario": scenario if period != "hist" else "N/A",
            "suitable_area_km2": result.get("suitable_area_km2"),
            "suitable_pct": result.get("suitable_pct"),
            "n_suitable_pixels": result.get("n_suitable_pixels"),
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
def get_raster_trend_tool(species_name: str, scenario: str = "ssp370") -> str:
    """
    Compara a área adequada de uma espécie entre o período histórico e os períodos futuros,
    indicando se a espécie está em expansão, contração ou estável.

    Parâmetros:
        species_name: nome científico da espécie
        scenario: "ssp126", "ssp370" ou "ssp585"

    Devolve a área por período e a variação percentual face ao histórico.
    Útil para perceber a tendência de invasão no futuro.
    """
    try:
        by_period = {}
        for p in ["hist", "2041-2070", "2071-2100"]:
            sc = None if p == "hist" else scenario
            row = _cache_lookup(species_name, p, sc)
            if row:
                by_period[p] = {"suitable_area_km2": row.get("suitable_area_km2"), "suitable_pct": None}
        if len(by_period) == 3:
            hist_area = by_period["hist"]["suitable_area_km2"] or 0
            area_2041 = by_period["2041-2070"]["suitable_area_km2"] or 0
            area_2071 = by_period["2071-2100"]["suitable_area_km2"] or 0
            return json.dumps({
                "species": species_name,
                "scenario": scenario,
                "by_period": by_period,
                "changes": {
                    "hist_to_2041_2070_km2": round(area_2041 - hist_area, 2),
                    "hist_to_2071_2100_km2": round(area_2071 - hist_area, 2),
                    "hist_to_2041_2070_pct": round((area_2041 - hist_area) / hist_area * 100, 2) if hist_area > 0 else 0,
                    "hist_to_2071_2100_pct": round((area_2071 - hist_area) / hist_area * 100, 2) if hist_area > 0 else 0,
                },
            }, ensure_ascii=False, indent=2)
        # Fallback: calcular dos TIFs
        from raster_tools import compare_periods
        result = compare_periods(species=species_name, scenario=scenario)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
def get_raster_top_species_tool(top_n: int = 5, period: str = "hist") -> str:
    """
    Devolve as espécies com maior área adequada segundo os modelos SDM,
    ordenadas por área (km²) decrescente.

    Parâmetros:
        top_n: número de espécies a devolver (padrão: 5)
        period: "hist", "2041-2070" ou "2071-2100"

    Útil para identificar as espécies mais prevalentes nos modelos de distribuição.
    """
    try:
        sc = None if period == "hist" else "ssp370"
        rows = _cache_by_period(period, sc)
        if rows:
            results = sorted(
                [{"species": r["species_name"], "suitable_area_km2": r.get("suitable_area_km2") or 0} for r in rows],
                key=lambda x: x["suitable_area_km2"],
                reverse=True,
            )
            return json.dumps(results[:top_n], ensure_ascii=False, indent=2)
        # Fallback: calcular dos TIFs
        from raster_tools import get_available_species, get_raster_files, compute_suitable_area
        all_species = get_available_species()
        results = []
        for sp in all_species:
            files = get_raster_files(species=sp, period=period, binary=True)
            if files:
                stats = compute_suitable_area(files[0])
                area = stats.get("suitable_area_km2", 0) or 0
                results.append({"species": sp, "suitable_area_km2": area, "suitable_pct": stats.get("suitable_pct", 0)})
        results.sort(key=lambda x: x["suitable_area_km2"], reverse=True)
        return json.dumps(results[:top_n], ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
def get_raster_municipality_overlap_tool(species_name: str, municipality_name: str) -> str:
    """
    Faz o recorte (clip) espacial do modelo SDM (raster) usando a fronteira do município
    para devolver a área exata adequada (em km²) apenas dentro desse concelho.
    """
    try:
        from raster_tools import get_raster_files, compute_suitable_area
        # Registos de campo no município
        if not DATA_FILE.exists():
            field_records = []
        else:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                all_records = json.load(f)
            field_records = [
                r for r in all_records
                if r.get("species", "").lower() == species_name.lower()
                and r.get("municipality", "").lower() == municipality_name.lower()
            ]

        # Área SDM histórica
        files = get_raster_files(species=species_name, period="hist", binary=True)
        if not files:
            return json.dumps({"erro": f"Sem raster disponível para a espécie {species_name}."})
            
        raster_path = files[0]
        
        # 2. Carregar o GeoJSON dos Municípios
        url = "https://raw.githubusercontent.com/nmota/caop_GeoJSON/master/Portugal_Municipalities.geojson"
        gdf = gpd.read_file(url)
        
        # 3. Filtrar pelo município pretendido
        muni_gdf = gdf[gdf['Concelho'].str.lower() == municipality_name.lower()]
        
        if muni_gdf.empty:
            return json.dumps({"erro": f"Polígono do município '{municipality_name}' não encontrado no GeoJSON."})
            
        # 4. Fazer o Recorte (Clip) do Raster
        with rasterio.open(raster_path) as src:
            # Converter coordenadas do município para baterem certo com o raster
            muni_gdf = muni_gdf.to_crs(src.crs)
            geom = [muni_gdf.geometry.values[0]]
            
            # Máscara: recorta a imagem pelos limites do concelho
            out_image, out_transform = mask(src, geom, crop=True)
            
            # Contar píxeis ativos (onde o raster SDM diz que há presença = 1)
            pixels_ativos = (out_image == 1).sum()
            
            # Calcular área em km2 (Resolução X * Resolução Y do pixel)
            res_x, res_y = src.res
            area_sp_km2 = (pixels_ativos * res_x * res_y) / 1_000_000
            
            # Calcular a área total do município para obter a percentagem
            area_municipio_km2 = muni_gdf.geometry.area.values[0] / 1_000_000
            pct_ocupacao = (area_sp_km2 / area_municipio_km2) * 100 if area_municipio_km2 > 0 else 0
            
            return json.dumps({
                "municipio": municipality_name,
                "especie": species_name,
                "area_total_municipio_km2": round(area_municipio_km2, 2),
                "area_adequada_especie_neste_municipio_km2": round(area_sp_km2, 2),
                "percentagem_do_municipio_ocupada": round(pct_ocupacao, 2)
            }, ensure_ascii=False, indent=2)
            
    except Exception as e:
        return json.dumps({"error": f"Falha ao recortar raster: {str(e)}"})

# ── Carregamento de prompts a partir de ficheiros .txt ───────────────────────

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    return (_PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()


SYSTEM_PROMPT = _load_prompt("system.txt")

LEVEL_PROMPTS = {
    "publico":   _load_prompt("level_publico.txt"),
    "tecnico":   _load_prompt("level_tecnico.txt"),
    "executivo": _load_prompt("level_executivo.txt"),
}

_USER_REQUEST_TEMPLATE = _load_prompt("user_request.txt")


def normalize_report_level(level: str | None) -> str:
    if not level:
        return "tecnico"
    normalized = level.strip().lower()
    if normalized in ("publico", "público"):
        return "publico"
    if normalized == "executivo":
        return "executivo"
    return "tecnico"


def build_agent():
    model = ChatOllama(model="llama3.1:8b", temperature=0)
    return create_agent(
        model=model,
        tools=[
            get_summary_tool,
            get_species_list_tool,
            get_records_by_species_tool,
            get_raster_species_list_tool,
            get_raster_suitable_area_tool,
            get_raster_trend_tool,
            get_raster_top_species_tool,
            get_raster_municipality_overlap_tool,
        ],
        system_prompt=SYSTEM_PROMPT,
    )


# Singleton do modelo — criado na primeira chamada, reutilizado em seguida
_model_instance = None


def _get_model() -> ChatOllama:
    global _model_instance
    if _model_instance is None:
        logger.info("A inicializar modelo LLM (Ollama llama3.1:8b)…")
        _model_instance = ChatOllama(model="llama3.1:8b", temperature=0)
    return _model_instance


def generate_agent_report(
    level: str | None = None,
    species: str | None = None,
    municipality: str | None = None,
    species_configs: list | None = None,
) -> str:
    normalized_level = normalize_report_level(level)
    level_instruction = LEVEL_PROMPTS.get(normalized_level, LEVEL_PROMPTS["tecnico"])

    # Pré-buscar dados raster para não obrigar o LLM a chamar ferramentas
    extra_data: dict = {}

    if species_configs:
        # Espécies específicas selecionadas pelo utilizador
        extra_data["species_data"] = []
        for cfg in species_configs:
            sp       = cfg["species"]
            period   = cfg.get("period", "hist")
            scenario = cfg.get("scenario", "ssp370")

            if municipality:
                overlap = json.loads(get_raster_municipality_overlap_tool.invoke({
                    "species_name": sp, "municipality_name": municipality
                }))
                entry = {
                    "especie": sp,
                    "periodo": period,
                    "cenario": scenario if period != "hist" else "histórico",
                    "area_adequada_municipio": overlap,
                }
            else:
                area  = json.loads(get_raster_suitable_area_tool.invoke({"species_name": sp, "period": period, "scenario": scenario}))
                trend = json.loads(get_raster_trend_tool.invoke({"species_name": sp, "scenario": scenario}))
                entry = {
                    "especie": sp,
                    "periodo": period,
                    "cenario": scenario if period != "hist" else "histórico",
                    "area_adequada_km2": area,
                    "tendencia_futura": trend,
                }
            extra_data["species_data"].append(entry)

    else:
        # Sem espécies selecionadas → visão geral das top invasoras por SDM
        try:
            top_hist = json.loads(get_raster_top_species_tool.invoke({"top_n": 8, "period": "hist"}))
            top_fut  = json.loads(get_raster_top_species_tool.invoke({"top_n": 8, "period": "2041-2070"}))
            extra_data["visao_geral_sdm"] = {
                "top_especies_historico": top_hist,
                "top_especies_futuro_2041_2070_ssp370": top_fut,
            }
        except Exception:
            extra_data["visao_geral_sdm"] = {"nota": "Dados SDM não disponíveis de momento."}

    municipality_note = f" no município **{municipality}**" if municipality else " em Portugal"
    species_list = [cfg["species"] for cfg in species_configs] if species_configs else []
    species_note = f" sobre as espécies: {', '.join(species_list)}" if species_list else " (visão geral)"

    user_request = _USER_REQUEST_TEMPLATE.format(
        level_instruction=level_instruction,
        species_note=species_note,
        municipality_note=municipality_note,
        extra_data_json=json.dumps(extra_data, ensure_ascii=False, indent=2),
    )

    logger.info("A gerar relatório — nível=%s species_configs=%s municipality=%s", normalized_level, species_configs, municipality)
    model = _get_model()
    result = model.invoke([HumanMessage(content=user_request)])
    return result.content