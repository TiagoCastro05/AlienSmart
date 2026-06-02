"""
SDM Raster Tools para AlienSmart
Funções para ler, analisar e comparar ficheiros raster de distribuição de espécies invasoras.
Baseado no guião: SDM Raster Tools - Nomenclatura, Arquitectura de Tools e Aplicações
"""

import re
import json
from pathlib import Path
import rasterio
import numpy as np
from pyproj import Transformer
import warnings
warnings.filterwarnings("ignore")

# Configurações
RASTERS_DIR = Path(__file__).parent.parent / "Dados rasters"
RASTER_PATTERN = r"^(.+?)_(\d+)_(.+?)(?:_(ssp\d+))?(_bin)?_eur\.tif$"

# ============================================================================
# CAMADA 1 - INVENTÁRIO E DESCOBERTA
# ============================================================================

def get_available_species() -> list[str]:
    """
    Retorna lista de espécies únicas disponíveis nos rasters.
    """
    if not RASTERS_DIR.exists():
        return []
    
    species_set = set()
    for filepath in RASTERS_DIR.glob("*.tif"):
        parsed = parse_raster_filename(filepath.name)
        if parsed and parsed.get("species"):
            species_set.add(parsed["species"])
    
    return sorted(list(species_set))


def get_raster_files(
    species: str = None,
    period: str = None,
    scenario: str = None,
    binary: bool = None
) -> list[str]:
    """
    Retorna lista de ficheiros raster filtrados por critérios.
    
    Args:
        species: Nome científico (ex: "Ailanthus_altissima")
        period: "hist", "2041-2070", "2071-2100"
        scenario: "ssp126", "ssp370", "ssp585"
        binary: True/False para filtrar binários/contínuos
    
    Returns:
        Lista de caminhos absolutos dos ficheiros.
    """
    if not RASTERS_DIR.exists():
        return []
    
    matching_files = []
    for filepath in RASTERS_DIR.glob("*.tif"):
        parsed = parse_raster_filename(filepath.name)
        if not parsed:
            continue
        
        # Aplicar filtros
        if species and parsed["species"] != species:
            continue
        if period and parsed["period"] != period:
            continue
        if scenario and parsed.get("scenario") != scenario:
            continue
        if binary is not None and parsed["is_binary"] != binary:
            continue
        
        matching_files.append(str(filepath))
    
    return sorted(matching_files)


def parse_raster_filename(filename: str) -> dict:
    """
    Extrai metadados do nome do ficheiro raster.
    
    Nomenclatura: [species]_[gbif_id]_[period]_[scenario]_[bin]_eur.tif
    Exemplos:
        - Ailanthus_altissima_3190653_2041_2070_ssp370_bin_eur.tif
        - Ailanthus_altissima_3190653_hist_eur.tif
    
    Returns:
        dict com: species, gbif_id, period, scenario, is_binary
        ou None se não corresponder ao padrão
    """
    match = re.match(RASTER_PATTERN, filename)
    if not match:
        return None
    
    species, gbif_id, period, scenario, bin_flag = match.groups()
    
    # Normalizar período: converter underscores em hífens (2041_2070 -> 2041-2070)
    period = period.replace("_", "-")
    
    return {
        "filename": filename,
        "species": species.replace("_", " "),
        "gbif_id": int(gbif_id),
        "period": period,
        "scenario": scenario,  # None se histórico
        "is_binary": bool(bin_flag),
    }


# ============================================================================
# CAMADA 2 - SÍNTESE ESTATÍSTICA
# ============================================================================

def _pixel_area_km2(src: rasterio.DatasetReader) -> float:
    """
    Calcula a área de um pixel em km².
    
    - CRS projetado equal-area (ex: EPSG:6933, EPSG:3035): cálculo direto.
    - CRS geográfico (ex: EPSG:4326): reprojecta para LAEA.
    """
    crs = src.crs
    if crs.is_projected:
        pixel_area_m2 = abs(src.transform.a * src.transform.e)
        return pixel_area_m2 / 1e6
    else:
        # CRS geográfico - reprojectar para LAEA
        row_mid, col_mid = src.height // 2, src.width // 2
        lon0, lat0 = src.xy(row_mid, col_mid, offset='ul')
        lon1, lat1 = src.xy(row_mid + 1, col_mid + 1, offset='ul')
        
        transformer = Transformer.from_crs(crs.to_epsg(), 3035, always_xy=True)
        x0, y0 = transformer.transform(lon0, lat0)
        x1, y1 = transformer.transform(lon1, lat1)
        
        return abs((x1 - x0) * (y1 - y0)) / 1e6


def compute_suitable_area(filepath: str, threshold: float = 0.5) -> dict:
    """
    Calcula a área adequada de uma espécie a partir de um raster SDM.
    
    Suporta rasters binários (int: 0/1) e contínuos (float: 0.0-1.0).
    CRS recomendado: EPSG:6933 (equal-area).
    
    Args:
        filepath: Caminho para o ficheiro .tif
        threshold: Limiar para rasters contínuos (default=0.5)
    
    Returns:
        dict com métricas de adequabilidade
    """
    filepath = str(filepath)
    if not Path(filepath).exists():
        return {"error": f"Ficheiro não encontrado: {filepath}"}
    
    try:
        with rasterio.open(filepath) as src:
            crs = src.crs
            transform = src.transform
            nodata = src.nodata
            
            data = src.read(1)  # Primeira banda
            
            # Detectar automaticamente se é binário ou contínuo
            valid_data = data[data != nodata] if nodata is not None else data.ravel()
            unique_vals = np.unique(valid_data)
            is_binary = set(unique_vals.tolist()).issubset({0, 1})
            
            # Máscaras
            valid_mask = (data != nodata) if nodata is not None else np.ones(data.shape, dtype=bool)
            suitable_mask = (data == 1) & valid_mask if is_binary else (data >= threshold) & valid_mask
            
            # Área por pixel
            pixel_area_km2 = _pixel_area_km2(src)
            
            # Contagens e áreas
            n_valid = int(np.sum(valid_mask))
            n_suitable = int(np.sum(suitable_mask))
            suitable_area_km2 = n_suitable * pixel_area_km2
            total_valid_area_km2 = n_valid * pixel_area_km2
            suitable_pct = (n_suitable / n_valid * 100) if n_valid > 0 else 0.0
            
            pixel_res_m = abs(transform.a) if crs.is_projected else None
            
            return {
                "suitable_area_km2": round(suitable_area_km2, 2),
                "total_valid_area_km2": round(total_valid_area_km2, 2),
                "suitable_pct": round(suitable_pct, 2),
                "pixel_res_m": round(pixel_res_m, 1) if pixel_res_m else None,
                "crs": crs.to_epsg() if crs else None,
                "is_binary": is_binary,
                "n_suitable_pixels": n_suitable,
                "n_valid_pixels": n_valid,
            }
    
    except Exception as e:
        return {"error": str(e)}


def get_raster_stats(filepath: str) -> dict:
    """
    Estatísticas básicas do raster.
    """
    filepath = str(filepath)
    if not Path(filepath).exists():
        return {"error": f"Ficheiro não encontrado: {filepath}"}
    
    try:
        with rasterio.open(filepath) as src:
            data = src.read(1)
            nodata = src.nodata
            valid_data = data[data != nodata] if nodata is not None else data.ravel()
            
            return {
                "min": round(float(np.min(valid_data)), 4),
                "max": round(float(np.max(valid_data)), 4),
                "mean": round(float(np.mean(valid_data)), 4),
                "std": round(float(np.std(valid_data)), 4),
                "count": int(np.sum(valid_data > 0)) if len(valid_data) > 0 else 0,
                "nodata_pct": round(100 * np.sum(data == nodata) / data.size, 2) if nodata else 0.0,
            }
    
    except Exception as e:
        return {"error": str(e)}


def compare_periods(species: str, scenario: str = "ssp370") -> dict:
    """
    Compara área adequada entre períodos (hist, 2041-2070, 2071-2100).
    """
    results = {}
    
    for period in ["hist", "2041-2070", "2071-2100"]:
        # Histórico não tem cenário; períodos futuros sim
        if period == "hist":
            files = get_raster_files(species=species, period=period, binary=True)
        else:
            files = get_raster_files(species=species, period=period, scenario=scenario, binary=True)
        
        if files:
            stats = compute_suitable_area(files[0])
            results[period] = {
                "suitable_area_km2": stats.get("suitable_area_km2", 0),
                "suitable_pct": stats.get("suitable_pct", 0),
            }
        else:
            results[period] = {"suitable_area_km2": 0, "suitable_pct": 0}
    
    # Calcular mudanças
    hist_area = results.get("hist", {}).get("suitable_area_km2", 0)
    period_2041 = results.get("2041-2070", {}).get("suitable_area_km2", 0)
    period_2071 = results.get("2071-2100", {}).get("suitable_area_km2", 0)
    
    return {
        "species": species,
        "scenario": scenario,
        "by_period": results,
        "changes": {
            "hist_to_2041_2070_km2": round(period_2041 - hist_area, 2),
            "hist_to_2071_2100_km2": round(period_2071 - hist_area, 2),
            "hist_to_2041_2070_pct": round((period_2041 - hist_area) / hist_area * 100, 2) if hist_area > 0 else 0,
            "hist_to_2071_2100_pct": round((period_2071 - hist_area) / hist_area * 100, 2) if hist_area > 0 else 0,
        }
    }


# ============================================================================
# CAMADA 3 - SOBREPOSIÇÃO ESPACIAL
# ============================================================================

def overlap_two_species(species1: str, species2: str, period: str = "hist", operation: str = "intersection") -> dict:
    """
    Calcula sobreposição entre dois rasters de espécies.
    
    Args:
        species1, species2: Nomes de espécies
        period: Período temporal
        operation: "intersection", "union", "difference"
    """
    files1 = get_raster_files(species=species1, period=period, binary=True)
    files2 = get_raster_files(species=species2, period=period, binary=True)
    
    if not files1 or not files2:
        return {"error": "Ficheiros não encontrados para uma ou ambas as espécies"}
    
    try:
        with rasterio.open(files1[0]) as src1:
            data1 = src1.read(1)
            pixel_area = _pixel_area_km2(src1)
        
        with rasterio.open(files2[0]) as src2:
            data2 = src2.read(1)
        
        # Garantir mesmas dimensões
        if data1.shape != data2.shape:
            return {"error": "Rasters têm dimensões diferentes"}
        
        if operation == "intersection":
            overlap = np.logical_and(data1 == 1, data2 == 1)
        elif operation == "union":
            overlap = np.logical_or(data1 == 1, data2 == 1)
        elif operation == "difference":
            overlap = np.logical_and(data1 == 1, np.logical_not(data2 == 1))
        else:
            return {"error": f"Operação desconhecida: {operation}"}
        
        overlap_km2 = np.sum(overlap) * pixel_area
        
        return {
            "species1": species1,
            "species2": species2,
            "operation": operation,
            "period": period,
            "overlap_km2": round(overlap_km2, 2),
            "overlap_pixels": int(np.sum(overlap)),
        }
    
    except Exception as e:
        return {"error": str(e)}


# ============================================================================
# CAMADA 4 - COMPARAÇÃO DE CENÁRIOS
# ============================================================================

def scenario_matrix(species: str, periods: list = None, scenarios: list = None) -> dict:
    """
    Gera matriz período × cenário com áreas adequadas.
    
    Args:
        species: Nome da espécie
        periods: Períodos a incluir (default: ["hist", "2041-2070", "2071-2100"])
        scenarios: Cenários a incluir (default: ["ssp126", "ssp370", "ssp585"])
    
    Returns:
        Matriz estruturada para comparações e raciocínio do agente
    """
    if periods is None:
        periods = ["hist", "2041-2070", "2071-2100"]
    if scenarios is None:
        scenarios = ["ssp126", "ssp370", "ssp585"]
    
    matrix = {}
    
    for period in periods:
        matrix[period] = {}
        
        if period == "hist":
            # Histórico não tem cenário
            files = get_raster_files(species=species, period=period, binary=True)
            if files:
                stats = compute_suitable_area(files[0])
                matrix[period]["hist"] = round(stats.get("suitable_area_km2", 0), 2)
            else:
                matrix[period]["hist"] = None
        else:
            # Períodos futuros têm cenários
            for scenario in scenarios:
                files = get_raster_files(species=species, period=period, scenario=scenario, binary=True)
                if files:
                    stats = compute_suitable_area(files[0])
                    matrix[period][scenario] = round(stats.get("suitable_area_km2", 0), 2)
                else:
                    matrix[period][scenario] = None
    
    return {
        "species": species,
        "matrix": matrix,
        "summary": _summarize_matrix(matrix)
    }


def _summarize_matrix(matrix: dict) -> dict:
    """
    Calcula resumo de tendências na matriz.
    """
    # Extrair valores válidos
    values = []
    for period_data in matrix.values():
        for val in period_data.values():
            if val is not None:
                values.append(val)
    
    if not values:
        return {"trend": "sem dados"}
    
    hist_val = matrix.get("hist", {}).get("hist")
    future_vals = [v for period_data in [matrix.get("2041-2070", {}), matrix.get("2071-2100", {})] 
                   for v in period_data.values() if v is not None]
    
    trend = "desconhecido"
    if hist_val and future_vals:
        avg_future = np.mean(future_vals)
        if avg_future > hist_val * 1.1:
            trend = "expansão"
        elif avg_future < hist_val * 0.9:
            trend = "contração"
        else:
            trend = "estável"
    
    return {
        "trend": trend,
        "min_area_km2": round(min(values), 2),
        "max_area_km2": round(max(values), 2),
        "avg_area_km2": round(np.mean(values), 2),
    }


# ============================================================================
# CAMADA 5 - SERVIR RASTERS NA WEB (LEAFLET)
# ============================================================================

def get_raster_bounds(filepath: str) -> dict:
    """
    Obtém os bounds (limites) de um raster em coordenadas geográficas (EPSG:4326).
    
    Útil para Leaflet imageBounds e zoom automático.
    
    Args:
        filepath: Caminho para o ficheiro .tif
    
    Returns:
        dict com bounds [[south, west], [north, east]] e meta informações
    """
    filepath = str(filepath)
    if not Path(filepath).exists():
        return {"error": f"Ficheiro não encontrado: {filepath}"}
    
    try:
        with rasterio.open(filepath) as src:
            crs = src.crs
            bounds = src.bounds  # (left, bottom, right, top)
            
            # Se CRS é geográfico, usar direto
            if crs.to_epsg() == 4326:
                return {
                    "bounds": [[bounds.bottom, bounds.left], [bounds.top, bounds.right]],
                    "crs": 4326,
                    "width": src.width,
                    "height": src.height,
                }
            
            # Converter para EPSG:4326 se for CRS projetado
            transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
            
            # Converter 4 cantos do bounds
            corners = [
                (bounds.left, bounds.bottom),   # SW
                (bounds.right, bounds.bottom),  # SE
                (bounds.left, bounds.top),      # NW
                (bounds.right, bounds.top),     # NE
            ]
            
            lons = []
            lats = []
            for lon, lat in corners:
                lon_wgs, lat_wgs = transformer.transform(lon, lat)
                lons.append(lon_wgs)
                lats.append(lat_wgs)
            
            return {
                "bounds": [[min(lats), min(lons)], [max(lats), max(lons)]],
                "crs_origin": crs.to_epsg(),
                "crs_target": 4326,
                "width": src.width,
                "height": src.height,
            }
    
    except Exception as e:
        return {"error": str(e)}


def get_raster_data_samples(filepath: str, max_samples: int = 1000) -> dict:
    """
    Extrai amostras de dados do raster para visualização e análise.
    
    Retorna:
    - Distribuição de valores
    - Amostra de pontos com coordenadas e valores
    - Estatísticas
    
    Args:
        filepath: Caminho para o ficheiro .tif
        max_samples: Número máximo de amostras a extrair
    
    Returns:
        dict com amostras de dados
    """
    filepath = str(filepath)
    if not Path(filepath).exists():
        return {"error": f"Ficheiro não encontrado: {filepath}"}
    
    try:
        with rasterio.open(filepath) as src:
            data = src.read(1)
            nodata = src.nodata
            crs = src.crs
            transform = src.transform
            
            # Máscara de valores válidos
            if nodata is not None:
                valid_mask = data != nodata
            else:
                valid_mask = np.ones(data.shape, dtype=bool)
            
            valid_data = data[valid_mask]
            
            # Estatísticas básicas
            stats = {
                "min": float(np.min(valid_data)),
                "max": float(np.max(valid_data)),
                "mean": float(np.mean(valid_data)),
                "median": float(np.median(valid_data)),
                "std": float(np.std(valid_data)),
                "count": int(np.sum(valid_mask)),
            }
            
            # Amostrar pontos aleatoriamente
            valid_coords = np.argwhere(valid_mask)
            if len(valid_coords) > max_samples:
                indices = np.random.choice(len(valid_coords), max_samples, replace=False)
                sample_coords = valid_coords[indices]
            else:
                sample_coords = valid_coords
            
            # Converter para lat/lon
            samples = []
            for row, col in sample_coords:
                lon, lat = src.xy(row, col)
                val = data[row, col]
                
                # Converter para EPSG:4326 se necessário
                if crs and crs.to_epsg() != 4326:
                    transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
                    lon, lat = transformer.transform(lon, lat)
                
                samples.append({
                    "lat": float(lat),
                    "lon": float(lon),
                    "value": float(val),
                })
            
            # Histograma de valores
            hist, bin_edges = np.histogram(valid_data, bins=20)
            
            return {
                "statistics": stats,
                "histogram": {
                    "counts": hist.tolist(),
                    "bins": bin_edges.tolist(),
                },
                "samples": samples,
                "total_samples": len(samples),
                "crs": crs.to_epsg() if crs else None,
            }
    
    except Exception as e:
        return {"error": str(e)}


def get_raster_legend(filepath: str) -> dict:
    """
    Gera legenda e informações de coloração para o raster.
    
    Returns:
        dict com paleta de cores e intervalos para Leaflet
    """
    filepath = str(filepath)
    if not Path(filepath).exists():
        return {"error": f"Ficheiro não encontrado: {filepath}"}
    
    try:
        with rasterio.open(filepath) as src:
            data = src.read(1)
            nodata = src.nodata
            
            # Máscara de valores válidos
            if nodata is not None:
                valid_mask = data != nodata
            else:
                valid_mask = np.ones(data.shape, dtype=bool)
            
            valid_data = data[valid_mask]
            
            # Detectar se é binário
            unique_vals = np.unique(valid_data)
            is_binary = set(unique_vals.tolist()).issubset({0, 1})
            
            min_val = float(np.min(valid_data))
            max_val = float(np.max(valid_data))
            
            if is_binary:
                # Legenda simples para binário
                return {
                    "type": "binary",
                    "classes": [
                        {"value": 0, "label": "Não adequado", "color": "#ffffff"},
                        {"value": 1, "label": "Adequado", "color": "#2d6a4f"},
                    ],
                }
            else:
                # Legenda contínua com gradiente
                # Usar cores do verde claro (0) ao verde escuro (1)
                colors = [
                    "#f7fcfd",  # branco-azulado (0.0)
                    "#e5f5f9",
                    "#ccecf0",
                    "#99d8c9",
                    "#66c2a5",
                    "#41ae76",
                    "#238b45",
                    "#2d6a4f",  # verde escuro (1.0)
                ]
                
                n_classes = len(colors)
                breaks = np.linspace(min_val, max_val, n_classes + 1)
                
                classes = []
                for i, color in enumerate(colors):
                    classes.append({
                        "min": float(breaks[i]),
                        "max": float(breaks[i + 1]),
                        "color": color,
                        "label": f"{breaks[i]:.2f} - {breaks[i+1]:.2f}",
                    })
                
                return {
                    "type": "continuous",
                    "min": min_val,
                    "max": max_val,
                    "classes": classes,
                }
    
    except Exception as e:
        return {"error": str(e)}
