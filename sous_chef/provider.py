"""The Recipe Book in the Processing Toolbox: the recipes (Processing models) made with Sous Chef,
each with its own symbol when one was chosen (an .svg next to the .model3 file)."""

from __future__ import annotations

import glob
import os

from qgis.core import QgsApplication, QgsProcessingModelAlgorithm, QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

HERE = os.path.dirname(__file__)


def toolbox_folder() -> str:
    folder = os.path.join(QgsApplication.qgisSettingsDirPath(), "sous_chef", "models")
    os.makedirs(folder, exist_ok=True)
    return folder


def icon_file(model_path: str) -> str:
    return os.path.splitext(model_path)[0] + ".svg"


PROVIDER_NAME = "Recipe Book"
NO_GROUP = {"", "Sous Chef", "Recipe Book"}   # shown directly in the Recipe Book


class _Model(QgsProcessingModelAlgorithm):
    """A saved model shown with its own symbol. QGIS works with copies of it (recently used
    tools, the model's window): createInstance makes those copies with the symbol too."""

    def __init__(self, svg="", path=""):
        super().__init__()
        self._svg = svg
        self._path = path
        self.readable = bool(path) and self.fromFile(path)
        if self.readable:
            self.setSourceFilePath(path)

    def createInstance(self):
        return _Model(self._svg, self._path)

    def icon(self):
        if self._svg:
            return QIcon(self._svg)
        from .gui.icons import recipe_icon
        return recipe_icon()   # Sous Chef's recipe card, not the standard Processing icon

    def svgIconPath(self):
        from .gui.icons import RECIPE_SVG
        return self._svg or RECIPE_SVG

    # a recipe without a group of its own sits directly in the Recipe Book
    def group(self):
        group = super().group()
        return "" if group in NO_GROUP else group

    def groupId(self):
        group = super().groupId()
        return "" if group in NO_GROUP else group


class RecipeBookProvider(QgsProcessingProvider):
    def __init__(self):
        super().__init__()
        self._models = []   # keep the Python objects alive

    def id(self):
        return "souschef"

    def name(self):
        return PROVIDER_NAME

    def longName(self):
        return "Recipes (Processing models) made with Sous Chef"

    def icon(self):
        from .gui.icons import plugin_icon
        return plugin_icon()

    def loadAlgorithms(self):
        self._models = []
        for path in sorted(glob.glob(os.path.join(toolbox_folder(), "*.model3"))):
            svg = icon_file(path)
            model = _Model(svg if os.path.isfile(svg) else "", path)
            if not model.readable:
                continue
            self._models.append(model)
            self.addAlgorithm(model)
