"""
qgis_render.py — Script standalone para renderização de mapas com PyQGIS.
Chamado como subprocesso pelo FastAPI com o ambiente QGIS 3.44.11.

Uso:
    python qgis_render.py <tif_path> <output_png> <colormap_name> <species> <period> [scenario]
        [--label-suitable=TEXTO] [--label-unsuitable=TEXTO]
"""
import sys
import os

# Garantir que os paths PyQGIS estão no sys.path
QGIS_ROOT = r"C:\Program Files\QGIS 3.44.11"
sys.path.insert(0, os.path.join(QGIS_ROOT, "apps", "qgis-ltr", "python"))

from qgis.core import (
    QgsApplication,
    QgsRasterLayer,
    QgsMapSettings,
    QgsPalettedRasterRenderer,
    QgsMapRendererCustomPainterJob,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
)
from qgis.PyQt.QtGui import QColor, QImage, QPainter, QFont, QPen, QBrush
from qgis.PyQt.QtCore import QSize, QRect, Qt

COLORMAP_STOPS = {
    "Greens5": ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
    "YlOrRd":  ["#ffffcc", "#fed976", "#fd8d3c", "#e31a1c", "#800026"],
    "Blues":   ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"],
    "RdPu":    ["#feebe2", "#fbb4b9", "#f768a1", "#ae017e", "#49006a"],
    "BurntYellow": ["#fff7d4", "#ffe27a", "#e8b339", "#c9892a", "#9c5e0a"],
}


def hex_to_qcolor(hex_color: str, alpha: int = 200) -> QColor:
    c = QColor(hex_color)
    c.setAlpha(alpha)
    return c


def build_map(tif_path: str, output_png: str, colormap_name: str,
              species: str, period: str, scenario: str | None,
              label_suitable: str = "Adequado", label_unsuitable: str = "Não adequado") -> None:

    QgsApplication.setPrefixPath(os.path.join(QGIS_ROOT, "apps", "qgis-ltr"), True)
    qgs = QgsApplication([], False)
    qgs.initQgis()

    # ── Basemap OSM via XYZ tiles ────────────────────────────────────────────
    osm_uri = (
        "type=xyz"
        "&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        "&zmax=12&zmin=0&crs=EPSG:3857"
    )
    basemap = QgsRasterLayer(osm_uri, "OSM", "wms")
    if not basemap.isValid():
        print("[qgis_render] AVISO: basemap OSM inválido, a continuar sem ele", file=sys.stderr)

    # ── Camada raster SDM ────────────────────────────────────────────────────
    raster_layer = QgsRasterLayer(tif_path, species)
    if not raster_layer.isValid():
        print(f"[qgis_render] ERRO: raster inválido: {tif_path}", file=sys.stderr)
        qgs.exitQgis()
        sys.exit(1)

    # QgsPalettedRasterRenderer — ideal para rasters binários (0/1)
    stops = COLORMAP_STOPS.get(colormap_name, COLORMAP_STOPS["Greens5"])
    LEGEND_ALPHA = 175  # semi-transparente, deixa o basemap ler por baixo
    suitable_color = hex_to_qcolor(stops[-1], alpha=LEGEND_ALPHA)

    classes = [
        QgsPalettedRasterRenderer.Class(0, QColor(0, 0, 0, 0), label_unsuitable),
        QgsPalettedRasterRenderer.Class(1, suitable_color,      label_suitable),
    ]
    renderer = QgsPalettedRasterRenderer(raster_layer.dataProvider(), 1, classes)
    raster_layer.setRenderer(renderer)

    # Basemap mais nítido (antes ficava demasiado lavado/branco)
    if basemap.isValid():
        basemap.renderer().setOpacity(0.9)

    # ── Reprojectar extensão do raster para EPSG:3857 ───────────────────────
    crs_src = raster_layer.crs()
    crs_dst = QgsCoordinateReferenceSystem("EPSG:3857")
    transform = QgsCoordinateTransform(crs_src, crs_dst, QgsProject.instance())
    extent_3857 = transform.transformBoundingBox(raster_layer.extent())

    # ── Dimensões baseadas no aspeto real da extensão geográfica ────────────
    geo_w = extent_3857.width()
    geo_h = extent_3857.height()
    TARGET_LONG = 900  # lado maior em píxeis
    if geo_w >= geo_h:
        W = TARGET_LONG
        H = max(1, round(TARGET_LONG * geo_h / geo_w))
    else:
        H = TARGET_LONG
        W = max(1, round(TARGET_LONG * geo_w / geo_h))

    # Em QgsMapSettings, a primeira layer da lista é desenhada por CIMA
    layers = [raster_layer]
    if basemap.isValid():
        layers.append(basemap)

    settings = QgsMapSettings()
    settings.setLayers(layers)
    settings.setOutputSize(QSize(W, H))
    settings.setExtent(extent_3857)
    settings.setDestinationCrs(crs_dst)
    settings.setBackgroundColor(QColor(240, 240, 240))
    settings.setOutputDpi(150)

    map_img = QImage(QSize(W, H), QImage.Format.Format_ARGB32_Premultiplied)
    map_img.fill(QColor(240, 240, 240))
    painter = QPainter(map_img)
    job = QgsMapRendererCustomPainterJob(settings, painter)
    job.start()
    job.waitForFinished()
    painter.end()

    # ── Legenda num painel próprio à direita do mapa ─────────────────────────
    PANEL_W = max(160, W // 4)
    final_img = QImage(QSize(W + PANEL_W, H), QImage.Format.Format_ARGB32_Premultiplied)
    final_img.fill(QColor(255, 255, 255))

    canvas = QPainter(final_img)
    canvas.setRenderHint(QPainter.Antialiasing)
    canvas.drawImage(0, 0, map_img)

    # Separador entre mapa e legenda
    canvas.setPen(QPen(QColor(200, 200, 200), 1))
    canvas.drawLine(W, 0, W, H)

    # Conteúdo da legenda
    pad   = 16
    sw    = 22
    sh    = 16
    font_title = QFont("Arial", max(9, PANEL_W // 14), QFont.Bold)
    font_label = QFont("Arial", max(8, PANEL_W // 16))

    lx = W + pad
    ly = pad + 10

    canvas.setFont(font_title)
    canvas.setPen(QPen(QColor(30, 30, 30)))
    canvas.drawText(lx, ly, "Legenda")
    ly += 16

    canvas.setPen(QPen(QColor(180, 180, 180)))
    canvas.drawLine(lx, ly, W + PANEL_W - pad, ly)
    ly += 24

    canvas.setFont(font_label)
    fm = canvas.fontMetrics()

    entries = [
        (suitable_color, label_suitable),
        (QColor(0, 0, 0, 0), label_unsuitable),
    ]
    for color, label in entries:
        canvas.setBrush(QBrush(color) if color.alpha() > 0 else QBrush(Qt.NoBrush))
        canvas.setPen(QPen(QColor(80, 80, 80), 1))
        canvas.drawRect(QRect(lx, ly, sw, sh))

        canvas.setPen(QPen(QColor(30, 30, 30)))
        text_y = ly + sh - (sh - fm.ascent()) // 2 - fm.descent() // 2
        canvas.drawText(lx + sw + 8, text_y, label)
        ly += sh + 16

    canvas.end()

    final_img.save(output_png)
    print(f"[qgis_render] PNG guardado: {output_png} ({final_img.width()}x{final_img.height()})")

    qgs.exitQgis()


if __name__ == "__main__":
    flags = {}
    positional = []
    for arg in sys.argv[1:]:
        if arg.startswith("--label-suitable="):
            flags["label_suitable"] = arg.split("=", 1)[1]
        elif arg.startswith("--label-unsuitable="):
            flags["label_unsuitable"] = arg.split("=", 1)[1]
        else:
            positional.append(arg)

    if len(positional) < 5:
        print("Uso: qgis_render.py <tif> <output_png> <colormap> <species> <period> [scenario] "
              "[--label-suitable=TEXTO] [--label-unsuitable=TEXTO]")
        sys.exit(1)

    tif      = positional[0]
    out_png  = positional[1]
    cmap     = positional[2]
    sp       = positional[3]
    period   = positional[4]
    scenario = positional[5] if len(positional) > 5 else None

    build_map(tif, out_png, cmap, sp, period, scenario, **flags)
