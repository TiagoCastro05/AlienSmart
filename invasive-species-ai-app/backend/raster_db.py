from supabase_client import supabase

def save_raster_stats(
    species,
    period,
    scenario,
    suitable_area,
    mean_suitability,
    max_suitability,
    raster_file
):
    return (
        supabase
        .table("raster_statistics")
        .insert({
            "species_name": species,
            "period": period,
            "scenario": scenario,
            "suitable_area_km2": suitable_area,
            "mean_suitability": mean_suitability,
            "max_suitability": max_suitability,
            "raster_file": raster_file
        })
        .execute()
    )
def get_raster_stats():
    return (
        supabase
        .table("raster_statistics")
        .select("*")
        .execute()
    )

def get_raster_stat(species: str, period: str, scenario: str | None = None):
    """Devolve uma linha da cache para espécie/período/cenário, ou None."""
    query = (
        supabase
        .table("raster_statistics")
        .select("*")
        .eq("species_name", species)
        .eq("period", period)
    )
    if scenario:
        query = query.eq("scenario", scenario)
    else:
        query = query.is_("scenario", "null")
    result = query.limit(1).execute()
    rows = result.data or []
    return rows[0] if rows else None


def get_raster_stats_by_period(period: str, scenario: str | None = None) -> list[dict]:
    """Devolve todas as linhas da cache para um período/cenário (uma query para todas as espécies)."""
    query = (
        supabase
        .table("raster_statistics")
        .select("species_name, suitable_area_km2")
        .eq("period", period)
    )
    if scenario:
        query = query.eq("scenario", scenario)
    else:
        query = query.is_("scenario", "null")
    result = query.execute()
    return result.data or []