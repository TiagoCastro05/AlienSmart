import logging
import io
import re
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

from agent_report import generate_agent_report, normalize_report_level, analyze_chart_with_ai
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
from municipio_raster import clip_raster_to_municipio, list_municipios
from report_tables import build_results_section
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


def build_municipio_area_section(species_configs: list, municipality: str) -> str:
    """
    Gera uma secção markdown com a área adequada de cada espécie DENTRO dos
    limites do concelho (recorte dos rasters SDM ao polígono do município).
    Devolve "" se não houver município, espécies ou dados.
    """
    if not municipality or not species_configs:
        return ""

    # O recorte ao concelho usa SEMPRE o período histórico (adequação atual):
    # mais intuitivo à escala local e evita mapas vazios/0% de projeções futuras.
    rows = []
    for cfg in species_configs:
        period, scenario = "hist", None
        files = get_raster_files(species=cfg["species"], period=period, scenario=scenario, binary=True)
        if not files:
            continue
        res = clip_raster_to_municipio(files[0], municipality)
        if "error" in res:
            logger.warning("[municipio section] %s", res["error"])
            continue
        for _k in ("clipped_path", "boundary_path", "suitable_path"):
            _p = res.pop(_k, None)
            if _p:
                Path(_p).unlink(missing_ok=True)
        rows.append((cfg["species"], res["suitable_area_km2"], res["suitable_pct"]))

    if not rows:
        return ""

    linhas = [
        f"## Análise no Município de {municipality}",
        "",
        f"Esta secção foca os modelos SDM **dentro dos limites administrativos do "
        f"concelho de {municipality}**, obtidos por recorte (clip) dos rasters ao "
        f"polígono do município. A tabela-resumo apresenta a área adequada por "
        f"espécie; as percentagens referem-se à fração da área válida do concelho.",
        "",
        "| Espécie | Área adequada no concelho (km²) | % da área do concelho |",
        "| --- | --- | --- |",
    ]
    for sp, km2, pct in rows:
        linhas.append(f"| {sp} | {km2} | {pct}% |")

    # Bloco por espécie: cabeçalho + marcador do mapa do recorte + análise.
    # O marcador [[MAPA:__municipio_{sp}__]] é substituído no pdf_builder pelo
    # PNG do recorte ao concelho, garantindo que a imagem do município aparece
    # mesmo aqui (e não misturada na secção de Resultados).
    for sp, km2, pct in rows:
        linhas += [
            "",
            f"### {sp} — recorte ao concelho de {municipality}",
            "",
            f"[[MAPA:__municipio_{sp}__]]",
            "",
            _municipio_species_analysis(sp, municipality, km2, pct),
        ]

    return "\n".join(linhas)


def _municipio_species_analysis(species: str, municipality: str, km2: float, pct: float) -> str:
    """Texto crítico curto sobre a adequação de uma espécie dentro do concelho."""
    if pct >= 66:
        nivel = (
            f"uma fração **muito elevada** do território, indicando que o concelho "
            f"oferece condições climáticas largamente favoráveis ao estabelecimento da espécie"
        )
    elif pct >= 33:
        nivel = (
            f"uma fração **moderada** do território, com áreas favoráveis e áreas "
            f"marginais distribuídas pelo concelho"
        )
    elif pct > 0:
        nivel = (
            f"uma fração **reduzida** do território, sugerindo adequação localizada "
            f"a condições específicas dentro do concelho"
        )
    else:
        nivel = (
            f"praticamente nenhuma área adequada, indicando condições climáticas "
            f"globalmente desfavoráveis à espécie neste concelho"
        )
    return (
        f"No concelho de {municipality}, *{species}* apresenta **{km2} km²** de área "
        f"climaticamente adequada, o que corresponde a **{pct}%** da área válida do "
        f"concelho — {nivel}. O mapa acima destaca a localização dessas áreas sobre "
        f"a cartografia base, apoiando a definição de prioridades de monitorização e "
        f"controlo à escala local."
    )


def _fmt_km2(value) -> str:
    if value is None:
        return "—"
    return f"{int(round(value)):,}".replace(",", " ")


def build_overlap_section(species_configs: list, municipality: str | None) -> str:
    """
    Secção dedicada às ZONAS DE SOBREPOSIÇÃO (≥2 espécies coexistem) — o mapa
    combinado nacional e, se houver concelho selecionado, o recorte ao concelho.
    Só faz sentido com 2+ espécies. Devolve "" caso contrário.
    """
    species_list = [c["species"] for c in (species_configs or []) if c.get("species")]
    if len(species_list) < 2:
        return ""

    from raster_tools import compute_suitable_area

    # Área de sobreposição a nível nacional (períodos/cenários escolhidos)
    nat_area = None
    try:
        comb = compute_combined_invasive_raster(species_configs, threshold=2)
        if "path" in comb:
            nat_area = compute_suitable_area(comb["path"]).get("suitable_area_km2")
            Path(comb["path"]).unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("[overlap section] área nacional falhou: %s", exc)

    # Área de sobreposição dentro do concelho (histórico)
    conc_area = conc_pct = None
    if municipality:
        try:
            hist_cfgs = [{"species": s, "period": "hist", "scenario": None} for s in species_list]
            comb = compute_combined_invasive_raster(hist_cfgs, threshold=2)
            if "path" in comb:
                clip = clip_raster_to_municipio(comb["path"], municipality)
                Path(comb["path"]).unlink(missing_ok=True)
                if "error" not in clip:
                    conc_area = clip.get("suitable_area_km2")
                    conc_pct = clip.get("suitable_pct")
                    for _k in ("clipped_path", "boundary_path", "suitable_path"):
                        _p = clip.get(_k)
                        if _p:
                            Path(_p).unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("[overlap section] área concelho falhou: %s", exc)

    linhas = [
        "**Zonas de Maior Sobreposição (Espécies Múltiplas)**",
        "",
        f"Esta secção identifica as zonas onde **duas ou mais** das espécies analisadas "
        f"({', '.join(species_list)}) coexistem climaticamente — ou seja, as áreas de "
        f"**maior pressão invasora combinada**, prioritárias para vigilância e controlo. "
        + ("O primeiro mapa mostra a sobreposição à escala nacional; o segundo, o recorte "
           "ao concelho." if municipality else "O mapa mostra a sobreposição à escala nacional."),
        "",
        "[[MAPA:__combined__]]",
    ]
    if municipality:
        linhas += ["", "[[MAPA:__municipio_combined__]]"]

    partes = ["**Análise crítica:** "]
    if nat_area is not None:
        partes.append(
            f"A nível nacional, a área onde pelo menos duas espécies coexistem é de "
            f"**{_fmt_km2(nat_area)} km²**. "
        )
    if municipality and conc_area is not None:
        if conc_area > 0:
            partes.append(
                f"No concelho de {municipality}, essa sobreposição cobre **{conc_area} km²** "
                f"(**{conc_pct}%** da área do concelho). "
            )
        else:
            partes.append(
                f"No concelho de {municipality}, não há atualmente coexistência das espécies "
                f"(0 km² de sobreposição). "
            )
    partes.append(
        "Estas zonas devem ser priorizadas nas ações de gestão, por concentrarem o risco "
        "de múltiplas espécies invasoras em simultâneo."
    )
    linhas += ["", "".join(partes).strip()]
    return "\n".join(linhas)


def _report_layout(level: str) -> dict:
    """Define que elementos cada nível de relatório inclui.

    Decisões acordadas com o utilizador:
      • técnico  — relatório completo (comportamento original): tabelas SDM,
        secções e mapas de município, sobreposição, mapas por espécie + combinado,
        gráficos de campo e de evolução SDM.
      • público  — leve: apenas um mapa por espécie e texto simples. SEM tabelas,
        SEM secção/mapas de município, SEM sobreposição ("soma"), SEM gráficos.
      • executivo — orientado a decisão: tabelas SDM + UM único mapa-resumo
        (sobreposição de todas as espécies, ou o mapa da única espécie) +
        gráficos de evolução SDM. SEM mapas por espécie nem secções de município.
    """
    if level == "publico":
        return {
            "sdm_tables":        False,
            "municipio_section": False,
            "overlap_section":   False,
            "per_species_maps":  True,
            "combined_map":      False,
            "municipio_maps":    False,
            "field_charts":      False,
            "sdm_charts":        False,
        }
    if level == "executivo":
        return {
            "sdm_tables":        True,
            "municipio_section": False,
            "overlap_section":   False,
            "per_species_maps":  False,   # só o mapa-resumo (ver _build_overview_map)
            "combined_map":      True,
            "municipio_maps":    False,
            "field_charts":      False,
            "sdm_charts":        True,
        }
    # técnico (predefinição) — relatório completo, comportamento original
    return {
        "sdm_tables":        True,
        "municipio_section": True,
        "overlap_section":   True,
        "per_species_maps":  True,
        "combined_map":      True,
        "municipio_maps":    True,
        "field_charts":      True,
        "sdm_charts":        True,
    }


_PROSE_PCT_RE = re.compile(r'[-+−]?\d{1,3}(?:[.,]\d+)?\s*%')


def _strip_prose_percentages(text: str) -> str:
    """Substitui percentagens numéricas no texto do LLM por "(ver tabela)".

    O modelo local fabrica percentagens (variações, "% nacional") que contradizem
    as tabelas determinísticas. Como esta função corre ANTES da injeção dessas
    tabelas/secções, só afeta a prosa do LLM — os valores corretos das tabelas
    ficam intactos.
    """
    out = _PROSE_PCT_RE.sub("(ver tabela)", text)
    # Junta sequências ("de (ver tabela) e (ver tabela)") numa só referência.
    out = re.sub(r'\(ver tabela\)(?:\s*(?:e|,|;)\s*\(ver tabela\))+', "(ver tabelas)", out)
    # Remove "respetivamente"/"respectivamente" que fica órfão sem os números.
    out = re.sub(r',?\s*\brespe[ct]+ivamente\b', "", out, flags=re.IGNORECASE)
    # Evita parêntese duplicado quando a % já estava entre parênteses.
    out = out.replace("((ver tabela)", "(ver tabela")
    out = re.sub(r'[ \t]{2,}', " ", out)
    return out


# Substituições pt-BR → pt-PT seguras e inequívocas. NÃO incluímos correções de
# colocação de clíticos ambíguas (ex.: "que se dá" está correto em pt-PT), só os
# casos lexicais claros e o padrão auxiliar+"se"+infinitivo (tipicamente do Brasil).
_PT_FIXES = [
    (r'\bem um\b',                'num'),
    (r'\bem uma\b',               'numa'),
    (r'\búmid',                   'húmid'),       # úmido/úmida/úmidos/úmidas
    (r'\bmonitorar\b',            'monitorizar'),
    (r'\bmonitoramento\b',        'monitorização'),
    (r'\bplaneja(r|mento)\b',     lambda m: 'planear' if m.group(1) == 'r' else 'planeamento'),
    (r'\bregistros?\b',           lambda m: m.group(0).replace('registr', 'regist')),
    (r'\brespectivamente\b',      'respetivamente'),
    (r'\bconscientização\b',      'sensibilização'),
    (r'\bconscientizar\b',        'sensibilizar'),
    (r'\bmudanças climáticas\b',  'alterações climáticas'),
    (r'\bmudança climática\b',    'alteração climática'),
    (r'\bleva em conta\b',        'tem em conta'),
    (r'\blevar em conta\b',       'ter em conta'),
    (r'\bmedidas de controle\b',  'medidas de controlo'),
    # auxiliar + "se" + infinitivo → ênclise no infinitivo (PT)
    (r'\b(pode|podem|deve|devem|vai|vão|tende|tendem|começa|começam) se (\w+r)\b',
     r'\1 \2-se'),
    # pronome-sujeito explícito + "se" + verbo finito → ênclise (PT exige ênclise
    # aqui; não há gatilho de próclise). Ex.: "Ela se dá" → "Ela dá-se".
    (r'\b(Ela|Ele|Elas|Eles) se (\w+)\b', r'\1 \2-se'),
]


def _normalize_pt_pt(text: str) -> str:
    """Corrige, de forma determinística, marcadores frequentes de pt-BR para pt-PT.

    O modelo local (llama3.1:8b) escorrega para português do Brasil mesmo com a
    instrução nos prompts; estas substituições garantem o resultado. Preserva a
    maiúscula inicial quando a palavra começa frase.
    """
    def _preserve_case(repl):
        def _f(m):
            out = repl(m) if callable(repl) else repl
            if m.group(0)[:1].isupper():
                out = out[:1].upper() + out[1:]
            return out
        return _f

    for pattern, repl in _PT_FIXES:
        # As regras com backreferences (\1) não precisam de preservar maiúscula.
        if isinstance(repl, str) and '\\' in repl:
            text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
        else:
            text = re.sub(pattern, _preserve_case(repl), text, flags=re.IGNORECASE)
    return text


def build_report_payload(
    level: str,
    species: str = None,
    municipality: str = None,
    species_configs: list = None,
) -> dict:
    layout = _report_layout(level)
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

    # O LLM (llama3.1:8b) tende a CALCULAR/INVENTAR percentagens no texto que não
    # batem certo com os dados (ex.: variações ou "% nacional" erradas). As únicas
    # percentagens fiáveis vêm das tabelas/secções determinísticas, inseridas a
    # seguir. Por isso removemos as percentagens do texto escrito pelo LLM ANTES
    # dessa injeção — assim limpamos só a prosa do modelo e preservamos as tabelas.
    report = _strip_prose_percentages(report)

    # Normaliza pt-BR → pt-PT no texto do LLM (as secções determinísticas que se
    # seguem já estão em pt-PT). Garante português europeu mesmo quando o modelo
    # escorrega para formas brasileiras.
    report = _normalize_pt_pt(report)

    # Substitui o marcador [[TABELAS_SDM]] pelas tabelas de área adequada
    # calculadas em Python (valores exatos). Se o LLM não tiver escrito o
    # marcador, acrescenta as tabelas no fim. Sem dados → remove o marcador.
    results_section = build_results_section(species_configs or []) if layout["sdm_tables"] else ""
    if "[[TABELAS_SDM]]" in report:
        report = report.replace("[[TABELAS_SDM]]", results_section or "")
    elif results_section:
        report = f"{report}\n\n{results_section}"

    # Acrescenta a análise por município (texto + tabela + mapas do recorte).
    # Posição: LOGO APÓS os Resultados, antes da Discussão — para manter o fluxo
    # nacional → concelho → discussão. Se não houver Discussão, vai para o fim.
    extra_blocks = []
    if layout["municipio_section"]:
        municipio_section = build_municipio_area_section(species_configs or [], municipality)
        if municipio_section:
            extra_blocks.append(municipio_section)
    # Secção dedicada às zonas de sobreposição (mapa combinado nacional + concelho).
    if layout["overlap_section"]:
        overlap_section = build_overlap_section(species_configs or [], municipality)
        if overlap_section:
            extra_blocks.append(overlap_section)
    if extra_blocks:
        block = "\n\n".join(extra_blocks)
        m = re.search(r'(?im)^\s*(?:#+\s*)?\*{0,2}\s*Discuss[aã]o', report)
        if m:
            report = report[:m.start()] + block + "\n\n" + report[m.start():]
        else:
            report = f"{report}\n\n{block}"

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


def build_combined_municipio_map_image(species_configs: list, municipality: str,
                                       threshold: int = 2) -> bytes | None:
    """
    Soma os rasters das espécies (sobreposição "muito invasiva") e RECORTA ao
    concelho — versão local do mapa combinado nacional. Usa período histórico,
    coerente com os restantes recortes ao concelho.
    """
    if len(species_configs) < 2 or not municipality:
        return None

    hist_cfgs = [{"species": c["species"], "period": "hist", "scenario": None}
                 for c in species_configs]
    combined = compute_combined_invasive_raster(hist_cfgs, threshold=threshold)
    if "error" in combined:
        logger.warning("[combined municipio map] %s", combined["error"])
        return None

    clip = clip_raster_to_municipio(combined["path"], municipality)
    Path(combined["path"]).unlink(missing_ok=True)
    if "error" in clip:
        logger.warning("[combined municipio map] %s", clip["error"])
        return None

    import subprocess, tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        out_png = tmp.name

    args = [
        str(QGIS_PYTHON), str(_QGIS_RENDER_SCRIPT),
        clip["clipped_path"], out_png, "BurntYellow", "Sobreposição", "hist",
        "--label-suitable=Muito invasivo",
        f"--boundary={clip['boundary_path']}",
    ]
    if clip.get("suitable_path"):
        args.append(f"--suitable={clip['suitable_path']}")

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
            logger.info("[combined municipio map] PNG gerado: %d bytes", len(data))
            return data
        logger.warning("[combined municipio map] PNG não gerado ou vazio")
        return None
    except Exception as exc:
        logger.warning("[combined municipio map] Erro no subprocesso PyQGIS: %s", exc, exc_info=True)
        return None
    finally:
        for _k in ("clipped_path", "boundary_path", "suitable_path"):
            _p = clip.get(_k)
            if _p:
                Path(_p).unlink(missing_ok=True)


def build_municipio_map_image(
    species: str,
    municipality: str,
    period: str = "hist",
    scenario: str = None,
    colormap_name: str = "Greens5",
) -> tuple[bytes | None, dict]:
    """
    Recorta o raster SDM de uma espécie aos limites de um município e renderiza
    o mapa (raster recortado + contorno do concelho) via PyQGIS.

    Returns:
        (png_bytes | None, stats) — stats inclui área adequada dentro do concelho.
    """
    files = get_raster_files(species=species, period=period, scenario=scenario, binary=True)
    if not files:
        logger.warning("[municipio map] Nenhum TIF para %r / %r / %r", species, period, scenario)
        return None, {"error": "raster não encontrado"}

    clip = clip_raster_to_municipio(files[0], municipality)
    if "error" in clip:
        logger.warning("[municipio map] %s", clip["error"])
        return None, clip

    import subprocess, tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        out_png = tmp.name

    args = [
        str(QGIS_PYTHON),
        str(_QGIS_RENDER_SCRIPT),
        clip["clipped_path"],
        out_png,
        colormap_name,
        species,
        period,
    ]
    if scenario:
        args.append(scenario)
    args.append(f"--boundary={clip['boundary_path']}")
    if clip.get("suitable_path"):
        args.append(f"--suitable={clip['suitable_path']}")

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
            logger.info("[municipio map] PNG gerado: %d bytes", len(data))
            return data, clip
        logger.warning("[municipio map] PNG não gerado ou vazio para %r/%r", species, municipality)
        return None, clip
    except Exception as exc:
        logger.warning("[municipio map] Erro no subprocesso PyQGIS: %s", exc, exc_info=True)
        return None, clip
    finally:
        Path(clip["clipped_path"]).unlink(missing_ok=True)
        Path(clip["boundary_path"]).unlink(missing_ok=True)
        if clip.get("suitable_path"):
            Path(clip["suitable_path"]).unlink(missing_ok=True)


def build_charts(summary: dict) -> list[dict]:
    """Gráficos de barras dos registos de campo. Devolve [{'caption','png'}]."""
    charts: list[dict] = []
    for title, data in (
        ("Registos de campo por espécie", summary.get("species_count", {})),
        ("Registos de campo por município", summary.get("municipality_count", {})),
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
        data_text = "; ".join(f"{l}: {v} registos" for l, v in items)
        analysis = analyze_chart_with_ai(title, data_text)
        charts.append({"caption": title, "png": buf.read(), "analysis": analysis})
    return charts


def build_sdm_evolution_charts(species_configs: list) -> list[dict]:
    """
    Gráfico de linhas da área adequada (km²) por período e cenário, a partir dos
    rasters SDM — um gráfico por espécie. Devolve [{'caption','png'}].
    """
    from report_tables import _area, SCENARIOS, FUTURE_PERIODS, SCENARIO_SHORT

    species_list = [c["species"] for c in (species_configs or []) if c.get("species")]
    x_labels = ["Histórico", "2041-2070", "2071-2100"]
    colors_sc = {"ssp126": "#1a9850", "ssp370": "#fdae61", "ssp585": "#d73027"}
    charts: list[dict] = []

    for sp in species_list:
        hist = _area(sp, "hist", None)
        fig, ax = plt.subplots(figsize=(6.5, 3.5))
        has_data = False
        for sc in SCENARIOS:
            ys = [
                hist if hist is not None else float("nan"),
                _area(sp, "2041-2070", sc),
                _area(sp, "2071-2100", sc),
            ]
            ys = [float("nan") if v is None else v for v in ys]
            if any(v == v for v in ys):  # algum valor não-NaN
                has_data = True
            ax.plot(x_labels, ys, marker="o", linewidth=2,
                    color=colors_sc.get(sc, "#555"), label=SCENARIO_SHORT[sc])
        if not has_data:
            plt.close(fig)
            continue
        ax.set_title(f"Evolução da área adequada (SDM) — {sp}")
        ax.set_ylabel("Área adequada (km²)")
        ax.grid(True, axis="y", linestyle=":", alpha=0.5)
        ax.legend(title="Cenário", fontsize=8)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150)
        plt.close(fig)
        buf.seek(0)
        caption = f"Evolução da área adequada (SDM) — {sp}"
        partes = []
        if hist is not None:
            partes.append(f"Histórico: {int(round(hist))} km²")
        for sc in SCENARIOS:
            a41 = _area(sp, "2041-2070", sc)
            a71 = _area(sp, "2071-2100", sc)
            partes.append(
                f"{SCENARIO_SHORT[sc]} — 2041-2070: "
                f"{int(round(a41)) if a41 is not None else 's/ dados'} km², "
                f"2071-2100: {int(round(a71)) if a71 is not None else 's/ dados'} km²"
            )
        analysis = analyze_chart_with_ai(caption, "; ".join(partes))
        charts.append({"caption": caption, "png": buf.read(), "analysis": analysis})
    return charts



def _build_species_maps(configs: list, include_combined: bool = True) -> list[dict]:
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
            maps.append({"species": sp, "match_species": sp, "period": period, "scenario": scenario, "map_png": png})
        else:
            logger.warning("[PDF maps] Mapa devolveu None para %r / %r / %r", sp, period, scenario)

    if include_combined and len(configs) >= 2:
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


def _build_overview_map(configs: list) -> list[dict]:
    """Um único mapa-resumo para o relatório executivo.

    Com 2+ espécies → mapa de sobreposição (todas as espécies juntas).
    Com 1 espécie   → o mapa nacional dessa espécie.
    """
    if not configs:
        return []
    if len(configs) >= 2:
        png = build_combined_map_image(configs)
        if png:
            return [{
                "species": "__combined__",
                "period": "hist",
                "scenario": None,
                "map_png": png,
                "title": "Mapa-resumo — Zonas de maior pressão invasora (sobreposição de espécies)",
            }]
        logger.warning("[PDF maps] Mapa-resumo combinado devolveu None")
        return []
    cfg = configs[0]
    sp = cfg["species"]
    period = cfg.get("period", "hist")
    scenario = cfg.get("scenario") if period != "hist" else None
    png = build_raster_map_image(sp, period, scenario, colormap_name=cfg.get("colormap", "Greens5"))
    if png:
        return [{"species": sp, "match_species": sp, "period": period,
                 "scenario": scenario, "map_png": png}]
    logger.warning("[PDF maps] Mapa-resumo (1 espécie) devolveu None para %r", sp)
    return []


def _build_report_visuals(level: str, configs: list, municipality: str,
                          summary: dict) -> tuple[list[dict], list[dict]]:
    """Constrói (species_maps, charts) de acordo com o layout do nível do relatório."""
    layout = _report_layout(level)
    species_maps: list[dict] = []
    if layout["per_species_maps"]:
        species_maps += _build_species_maps(configs, include_combined=layout["combined_map"])
    elif layout["combined_map"]:
        species_maps += _build_overview_map(configs)
    if layout["municipio_maps"]:
        species_maps += _build_municipio_maps(configs, municipality)

    charts: list[dict] = []
    if layout["field_charts"]:
        charts += build_charts(summary)
    if layout["sdm_charts"]:
        charts += build_sdm_evolution_charts(configs)
    return species_maps, charts


def _build_municipio_maps(configs: list, municipality: str) -> list[dict]:
    """Mapas dos rasters recortados aos limites do concelho (um por espécie)."""
    maps = []
    if not municipality:
        return maps
    for cfg in configs:
        sp            = cfg["species"]
        # Recorte ao concelho sempre histórico (coerente com build_municipio_area_section)
        period, scenario = "hist", None
        colormap_name = cfg.get("colormap", "Greens5")
        png, _ = build_municipio_map_image(sp, municipality, period, scenario, colormap_name=colormap_name)
        if png:
            # match_species único (igual ao species) para o mapa do recorte ser
            # inserido SÓ pelo marcador da secção do município, e não pelo
            # marcador [[MAPA:{especie}]] da secção de Resultados.
            maps.append({
                "species": f"__municipio_{sp}__",
                "match_species": f"__municipio_{sp}__",
                "period": period,
                "scenario": scenario,
                "map_png": png,
                "title": f"Recorte ao concelho de {municipality} — {sp}",
            })
        else:
            logger.warning("[PDF maps] Mapa de município devolveu None para %r/%r", sp, municipality)

    # Mapa de sobreposição (≥2 espécies) recortado ao concelho — gémeo do nacional
    if len(configs) >= 2:
        combined_png = build_combined_municipio_map_image(configs, municipality)
        if combined_png:
            maps.append({
                "species": "__municipio_combined__",
                "match_species": "__municipio_combined__",
                "period": "hist",
                "scenario": None,
                "map_png": combined_png,
                "title": f"Sobreposição (≥2 espécies) no concelho de {municipality}",
            })
        else:
            logger.warning("[PDF maps] Mapa combinado de município devolveu None para %r", municipality)
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
    species_maps, charts = _build_report_visuals(
        normalized_level, configs, municipality, payload["summary"]
    )
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
    species_maps, charts = _build_report_visuals(
        normalized_level, configs, municipality, payload["summary"]
    )
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


@app.get("/raster/municipios")
def get_raster_municipios():
    """Lista os concelhos disponíveis nos limites (para o seletor de município)."""
    try:
        return {"municipios": list_municipios()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao carregar municípios: {exc}")


@app.get("/raster/municipio-map/{species}")
def get_municipio_map(
    species: str,
    municipio: str,
    period: str = "hist",
    scenario: str = None,
    colormap: str = "Greens5",
):
    """Gera o mapa do raster da espécie recortado aos limites de um concelho."""
    png, stats = build_municipio_map_image(
        species, municipio, period=period, scenario=scenario, colormap_name=colormap
    )
    if not png:
        raise HTTPException(
            status_code=404,
            detail=stats.get("error", "Não foi possível gerar o mapa do município."),
        )
    return Response(content=png, media_type="image/png")


@app.get("/raster/municipio-area/{species}")
def get_municipio_area(species: str, municipio: str, period: str = "hist", scenario: str = None):
    """Área adequada da espécie dentro de um concelho (sem renderizar o mapa)."""
    files = get_raster_files(species=species, period=period, scenario=scenario, binary=True)
    if not files:
        raise HTTPException(status_code=404, detail=f"Nenhum raster encontrado para {species}")
    result = clip_raster_to_municipio(files[0], municipio)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    # remove caminhos de ficheiros temporários da resposta
    for _k in ("clipped_path", "boundary_path", "suitable_path"):
        _p = result.pop(_k, None)
        if _p:
            Path(_p).unlink(missing_ok=True)
    return {"species": species, "period": period, "scenario": scenario, **result}


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
