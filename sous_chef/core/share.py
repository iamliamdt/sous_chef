"""Sharing a recipe: as one zip file (the recipe with its styles and symbol), or as a
Python script (a Processing script that does the same as the recipe)."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile

from qgis.core import QgsProcessing, QgsProcessingModelAlgorithm, QgsProcessingModelChildParameterSource

from . import builder

STYLE_EXPRESSION = re.compile(r"@model_folder\s*\|\|\s*'([^']+)'")


def recipe_files(path: str) -> list:
    """The recipe and what belongs with it: its styles folder and its symbol."""
    base = os.path.splitext(path)[0]
    return [p for p in (path, base + "_styles", base + ".svg") if os.path.exists(p)]


def zip_recipe(path: str, zip_path: str) -> list:
    """Put a saved recipe and its styles and symbol in one zip. Returns the names added."""
    added = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in recipe_files(path):
            if os.path.isdir(item):
                for folder, _dirs, files in os.walk(item):
                    for name in files:
                        full = os.path.join(folder, name)
                        zf.write(full, os.path.relpath(full, os.path.dirname(item)))
            else:
                zf.write(item, os.path.basename(item))
            added.append(os.path.basename(item))
    return added


def zip_plan(plan, name: str, group: str, icon_path: str, zip_path: str) -> list:
    """Build the recipe being reviewed (with its styles and symbol) straight into a zip."""
    folder = tempfile.mkdtemp(prefix="sous_chef_share_")
    try:
        base = builder.slug(name, "recipe")
        model = builder.build_model(plan, name, group, style_dir=os.path.join(folder, base + "_styles"))
        recipe = builder.save_model(model, os.path.join(folder, base + ".model3"))
        if icon_path and os.path.isfile(icon_path):
            shutil.copyfile(icon_path, os.path.join(folder, base + ".svg"))
        return zip_recipe(recipe, zip_path)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def python_script(model: QgsProcessingModelAlgorithm, script_path: str) -> str:
    """Write the recipe as a Processing script. Kept styles are copied to a folder next to
    the script (a script has no recipe folder to find them in). Returns the script's text."""
    model_folder = os.path.dirname(model.sourceFilePath()) if model.sourceFilePath() else ""
    styles = os.path.splitext(script_path)[0] + "_styles"
    for cid in list(model.childAlgorithms()):
        child = model.childAlgorithm(cid)
        if child.algorithmId() != "native:setlayerstyle":
            continue
        sources = child.parameterSources().get("STYLE", [])
        text = sources[0].expression() if sources and hasattr(sources[0], "expression") else ""
        match = STYLE_EXPRESSION.search(text or "")
        if not match:
            continue
        qml = os.path.normpath(model_folder + match.group(1))
        if os.path.isfile(qml):
            os.makedirs(styles, exist_ok=True)
            target = os.path.join(styles, os.path.basename(qml))
            shutil.copyfile(qml, target)
            child.addParameterSources("STYLE", [QgsProcessingModelChildParameterSource.fromStaticValue(target)])
    lines = model.asPythonCode(QgsProcessing.PythonOutputType.PythonQgsProcessingAlgorithmSubclass, 4)
    text = "\n".join(lines) + "\n"
    with open(script_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def script_from_file(recipe_path: str, script_path: str) -> str:
    model = QgsProcessingModelAlgorithm()
    if not model.fromFile(recipe_path):
        raise ValueError("This file could not be read as a Processing model.")
    model.setSourceFilePath(recipe_path)
    return python_script(model, script_path)


def script_from_plan(plan, name: str, group: str, script_path: str) -> str:
    folder = tempfile.mkdtemp(prefix="sous_chef_share_")
    try:
        base = builder.slug(name, "recipe")
        model = builder.build_model(plan, name, group, style_dir=os.path.join(folder, base + "_styles"))
        builder.save_model(model, os.path.join(folder, base + ".model3"))
        return python_script(model, script_path)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
