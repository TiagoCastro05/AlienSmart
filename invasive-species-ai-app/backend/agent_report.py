from __future__ import annotations
from collections import Counter
from pathlib import Path
import os
import json
import geopandas as gpd
import rasterio
from rasterio.mask import mask
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_ollama import ChatOllama

load_dotenv()
DATA_FILE = Path(__file__).parent / "records.json"

_selected_species = None
_selected_municipality = None


def load_records() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as file:
        records = json.load(file)
        if _selected_species:
            records = [r for r in records if r.get("species", "").lower() == _selected_species.lower()]
        if _selected_municipality:
            records = [r for r in records if r.get("municipality", "").lower() == _selected_municipality.lower()]
        return records


def calculate_summary() -> dict:
    records = load_records()
    total = len(records)
    species_count = Counter(record.get("species") for record in records if record.get("species"))
    municipality_count = Counter(record.get("municipality") for record in records if record.get("municipality"))
    species_percentages = (
        {sp: round((cnt / total) * 100, 1) for sp, cnt in species_count.items()} if total > 0 else {}
    )
    hotspots = [
        {"municipality": m, "records": c}
        for m, c in municipality_count.most_common()
        if c >= 3
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


# ── Tools originais ──────────────────────────────────────────────────────────

@tool
def get_summary_tool() -> str:
    """Devolve um resumo estatístico dos registos de campo (Records.json) em JSON."""
    return json.dumps(calculate_summary(), ensure_ascii=False, indent=2)


@tool
def get_species_list_tool() -> str:
    """Devolve a lista de espécies presentes nos registos de campo."""
    records = load_records()
    species = sorted({r.get("species") for r in records if r.get("species")})
    return json.dumps(species, ensure_ascii=False)


@tool
def get_records_by_species_tool(species_name: str) -> str:
    """Devolve todos os registos de campo de uma espécie específica."""
    records = load_records()
    filtered = [r for r in records if r.get("species", "").lower() == species_name.lower()]
    return json.dumps(filtered, ensure_ascii=False, indent=2)


# ── Novas tools raster ───────────────────────────────────────────────────────

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
    Faz o recorte (clip) espacial do modelo SDM (raster) usando a fronteira do município
    para devolver a área exata adequada (em km²) apenas dentro desse concelho.
    """
    try:
        from raster_tools import get_raster_files
        
        # 1. Obter o Raster da Espécie (Histórico Binário)
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

# ── System prompt e level prompts ────────────────────────────────────────────

SYSTEM_PROMPT = """
És um assistente especializado em escrever relatórios sobre espécies invasoras
a partir de dados georreferenciados e modelos de distribuição de espécies (SDM).

Tens acesso a dois tipos de dados:
1. Registos de campo (Records.json): observações pontuais georreferenciadas com espécie, município e data.
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

Regras obrigatórias:
- Não existem "dados de campo" nem "observações pontuais". Fala apenas de "Área adequada", "Modelos Preditivos" ou "Distribuição Estimada".
- Se te pedirem um relatório sobre um município, USA APENAS os dados da ferramenta de recorte (municipality_overlap) para referir áreas e percentagens locais.
- Não mistures a área total de Portugal com a área do Município.
- Não inventes origens biológicas para as espécies nem causalidades que não estejam nos números.
- Escreve em português europeu.

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
"""
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


def generate_agent_report(level=None, species=None, municipality=None, species_configs=None):
    # NÃO definir _selected_species/_selected_municipality — não queremos filtrar o Records.json
    normalized_level = normalize_report_level(level)
    level_instruction = LEVEL_PROMPTS.get(normalized_level, LEVEL_PROMPTS["tecnico"])

    extra_data = {}
    if species_configs:
        extra_data["species_data"] = []
        for cfg in species_configs:
            sp = cfg["species"]
            period = cfg.get("period", "hist")
            scenario = cfg.get("scenario", "ssp370")
            binary = cfg.get("binary", True)

            area = json.loads(get_raster_suitable_area_tool.invoke({
                "species_name": sp, "period": period, "scenario": scenario
            }))
            trend = json.loads(get_raster_trend_tool.invoke({
                "species_name": sp, "scenario": scenario
            }))
            entry = {
                "species": sp,
                "period": period,
                "scenario": scenario if period != "hist" else "histórico",
                "binary": "contínuo" if not binary else "binário",
                "suitable_area": area,
                "trend": trend,
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

    model = ChatOllama(model="llama3.1:8b", temperature=0)
    from langchain_core.messages import HumanMessage
    result = model.invoke([HumanMessage(content=user_request)])
    return result.content