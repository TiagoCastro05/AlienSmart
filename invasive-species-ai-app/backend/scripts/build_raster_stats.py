#!/usr/bin/env python
"""Calcula estatísticas para todos os rasters e guarda no Supabase.

Executa uma vez (ou sempre que os TIFFs mudam):
    python scripts/build_raster_stats.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from raster_tools import (
    get_available_species,
    get_raster_files,
    parse_raster_filename,
    compute_suitable_area,
    get_raster_stats,
)
from services.raster_service import save_raster_statistics


def build() -> None:
    species_list = get_available_species()
    print(f"Espécies encontradas: {len(species_list)}\n")

    saved = 0
    errors = 0

    for species in species_list:
        files = get_raster_files(species=species)
        for filepath in files:
            name = Path(filepath).name
            meta = parse_raster_filename(name)
            if meta is None:
                print(f"  [SKIP] Nome inválido: {name}")
                continue

            period   = meta.get("period")
            scenario = meta.get("scenario")

            try:
                stats     = get_raster_stats(filepath)
                area_data = compute_suitable_area(filepath, threshold=0.5)

                save_raster_statistics(
                    species=species,
                    period=period,
                    scenario=scenario,
                    suitable_area=area_data.get("suitable_area_km2"),
                    mean_suitability=stats.get("mean"),
                    max_suitability=stats.get("max"),
                    raster_file=name,
                )
                saved += 1
                print(f"  [OK] {species} / {period} / {scenario}")
            except Exception as exc:
                errors += 1
                print(f"  [ERRO] {species} / {period} / {scenario}: {exc}")

    print(f"\nConcluído — guardados: {saved}, erros: {errors}")


if __name__ == "__main__":
    build()
