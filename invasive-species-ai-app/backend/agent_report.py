from __future__ import annotations
from collections import Counter
from pathlib import Path
import json
import os
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI

load_dotenv()
DATA_FILE = Path(__file__).parent / "records.json"


def load_records() -> list[dict]:
    """Carrega os registos a partir do ficheiro JSON.
    Retorna uma lista vazia se o ficheiro não existir ou estiver vazio.
    """
    if not DATA_FILE.exists():
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def calculate_summary() -> dict:
    records = load_records()
    total = len(records)
    species_count = Counter(record.get("species") for record in records)
    municipality_count = Counter(record.get("municipality") for record in records)
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
    """Devolve um resumo estatístico dos registos de espécies invasoras em JSON."""
    return json.dumps(calculate_summary(), ensure_ascii=False, indent=2)


@tool
def get_species_list_tool() -> str:
    """Devolve a lista de espécies presentes nos registos."""
    records = load_records()
    species = sorted({record.get("species") for record in records if record.get("species")})
    return json.dumps(species, ensure_ascii=False)


@tool
def get_records_by_species_tool(species_name: str) -> str:
    """Devolve os registos associados a uma espécie específica."""
    records = load_records()
    filtered = [
        record for record in records
        if record.get("species") and record["species"].lower() == species_name.lower()
    ]
    return json.dumps(filtered, ensure_ascii=False, indent=2)


SYSTEM_PROMPT = """
És um assistente técnico-científico que ajuda a escrever relatórios preliminares
sobre espécies invasoras a partir de dados georreferenciados.
Regras obrigatórias:
- Usa apenas dados obtidos através das ferramentas disponíveis.
- Antes de escrever o relatório, chama get_summary_tool.
- Não inventes espécies, municípios, números, percentagens ou fontes.
- Não afirmes causalidade nem impacto ecológico sem dados adicionais.
- Usa linguagem clara, prudente e adequada a contexto técnico.
- Inclui sempre uma secção de limitações.
- Inclui recomendações apenas como propostas preliminares de monitorização.
- Escreve em português europeu.
Estrutura obrigatória do relatório:
1. Título
2. Resumo executivo
3. Principais resultados
4. Áreas prioritárias de monitorização
5. Limitações dos dados
6. Recomendações preliminares
"""


def build_agent():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY não definida no ficheiro .env")
    
    model = ChatOpenAI(
        model="gpt-5.4-nano",  # Alterado para o modelo ultra-económico atual
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
  


def generate_agent_report() -> str:
    agent = build_agent()
    user_request = (
        """
Gera um relatório preliminar sobre a prevalência de espécies invasoras.
Deves usar as ferramentas disponíveis para obter os dados.
Não uses conhecimento externo para completar valores em falta.
"""
    )
    result = agent.invoke({
        "messages": [
            {"role": "user", "content": user_request}
        ]
    })
    # A estrutura do resultado pode variar conforme a versão da biblioteca;
    # tentamos extrair a última mensagem quando disponível.
    final_message = None
    if isinstance(result, dict) and "messages" in result and result["messages"]:
        final_message = result["messages"][-1]
    elif hasattr(result, "message"):
        final_message = result.message
    if final_message is None:
        return json.dumps({"error": "Não foi possível obter resposta do agente"}, ensure_ascii=False)
    # final_message pode ser objecto com attribute 'content' ou dict
    content = getattr(final_message, "content", None) or (final_message.get("content") if isinstance(final_message, dict) else None)
    return content or str(final_message)
