from __future__ import annotations
from collections import Counter
from pathlib import Path
import os
import json
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

load_dotenv()
DATA_FILE = Path(__file__).parent / "records.json"

# Variáveis globais para armazenar os filtros
_selected_species = None
_selected_municipality = None


def load_records() -> list[dict]:
    """Carrega os registos a partir do ficheiro JSON.
    Retorna uma lista vazia se o ficheiro não existir ou estiver vazio.
    """
    if not DATA_FILE.exists():
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as file:
        records = json.load(file)
        # Filtrar por espécie se selecionada
        if _selected_species:
            records = [r for r in records if r.get("species", "").lower() == _selected_species.lower()]
        # Filtrar por município se selecionado
        if _selected_municipality:
            records = [r for r in records if r.get("municipality", "").lower() == _selected_municipality.lower()]
        return records


def calculate_summary() -> dict:
    """Calcula indicadores determinísticos para alimentar as ferramentas do agente."""
    records = load_records()
    total = len(records)
    species_count = Counter(record.get("species") for record in records if record.get("species"))
    municipality_count = Counter(record.get("municipality") for record in records if record.get("municipality"))
    
    species_percentages = (
        {species: round((count / total) * 100, 1) for species, count in species_count.items()}
        if total > 0
        else {}
    )
    hotspots = [
        {"municipality": municipality, "records": count}
        for municipality, count in municipality_count.most_common()
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


@tool
def get_summary_tool() -> str:
    """Devolve um resumo estatístico dos registos de espécies invasoras em formato JSON."""
    return json.dumps(calculate_summary(), ensure_ascii=False, indent=2)


@tool
def get_species_list_tool() -> str:
    """Devolve a lista de espécies presentes nos registos."""
    records = load_records()
    species = sorted({record.get("species") for record in records if record.get("species")})
    return json.dumps(species, ensure_ascii=False)


@tool
def get_records_by_species_tool(species_name: str) -> str:
    """Devolve todos os registos detalhados associados a uma espécie específica."""
    records = load_records()
    filtered = [
        record for record in records
        if record.get("species") and record["species"].lower() == species_name.lower()
    ]
    return json.dumps(filtered, ensure_ascii=False, indent=2)


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
"""
}


def normalize_report_level(level: str | None) -> str:
    if not level:
        return "tecnico"
    normalized = level.strip().lower()
    if normalized in ("publico", "público"):
        return "publico"
    if normalized in ("executivo", "tecnico", "técnico"):
        return "executivo" if normalized == "executivo" else "tecnico"
    return "tecnico"


def build_agent():
    """Gera a instância do agente de IA local usando Ollama."""
    # Usamos o ChatOllama com o modelo llama3.2 para garantir suporte a ferramentas (Tool Calling)
    model = ChatOllama(
        model="llama3.2",
        temperature=0,
    )

    return create_agent(
        model=model,
        tools=[
            get_summary_tool,
            get_species_list_tool,
            get_records_by_species_tool,
        ],
        system_prompt=SYSTEM_PROMPT,
    )


def generate_agent_report(level: str | None = None, species: str | None = None, municipality: str | None = None) -> str:
    """Invoca o agente para obter o relatório final estruturado."""
    global _selected_species, _selected_municipality
    _selected_species = species
    _selected_municipality = municipality
    
    normalized_level = normalize_report_level(level)
    agent = build_agent()
    
    # Obtém o prompt específico para o nível
    level_instruction = LEVEL_PROMPTS.get(normalized_level, LEVEL_PROMPTS["tecnico"])
    
    # Constrói o pedido do utilizador com as instruções específicas
    species_note = f" (apenas da espécie {species})" if species else ""
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
    
    result = agent.invoke({
        "messages": [
            {"role": "user", "content": user_request}
        ]
    })
    
    # Processa e extrai a mensagem final do resultado
    final_message = None
    if isinstance(result, dict) and "messages" in result and result["messages"]:
        final_message = result["messages"][-1]
    elif hasattr(result, "message"):
        final_message = result.message
        
    if final_message is None:
        _selected_species = None
        _selected_municipality = None
        return json.dumps({"error": "Não foi possível obter resposta do agente"}, ensure_ascii=False)
        
    content = getattr(final_message, "content", None) or (final_message.get("content") if isinstance(final_message, dict) else None)
    _selected_species = None
    _selected_municipality = None
    return content or str(final_message)