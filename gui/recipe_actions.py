"""Right-click menu of recipes in the Recipe Book (Processing Toolbox): open in Sous Chef,
edit in the Model Designer, show in folder, delete."""

from __future__ import annotations

import os
import shutil

from qgis.core import QgsApplication, QgsProcessingAlgorithm
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox

from ..i18n import tr

try:
    from processing.gui.ContextAction import ContextAction
except ImportError:  # Processing not available: no menu
    ContextAction = object


class _RecipeAction(ContextAction):
    def __init__(self, plugin, name):
        super().__init__()
        self.plugin = plugin
        self.name = tr(name)

    def isEnabled(self):
        alg = getattr(self, "itemData", None)
        return (isinstance(alg, QgsProcessingAlgorithm) and alg.provider() is not None
                and alg.provider().id() == "souschef" and bool(self.path()))

    def path(self):
        alg = getattr(self, "itemData", None)
        try:
            return alg.sourceFilePath() if alg is not None else ""
        except AttributeError:
            return ""


class OpenRecipe(_RecipeAction):
    def __init__(self, plugin):
        super().__init__(plugin, "Open in Sous Chef…")

    def icon(self):
        from .icons import plugin_icon
        return plugin_icon()

    def execute(self):
        self.plugin.open_model(self.path())


class EditRecipe(_RecipeAction):
    def __init__(self, plugin):
        super().__init__(plugin, "Edit in Model Designer…")

    def icon(self):
        return QgsApplication.getThemeIcon("/processingModel.svg")

    def execute(self):
        self.plugin.open_in_designer(self.path())


class ShowRecipeFolder(_RecipeAction):
    def __init__(self, plugin):
        super().__init__(plugin, "Show in Folder")

    def icon(self):
        return QgsApplication.getThemeIcon("/mIconFolderOpen.svg")

    def execute(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self.path())))


class ShareRecipe(_RecipeAction):
    def __init__(self, plugin):
        super().__init__(plugin, "Share as Zip…")

    def icon(self):
        return QgsApplication.getThemeIcon("/mActionFileSave.svg")

    def execute(self):
        from ..core import share
        path, _ = QFileDialog.getSaveFileName(None, "Share recipe as one zip file",
                                              os.path.splitext(self.path())[0] + ".zip", "Zip files (*.zip)")
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        try:
            share.zip_recipe(self.path(), path)
        except Exception as e:  # report instead of crashing QGIS
            QMessageBox.warning(None, "Sous Chef", f"The zip file could not be made:\n\n{e}")


class ScriptRecipe(_RecipeAction):
    def __init__(self, plugin):
        super().__init__(plugin, "Save as Python Script…")

    def icon(self):
        return QgsApplication.getThemeIcon("/mIconPythonFile.svg")

    def execute(self):
        from ..core import share
        path, _ = QFileDialog.getSaveFileName(None, "Save recipe as Python script",
                                              os.path.splitext(self.path())[0] + ".py", "Python scripts (*.py)")
        if not path:
            return
        if not path.lower().endswith(".py"):
            path += ".py"
        try:
            share.script_from_file(self.path(), path)
        except Exception as e:  # report instead of crashing QGIS
            QMessageBox.warning(None, "Sous Chef", f"The script could not be saved:\n\n{e}")


def recipe_files(path: str) -> list:
    """The recipe and everything kept with it: styles folder, symbol, backup."""
    base = os.path.splitext(path)[0]
    return [p for p in (path, base + ".svg", path + ".bak", base + "_styles") if os.path.exists(p)]


def delete_recipe(path: str) -> list:
    removed = []
    for p in recipe_files(path):
        try:
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
            removed.append(os.path.basename(p))
        except OSError:
            pass
    provider = QgsApplication.processingRegistry().providerById("souschef")
    if provider is not None:
        provider.refreshAlgorithms()
    return removed


class DeleteRecipe(_RecipeAction):
    def __init__(self, plugin):
        super().__init__(plugin, "Delete Recipe…")

    def icon(self):
        return QgsApplication.getThemeIcon("/mActionDeleteSelected.svg")

    def execute(self):
        path = self.path()
        files = [os.path.basename(p) + ("/" if os.path.isdir(p) else "") for p in recipe_files(path)]
        answer = QMessageBox.question(
            None, "Delete Recipe",
            f"Delete the recipe “{self.itemData.displayName()}”?\n\nThese files are removed:\n" + "\n".join(files)
            + "\n\nThis can't be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            delete_recipe(path)


def make_actions(plugin) -> list:
    if ContextAction is object:
        return []
    return [OpenRecipe(plugin), EditRecipe(plugin), ShareRecipe(plugin), ScriptRecipe(plugin), ShowRecipeFolder(plugin),
            DeleteRecipe(plugin)]
