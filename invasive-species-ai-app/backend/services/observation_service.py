import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_FALLBACK_PATH = Path(__file__).parent.parent / "records.json"

try:
    from supabase_client import supabase as _sb
    _USE_SUPABASE = True
except Exception as _err:
    _sb = None
    _USE_SUPABASE = False
    logger.warning("Supabase não configurado — a usar records.json como fallback. (%s)", _err)

# Campos a selecionar com joins — devolve o formato plano esperado pelo resto da app
_SELECT = (
    "id, latitude, longitude, observed_on, notes, validation_status, "
    "species(scientific_name), municipality(name), data_source(name)"
)


def _flatten(row: dict) -> dict:
    """Converte uma linha com joins aninhados no formato plano usado pela app."""
    return {
        "id":           row["id"],
        "species":      (row["species"] or {}).get("scientific_name"),
        "latitude":     row["latitude"],
        "longitude":    row["longitude"],
        "date":         str(row["observed_on"]) if row.get("observed_on") else None,
        "municipality": (row["municipality"] or {}).get("name"),
        "source":       (row["data_source"] or {}).get("name"),
    }


def _species_id(species: str) -> int | None:
    result = _sb.table("species").select("id").ilike("scientific_name", species).execute()
    return result.data[0]["id"] if result.data else None


def _municipality_id(municipality: str) -> int | None:
    result = _sb.table("municipality").select("id").ilike("name", municipality).execute()
    return result.data[0]["id"] if result.data else None


def get_observations(species: str = None, municipality: str = None) -> list[dict]:
    """Devolve observações da BD Supabase, com fallback para records.json."""
    if _USE_SUPABASE:
        try:
            query = _sb.table("observation").select(_SELECT)

            if species:
                sid = _species_id(species)
                if sid is None:
                    return []
                query = query.eq("species_id", sid)

            if municipality:
                mid = _municipality_id(municipality)
                if mid is None:
                    return []
                query = query.eq("municipality_id", mid)

            result = query.execute()
            return [_flatten(r) for r in (result.data or [])]
        except Exception as exc:
            logger.error("Erro ao consultar Supabase — fallback para JSON: %s", exc)

    # fallback: records.json (formato plano)
    if not _FALLBACK_PATH.exists():
        return []
    with open(_FALLBACK_PATH, "r", encoding="utf-8") as fh:
        records = json.load(fh)
    if species:
        records = [r for r in records if r.get("species", "").lower() == species.lower()]
    if municipality:
        records = [r for r in records if r.get("municipality", "").lower() == municipality.lower()]
    return records
