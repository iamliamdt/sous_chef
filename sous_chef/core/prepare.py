"""Turn recorded actions that are not ordinary Processing runs into model steps.

- A Field Calculator edit (attribute table)  -> a "Field calculator" step
- A layer saved with "Save Features As"      -> a "Save vector features to file" step
- "Select by expression/attribute/location" followed by a step that used only the
  selected features                          -> an "Extract by ..." step that the later
                                                step uses instead
- A selection nothing used                   -> left out (it makes no new layer)
- A run of a saved recipe (a Processing model) -> its own steps, so the new recipe
  stands on its own and they can be changed like any others

prepare_steps() returns the combined, time-ordered list of steps and reasons for
steps that should start unticked.
"""

from __future__ import annotations

import dataclasses
import os
import re

from qgis.core import QgsExpression, QgsProject

from .history import ManualEdit, SavedCopy, Step, make_step

SELECTION_TO_EXTRACT = {
    "qgis:selectbyexpression": ("native:extractbyexpression", ("INPUT", "EXPRESSION")),
    "qgis:selectbyattribute": ("native:extractbyattribute", ("INPUT", "FIELD", "OPERATOR", "VALUE")),
    "native:selectbylocation": ("native:extractbylocation", ("INPUT", "PREDICATE", "INTERSECT")),
}

# QVariant / QMetaType ids -> native:fieldcalculator FIELD_TYPE options
FIELD_TYPES = {1: 6, 2: 1, 3: 1, 4: 1, 5: 1, 6: 0, 10: 2, 14: 3, 15: 4, 16: 5}

# made steps get negative numbers in separate ranges (Processing history entries are positive)
EDIT_IDS, COPY_IDS, EXTRACT_IDS = -10_000_000, -2_000_000, -3_000_000
UNPACK_IDS = -40_000_000


def unpack_recipes(steps: list) -> list:
    """Replace each run of a recipe (or any Processing model) by the steps inside it.

    QGIS logs what every step of the model made (CHILD_RESULTS). What each step used is
    worked out from the model: its fixed values, the run's own inputs, and the results
    of earlier steps. So the steps connect to the layers and to each other just like
    recorded ones. A run that can't be worked out this way stays one step."""
    out = []
    for step in steps:
        out.extend(_unpacked(step) or [step])
    return out


def _unpacked(step: Step) -> list | None:
    from qgis.core import Qgis, QgsApplication, QgsProcessingModelAlgorithm
    if not step.ok or not isinstance(step.alg, QgsProcessingModelAlgorithm):
        return None
    child_results = (step.results or {}).get("CHILD_RESULTS")
    if not isinstance(child_results, dict):
        return None
    model = step.alg
    children = model.childAlgorithms()
    # an order where each step comes after the steps it uses (the log's order isn't the run's)
    logged = [cid for cid in child_results if cid in children]
    needs = {cid: {src.outputChildId() for sources in children[cid].parameterSources().values() for src in sources
                   if src.source() == Qgis.ProcessingModelChildParameterSource.ChildOutput} & set(logged)
             for cid in logged}
    order = []
    while len(order) < len(logged):
        ready = [cid for cid in logged if cid not in order and needs[cid] <= set(order)]
        if not ready:
            return None   # steps that depend on each other in a loop
        order.append(ready[0])
    destinations = {}
    for d in model.destinationParameterDefinitions():
        meta = d.metadata() or {}
        destinations[(meta.get("_modelChildId"), meta.get("_modelChildOutputName"))] = d.name()
    name = model.displayName()
    made = []
    for i, cid in enumerate(order):
        child = children[cid]
        if child.algorithmId() == "native:setlayerstyle":
            continue   # Sous Chef's own "keep the style" steps: the layer keeps its style anyway
        alg = QgsApplication.processingRegistry().createAlgorithmById(child.algorithmId())
        if alg is None:
            return None   # a tool that isn't installed: keep the recipe as one step
        inputs = {}
        for d in alg.parameterDefinitions():
            if d.isDestination():
                outer = destinations.get((cid, d.name()))
                inputs[d.name()] = step.inputs.get(outer, "TEMPORARY_OUTPUT") if outer else "TEMPORARY_OUTPUT"
                continue
            sources = child.parameterSources().get(d.name(), [])
            if not sources:
                continue
            values = []
            for source in sources:
                value, ok = _source_value(source, step, model, child_results)
                if not ok:
                    return None
                values.append(value)
            inputs[d.name()] = values if (d.type() == "multilayer" or len(values) > 1) else values[0]
        timestamp = step.timestamp.addMSecs(i) if step.timestamp is not None else None
        inner = Step(UNPACK_IDS - abs(step.entry_id) * 1000 - i, timestamp, child.algorithmId(), inputs,
                     dict(child_results.get(cid) or {}), alg, "", f"from the recipe “{name}”")
        inner.project = step.project
        inner.batch_id = f"{step.batch_id}:{cid}" if step.batch_id else ""
        made.append(inner)
    return made or None


def _source_value(source, step, model, child_results):
    """(value, ok) of one source of a step inside a model, for this run of the model."""
    from qgis.core import Qgis, QgsExpressionContext, QgsExpressionContextScope
    kind = source.source()
    if kind == Qgis.ProcessingModelChildParameterSource.StaticValue:
        return source.staticValue(), True
    if kind == Qgis.ProcessingModelChildParameterSource.ModelParameter:
        return step.inputs.get(source.parameterName()), True
    if kind == Qgis.ProcessingModelChildParameterSource.ChildOutput:
        made = child_results.get(source.outputChildId()) or {}
        return made.get(source.outputName()), source.outputName() in made
    if kind == Qgis.ProcessingModelChildParameterSource.Expression:
        # e.g. a Field Calculator formula put together from asked columns: the run's values
        scope = QgsExpressionContextScope()
        for d in model.parameterDefinitions():
            if not d.isDestination():
                scope.setVariable(d.name(), step.inputs.get(d.name()))
        path = model.sourceFilePath()
        scope.setVariable("model_path", path)
        scope.setVariable("model_folder", os.path.dirname(path) if path else "")
        context = QgsExpressionContext()
        context.appendScope(scope)
        expression = QgsExpression(source.expression())
        value = expression.evaluate(context)
        return value, not expression.hasEvalError()
    return None, False


def selected_only(python_command: str, param: str) -> bool:
    """True if the logged Python command ran ``param`` on selected features only."""
    if not python_command:
        return False
    match = re.search(r"'%s'\s*:\s*QgsProcessingFeatureSourceDefinition\(([^)]*)\)" % re.escape(param),
                      python_command)
    return bool(match and "selectedFeaturesOnly=True" in match.group(1))


def edit_to_steps(index: int, edit: ManualEdit) -> list:
    """One "Field calculator" step per field a recognised Field Calculator run explains,
    in an order where a field is calculated before expressions that use it."""
    calculated = dict(edit.calculated)
    order = []
    while calculated:
        ready = [f for f, e in calculated.items()
                 if not (set(QgsExpression(e).referencedColumns()) & (set(calculated) - {f}))]
        for f in ready or list(calculated)[:1]:
            order.append((f, calculated.pop(f)))
    layer = QgsProject.instance().mapLayer(edit.layer_id)
    added = {a[0]: a for a in edit.added_fields}
    steps = []
    for k, (name, expression) in enumerate(order):
        if name in added:
            _name, _type_name, length, precision, type_id = added[name]
            field_type = FIELD_TYPES.get(type_id, 2)
        else:
            existing = layer.fields().field(name) if layer is not None and layer.fields().indexOf(name) >= 0 else None
            if existing is not None:
                t = existing.type()
                field_type = FIELD_TYPES.get(getattr(t, "value", t), 2)
                length, precision = existing.length(), existing.precision()
            else:
                field_type, length, precision = 2, 0, 0
        inputs = {"INPUT": edit.source, "FIELD_NAME": name, "FIELD_TYPE": field_type,
                  "FIELD_LENGTH": max(int(length or 0), 0), "FIELD_PRECISION": max(int(precision or 0), 0),
                  "FORMULA": expression, "OUTPUT": "TEMPORARY_OUTPUT"}
        # The result is "the edited layer": later steps using that layer link to this step.
        step = make_step(EDIT_IDS - index * 100 - k, edit.timestamp, "native:fieldcalculator", inputs,
                         {"OUTPUT": edit.source}, "Field Calculator in the attribute table")
        step.note = ("new field" if name in added else "existing field") + f" '{name}'"
        steps.append(step)
    return steps


def copy_to_step(index: int, copy: SavedCopy) -> Step:
    path = copy.copy_path.split("|")[0]
    inputs = {"INPUT": copy.source, "OUTPUT": path}
    return make_step(COPY_IDS - index, copy.timestamp, "native:savefeatures", inputs,
                     {"OUTPUT": copy.copy_path}, "Save Features As")


def prepare_steps(steps: list, edits: list | None = None, copies: list | None = None):
    """Combined steps (time order) and {entry_id: reason} for steps to leave out by default.

    Returns (steps, reasons, converted_edits) where converted_edits are the indices
    of edits that became Field Calculator steps.
    """
    from .builder import norm_source, same_source  # avoid import cycle

    edits, copies = edits or [], copies or []
    steps = unpack_recipes(steps)
    combined = [s for s in steps]
    converted = set()
    for i, edit in enumerate(edits):
        made = [st for st in edit_to_steps(i, edit) if st.alg is not None]
        combined += made
        if made and edit.fully_explained:
            converted.add(i)   # nothing left that a model can't repeat

    # Save As copies that are really Processing outputs are not copies
    result_keys = [norm_source(v) for s in steps for v in (s.results or {}).values() if isinstance(v, str)]
    for i, copy in enumerate(copies):
        if not any(same_source(norm_source(copy.copy_path), k) for k in result_keys):
            combined.append(copy_to_step(i, copy))

    order = {id(s): n for n, s in enumerate(combined)}
    combined.sort(key=lambda s: (s.msecs, order[id(s)]))

    # Selections followed by "selected features only" -> Extract steps
    reasons = {}
    result, extracts = [], {}
    last_selection = {}  # layer key -> select step
    for step in combined:
        if step.ok and step.alg_id in SELECTION_TO_EXTRACT:
            method = step.inputs.get("METHOD", 0)
            if method in (0, "0", None):
                last_selection[norm_source(step.inputs.get("INPUT"))] = step
                reasons[step.entry_id] = "selection only: makes no new layer, and no later step used it"
            else:
                reasons[step.entry_id] = ("selection added to/removed from an earlier one: can't be turned "
                                          "into a recipe step")
            result.append(step)
            continue
        if step.ok and step.alg is not None:
            new_inputs = None
            for d in step.alg.parameterDefinitions():
                name = d.name()
                if name not in step.inputs or not selected_only(step.python_command, name):
                    continue
                key = norm_source(step.inputs[name])
                select = next((sel for k, sel in last_selection.items() if same_source(k, key)), None)
                if select is None:
                    continue
                if select.entry_id not in extracts:
                    alg_id, params = SELECTION_TO_EXTRACT[select.alg_id]
                    inputs = {p: select.inputs[p] for p in params if p in select.inputs}
                    inputs["OUTPUT"] = "TEMPORARY_OUTPUT"
                    marker = f"souschef-selection-{abs(select.entry_id)}"
                    extract = make_step(EXTRACT_IDS - len(extracts), select.timestamp, alg_id, inputs,
                                        {"OUTPUT": marker}, "replaces the selection")
                    extracts[select.entry_id] = extract
                    result.insert(result.index(select) + 1, extract)
                    reasons[select.entry_id] = "selection: replaced by the Extract step below"
                new_inputs = dict(new_inputs or step.inputs)
                new_inputs[name] = extracts[select.entry_id].results["OUTPUT"]
                handled = set(step.selected_handled) | {name}
                step = dataclasses.replace(step, inputs=new_inputs, selected_handled=handled)
        result.append(step)
    return result, reasons, converted
