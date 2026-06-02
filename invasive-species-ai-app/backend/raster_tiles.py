"""
Endpoints para servir rasters SDM como imagens PNG com bounds para o Leaflet.
Adiciona ao main.py com: from raster_tiles import router as tiles_router
                          app.include_router(tiles_router)
"""

from pathlib import Path
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
import numpy as np
import io

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


def _compute_bounds_wgs84(src: rasterio.DatasetReader) -> dict:
    if not src.crs:
        raise RuntimeError("Raster sem CRS definido")

    dst_crs = CRS.from_epsg(4326)
    if src.crs == dst_crs:
        bounds = src.bounds
        west, south, east, north = bounds.left, bounds.bottom, bounds.right, bounds.top
    else:
        west, south, east, north = transform_bounds(
            src.crs, dst_crs, *src.bounds, densify_pts=21
        )

    return {
        "south": round(float(south), 6),
        "west": round(float(west), 6),
        "north": round(float(north), 6),
        "east": round(float(east), 6),
    }


def _raster_to_png_overlay(filepath: str, colormap: str = "YlOrRd") -> tuple[bytes, dict]:
    """
    Converte um raster .tif para PNG RGBA + bounds em WGS84.
    
    Returns:
        (png_bytes, bounds_dict)
        bounds_dict = {"south": float, "west": float, "north": float, "east": float}
    """
    if not RASTERIO_OK:
        raise RuntimeError("rasterio não está instalado")
    if not PIL_OK:
        raise RuntimeError("Pillow não está instalado: pip install Pillow")

    with rasterio.open(filepath) as src:
        # Reprojectar para WGS84 se necessário
        dst_crs = CRS.from_epsg(4326)

        if src.crs != dst_crs:
            transform, width, height = calculate_default_transform(
                src.crs, dst_crs, src.width, src.height, *src.bounds
            )
            data_wgs84 = np.zeros((height, width), dtype=np.float32)
            nodata_val = src.nodata if src.nodata is not None else -9999

            reproject(
                source=rasterio.band(src, 1),
                destination=data_wgs84,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=transform,
                dst_crs=dst_crs,
                resampling=Resampling.nearest,
                src_nodata=nodata_val,
                dst_nodata=nodata_val,
            )

        else:
            data_wgs84 = src.read(1).astype(np.float32)
            nodata_val = src.nodata if src.nodata is not None else -9999
        bounds_dict = _compute_bounds_wgs84(src)

        # Máscara de nodata
        mask = data_wgs84 != nodata_val

        # Detectar binario vs continuo
        valid = data_wgs84[mask]
        if len(valid) == 0:
            raise ValueError("Raster sem dados válidos")

        unique_vals = np.unique(valid)
        is_binary = set(unique_vals.tolist()).issubset({0, 1})

        if is_binary:
            rgba = np.zeros((data_wgs84.shape[0], data_wgs84.shape[1], 4), dtype=np.uint8)
            hits = (data_wgs84 == 1) & mask
            rgba[hits, 0] = 45
            rgba[hits, 1] = 106
            rgba[hits, 2] = 79
            rgba[hits, 3] = 200
        else:
            vmin, vmax = float(valid.min()), float(valid.max())
            if vmax == vmin:
                normalized = np.zeros_like(data_wgs84)
            else:
                normalized = np.clip((data_wgs84 - vmin) / (vmax - vmin), 0, 1)

            # Aplicar colormap (sem matplotlib — implementação manual)
            rgba = _apply_colormap(normalized, mask, colormap)

        # Converter para PNG
        img = Image.fromarray(rgba, mode="RGBA")

        # Redimensionar se muito grande (max 1024px no lado maior)
        max_dim = 1024
        h, w = rgba.shape[:2]
        if max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            new_w, new_h = int(w * scale), int(h * scale)
            img = img.resize((new_w, new_h), Image.NEAREST)

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


def _ensure_cached(filepath: str, colormap: str) -> dict:
    TILES_DIR.mkdir(parents=True, exist_ok=True)

    map_name = Path(filepath).stem
    key = _cache_key(map_name, colormap)
    png_name = f"{map_name}_{colormap}.png"
    png_path = TILES_DIR / png_name

    index = _load_index()
    entry = index.get(key)

    if png_path.exists() and entry:
        try:
            with rasterio.open(filepath) as src:
                entry["bounds"] = _compute_bounds_wgs84(src)
            index[key] = entry
            _save_index(index)
        except Exception:
            pass
        return entry

    png_bytes, bounds = _raster_to_png_overlay(filepath, colormap=colormap)

    if not png_path.exists():
        png_path.write_bytes(png_bytes)

    entry = {
        "map_name": map_name,
        "png_name": png_name,
        "bounds": bounds,
        "colormap": colormap,
    }
    index[key] = entry
    _save_index(index)
    return entry


def _apply_colormap(normalized: np.ndarray, mask: np.ndarray, colormap: str) -> np.ndarray:
    """
    Aplica colormap manual a array normalizado [0,1].
    Retorna array RGBA uint8.
    """
    h, w = normalized.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    # Colormaps definidos como gradientes RGB
    COLORMAPS = {
        "YlOrRd": [
            (255, 255, 204),
            (254, 217, 142),
            (254, 153, 41),
            (240, 59,  32),
            (189, 0,   38),
        ],
        "Greens": [
            (237, 248, 233),
            (186, 228, 179),
            (116, 196, 118),
            (49,  163, 84),
            (0,   109, 44),
        ],
        "Blues": [
            (239, 243, 255),
            (189, 215, 231),
            (107, 174, 214),
            (49,  130, 189),
            (8,   81,  156),
        ],
        "RdPu": [
            (253, 224, 221),
            (251, 180, 185),
            (247, 104, 161),
            (174, 1,   126),
            (73,  0,   106),
        ],
    }

    colors = COLORMAPS.get(colormap, COLORMAPS["YlOrRd"])
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
    rgba_flat[flat_mask, 3] = 200  # alpha (ligeiramente transparente)

    return rgba


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/tile/{species}")
def get_raster_tile(
    species: str,
    period: str = "hist",
    scenario: str = None,
    colormap: str = "YlOrRd",
):
    """
    Devolve um raster SDM como imagem PNG para overlay no Leaflet.
    
    Parâmetros:
        - species: Nome científico (ex: Ailanthus_altissima)
        - period: hist | 2041-2070 | 2071-2100
        - scenario: ssp126 | ssp370 | ssp585 (não usado em hist)
        - colormap: YlOrRd | Greens | Blues | RdPu
    
    Resposta: imagem PNG (usa junto com /raster/bounds/{species})
    """
    files = get_raster_files(species=species, period=period, scenario=scenario, binary=True)
    if not files:
        # Tentar sem filtro binário
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
    """
    Devolve os bounds geográficos (WGS84) de um raster para posicionar o overlay no Leaflet.
    
    Retorna: { south, west, north, east }
    """
    files = get_raster_files(species=species, period=period, scenario=scenario, binary=True)
    if not files:
        files = get_raster_files(species=species, period=period, scenario=scenario)
    if not files:
        raise HTTPException(
            status_code=404,
            detail=f"Nenhum raster encontrado para {species} / {period} / {scenario}"
        )

    try:
        entry = _ensure_cached(files[0], colormap="YlOrRd")
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
    colormap: str = "YlOrRd",
):
    """
    Endpoint de conveniência: devolve URL da tile + bounds num único pedido.
    O frontend usa isto para montar o ImageOverlay do Leaflet sem dois pedidos.
    
    Retorna:
    {
        "tile_url": "/raster/tile/{species}?period=...&scenario=...&colormap=...",
        "bounds": [[south, west], [north, east]],   ← formato Leaflet
        "species": "...",
        "period": "...",
        "scenario": "..."
    }
    """
    files = get_raster_files(species=species, period=period, scenario=scenario, binary=True)
    if not files:
        files = get_raster_files(species=species, period=period, scenario=scenario)
    if not files:
        raise HTTPException(
            status_code=404,
            detail=f"Nenhum raster encontrado para {species} / {period} / {scenario}"
        )

    try:
        entry = _ensure_cached(files[0], colormap=colormap)

        scenario_param = f"&scenario={scenario}" if scenario else ""
        tile_url = f"/raster/tile/{species}?period={period}{scenario_param}&colormap={colormap}"

        return {
            "tile_url": tile_url,
            "bounds": [
                [entry["bounds"]["south"], entry["bounds"]["west"]],
                [entry["bounds"]["north"], entry["bounds"]["east"]],
            ],
            "species": species,
            "period": period,
            "scenario": scenario,
            "file": Path(files[0]).name,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))