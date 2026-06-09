from raster_db import save_raster_stats, get_raster_stats

save_raster_stats(
    species="Acacia dealbata",
    period="hist",
    scenario="ssp126",
    suitable_area=123.45,
    mean_suitability=0.67,
    max_suitability=0.95,
    raster_file="acacia_hist.tif"
)

result = get_raster_stats()

print(result)