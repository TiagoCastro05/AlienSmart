from collections import Counter
from pathlib import Path
import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from agent_report import generate_agent_report

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

@app.get("/records/by-species/{species_name}")
def get_records_by_species(species_name: str):
    """Devolve os registos de uma espécie específica."""
    records = load_records()
    return [
        record for record in records
        if record["species"].lower() == species_name.lower()
    ]

@app.get("/summary")
def get_summary():
    """Calcula indicadores simples de prevalência."""
    records = load_records()
    total = len(records)
    species_count = Counter(record["species"] for record in records)
    municipality_count = Counter(record["municipality"] for record in records)
    species_percentages = {
        species: round((count / total) * 100, 1)
        for species, count in species_count.items()
    } if total > 0 else {}
    hotspots = [
        {
            "municipality": municipality,
            "records": count,
        }
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
# Garante que tens este endpoint no backend/main.py

@app.post("/report-template")
def get_report_template():
    """
    Gera um relatório estático com base num template predefinido (Fallback).
    Não consome créditos da OpenAI.
    """
    try:
        # 1. Obter os dados determinísticos do teu próprio backend
        summary_data = get_summary() # Ou a função/lógica que calcula o resumo
        
        # 2. Criar o texto fixo injetando as variáveis
        report_text = f"""## 1. Título
**Relatório Automatizado (Template): Prevalência de Espécies Invasoras**

## 2. Resumo executivo
Com base no sistema de dados, existem atualmente {summary_data.get('total_records', 0)} registos filtrados.
A espécie mais abundante registada é *{summary_data.get('most_common_species', 'N/A')}*.

## 3. Limitações
- Este relatório foi gerado automaticamente por um template fixo e não contém interpretação contextualizada por IA.
"""
        
        # 3. Retornar o formato esperado com o source correto
        return {
            "source": "template_fallback",
            "report": report_text,
            "validation": {
                "valid": True,
                "problems": []
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar template: {str(e)}")
    

@app.post("/report")
def generate_report():
    summary = get_summary()
    try:
        report = generate_agent_report()
        source = "langchain_agent"
    except Exception as error:
        report = f"""
Relatório preliminar gerado por template
Não foi possível gerar o relatório com o agente de IA.
Motivo técnico: {str(error)}
Foram analisados {summary.get('total_records')} registos.
A espécie dominante nos dados é {summary.get('most_common_species')}.
O município com mais registos é {summary.get('most_common_municipality')}.
Limitações:
- Este relatório foi gerado por fallback determinístico.
- A análise é apenas preliminar.
- Os dados podem refletir enviesamentos de amostragem.
""".strip()
        source = "template_fallback"

    validation = validate_report_text(report, summary)
    return {
        "source": source,
        "report": report,
        "validation": validation,
    }