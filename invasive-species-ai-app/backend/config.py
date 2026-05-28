"""
Configurações globais para AlienSmart com suporte a dados raster.
"""

from pathlib import Path

# Caminhos
PROJECT_ROOT = Path(__file__).parent.parent
RASTERS_DIR = PROJECT_ROOT / "Dados rasters"
DATA_DIR = Path(__file__).parent
DATA_FILE = DATA_DIR / "records.json"

# Raster Configuration
RASTER_CRS_DEFAULT = 6933  # EPSG:6933 (equal-area)
RASTER_CRS_GEOGRAPHIC = 4326  # EPSG:4326 (geographic)
RASTER_CRS_LAEA = 3035  # EPSG:3035 (LAEA Europa)

# Períodos e Cenários
PERIODS = ["hist", "2041-2070", "2071-2100"]
SCENARIOS = ["ssp126", "ssp370", "ssp585"]
PERIODS_DESCRIPTION = {
    "hist": "Período histórico 1981-2024",
    "2041-2070": "Projeção média 2041-2070 (CMIP6)",
    "2071-2100": "Projeção longa 2071-2100 (CMIP6)",
}
SCENARIOS_DESCRIPTION = {
    "ssp126": "Emissões muito baixas (SSP1-1.9)",
    "ssp370": "Emissões intermédias (SSP3-7.0)",
    "ssp585": "Emissões muito altas (SSP5-8.5)",
}

# API
API_TITLE = "Invasive Species AI API"
API_DESCRIPTION = "API para explorar registos e dados raster de espécies invasoras"
API_VERSION = "0.2.0"

print(f"[CONFIG] Rasters directory: {RASTERS_DIR}")
print(f"[CONFIG] Data file: {DATA_FILE}")
print(f"[CONFIG] API version: {API_VERSION}")
