"""QGIS's own icons for layer types and setting types, so the flowchart shows at a
glance whether an input or output is a point, line, polygon, raster, number, text…"""

from __future__ import annotations

import os

from qgis.core import QgsApplication, QgsProcessingParameterNumber

LAYER_ICONS = {
    "point": "mIconPointLayer.svg",
    "line": "mIconLineLayer.svg",
    "polygon": "mIconPolygonLayer.svg",
    "raster": "mIconRasterLayer.svg",
    "table": "mIconTableLayer.svg",
    "mesh": "mIconMeshLayer.svg",
    "layer": "mIconVector.svg",
}

LAYER_WORDS = {
    "point": "point layer", "line": "line layer", "polygon": "polygon layer", "raster": "raster layer",
    "table": "table (no geometry)", "mesh": "mesh layer", "layer": "layer",
}

SETTING_ICONS = {
    "distance": ("mActionMeasure.svg", "distance"),
    "area": ("mIconFieldFloat.svg", "area"),
    "volume": ("mIconFieldFloat.svg", "volume"),
    "duration": ("mIconFieldDateTime.svg", "duration"),
    "scale": ("mIconFieldFloat.svg", "scale"),
    "string": ("mIconFieldText.svg", "text"),
    "expression": ("mIconExpression.svg", "expression"),
    "boolean": ("mIconFieldBool.svg", "yes/no"),
    "enum": ("mIconListView.svg", "choice from a list"),
    "crs": ("mIconProjectionEnabled.svg", "coordinate reference system"),
    "extent": ("mActionZoomToLayer.svg", "extent"),
    "point": ("mIconFieldGeometry.svg", "point"),
    "geometry": ("mIconFieldGeometry.svg", "geometry"),
    "field": ("mSourceFields.svg", "field name"),
    "color": ("mIconColorBox.svg", "colour"),
    "datetime": ("mIconFieldDateTime.svg", "date/time"),
    "file": ("mIconFile.svg", "file"),
    "folder": ("mIconFolder.svg", "folder"),
}


def layer_icon(kind: str) -> tuple[str, str]:
    """(icon file, words) for a layer kind."""
    kind = kind if kind in LAYER_ICONS else "layer"
    return LAYER_ICONS[kind], LAYER_WORDS[kind]


def setting_icon(definition) -> tuple[str, str]:
    """(icon file, words) for a setting's parameter definition."""
    if definition is None:   # a column a Field Calculator expression reads
        return "mSourceFields.svg", "column"
    kind = definition.type()
    if kind == "number":
        if definition.dataType() == QgsProcessingParameterNumber.Type.Integer:
            return "mIconFieldInteger.svg", "whole number"
        return "mIconFieldFloat.svg", "decimal number"
    return SETTING_ICONS.get(kind, ("mIconFieldText.svg", "value"))


def image(name: str, size: int = 14):
    """The icon as a QImage, for text documents and pixmap items."""
    return QgsApplication.getThemeIcon("/" + name).pixmap(size, size).toImage()


def plugin_icon(recording: bool = False):
    """Sous Chef's icon (recipe book, chef's hat, record dot), drawn for each size it's shown at."""
    from qgis.PyQt.QtCore import QSize
    from qgis.PyQt.QtGui import QIcon
    folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons")
    tag = "_recording" if recording else ""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128):
        icon.addFile(os.path.join(folder, f"sous_chef{tag}_{size}.png"), QSize(size, size))
    return icon


RECIPE_SVG = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons", "recipe.svg")


def recipe_icon():
    """The default icon of a saved recipe (one without a symbol of its own): a recipe card
    with a chef's hat and a gear."""
    from qgis.PyQt.QtCore import QSize
    from qgis.PyQt.QtGui import QIcon
    folder = os.path.dirname(RECIPE_SVG)
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128):
        icon.addFile(os.path.join(folder, f"recipe_{size}.png"), QSize(size, size))
    return icon
