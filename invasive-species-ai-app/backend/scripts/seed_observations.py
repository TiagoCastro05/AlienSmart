#!/usr/bin/env python
"""Povoa as tabelas normalizadas do Supabase a partir de records.json.

Executa uma vez:
    python scripts/seed_observations.py

Ordem de inserção:
  1. species       (scientific_name UNIQUE)
  2. municipality  (name UNIQUE)
  3. data_source   (name UNIQUE)
  4. observation   (referencia as tabelas acima por FK)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from supabase_client import supabase

RECORDS_FILE = Path(__file__).parent.parent / "records.json"


def _upsert_lookup(table: str, conflict_col: str, rows: list[dict]) -> dict[str, int]:
    """Faz upsert das linhas e devolve um dict name→id."""
    for row in rows:
        supabase.table(table).upsert(row, on_conflict=conflict_col).execute()
    result = supabase.table(table).select(f"id, {conflict_col}").execute()
    return {r[conflict_col]: r["id"] for r in result.data}


def seed() -> None:
    if not RECORDS_FILE.exists():
        print(f"[ERRO] Ficheiro não encontrado: {RECORDS_FILE}")
        sys.exit(1)

    with open(RECORDS_FILE, "r", encoding="utf-8") as fh:
        records = json.load(fh)

    # Verificar se já existem observações
    existing = supabase.table("observation").select("id", count="exact").execute()
    if existing.count and existing.count > 0:
        print(f"Tabela já tem {existing.count} observações. A ignorar seed.")
        return

    # 1. species
    print("A inserir espécies…")
    species_map = _upsert_lookup(
        "species",
        "scientific_name",
        [{"scientific_name": r["species"]} for r in records],
    )

    # 2. municipality
    print("A inserir municípios…")
    municipality_map = _upsert_lookup(
        "municipality",
        "name",
        [{"name": r["municipality"]} for r in records if r.get("municipality")],
    )

    # 3. data_source
    print("A inserir fontes…")
    source_map = _upsert_lookup(
        "data_source",
        "name",
        [{"name": r["source"], "source_type": "field_observation"} for r in records if r.get("source")],
    )

    # 4. observations
    print("A inserir observações…")
    rows = []
    skipped = 0
    for r in records:
        sp_id  = species_map.get(r["species"])
        mun_id = municipality_map.get(r.get("municipality", ""))
        src_id = source_map.get(r.get("source", ""))

        if not (sp_id and mun_id and src_id):
            skipped += 1
            print(f"  [SKIP] Registo {r.get('id')} — FK em falta")
            continue

        rows.append({
            "species_id":      sp_id,
            "municipality_id": mun_id,
            "source_id":       src_id,
            "latitude":        r["latitude"],
            "longitude":       r["longitude"],
            "observed_on":     r.get("date"),
        })

    result = supabase.table("observation").insert(rows).execute()
    print(f"\n[OK] Inseridas {len(result.data)} observações ({skipped} ignoradas).")


if __name__ == "__main__":
    seed()
