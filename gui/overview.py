"""A small overview map: a plain outline of the world (QGIS's own world map, no project
layers) in the Equal Earth projection, with a box where the layers are. A box too small to see
becomes a marker. With several areas (separate parts), the current one is highlighted."""

from __future__ import annotations
from ..core import log

import os

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFillSymbol,
    QgsMapRendererCustomPainterJob,
    QgsMapSettings,
    QgsProject,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QPointF, QRectF, QSize, Qt
from qgis.PyQt.QtGui import QColor, QImage, QPainter, QPen

EQUAL_EARTH = "EPSG:8857"
WORLD = QgsRectangle(-17243959.06, -8392927.6, 17243959.06, 8392927.6)   # Equal Earth's extent
ACCENT = QColor("#E07A1F")
_LAYER = []
_BASE = {}


def _world():
    if not _LAYER:
        path = os.path.join(QgsApplication.pkgDataPath(), "resources", "data", "world_map.gpkg")
        layer = QgsVectorLayer(f"{path}|layername=countries", "world", "ogr")
        _LAYER.append(layer if layer.isValid() else None)
    return _LAYER[0]


def colours(t: dict) -> dict:
    """Sea, land and outline colours that suit a design's colours."""
    bg = QColor(t.get("bg", "#FFFFFF"))
    dark = bg.lightness() < 110
    sea = bg.lighter(118) if dark else bg.darker(103)
    ink = QColor(t.get("muted", "#6B7178"))
    land = QColor(int(sea.red() * 0.62 + ink.red() * 0.38), int(sea.green() * 0.62 + ink.green() * 0.38),
                  int(sea.blue() * 0.62 + ink.blue() * 0.38))   # a plain silhouette, no borders
    return {"sea": sea.name(), "land": land.name(), "line": ink.name(), "box": t.get("box", ACCENT.name())}


def _render(extent: QgsRectangle, size: QSize, c: dict) -> tuple:
    world = _world()
    settings = QgsMapSettings()
    settings.setDestinationCrs(QgsCoordinateReferenceSystem(EQUAL_EARTH))
    settings.setOutputSize(size)
    settings.setExtent(extent)
    settings.setBackgroundColor(QColor(c["sea"]))
    image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(c["sea"]))
    if world is not None:
        world.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
            {"color": c["land"], "outline_color": c["land"], "outline_width": "0.05"})))
        settings.setLayers([world])
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        job = QgsMapRendererCustomPainterJob(settings, p)
        job.renderSynchronously()
        p.end()
    return image, settings


def inset(areas, size: QSize, t: dict, zoom="world") -> QImage:
    """``areas``: [(QgsRectangle, its QgsCoordinateReferenceSystem, highlighted)]. ``zoom``:
    "world", or "region" for the part of the world around the areas."""
    c = colours(t)
    zoom = t.get("zoom", zoom)
    target = QgsCoordinateReferenceSystem(EQUAL_EARTH)
    boxes = []
    for rect, crs, on in areas:
        try:
            xf = QgsCoordinateTransform(crs, target, QgsProject.instance())
            boxes.append((xf.transformBoundingBox(rect), on))
        except Exception as error:   # safe to carry on: noted in the log
            log.ignored(error)
            continue
    extent = QgsRectangle(WORLD)
    if zoom == "region" and boxes:
        u = QgsRectangle(boxes[0][0])
        for b, _on in boxes[1:]:
            u.combineExtentWith(b)
        span = max(u.width(), u.height() * 2, 3_000_000) * 2.2
        cx, cy = u.center().x(), u.center().y()
        extent = QgsRectangle(cx - span / 2, cy - span / 4, cx + span / 2, cy + span / 4)
    key = (size.width(), size.height(), tuple(sorted(c.items())), zoom == "world" or (extent.toString(0)))
    if key not in _BASE:
        if len(_BASE) > 20:
            _BASE.clear()
        _BASE[key] = _render(extent, size, c)
    base, settings = _BASE[key]
    image = QImage(base)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    m2p = settings.mapToPixel()
    for rect, on in sorted(boxes, key=lambda b: b[1]):   # the highlighted one on top
        a = m2p.transform(rect.xMinimum(), rect.yMaximum())
        b = m2p.transform(rect.xMaximum(), rect.yMinimum())
        r = QRectF(QPointF(a.x(), a.y()), QPointF(b.x(), b.y())).normalized()
        colour = QColor(c["box"]) if on else QColor(c["line"])
        if r.width() < 6 and r.height() < 6:   # too small to see as a box: a marker
            centre = r.center()
            p.setPen(QPen(QColor(c["sea"]), 2.5))
            p.setBrush(colour)
            p.drawEllipse(centre, 4.5 if on else 3.5, 4.5 if on else 3.5)
        else:
            p.setPen(QPen(colour, 2 if on else 1.2))
            fill = QColor(colour)
            fill.setAlphaF(0.18 if on else 0.08)
            p.setBrush(fill)
            p.drawRect(r)
    p.setPen(QPen(QColor(c["line"]), 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(QRectF(0.5, 0.5, image.width() - 1, image.height() - 1))
    p.end()
    return image


def place(p: QPainter, image: QImage, area: QRectF, corner="top-right", margin=10):
    """Draws the inset in a corner of ``area``."""
    w, h = image.width(), image.height()
    x = area.left() + margin if "left" in corner else area.right() - w - margin
    y = area.top() + margin if "top" in corner else area.bottom() - h - margin
    p.drawImage(QPointF(x, y), image)
