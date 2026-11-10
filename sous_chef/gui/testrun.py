"""Taste Test: run the recipe on the layers it was recorded with, and compare each result
with the layer the recording made (number of features, columns)."""

from __future__ import annotations

import os
import shutil
import tempfile

from qgis.core import QgsProcessingContext, QgsProcessingFeedback, QgsProcessingUtils, QgsProject
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core import builder, cleanup
from ..i18n import tr
from . import style


def _layer_of(value, controller, project):
    if not isinstance(value, str) or not value:
        return None
    layer = project.mapLayer(value)
    if layer is None:
        layer = builder._layer_for(builder.norm_source(value), project)
    if layer is None and value in controller.snapshot:
        layer = builder._layer_for(builder.norm_source(controller.snapshot[value][0]), project)
    return layer


def run_test(controller):
    """{"rows": [(output, recorded, test, ok)], "error": str, "missing": [names], "context": ctx}"""
    project = QgsProject.instance()
    plan = controller.plan
    folder = tempfile.mkdtemp(prefix=cleanup.TEST_RUN_PREFIX)
    model = builder.build_model(plan, controller.model_name or "Test", "Test", style_dir=os.path.join(folder, "styles"))
    path = os.path.join(folder, "test.model3")
    builder.save_model(model, path)
    model.setSourceFilePath(path)   # so kept styles are found (@model_folder)
    params, missing = {}, []
    for input_plan in plan.inputs.values():
        if not input_plan.key:
            continue
        layer = next((_layer_of(v, controller, project) for _n, _p, v in input_plan.used_by
                      if _layer_of(v, controller, project) is not None), None)
        if layer is None:
            missing.append(input_plan.recorded or input_plan.label)
        params[input_plan.key] = layer
    if missing:
        shutil.rmtree(folder, ignore_errors=True)
        return {"rows": [], "error": "", "missing": missing, "context": None}
    for d in model.parameterDefinitions():
        if not d.isDestination():
            if d.name() not in params:
                params[d.name()] = d.defaultValue()   # asked settings: the recorded value
            continue
        default = d.defaultValue()
        if d.type() == "fileDestination" or (isinstance(default, str) and os.path.splitext(default)[1]):
            ext = os.path.splitext(default)[1] if isinstance(default, str) and default else ".gpkg"
            params[d.name()] = os.path.join(folder, f"{d.name()}{ext}")
        else:
            params[d.name()] = "TEMPORARY_OUTPUT"
    context = QgsProcessingContext()
    context.setProject(project)
    feedback = QgsProcessingFeedback()
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        import processing
        results = processing.run(model, params, context=context, feedback=feedback)
    except Exception as e:  # the model failed: show QGIS's message (it names the step)
        return {"rows": [], "error": str(e), "missing": [], "context": None, "folder": folder}
    finally:
        QApplication.restoreOverrideCursor()
    rows = []
    outputs = {d.description(): d.name() for d in model.outputDefinitions()}
    for step_plan, param in plan.outputs:
        name = outputs.get(param.display) or next((n for desc, n in outputs.items() if desc.startswith(param.display)), None)
        value = results.get(name) if name else None
        test = QgsProcessingUtils.mapLayerFromString(value, context) if isinstance(value, str) else (
            value if hasattr(value, "featureCount") else None)
        recorded = _layer_of((step_plan.step.results or {}).get(param.name), controller, project)
        rows.append((param.display, _describe(recorded), _describe(test), _same(recorded, test)))
    return {"rows": rows, "error": "", "missing": [], "context": context, "folder": folder}


def _describe(layer):
    """(features, columns) for a vector layer; ("W×H", bands) for a raster."""
    if layer is None:
        return None
    if hasattr(layer, "featureCount"):
        return (layer.featureCount(), tuple(layer.fields().names()))
    if hasattr(layer, "bandCount"):
        return (f"{layer.width()}×{layer.height()}", tuple(f"band {b}" for b in range(1, layer.bandCount() + 1)))
    return (None, ())


def _same(recorded, test):
    if recorded is None or test is None:
        return None
    a, b = _describe(recorded), _describe(test)
    fields_a = {f for f in a[1] if f.lower() != "fid"}
    fields_b = {f for f in b[1] if f.lower() != "fid"}
    return a[0] == b[0] and fields_a == fields_b


class TestRunDialog(QDialog):
    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Taste test"))
        self.resize(720, 360)
        lay = QVBoxLayout(self)
        if result["missing"]:
            text = ("The taste test needs the layers the recipe was recorded with, and these aren't in the project: "
                    + ", ".join(result["missing"]) + ".")
        elif result["error"]:
            text = "The recipe failed:\n\n" + result["error"]
        else:
            ok = [r for r in result["rows"] if r[3]]
            unknown = [r for r in result["rows"] if r[3] is None]
            text = (f"The recipe ran on the recorded layers. {len(ok)} of {len(result['rows'])} result(s) match "
                    "the recorded ones" + (f"; {len(unknown)} couldn't be compared (their recorded layer is "
                                           "gone)" if unknown else "") + ".")
        label = QLabel(text)
        label.setWordWrap(True)
        lay.addWidget(label)
        if result["rows"]:
            table = QTableWidget(len(result["rows"]), 4)
            table.setHorizontalHeaderLabels(["Result", "Recorded", "Taste test", ""])
            for i, (name, recorded, test, same) in enumerate(result["rows"]):
                cells = [name, _cell(recorded), _cell(test),
                         "✓ same" if same else ("?" if same is None else "⚠ differs")]
                for j, value in enumerate(cells):
                    item = QTableWidgetItem(value)
                    if j == 3 and same is False:
                        item.setForeground(Qt.GlobalColor.darkRed)
                    if j == 3 and same:
                        item.setForeground(Qt.GlobalColor.darkGreen)
                    table.setItem(i, j, item)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            table.verticalHeader().setVisible(False)
            lay.addWidget(table)
            hint = QLabel(tr("Compared: number of features and the columns (rasters: size and bands). The "
                             "taste-test results are not added to the project."))
            hint.setStyleSheet(f"color: {style.MUTED};")
            hint.setWordWrap(True)
            lay.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        if result["error"]:
            from . import report
            report.remember("Taste test", result["error"])
            button = buttons.addButton(tr("Report…"), QDialogButtonBox.ButtonRole.HelpRole)
            button.clicked.connect(lambda: report.show(self))
        lay.addWidget(buttons)


def _cell(described):
    if described is None:
        return "—"
    count, fields = described
    if isinstance(count, str):   # a raster
        return f"{count} pixels · {len(fields)} band{'s' if len(fields) != 1 else ''}"
    shown = [f for f in fields if f.lower() != "fid"]
    return f"{count} features · {len(shown)} columns" if count is not None else "layer"
