"""
report_tables.py — Tabelas de resultados SDM calculadas de forma DETERMINÍSTICA.

Em vez de deixar o LLM inventar números, estas tabelas são construídas em Python
a partir dos rasters SDM (área adequada em km²), garantindo valores sempre
corretos. O LLM apenas escreve o texto à volta (discussão, recomendações…).

Estrutura (acordada com o utilizador):

  • 1 espécie → tabela "Área Adequada (km²)" com:
        - linhas  = períodos (Histórico, 2041-2070, 2071-2100)
        - colunas = cenários (SSP1-2.6, SSP3-7.0, SSP5-8.5)
        - Histórico só tem 1 valor → as outras colunas levam "—"
    Seguida de uma análise crítica da tabela.

  • 2+ espécies → além da tabela por espécie, uma tabela comparativa sob o
    cenário escolhido na 1ª espécie:
        - linhas  = espécies
        - colunas = Histórico, 2041-2070, 2071-2100 (km²), Variação %,
                    Tendência, Média dos 3 cenários (período final)
    Seguida de uma análise crítica.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from raster_tools import get_raster_files, compute_suitable_area

logger = logging.getLogger(__name__)

SCENARIOS = ["ssp126", "ssp370", "ssp585"]
FUTURE_PERIODS = ["2041-2070", "2071-2100"]
ALL_PERIODS = ["hist", "2041-2070", "2071-2100"]

SCENARIO_LABELS = {
    "ssp126": "SSP1-2.6 (otimista)",
    "ssp370": "SSP3-7.0 (intermédio)",
    "ssp585": "SSP5-8.5 (pessimista)",
}
SCENARIO_SHORT = {"ssp126": "SSP1-2.6", "ssp370": "SSP3-7.0", "ssp585": "SSP5-8.5"}
PERIOD_LABELS = {"hist": "Histórico", "2041-2070": "2041-2070", "2071-2100": "2071-2100"}


@lru_cache(maxsize=512)
def _area(species: str, period: str, scenario: str | None) -> float | None:
    """Área adequada (km²) de uma espécie num período/cenário, ou None se não houver raster."""
    sc = None if period == "hist" else scenario
    files = get_raster_files(species=species, period=period, scenario=sc, binary=True)
    if not files:
        return None
    try:
        return compute_suitable_area(files[0]).get("suitable_area_km2")
    except Exception as exc:
        logger.warning("[report_tables] área falhou %s/%s/%s: %s", species, period, scenario, exc)
        return None


def _fmt(value: float | None) -> str:
    """Inteiro com espaço como separador de milhares; '—' quando não há valor."""
    if value is None:
        return "—"
    return f"{int(round(value)):,}".replace(",", " ")


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    sinal = "+" if value > 0 else ""
    return f"{sinal}{value:.0f}%"


def _trend_label(pct: float | None) -> str:
    """Etiqueta qualitativa da tendência a partir da variação % histórico→final."""
    if pct is None:
        return "—"
    if pct <= -50:
        return "forte declínio"
    if pct <= -15:
        return "declínio"
    if pct < 15:
        return "estável"
    if pct < 50:
        return "expansão"
    return "forte expansão"


def _variation_pct(hist: float | None, final: float | None) -> float | None:
    if not hist or final is None:
        return None
    return (final - hist) / hist * 100


# ── Tabela de 1 espécie (período × cenário) ──────────────────────────────────

def build_single_species_section(species: str) -> str:
    """Secção markdown: cabeçalho + tabela período×cenário + análise crítica."""
    hist = _area(species, "hist", None)
    grid = {p: {s: _area(species, p, s) for s in SCENARIOS} for p in FUTURE_PERIODS}

    if hist is None and all(v is None for p in FUTURE_PERIODS for v in grid[p].values()):
        return ""  # sem dados para esta espécie

    linhas = [
        f"### {species} — Área adequada (km²)",
        "",
        # Marcador: o pdf_builder insere aqui o(s) mapa(s) desta espécie,
        # garantindo a ordem Mapa → Tabela → Análise dentro do bloco.
        f"[[MAPA:{species}]]",
        "",
        "| Período | "
        + " | ".join(SCENARIO_LABELS[s] for s in SCENARIOS)
        + " |",
        "| --- | " + " | ".join(["---"] * len(SCENARIOS)) + " |",
        # Histórico: 1 valor na 1ª coluna, "—" nas restantes
        f"| {PERIOD_LABELS['hist']} | {_fmt(hist)} | "
        + " | ".join("—" for _ in SCENARIOS[1:])
        + " |",
    ]
    for p in FUTURE_PERIODS:
        linhas.append(
            f"| {PERIOD_LABELS[p]} | "
            + " | ".join(_fmt(grid[p][s]) for s in SCENARIOS)
            + " |"
        )

    linhas += ["", _single_species_analysis(species, hist, grid)]
    return "\n".join(linhas)


def _single_species_analysis(species: str, hist: float | None, grid: dict) -> str:
    final = grid["2071-2100"]
    valid_final = {s: v for s, v in final.items() if v is not None}

    partes = [f"**Análise crítica:** "]
    if hist is not None:
        partes.append(
            f"No período histórico, *{species}* apresenta uma área climaticamente "
            f"adequada de {_fmt(hist)} km². "
        )

    inter = final.get("ssp370")
    var_inter = _variation_pct(hist, inter)
    if inter is not None and var_inter is not None:
        partes.append(
            f"Sob o cenário intermédio (SSP3-7.0), projeta-se {_fmt(inter)} km² em "
            f"2071-2100 ({_fmt_pct(var_inter)} face ao histórico). "
        )

    if valid_final:
        best_s = max(valid_final, key=valid_final.get)
        worst_s = min(valid_final, key=valid_final.get)
        if best_s != worst_s:
            partes.append(
                f"No fim do século, o cenário {SCENARIO_SHORT[best_s]} é o mais favorável "
                f"({_fmt(valid_final[best_s])} km²) e o {SCENARIO_SHORT[worst_s]} o mais "
                f"desfavorável ({_fmt(valid_final[worst_s])} km²). "
            )

    trend = _trend_label(var_inter)
    if trend != "—":
        partes.append(f"A tendência dominante é de **{trend}** da área adequada.")
    return "".join(partes).strip()


# ── Tabela comparativa (2+ espécies, cenário fixo) ───────────────────────────

def build_multi_species_section(species_list: list[str], scenario: str = "ssp370") -> str:
    """Tabela comparativa de várias espécies sob um cenário fixo + análise crítica."""
    if len(species_list) < 2:
        return ""
    scenario = scenario if scenario in SCENARIOS else "ssp370"

    rows = []
    for sp in species_list:
        hist = _area(sp, "hist", None)
        a2041 = _area(sp, "2041-2070", scenario)
        a2071 = _area(sp, "2071-2100", scenario)
        var = _variation_pct(hist, a2071)
        rows.append((sp, hist, a2041, a2071, var))

    linhas = [
        f"### Comparação entre espécies — Cenário {SCENARIO_LABELS[scenario]}",
        "",
        "| Espécie | Histórico (km²) | 2041-2070 (km²) | 2071-2100 (km²) "
        "| Variação | Tendência |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for sp, hist, a2041, a2071, var in rows:
        linhas.append(
            f"| *{sp}* | {_fmt(hist)} | {_fmt(a2041)} | {_fmt(a2071)} "
            f"| {_fmt_pct(var)} | {_trend_label(var)} |"
        )

    linhas += ["", _multi_species_analysis(rows, scenario)]
    return "\n".join(linhas)


def _multi_species_analysis(rows: list[tuple], scenario: str) -> str:
    com_var = [(sp, var) for sp, _, _, _, var in rows if var is not None]
    partes = ["**Análise crítica:** "]
    partes.append(
        f"A tabela compara as espécies selecionadas sob o cenário "
        f"{SCENARIO_SHORT[scenario]}. "
    )
    if com_var:
        mais_perde = min(com_var, key=lambda x: x[1])
        mais_ganha = max(com_var, key=lambda x: x[1])
        partes.append(
            f"*{mais_perde[0]}* regista a maior contração projetada "
            f"({_fmt_pct(mais_perde[1])} até 2071-2100), "
        )
        if mais_ganha[0] != mais_perde[0]:
            partes.append(
                f"enquanto *{mais_ganha[0]}* é a menos afetada "
                f"({_fmt_pct(mais_ganha[1])}). "
            )
        em_declinio = sum(1 for _, v in com_var if v < -15)
        partes.append(
            f"No total, {em_declinio} de {len(com_var)} espécies apresentam declínio "
            f"da área adequada neste cenário."
        )
    return "".join(partes).strip()


# ── Secção completa de Resultados ────────────────────────────────────────────

def build_results_section(species_configs: list) -> str:
    """
    Secção determinística de Resultados:
      - uma tabela por espécie (todos os cenários) + análise crítica;
      - se 2+ espécies, uma tabela comparativa sob o cenário da 1ª espécie.
    Devolve "" se não houver espécies.
    """
    species_list = [c["species"] for c in (species_configs or []) if c.get("species")]
    if not species_list:
        return ""

    # Sem título "Resultados" próprio: a secção é inserida no lugar do marcador
    # [[TABELAS_SDM]], que já vem sob o cabeçalho **Resultados** do relatório.
    blocos = []
    for sp in species_list:
        sec = build_single_species_section(sp)
        if sec:
            blocos += [sec, ""]

    if len(species_list) >= 2:
        scenario = species_configs[0].get("scenario") or "ssp370"
        comp = build_multi_species_section(species_list, scenario)
        if comp:
            blocos += [comp, ""]

    return "\n".join(blocos).strip()
