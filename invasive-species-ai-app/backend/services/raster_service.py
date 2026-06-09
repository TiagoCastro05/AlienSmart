import logging
from raster_db import save_raster_stats as _save, get_raster_stats as _get

logger = logging.getLogger(__name__)


def save_raster_statistics(
    species: str,
    period: str,
    scenario: str,
    suitable_area: float,
    mean_suitability: float,
    max_suitability: float,
    raster_file: str,
) -> None:
    try:
        _save(species, period, scenario, suitable_area, mean_suitability, max_suitability, raster_file)
        logger.info("Estatísticas guardadas: %s / %s / %s", species, period, scenario)
    except Exception as exc:
        logger.error("Erro ao guardar estatísticas raster: %s", exc)
        raise


def get_all_raster_statistics() -> list[dict]:
    try:
        result = _get()
        return result.data or []
    except Exception as exc:
        logger.error("Erro ao consultar estatísticas raster: %s", exc)
        return []
