"""
Endpoints para servir rasters SDM como imagens PNG com bounds para o Leaflet.
Adiciona ao main.py com: from raster_tiles import router as tiles_router
                         app.include_router(tiles_router)
"""

import hashlib
import logging
from pathlib import Path
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
import numpy as np
import io

from pyproj import Transformer

logger = logging.getLogger(__name__)

try:
    import rasterio
    from rasterio.warp import reproject, Resampling, calculate_default_transform, transform_bounds
    from rasterio.crs import CRS
    RASTERIO_OK = True
except ImportError:
    RASTERIO_OK = False

try:
    from PIL import Image
    PIL_OK = True
except ImportError:
    PIL_OK = False

from raster_tools import get_raster_files

router = APIRouter(prefix="/raster", tags=["raster-tiles"])

STATIC_DIR = Path(__file__).parent / "static"
TILES_DIR = STATIC_DIR / "tiles"
TILES_INDEX_PATH = STATIC_DIR / "tiles_index.json"

# Dicionário de cores movido para o nível global para ser usado por rasters contínuos e binários
COLORMAPS = {
    "Greens5": [(237, 248, 233), (186, 228, 179), (116, 196, 120), (49,  163, 84), (0,   109, 44)],
    "YlOrRd":  [(255, 255, 204), (254, 217, 142), (254, 153, 41),  (240, 59,  32), (189, 0,   38)],
    "Greens":  [(237, 248, 233), (186, 228, 179), (116, 196, 118), (49,  163, 84), (0,   109, 44)],
    "Blues":   [(239, 243, 255), (189, 215, 231), (107, 174, 214), (49,  130, 189), (8,   81,  156)],
    "RdPu":    [(253, 224, 221), (251, 180, 185), (247, 104, 161), (174, 1,   126), (73,  0,   106)],
}

def _compute_bounds_from_transform(transform, width, height) -> dict:
    west, north = transform * (0, 0)
    east, south = transform * (width, height)
    return {
        "south": round(float(south), 6),
        "west": round(float(west), 6),
        "north": round(float(north), 6),
        "east": round(float(east), 6),
    }

def _raster_to_png_overlay(filepath: str, colormap: str = "Greens5") -> tuple[bytes, dict]:
    if not RASTERIO_OK or not PIL_OK:
        raise RuntimeError("rasterio ou Pillow não estão instalados")

    with rasterio.open(filepath) as src:
        nodata_val = src.nodata if src.nodata is not None else -9999
        raw_sample = src.read(1)
        raw_valid = raw_sample[raw_sample != nodata_val]
        is_binary = (
            len(raw_valid) > 0
            and set(np.unique(raw_valid).tolist()).issubset({0, 1, 0.0, 1.0})
        )

        web_mercator_crs = CRS.from_epsg(3857)
        wgs84_crs = CRS.from_epsg(4326)

        transform_3857, width, height = calculate_default_transform(
            src.crs, web_mercator_crs, src.width, src.height, *src.bounds
        )

        data_web = np.zeros((height, width), dtype=np.float32)

        reproject(
            source=rasterio.band(src, 1),
            destination=data_web,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=transform_3857,
            dst_crs=web_mercator_crs,
            resampling=Resampling.nearest if is_binary else Resampling.bilinear,
            src_nodata=nodata_val,
            dst_nodata=nodata_val,
        )

        west_m, north_m = transform_3857 * (0, 0)
        east_m, south_m = transform_3857 * (width, height)

        transformer = Transformer.from_crs(web_mercator_crs, wgs84_crs, always_xy=True)
        west, south = transformer.transform(west_m, south_m)
        east, north = transformer.transform(east_m, north_m)

        bounds_dict = {
            "south": round(float(south), 6),
            "west": round(float(west), 6),
            "north": round(float(north), 6),
            "east": round(float(east), 6),
        }

        mask = (data_web != nodata_val) & np.isfinite(data_web)
        valid = data_web[mask]
        if len(valid) == 0:
            raise ValueError("Raster sem dados válidos")

        if is_binary:
            rgba = np.zeros((data_web.shape[0], data_web.shape[1], 4), dtype=np.uint8)
            hits = (data_web == 1) & mask
            # CORREÇÃO: Vai buscar a cor mais escura da paleta escolhida (o último elemento do array)
            colors_list = COLORMAPS.get(colormap, COLORMAPS["Greens5"])
            base_color = colors_list[-1]
            rgba[hits, 0] = base_color[0]
            rgba[hits, 1] = base_color[1]
            rgba[hits, 2] = base_color[2]
            rgba[hits, 3] = 200
        else:
            vmin, vmax = float(valid.min()), float(valid.max())
            normalized = np.zeros_like(data_web) if vmax == vmin else np.clip((data_web - vmin) / (vmax - vmin), 0, 1)
            rgba = _apply_colormap(normalized, mask, colormap)

        img = Image.fromarray(rgba, mode="RGBA")

        max_dim = 1024
        h, w = rgba.shape[:2]
        if max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            img = img.resize((int(w * scale), int(h * scale)), Image.BILINEAR)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)

        return buf.read(), bounds_dict


def _load_index() -> dict:
    if not TILES_INDEX_PATH.exists():
        return {}
    with open(TILES_INDEX_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def _save_index(index: dict) -> None:
    TILES_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TILES_INDEX_PATH, "w", encoding="utf-8") as file:
        json.dump(index, file, ensure_ascii=False, indent=2)


def _cache_key(map_name: str, colormap: str) -> str:
    return f"{map_name}__{colormap}"


def _file_hash(filepath: str) -> str:
    """SHA-1 dos primeiros 64 KB do TIFF — rápido e suficiente para detectar mudanças."""
    h = hashlib.sha1()
    with open(filepath, "rb") as fh:
        h.update(fh.read(65536))
    return h.hexdigest()


def _ensure_cached(filepath: str, colormap: str) -> dict:
    TILES_DIR.mkdir(parents=True, exist_ok=True)

    path_obj = Path(filepath)
    map_name = path_obj.stem
    key      = _cache_key(map_name, colormap)
    png_name = f"{map_name}_{colormap}.png"
    png_path = TILES_DIR / png_name

    current_mtime = path_obj.stat().st_mtime
    current_hash  = _file_hash(filepath)

    index = _load_index()
    entry = index.get(key)

    cache_valid = (
        entry is not None
        and png_path.exists()
        and entry.get("raster_hash") == current_hash
        and entry.get("last_modified") == current_mtime
        and _bounds_valid(entry.get("bounds", {}))
    )

    if cache_valid:
        return entry

    if entry and png_path.exists():
        logger.info("TIFF alterado, a regenerar cache: %s", path_obj.name)

    png_bytes, bounds = _raster_to_png_overlay(filepath, colormap=colormap)
    png_path.write_bytes(png_bytes)

    entry = {
        "map_name":      map_name,
        "png_name":      png_name,
        "bounds":        bounds,
        "colormap":      colormap,
        "last_modified": current_mtime,
        "raster_hash":   current_hash,
    }
    index[key] = entry
    _save_index(index)
    return entry


def _bounds_valid(bounds: dict) -> bool:
    return (
        bool(bounds)
        and abs(bounds.get("west",  9999)) <= 180
        and abs(bounds.get("east",  9999)) <= 180
        and abs(bounds.get("south", 9999)) <= 90
        and abs(bounds.get("north", 9999)) <= 90
    )


def _apply_colormap(normalized: np.ndarray, mask: np.ndarray, colormap: str) -> np.ndarray:
    h, w = normalized.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    colors = COLORMAPS.get(colormap, COLORMAPS["Greens5"])
    n = len(colors) - 1

    vals = normalized[mask]
    indices = (vals * n).astype(int)
    indices = np.clip(indices, 0, n - 1)
    fracs = (vals * n) - indices

    r = np.zeros(len(vals), dtype=np.uint8)
    g = np.zeros(len(vals), dtype=np.uint8)
    b = np.zeros(len(vals), dtype=np.uint8)

    for i in range(len(vals)):
        c0 = colors[indices[i]]
        c1 = colors[min(indices[i] + 1, n)]
        f = fracs[i]
        r[i] = int(c0[0] * (1 - f) + c1[0] * f)
        g[i] = int(c0[1] * (1 - f) + c1[1] * f)
        b[i] = int(c0[2] * (1 - f) + c1[2] * f)

    flat_mask = mask.ravel()
    rgba_flat = rgba.reshape(-1, 4)
    rgba_flat[flat_mask, 0] = r
    rgba_flat[flat_mask, 1] = g
    rgba_flat[flat_mask, 2] = b
    rgba_flat[flat_mask, 3] = 200

    return rgba


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/tile/{species}")
def get_raster_tile(
    species: str,
    period: str = "hist",
    scenario: str = None,
    colormap: str = "Greens5",
    binary: bool = True,
):
    files = get_raster_files(species=species, period=period, scenario=scenario, binary=binary)
    if not files:
        files = get_raster_files(species=species, period=period, scenario=scenario)
    if not files:
        raise HTTPException(
            status_code=404,
            detail=f"Nenhum raster encontrado para {species} / {period} / {scenario}"
        )

    try:
        entry = _ensure_cached(files[0], colormap=colormap)
        png_path = TILES_DIR / entry["png_name"]
        return Response(content=png_path.read_bytes(), media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bounds/{species}")
def get_raster_bounds(
    species: str,
    period: str = "hist",
    scenario: str = None,
):
    files = get_raster_files(species=species, period=period, scenario=scenario, binary=True)
    if not files:
        files = get_raster_files(species=species, period=period, scenario=scenario)
    if not files:
        raise HTTPException(
            status_code=404,
            detail=f"Nenhum raster encontrado para {species} / {period} / {scenario}"
        )

    try:
        entry = _ensure_cached(files[0], colormap="Greens5")
        return {
            "species": species,
            "period": period,
            "scenario": scenario,
            "file": Path(files[0]).name,
            "bounds": entry["bounds"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/overlay-info/{species}")
def get_overlay_info(
    species: str,
    period: str = "hist",
    scenario: str = None,
    colormap: str = "Greens5",
    binary: bool = True,
):
    files = get_raster_files(species=species, period=period, scenario=scenario, binary=binary)
    if not files:
        files = get_raster_files(species=species, period=period, scenario=scenario)
    if not files:
        raise HTTPException(status_code=404, detail="Nenhum raster encontrado")

    try:
        entry = _ensure_cached(files[0], colormap=colormap)

        south = entry["bounds"]["south"]
        west = entry["bounds"]["west"]
        north = entry["bounds"]["north"]
        east = entry["bounds"]["east"]

        scenario_param = f"&scenario={scenario}" if scenario else ""
        binary_param = f"&binary={str(binary).lower()}"
        tile_url = f"/raster/tile/{species}?period={period}{scenario_param}{binary_param}&colormap={colormap}"

        return {
            "tile_url": tile_url,
            "bounds": [
                [float(south), float(west)],
                [float(north), float(east)]
            ],
            "species": species,
            "period": period,
            "scenario": scenario,
            "binary": binary,
        }
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=traceback.format_exc())