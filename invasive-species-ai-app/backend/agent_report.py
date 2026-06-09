from __future__ import annotations

import json
import logging
from collections import Counter

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage

from services.observation_service import get_observations

load_dotenv()

logger = logging.getLogger(__name__)

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
        from raster_tools import get_raster_files, compute_suitable_area
        sc = None if period == "hist" else scenario
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
    Estima a presença de uma espécie invasora num município específico,
    verificando se os registos de campo confirmam a sua presença nessa área
    e qual a área adequada total do modelo SDM.

    Parâmetros:
        species_name: nome científico da espécie
        municipality_name: nome do município (ex: "Braga")

    Devolve registos de campo no município e área SDM total da espécie.
    Nota: sem clip espacial, a área SDM é para Portugal inteiro.
    """
    try:
        from raster_tools import get_raster_files, compute_suitable_area
        field_records = get_observations(species=species_name, municipality=municipality_name)

        files = get_raster_files(species=species_name, period="hist", binary=True)
        sdm_info = {}
        if files:
            stats = compute_suitable_area(files[0])
            sdm_info = {
                "suitable_area_km2_portugal": stats.get("suitable_area_km2"),
                "suitable_pct_portugal": stats.get("suitable_pct"),
            }

        return json.dumps({
            "species": species_name,
            "municipality": municipality_name,
            "field_records_in_municipality": len(field_records),
            "field_records_details": field_records,
            "sdm_data": sdm_info,
            "note": "A área SDM é para Portugal Continental. Não existe clip por município nesta versão.",
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── System prompt e level prompts ────────────────────────────────────────────

SYSTEM_PROMPT = """
És um assistente especializado em escrever relatórios sobre espécies invasoras
a partir de dados georreferenciados e modelos de distribuição de espécies (SDM).

Tens acesso a dois tipos de dados:
1. Registos de campo: observações pontuais georreferenciadas com espécie, município e data.
2. Modelos SDM (rasters): modelos de distribuição que estimam a área adequada para cada espécie em Portugal,
   para períodos histórico e futuros (SSP126, SSP370, SSP585).

Regras obrigatórias:
- Usa apenas dados obtidos através das ferramentas disponíveis.
- Começa SEMPRE por chamar get_summary_tool e get_raster_species_list_tool.
- Para relatórios sobre uma espécie específica, chama get_raster_suitable_area_tool e get_raster_trend_tool.
- Para relatórios gerais, chama get_raster_top_species_tool para identificar as espécies mais prevalentes.
- Se for mencionado um município, chama get_raster_municipality_overlap_tool.
- Não inventes espécies, municípios, números, percentagens ou referências bibliográficas.
- Não afirmes causalidade nem impacto ecológico sem dados das ferramentas.
- Inclui sempre uma secção de limitações.
- Escreve em português europeu.
- Distingue claramente dados de campo (observações diretas) de dados SDM (modelos preditivos).
"""

LEVEL_PROMPTS = {
    "publico": """
RELATÓRIO PARA PÚBLICO GERAL (3-5 páginas)

Audiência: Público geral sem conhecimentos técnicos sobre espécies invasoras.

Características obrigatórias:
- Linguagem muito simples, acessível e amigável
- Sem jargão técnico ou científico
- Explica conceitos básicos como "espécie invasora" e "modelo de distribuição"
- Foco em informações práticas e interessantes
- Tom educativo e não alarmista

Estrutura obrigatória:
1. Título atrativo
2. Introdução: O que são espécies invasoras
3. As espécies encontradas: descrição acessível de cada espécie, incluindo área que ocupam em Portugal
4. Onde estão concentradas: áreas com mais registos de campo
5. O que nos dizem os modelos: explicação simples do que os SDM indicam para o futuro
6. O que podemos fazer: dicas práticas
7. Limitações: explicação clara das limitações dos dados
8. Conclusão: mensagem positiva
""",
    "tecnico": """
RELATÓRIO TÉCNICO (10-15 páginas)

Audiência: Especialistas, biólogos, ecologistas, gestores ambientais.

Características obrigatórias:
- Linguagem técnica e científica
- Análise detalhada de padrões e tendências
- Integração de dados de campo com dados SDM
- Rigor metodológico

Estrutura obrigatória:
1. Título
2. Resumo executivo
3. Introdução: contexto ecológico
4. Metodologia: descrição dos dados de campo e dos modelos SDM utilizados
5. Resultados:
   - Distribuição geográfica (registos de campo)
   - Área adequada por espécie (dados SDM históricos, em km²)
   - Tendências futuras por cenário climático (SSP)
   - Comparação entre espécies
6. Discussão: implicações, áreas de risco, dinâmica de colonização
7. Recomendações técnicas
8. Limitações: distinguir limitações dos registos de campo vs. limitações dos modelos SDM
9. Conclusão
""",
    "executivo": """
RELATÓRIO EXECUTIVO (5-8 páginas)

Audiência: Gestores, tomadores de decisão, autoridades ambientais.

Características obrigatórias:
- Linguagem clara e profissional
- Foco em factos essenciais e ações
- Orientado para decisão

Estrutura obrigatória:
1. Título
2. Resumo executivo: 3-4 pontos-chave
3. Situação atual: espécies identificadas, área SDM, registos de campo
4. Achados principais: espécies prioritárias, hotspots, tendências futuras
5. Prioridades de ação: áreas e espécies que requerem intervenção
6. Próximos passos: monitorização, responsabilidades
7. Limitações
8. Conclusão
""",
}


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
        extra_data["species_data"] = []
        for cfg in species_configs:
            sp       = cfg["species"]
            period   = cfg.get("period", "hist")
            scenario = cfg.get("scenario", "ssp370")
            binary   = cfg.get("binary", True)

            area  = json.loads(get_raster_suitable_area_tool.invoke({"species_name": sp, "period": period, "scenario": scenario}))
            trend = json.loads(get_raster_trend_tool.invoke({"species_name": sp, "scenario": scenario}))
            entry = {
                "species":        sp,
                "period":         period,
                "scenario":       scenario if period != "hist" else "histórico",
                "binary":         "contínuo" if not binary else "binário",
                "suitable_area":  area,
                "trend":          trend,
            }
            if municipality:
                overlap = json.loads(get_raster_municipality_overlap_tool.invoke({
                    "species_name": sp, "municipality_name": municipality
                }))
                entry["municipality_data"] = overlap
            extra_data["species_data"].append(entry)
    else:
        extra_data["top5_species"] = json.loads(
            get_raster_top_species_tool.invoke({"top_n": 5, "period": "hist"})
        )

    municipality_note = f" no município **{municipality}**" if municipality else " em Portugal"
    species_list = [cfg["species"] for cfg in species_configs] if species_configs else []
    species_note = f" sobre as espécies: {', '.join(species_list)}" if species_list else ""

    user_request = f"""
{level_instruction}

Gera agora o relatório{species_note}{municipality_note} seguindo rigorosamente a estrutura acima.

## DADOS REAIS — USA APENAS ESTES, IGNORA QUALQUER OUTRO CONHECIMENTO:

### Dados SDM por espécie (período e tipo conforme selecionado pelo utilizador):
{json.dumps(extra_data, ensure_ascii=False, indent=2)}

INSTRUÇÕES CRÍTICAS:
- Analisa APENAS as espécies listadas em "species_data" acima.
- Para cada espécie usa EXATAMENTE o período, tipo (binário/contínuo) e cenário indicados.
- Os valores de área adequada e tendência são os únicos dados numéricos que podes usar.
- NÃO menciones outras espécies que não estejam nos dados acima.
- NÃO inventes números, áreas ou tendências.
- Escreve em português europeu.
"""

    logger.info("A gerar relatório — nível=%s species_configs=%s municipality=%s", normalized_level, species_configs, municipality)
    model = _get_model()
    result = model.invoke([HumanMessage(content=user_request)])
    return result.content
