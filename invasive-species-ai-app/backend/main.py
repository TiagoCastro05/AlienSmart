from collections import Counter
from pathlib import Path
import json
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
        "hotspots": hotspots,
    }

@app.post("/report-template")
def generate_template_report():
    """Gera um relatório básico usando um template pré-definido."""
    summary = get_summary()
    
    # Verifica se existem dados para evitar erros de chave se o summary estiver vazio
    if summary.get("total_records", 0) == 0:
        return {"report": "Não existem dados suficientes para gerar o relatório."}

    # Formatação mais limpa para os dicionários de contagem
    species_details = "\n".join([f"- {k}: {v}" for k, v in summary['species_count'].items()])
    municipality_details = "\n".join([f"- {k}: {v}" for k, v in summary['municipality_count'].items()])

    report = f"""
Relatório preliminar sobre espécies invasoras
=======================================
Foram analisados {summary['total_records']} registos georreferenciados.

Destaques:
----------
- A espécie com maior número de registos é: {summary['most_common_species']}
- O município com maior número de ocorrências é: {summary['most_common_municipality']}

Distribuição por espécie:
{species_details}

Distribuição por município:
{municipality_details}

Notas:
------
Este relatório deve ser interpretado com cautela, pois os dados podem refletir diferenças no esforço de amostragem.

Recomendações preliminares:
1. Validar os registos com observações de campo.
2. Priorizar municípios com maior número de ocorrências.
3. Atualizar a base de dados periodicamente.
4. Cruzar estes dados com informação ecológica adicional.
"""
    return {"report": report.strip()}

