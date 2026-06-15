"""
qgis_render.py — Script standalone para renderização de mapas com PyQGIS.
Chamado como subprocesso pelo FastAPI com o ambiente QGIS 3.44.11.

Uso:
    python qgis_render.py <tif_path> <output_png> <colormap_name> <species> <period> [scenario]
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
from qgis.PyQt.QtGui import QColor, QImage, QPainter
from qgis.PyQt.QtCore import QSize

COLORMAP_STOPS = {
    "Greens5": ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
    "YlOrRd":  ["#ffffcc", "#fed976", "#fd8d3c", "#e31a1c", "#800026"],
    "Blues":   ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"],
    "RdPu":    ["#feebe2", "#fbb4b9", "#f768a1", "#ae017e", "#49006a"],
}


def hex_to_qcolor(hex_color: str, alpha: int = 200) -> QColor:
    c = QColor(hex_color)
    c.setAlpha(alpha)
    return c


def build_map(tif_path: str, output_png: str, colormap_name: str,
              species: str, period: str, scenario: str | None) -> None:

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
    suitable_color = hex_to_qcolor(stops[-1], alpha=190)

    classes = [
        QgsPalettedRasterRenderer.Class(0, QColor(0, 0, 0, 0), "Não adequado"),
        QgsPalettedRasterRenderer.Class(1, suitable_color,      "Adequado"),
    ]
    renderer = QgsPalettedRasterRenderer(raster_layer.dataProvider(), 1, classes)
    raster_layer.setRenderer(renderer)

    # ── Reprojectar extensão do raster para EPSG:3857 ───────────────────────
    crs_src = raster_layer.crs()
    crs_dst = QgsCoordinateReferenceSystem("EPSG:3857")
    transform = QgsCoordinateTransform(crs_src, crs_dst, QgsProject.instance())
    extent_3857 = transform.transformBoundingBox(raster_layer.extent())

    # ── Renderização ─────────────────────────────────────────────────────────
    W, H = 900, 1000
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

    img = QImage(QSize(W, H), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(240, 240, 240))
    painter = QPainter(img)
    job = QgsMapRendererCustomPainterJob(settings, painter)
    job.start()
    job.waitForFinished()
    painter.end()

    img.save(output_png)
    print(f"[qgis_render] PNG guardado: {output_png} ({img.width()}x{img.height()})")

    qgs.exitQgis()


if __name__ == "__main__":
    if len(sys.argv) < 6:
        print("Uso: qgis_render.py <tif> <output_png> <colormap> <species> <period> [scenario]")
        sys.exit(1)

    tif     = sys.argv[1]
    out_png = sys.argv[2]
    cmap    = sys.argv[3]
    sp      = sys.argv[4]
    period  = sys.argv[5]
    scenario = sys.argv[6] if len(sys.argv) > 6 else None

    build_map(tif, out_png, cmap, sp, period, scenario)
