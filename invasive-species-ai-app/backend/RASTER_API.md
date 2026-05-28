# AlienSmart Raster API - Documentação

## Visão Geral

A API agora suporta análise de dados raster de distribuição de espécies invasoras. Os dados raster são ficheiros GeoTIFF que codificam:

- **Adequabilidade ambiental** para cada espécie
- **Períodos temporais**: histórico (1981-2024), 2041-2070, 2071-2100
- **Cenários climáticos**: SSP126, SSP370, SSP585
- **Resolução**: ~1km com igual-área (EPSG:6933)

---

## Organização de Dados

### Nomenclatura de Ficheiros

```
[especie]_[gbif_id]_[periodo]_[cenario]_[bin]_eur.tif
```

**Exemplos:**

- `Ailanthus_altissima_3190653_hist_eur.tif` → Histórico, contínuo
- `Ailanthus_altissima_3190653_2041_2070_ssp370_bin_eur.tif` → Futuro, binário

**Componentes:**

- `especie`: Nome científico (ex: Ailanthus_altissima)
- `gbif_id`: ID único no GBIF
- `periodo`: "hist" ou "2041-2070" ou "2071-2100"
- `cenario`: "ssp126", "ssp370", "ssp585" (só para períodos futuros)
- `bin`: Presente se binário (0/1), ausente se contínuo (0.0-1.0)

---

## Endpoints de Raster

### 🔍 Camada 1: Inventário e Descoberta

#### GET `/raster/species`

Lista de espécies com dados raster disponíveis.

```bash
curl http://localhost:8000/raster/species
```

**Resposta:**

```json
{
  "species": [
    "Acacia_dealbata",
    "Ailanthus_altissima",
    "Ambrosia_artemisiifolia",
    ...
  ]
}
```

#### GET `/raster/files`

Ficheiros raster com filtros avançados.

```bash
curl "http://localhost:8000/raster/files?species=Ailanthus_altissima&period=hist"
```

**Parâmetros:**

- `species`: Nome científico (opcional)
- `period`: "hist" | "2041-2070" | "2071-2100" (opcional)
- `scenario`: "ssp126" | "ssp370" | "ssp585" (opcional)
- `binary`: true/false (opcional)

**Resposta:**

```json
{
  "count": 2,
  "files": [
    "Ailanthus_altissima_3190653_hist_eur.tif",
    "Ailanthus_altissima_3190653_hist_bin_eur.tif"
  ]
}
```

#### GET `/raster/parse/{filename}`

Extrai metadados de um nome de ficheiro.

```bash
curl http://localhost:8000/raster/parse/Ailanthus_altissima_3190653_2041_2070_ssp370_bin_eur.tif
```

**Resposta:**

```json
{
  "filename": "Ailanthus_altissima_3190653_2041_2070_ssp370_bin_eur.tif",
  "species": "Ailanthus_altissima",
  "gbif_id": 3190653,
  "period": "2041-2070",
  "scenario": "ssp370",
  "is_binary": true
}
```

---

### 📊 Camada 2: Síntese Estatística

#### GET `/raster/suitable-area/{species}`

Calcula a área adequada (em km²) para uma espécie.

```bash
curl "http://localhost:8000/raster/suitable-area/Ailanthus_altissima?period=hist"
```

**Parâmetros:**

- `species`: Nome científico (obrigatório, no path)
- `period`: Período (default: "hist")
- `scenario`: Cenário (opcional, para períodos futuros)
- `threshold`: Limiar para rasters contínuos (default: 0.5)

**Resposta:**

```json
{
  "species": "Ailanthus_altissima",
  "period": "hist",
  "scenario": null,
  "threshold": 0.5,
  "data": {
    "suitable_area_km2": 142380.5,
    "total_valid_area_km2": 9843200.0,
    "suitable_pct": 1.45,
    "pixel_res_m": 1000.0,
    "crs": 6933,
    "is_binary": true,
    "n_suitable_pixels": 142381
  }
}
```

#### GET `/raster/stats/{species}`

Estatísticas básicas (min, max, média, std).

```bash
curl "http://localhost:8000/raster/stats/Ailanthus_altissima?period=hist"
```

#### GET `/raster/compare-periods/{species}`

Compara mudanças de adequabilidade entre períodos.

```bash
curl "http://localhost:8000/raster/compare-periods/Ailanthus_altissima?scenario=ssp370"
```

**Resposta:**

```json
{
  "species": "Ailanthus_altissima",
  "scenario": "ssp370",
  "by_period": {
    "hist": {
      "suitable_area_km2": 142380.5,
      "suitable_pct": 1.45
    },
    "2041-2070": {
      "suitable_area_km2": 185420.3,
      "suitable_pct": 1.88
    },
    "2071-2100": {
      "suitable_area_km2": 220150.1,
      "suitable_pct": 2.24
    }
  },
  "changes": {
    "hist_to_2041_2070_km2": 43039.8,
    "hist_to_2071_2100_km2": 77769.6,
    "hist_to_2041_2070_pct": 30.22,
    "hist_to_2071_2100_pct": 54.63
  }
}
```

---

### 🎯 Camada 3: Sobreposição Espacial

#### GET `/raster/overlap`

Calcula sobreposição entre dois rasters de espécies.

```bash
curl "http://localhost:8000/raster/overlap?species1=Ailanthus_altissima&species2=Ambrosia_artemisiifolia&operation=intersection"
```

**Parâmetros:**

- `species1`: Primeira espécie
- `species2`: Segunda espécie
- `period`: Período (default: "hist")
- `operation`: "intersection" | "union" | "difference" (default: "intersection")

**Resposta:**

```json
{
  "species1": "Ailanthus_altissima",
  "species2": "Ambrosia_artemisiifolia",
  "operation": "intersection",
  "period": "hist",
  "overlap_km2": 25430.15,
  "overlap_pixels": 25430
}
```

---

### 🌍 Camada 4: Comparação de Cenários

#### GET `/raster/scenarios/{species}`

Matriz período × cenário com análise de tendências.

```bash
curl http://localhost:8000/raster/scenarios/Ailanthus_altissima
```

**Resposta:**

```json
{
  "species": "Ailanthus_altissima",
  "matrix": {
    "hist": {
      "hist": 142380.5
    },
    "2041-2070": {
      "ssp126": 165200.2,
      "ssp370": 185420.3,
      "ssp585": 210150.8
    },
    "2071-2100": {
      "ssp126": 180350.5,
      "ssp370": 220150.1,
      "ssp585": 260480.3
    }
  },
  "summary": {
    "trend": "expansão",
    "min_area_km2": 142380.5,
    "max_area_km2": 260480.3,
    "avg_area_km2": 203826.32
  }
}
```

---

### 🔬 Endpoint Especial: Resumo Completo

#### GET `/raster/summary/{species}`

Análise integrada completa com todas as camadas.

```bash
curl http://localhost:8000/raster/summary/Ailanthus_altissima
```

**Combina:**

- Disponibilidade de períodos
- Análise de cenários (Camada 4)
- Comparação temporal (Camada 2)

---

## Exemplos de Uso

### Python

```python
import requests

BASE_URL = "http://localhost:8000"

# 1. Listar espécies
species_list = requests.get(f"{BASE_URL}/raster/species").json()
print(f"Espécies disponíveis: {len(species_list['species'])}")

# 2. Área adequada
area = requests.get(
    f"{BASE_URL}/raster/suitable-area/Ailanthus_altissima",
    params={"period": "hist"}
).json()
print(f"Área adequada: {area['data']['suitable_area_km2']} km²")

# 3. Comparação de cenários
scenarios = requests.get(
    f"{BASE_URL}/raster/scenarios/Ailanthus_altissima"
).json()
print(f"Tendência: {scenarios['summary']['trend']}")
```

### JavaScript/Frontend

```javascript
const BASE_URL = "http://localhost:8000";

// Listar espécies
fetch(`${BASE_URL}/raster/species`)
  .then((r) => r.json())
  .then((data) => console.log(`Espécies: ${data.species.length}`));

// Análise completa
fetch(`${BASE_URL}/raster/summary/Ailanthus_altissima`)
  .then((r) => r.json())
  .then((data) => {
    console.log(`Tendência: ${data.scenario_analysis.summary.trend}`);
  });
```

---

## Estrutura de Resposta Padrão

Todas as respostas de erro retornam:

```json
{
  "detail": "Descrição do erro"
}
```

Com status HTTP apropriado (400, 404, 500).

---

## Performance e Limitações

- **Tempo de leitura**: ~100-500ms por raster (1°-2° banda)
- **Máximo de ficheiros**: ~1000 (120 espécies × ~8 ficheiros)
- **Resolução**: ~1km (pixel = ~1000m)
- **CRS padrão**: EPSG:6933 (equal-area)

---

## Próximos Passos

1. **Visualização de mapas** no frontend com Leaflet
2. **Cache de resultados** para queries frequentes
3. **Exportação de rasters** em formatos adicionais
4. **Análise de agentes** com raciocínio sobre cenários climáticos
