"""
Recorte (clip) de rasters SDM aos limites de um município.

Os limites administrativos dos concelhos NÃO vêm incluídos no projeto: são
descarregados automaticamente da fonte pública (CAOP via repositório
``nmota/caop_GeoJSON``, derivado dos dados oficiais da Direção-Geral do
Território) na primeira utilização e guardados em cache local em:

    backend/data/municipios.geojson

Nas utilizações seguintes usa-se a cache (rápido e funciona offline). Para
forçar nova transferência, apaga esse ficheiro.

Usa-se o MESMO ficheiro que o frontend (``Portugal_Municipalities.geojson``),
para que os nomes de concelho selecionados na interface casem com os do recorte.

Notas:
  - Cobertura: Portugal inteiro (308 concelhos, incluindo Açores e Madeira).
  - Campo do nome do concelho: ``Concelho`` (detetado automaticamente).
  - Qualquer CRS é reprojetado para o CRS do raster no momento do recorte.
"""

import os
import urllib.request
import unicodedata
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask
from rasterio import features as rio_features

from raster_tools import _pixel_area_km2

# Caminho da cache local. Aceita .geojson, .gpkg ou .shp.
DATA_DIR = Path(__file__).parent / "data"
MUNICIPIOS_PATH = DATA_DIR / "municipios.geojson"

# Fonte pública dos limites dos concelhos (CAOP em GeoJSON) — o mesmo ficheiro
# usado pelo frontend (MUNICIPIOS_GEOJSON_URL em script.js).
MUNICIPIOS_URL = (
    "https://raw.githubusercontent.com/nmota/caop_GeoJSON/master/"
    "Portugal_Municipalities.geojson"
)

# Nomes de campo onde pode estar o nome do concelho (vários datasets usam nomes
# diferentes). O primeiro que existir no ficheiro é usado.
NAME_FIELD_CANDIDATES = [
    "Concelho", "concelho", "CONCELHO",
    "Municipio", "municipio", "MUNICIPIO", "Município", "município",
    "NAME_2", "name_2", "NAME", "name", "Nome", "nome",
    "DESIGNACAO", "Designacao", "designacao",
]


def _normalize(value) -> str:
    """Minúsculas, sem acentos e sem espaços nas pontas — para casar nomes.
    Tolera valores não-string (ex.: NaN em campos vazios do GeoJSON)."""
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    nfkd = unicodedata.normalize("NFKD", value)
    sem_acentos = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return sem_acentos.lower().strip()


def _resolve_path() -> Path | None:
    """Devolve um ficheiro de limites já existente em cache (.geojson/.gpkg/.shp)."""
    if MUNICIPIOS_PATH.exists():
        return MUNICIPIOS_PATH
    for ext in (".gpkg", ".shp", ".json"):
        candidate = MUNICIPIOS_PATH.with_suffix(ext)
        if candidate.exists():
            return candidate
    return None


def ensure_municipios_file() -> Path:
    """
    Garante a existência do ficheiro de limites em cache; descarrega-o da fonte
    pública se ainda não existir. Devolve o caminho do ficheiro.
    """
    existing = _resolve_path()
    if existing is not None:
        return existing

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MUNICIPIOS_PATH.with_suffix(".geojson.part")
    req = urllib.request.Request(MUNICIPIOS_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
        out.write(resp.read())
    tmp.replace(MUNICIPIOS_PATH)  # rename atómico só após download completo
    return MUNICIPIOS_PATH


@lru_cache(maxsize=1)
def load_municipios():
    """
    Carrega o GeoDataFrame dos concelhos e o nome do campo de nome detetado.
    Descarrega os limites da fonte pública na primeira utilização.

    Returns:
        (geodataframe, name_field) — ou levanta ValueError se o campo não existir.
    """
    import geopandas as gpd  # import tardio: só necessário quando se usa município

    path = ensure_municipios_file()
    gdf = gpd.read_file(path)

    name_field = next((c for c in NAME_FIELD_CANDIDATES if c in gdf.columns), None)
    if name_field is None:
        raise ValueError(
            f"Não encontrei o campo do nome do concelho em {path.name}. "
            f"Colunas disponíveis: {list(gdf.columns)}. "
            f"Acrescenta o nome correto a NAME_FIELD_CANDIDATES."
        )

    # coluna normalizada para casar nomes de forma tolerante
    gdf = gdf.copy()
    gdf["_nome_norm"] = gdf[name_field].astype(str).map(_normalize)
    return gdf, name_field


def list_municipios() -> list[str]:
    """Lista os nomes de concelho disponíveis no ficheiro de limites."""
    gdf, name_field = load_municipios()
    return sorted(gdf[name_field].astype(str).unique().tolist())


def find_municipio(municipio: str):
    """
    Encontra a geometria de um concelho pelo nome (tolerante a acentos/maiúsculas).

    Returns:
        GeoSeries da linha correspondente, ou None se não existir.
    """
    gdf, _ = load_municipios()
    alvo = _normalize(municipio)
    match = gdf[gdf["_nome_norm"] == alvo]
    if match.empty:  # tentativa parcial (ex.: "Lisboa" vs "Grande Lisboa")
        match = gdf[gdf["_nome_norm"].str.contains(alvo, regex=False, na=False)]
    if match.empty:
        return None
    return match.iloc[0]


def clip_raster_to_municipio(raster_path: str, municipio: str,
                             out_path: str | None = None) -> dict:
    """
    Recorta um raster SDM aos limites de um concelho e calcula a área adequada
    dentro desse concelho.

    Args:
        raster_path: caminho para o .tif SDM.
        municipio:   nome do concelho.
        out_path:    onde gravar o .tif recortado (opcional; cria temporário).

    Returns:
        dict com:
          - clipped_path: caminho do .tif recortado (pronto para o build_map)
          - boundary_path: GeoJSON (EPSG:4326) com o contorno do concelho
          - suitable_path: GeoJSON (EPSG:4326) com a área adequada vetorizada e
            suavizada (evita o aspeto "pixelizado" dos blocos do raster); None
            se não houver área adequada
          - suitable_area_km2 / suitable_pct / n_suitable_pixels / n_valid_pixels
          - municipio: nome resolvido
        ou {"error": ...} em caso de falha.
    """
    raster_path = str(raster_path)
    if not Path(raster_path).exists():
        return {"error": f"Raster não encontrado: {raster_path}"}

    row = find_municipio(municipio)
    if row is None:
        return {"error": f"Município não encontrado nos limites: {municipio!r}"}

    try:
        import geopandas as gpd

        src_crs = load_municipios()[0].crs

        with rasterio.open(raster_path) as src:
            # reprojeta a geometria do concelho para o CRS do raster
            geom = gpd.GeoSeries([row.geometry], crs=src_crs)
            geom = geom.to_crs(src.crs)

            clipped, clip_transform = rio_mask(
                src, geom.geometry, crop=True, filled=True
            )
            band = clipped[0]
            nodata = src.nodata

            valid_mask = (band != nodata) if nodata is not None else np.ones(band.shape, dtype=bool)
            valid_vals = band[valid_mask]
            is_binary = set(np.unique(valid_vals).tolist()).issubset({0, 1}) if valid_vals.size else True
            suitable_mask = (band == 1) & valid_mask if is_binary else (band >= 0.5) & valid_mask

            pixel_area = _pixel_area_km2(src)
            n_valid = int(np.sum(valid_mask))
            n_suitable = int(np.sum(suitable_mask))
            suitable_pct = (n_suitable / n_valid * 100) if n_valid > 0 else 0.0

            # grava o raster recortado para ser renderizado pelo build_map
            if out_path is None:
                fd, out_path = tempfile.mkstemp(suffix="_municipio.tif")
                os.close(fd)
            profile = src.profile.copy()
            profile.update(
                height=band.shape[0],
                width=band.shape[1],
                transform=clip_transform,
            )
            with rasterio.open(out_path, "w", **profile) as dst:
                dst.write(band, 1)

        # exporta o contorno do concelho em EPSG:4326 para o QGIS desenhar por cima
        fd, boundary_path = tempfile.mkstemp(suffix="_municipio.geojson")
        os.close(fd)
        gpd.GeoSeries([row.geometry], crs=src_crs).to_crs(4326).to_file(
            boundary_path, driver="GeoJSON"
        )

        # vetoriza as células adequadas num polígono suavizado (menos pixelizado)
        suitable_path = None
        if n_suitable > 0:
            from shapely.geometry import shape
            from shapely.ops import unary_union

            shapes = rio_features.shapes(
                suitable_mask.astype("uint8"), mask=suitable_mask, transform=clip_transform
            )
            geoms = [shape(g) for g, v in shapes if v == 1]
            if geoms:
                merged = unary_union(geoms)
                px = abs(clip_transform.a)  # tamanho do pixel (unidades do CRS)
                # arredonda os "degraus" dos pixéis: dilata, contrai e simplifica
                merged = merged.buffer(px * 0.30).buffer(-px * 0.30).simplify(px * 0.40)
                if not merged.is_empty:
                    fd, suitable_path = tempfile.mkstemp(suffix="_suitable.geojson")
                    os.close(fd)
                    gpd.GeoSeries([merged], crs=src.crs).to_crs(4326).to_file(
                        suitable_path, driver="GeoJSON"
                    )

        return {
            "clipped_path": str(out_path),
            "boundary_path": str(boundary_path),
            "suitable_path": str(suitable_path) if suitable_path else None,
            "municipio": municipio,
            "suitable_area_km2": round(n_suitable * pixel_area, 2),
            "suitable_pct": round(suitable_pct, 2),
            "n_suitable_pixels": n_suitable,
            "n_valid_pixels": n_valid,
            "is_binary": is_binary,
        }

    except Exception as e:
        return {"error": str(e)}
