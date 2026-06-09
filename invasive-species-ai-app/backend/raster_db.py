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