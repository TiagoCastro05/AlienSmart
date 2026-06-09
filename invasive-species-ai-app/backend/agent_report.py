from __future__ import annotations

import json
import logging
from collections import Counter

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_ollama import ChatOllama

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


@tool
def get_summary_tool() -> str:
    """Devolve um resumo estatístico dos registos de espécies invasoras em formato JSON."""
    return json.dumps(calculate_summary(), ensure_ascii=False, indent=2)


@tool
def get_species_list_tool() -> str:
    """Devolve a lista de espécies presentes nos registos."""
    records = _get_records()
    species = sorted({r.get("species") for r in records if r.get("species")})
    return json.dumps(species, ensure_ascii=False)


@tool
def get_records_by_species_tool(species_name: str) -> str:
    """Devolve todos os registos detalhados associados a uma espécie específica."""
    records = get_observations(species=species_name)
    return json.dumps(records, ensure_ascii=False, indent=2)


SYSTEM_PROMPT = """
És um assistente especializado em escrever relatórios sobre espécies invasoras
a partir de dados georreferenciados.

Regras obrigatórias para TODOS os relatórios:
- Usa apenas dados obtidos através das ferramentas disponíveis.
- Antes de escrever o relatório, chama get_summary_tool.
- Não inventes espécies, municípios, números, percentagens ou fontes.
- Não afirmes causalidade nem impacto ecológico sem dados adicionais.
- Inclui sempre uma secção de limitações.
- Escreve em português europeu.
"""

LEVEL_PROMPTS = {
    "publico": """
RELATÓRIO PARA PÚBLICO GERAL (3-5 páginas)

Audiência: Público geral sem conhecimentos técnicos sobre espécies invasoras.

Características obrigatórias:
- Linguagem muito simples, acessível e amigável
- Sem jargão técnico ou científico
- Explica conceitos básicos como "espécie invasora"
- Foco em informações práticas e interessantes
- Usa exemplos do dia a dia quando apropriado
- Tom educativo e não alarmista

Estrutura obrigatória:
1. Título atrativo
2. Introdução: Explica o que são espécies invasoras de forma simples
3. As espécies encontradas: Descreve cada espécie descoberta em linguagem acessível
4. Onde estão concentradas: Identificação das áreas com mais registos
5. O que podemos fazer: Dicas práticas para o público
6. Limitações: Explica brevemente as limitações dos dados de forma clara
7. Conclusão: Mensagem positiva e motivadora

Instruções de escrita:
- Cada parágrafo deve ser curto (máximo 5 linhas)
- Usa títulos e subtítulos para organizar o conteúdo
- Inclui curiosidades ou dados interessantes quando apropriado
- Evita números muito complexos (agrupa quando necessário)
- Usa "nós" e "vós" para criar proximidade
""",
    "tecnico": """
RELATÓRIO TÉCNICO (10-15 páginas)

Audiência: Especialistas em espécies invasoras, biólogos, ecologistas, gestores ambientais.

Características obrigatórias:
- Linguagem técnica e científica
- Terminologia especializada apropriada
- Análise detalhada de padrões e tendências
- Discussão de implicações ecológicas
- Rigor metodológico

Estrutura obrigatória:
1. Título
2. Resumo executivo (1 página)
3. Introdução: Contexto ecológico e importância do monitoramento (1-2 páginas)
4. Metodologia: Descrição dos dados e método de análise (1-2 páginas)
5. Resultados: Análise detalhada (3-4 páginas)
   - Distribuição geográfica e padrões de dispersão
   - Análise por espécie com detalhes de prevalência
   - Comparação entre áreas e municípios
   - Tendências observadas
6. Discussão: Implicações e análise crítica (2-3 páginas)
   - Impacto potencial das espécies encontradas
   - Áreas de risco elevado para invasão
   - Dinâmica de colonização
7. Recomendações técnicas para monitorização (1-2 páginas)
8. Limitações dos dados e gaps de conhecimento (1 página)
9. Referências a metodologias (se aplicável)

Instruções de escrita:
- Inclui análise crítica dos dados
- Pode incluir variabilidades, incertezas e limitações técnicas
- Discussão de implicações para conservação
- Sugere abordagens de controlo quando apropriado
- Inclui propostas de investigação futura
""",
    "executivo": """
RELATÓRIO EXECUTIVO (5-8 páginas)

Audiência: Gestores, tomadores de decisão, autoridades ambientais.

Características obrigatórias:
- Linguagem clara mas profissional
- Foco em factos essenciais e ações
- Brevidade e clareza
- Orientado para decisão

Estrutura obrigatória:
1. Título
2. Resumo executivo (meia página): Os 3-4 pontos-chave
3. Situação atual: Panorama das espécies invasoras (1-2 páginas)
   - Número de espécies identificadas
   - Distribuição geográfica principal
   - Áreas de maior concentração
4. Achados principais (1 página)
   - Espécies de maior prevalência
   - Hotspots identificados
   - Nível de preocupação por área
5. Prioridades de ação (1-2 páginas)
   - Áreas que requerem intervenção imediata
   - Espécies que merecem atenção prioritária
   - Ações recomendadas por ordem de urgência
6. Próximos passos (meia página)
   - Plano de monitorização
   - Responsabilidades
   - Cronograma
7. Limitações (meia página): Breve explicação das limitações
8. Conclusão: Síntese e apelo à ação

Instruções de escrita:
- Evita detalhes técnicos desnecessários
- Usa tabelas e listas com bullet points
- Propõe ações concretas e exequíveis
- Tom profissional mas acessível
- Foco em impacto e relevância para decisores
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
    model = ChatOllama(model="llama3.2", temperature=0)
    return create_agent(
        model=model,
        tools=[get_summary_tool, get_species_list_tool, get_records_by_species_tool],
        system_prompt=SYSTEM_PROMPT,
    )


# Singleton — criado na primeira chamada, reutilizado em seguida
_agent_instance = None


def _get_agent():
    global _agent_instance
    if _agent_instance is None:
        logger.info("A inicializar agente LLM (Ollama llama3.2)…")
        _agent_instance = build_agent()
    return _agent_instance


def generate_agent_report(
    level: str | None = None,
    species: str | None = None,
    municipality: str | None = None,
) -> str:
    global _selected_species, _selected_municipality
    _selected_species = species
    _selected_municipality = municipality

    normalized_level = normalize_report_level(level)
    agent = _get_agent()

    level_instruction = LEVEL_PROMPTS.get(normalized_level, LEVEL_PROMPTS["tecnico"])
    species_note      = f" (apenas da espécie {species})"      if species      else ""
    municipality_note = f" (apenas do município {municipality})" if municipality else ""

    user_request = f"""
{level_instruction}

Gera agora o relatório{species_note}{municipality_note} seguindo rigorosamente:
1. A estrutura definida acima
2. O tamanho de páginas indicado
3. As características de linguagem e conteúdo especificadas

Começa por chamar get_summary_tool para obter os dados.
Depois, se necessário, chama get_species_list_tool e get_records_by_species_tool para obter detalhes adicionais.
Por fim, escreve o relatório completo conforme a estrutura definida.

IMPORTANTE: Segue EXATAMENTE a estrutura e instruções acima. Não uses estruturas diferentes.
"""

    logger.info("A gerar relatório — nível=%s species=%s municipality=%s", normalized_level, species, municipality)
    try:
        result = agent.invoke({"messages": [{"role": "user", "content": user_request}]})
    finally:
        _selected_species = None
        _selected_municipality = None

    final_message = None
    if isinstance(result, dict) and result.get("messages"):
        final_message = result["messages"][-1]
    elif hasattr(result, "message"):
        final_message = result.message

    if final_message is None:
        raise RuntimeError("Agente não devolveu resposta.")

    content = (
        getattr(final_message, "content", None)
        or (final_message.get("content") if isinstance(final_message, dict) else None)
    )
    return content or str(final_message)
