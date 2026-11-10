"""Reading Processing runs from the QGIS history, and recording a workflow live.

Every algorithm run from a Processing tool window is logged by QGIS as a history entry:

    {"algorithm_id": "native:buffer",
     "parameters": {"inputs": {"INPUT": "<layer source>", "DISTANCE": 10, ...}},
     "results": {"OUTPUT": "<file path or id of the loaded result layer>"},
     "python_command": "processing.run(...)"}

"results" is only added when the run succeeds. Batch runs and "Iterate over this
layer" runs are NOT logged by QGIS: while recording, the Recorder hooks into
Processing's batch window and iterate function to capture them itself.

While recording, the Recorder also notes things that are not Processing runs:
edits saved to layers by hand (with the Field Calculator's expression when one
was used), copies made with "Save Features As", every layer added (to link
temporary results and recognise extents taken from a layer), and project changes.
"""

from __future__ import annotations
from . import log

import os
import re
from dataclasses import dataclass, field

from qgis.core import (
    QgsApplication,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsProcessingAlgorithm,
    QgsProcessingOutputLayerDefinition,
    QgsProject,
    QgsSettings,
    QgsVectorLayer,
)
from qgis.gui import QgsGui
from qgis.PyQt.QtCore import QDateTime, QObject, QUrl, pyqtSignal
from qgis.PyQt.QtWidgets import QApplication, QWidget

PROVIDER_ID = "processing"
RECENT_EXPRESSIONS = "expressions/recent"
BATCH_IDS, ITERATE_IDS = -20_000_000, -30_000_000


@dataclass
class Step:
    entry_id: int
    timestamp: QDateTime
    alg_id: str
    inputs: dict
    results: dict | None
    alg: QgsProcessingAlgorithm | None = None
    python_command: str = ""
    synthetic: str = ""   # set for steps made from non-Processing actions, e.g. "from the attribute table"
    selected_handled: set = field(default_factory=set)  # params whose "selected only" was turned into a step
    batch_id: str = ""    # rows of one batch run share an id
    project: str = ""     # project file the step was run in, when known
    note: str = ""        # extra explanation shown in the review
    opened: dict = field(default_factory=dict)  # steps read from a model: {"style_files": {output: .qml path}}

    @property
    def ok(self) -> bool:
        return self.results is not None and self.alg is not None

    @property
    def label(self) -> str:
        name = self.alg.displayName() if self.alg else self.alg_id
        return f"{name} ({self.synthetic})" if self.synthetic else name

    @property
    def time_text(self) -> str:
        return self.timestamp.toString("dd MMM HH:mm:ss") if self.timestamp else ""

    @property
    def msecs(self) -> int:
        return self.timestamp.toMSecsSinceEpoch() if self.timestamp else 0


def step_from_entry(entry_id: int, timestamp, data: dict) -> Step | None:
    if not isinstance(data, dict) or "algorithm_id" not in data:
        return None
    params = data.get("parameters") or {}
    inputs = params.get("inputs", params) if isinstance(params, dict) else {}
    alg = QgsApplication.processingRegistry().createAlgorithmById(data["algorithm_id"])
    results = data.get("results")
    return Step(entry_id, timestamp, data["algorithm_id"], dict(inputs or {}),
                dict(results) if isinstance(results, dict) else None, alg,
                data.get("python_command", "") or "")


def make_step(entry_id: int, timestamp: QDateTime, alg_id: str, inputs: dict, results: dict,
              synthetic: str) -> Step:
    """A step for something that was not a logged Processing run (e.g. the Field Calculator)."""
    alg = QgsApplication.processingRegistry().createAlgorithmById(alg_id)
    return Step(entry_id, timestamp, alg_id, inputs, results, alg, "", synthetic)


@dataclass
class ManualEdit:
    """Changes saved to a layer by hand (editing, attribute table, Field Calculator)."""
    timestamp: QDateTime
    layer_id: str
    layer_name: str
    source: str
    changes: dict = field(default_factory=dict)          # kind -> count
    changed_fields: set = field(default_factory=set)     # names of fields whose values changed
    added_fields: list = field(default_factory=list)     # (name, type name, length, precision, QVariant/QMetaType id)
    expression: str = ""                                 # newest recently used expression (first guess)
    expressions: dict = field(default_factory=dict)      # field -> expression that reproduces its saved values
    partial: set = field(default_factory=set)            # fields only updated for some features (selected only)

    @property
    def calculated(self) -> dict:
        """{field: expression} for fields a Field Calculator run explains."""
        return {f: e for f, e in self.expressions.items() if f not in self.partial}

    @property
    def unexplained_fields(self) -> list:
        fields = sorted(self.changed_fields | {f[0] for f in self.added_fields})
        return [f for f in fields if f not in self.calculated]

    @property
    def other_changes(self) -> dict:
        return {k: n for k, n in self.changes.items()
                if n and k in ("geometries", "added", "removed", "fields_removed")}

    @property
    def fully_explained(self) -> bool:
        return bool(self.calculated) and not self.unexplained_fields and not self.other_changes

    def describe(self) -> str:
        """What a model can't repeat (Field Calculator runs that were recognised are left out)."""
        names = {"geometries": "geometry(ies) changed", "added": "feature(s) added", "removed": "feature(s) deleted",
                 "fields_removed": "field(s) deleted"}
        parts = [f"{n} {names[k]}" for k, n in self.other_changes.items()]
        for f in self.unexplained_fields:
            if f in self.partial:
                parts.append(f"field '{f}' recalculated for only some features (e.g. selected only): "
                             f"{self.expressions[f]}")
            else:
                parts.append(f"field '{f}' changed in a way no recently used expression reproduces "
                             "(values may have been typed)")
        if not parts and not self.calculated:
            parts = [f"{n} change(s) saved" for n in [sum(self.changes.values())] if n] or ["changes saved"]
        text = "; ".join(parts)
        if self.calculated:
            text += ("; " if text else "") + "made into Field Calculator step(s): " + ", ".join(self.calculated)
        return text


@dataclass
class SavedCopy:
    """A layer saved with "Save Features As" and added to the map."""
    timestamp: QDateTime
    source: str           # the original layer
    source_name: str
    copy_path: str        # the new file (layer source)
    copy_name: str


def steps_from_history(start: QDateTime | None = None, end: QDateTime | None = None) -> list[Step]:
    """Processing runs logged between ``start`` and ``end`` (oldest first)."""
    registry = QgsGui.historyProviderRegistry()
    entries = registry.queryEntries(start or QDateTime(), end or QDateTime(), PROVIDER_ID)
    steps = [step_from_entry(e.id, e.timestamp, e.entry) for e in entries]
    steps = [s for s in steps if s is not None]
    steps.sort(key=lambda s: (s.msecs, s.entry_id))
    return steps


def _recent_expressions() -> dict:
    settings = QgsSettings()
    settings.beginGroup(RECENT_EXPRESSIONS)
    try:
        return {key: list(settings.value(key) or []) for key in settings.childKeys()}
    finally:
        settings.endGroup()


def _same_value(a, b) -> bool:
    if a is None or b is None or (hasattr(b, "isNull") and b.isNull()):
        return (a is None or (hasattr(a, "isNull") and a.isNull())) and \
               (b is None or (hasattr(b, "isNull") and b.isNull()))
    try:
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(b)))
    except (TypeError, ValueError):
        return str(a) == str(b)


def _as_stored(expression, context, feature, target_field):
    """The expression's result as the field would store it (rounded to its precision, cut to its length)."""
    context.setFeature(feature)
    value = expression.evaluate(context)
    try:
        return target_field.convertCompatible(value)
    except (ValueError, TypeError):
        if isinstance(value, str) and target_field.length() > 0:
            return value[:target_field.length()]
        return value


# Expressions run with the attribute table's quick calculation bar (QGIS does not keep them in
# its recently-used lists): noted by BarWatcher when the bar's Update button is pressed.
BAR_EXPRESSIONS: list = []


class BarWatcher(QObject):
    """Notes what the attribute table's quick calculation bar runs. Hooks an attribute table
    the first time something inside it gets keyboard focus (cheap: no global event filter)."""

    def start(self):
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._focus_changed)

    def stop(self):
        app = QApplication.instance()
        try:
            app.focusChanged.disconnect(self._focus_changed)
        except (TypeError, RuntimeError, AttributeError):
            pass

    def _focus_changed(self, _old, new):
        widget, depth = new, 0
        while widget is not None and depth < 20:
            if widget.metaObject().className() == "QgsAttributeTableDialog":
                self._hook(widget)
                return
            widget, depth = widget.parentWidget(), depth + 1

    def _hook(self, dialog):
        if dialog.property("souschef_hooked"):
            return
        dialog.setProperty("souschef_hooked", True)
        expression_widget = dialog.findChild(QWidget, "mUpdateExpressionText")
        combo = dialog.findChild(QWidget, "mFieldCombo")
        button = dialog.findChild(QWidget, "mRunFieldCalc")   # "Update All" ("Update Selected" is left out)
        if expression_widget is None or button is None or not hasattr(button, "pressed"):
            return

        def note():
            try:
                text = expression_widget.asExpression()
                field_name = combo.currentField() if combo is not None and hasattr(combo, "currentField") else ""
                if text:
                    BAR_EXPRESSIONS.append({"expression": text, "field": field_name or ""})
                    del BAR_EXPRESSIONS[:-50]
            except Exception as error:   # safe to carry on: noted in the log
                log.ignored(error)
        button.pressed.connect(note)


def _layer_signature(layer) -> tuple:
    gtype = layer.geometryType()
    # GeoPackage copies gain a "fid" field: ignore it when comparing
    names = tuple(n for n in layer.fields().names() if n.lower() != "fid")
    return (getattr(gtype, "value", gtype), names, layer.featureCount())


def _extent_of(layer):
    e = layer.extent()
    return (e.xMinimum(), e.xMaximum(), e.yMinimum(), e.yMaximum())


def _now_ms() -> int:
    return QDateTime.currentDateTime().toMSecsSinceEpoch()


FILTER_IDS, JOIN_IDS, RASTERCALC_IDS, VIRTUAL_IDS = -50_000_000, -60_000_000, -70_000_000, -80_000_000


def _without_subset(source: str) -> str:
    return re.sub(r"\|subset=.*$", "", source or "")


def _join_keys(layer) -> set:
    """The joins a layer has, as comparable keys."""
    keys = set()
    try:
        for join in layer.vectorJoins():
            subset = join.joinFieldNamesSubset() or []
            keys.add((join.joinLayerId(), join.joinFieldName(), join.targetFieldName(), join.prefix() or "",
                      tuple(subset)))
    except Exception as error:   # safe to carry on: noted in the log
        log.ignored(error)
    return keys


class _DialogWatcher(QObject):
    """Notices QGIS windows that aren't Processing tools (the Raster Calculator), so their
    runs can be recorded. It listens to focus changes (a window opening gives one of its
    fields the focus), not to every event, which would slow QGIS down and isn't safe."""

    def __init__(self, recorder):
        super().__init__()
        self.recorder = recorder

    def focus_changed(self, _old, now):
        if now is None:
            return
        try:
            window = now.window()
            mark = f"souschef_rastercalc_{id(self)}"   # the recorder and the background tracker each hook it
            if window.metaObject().className() == "QgsRasterCalcDialog" and not window.property(mark):
                window.setProperty(mark, True)
                window.accepted.connect(lambda d=window: self.recorder._raster_calc(d))
        except Exception as error:   # safe to carry on: noted in the log
            log.ignored(error)


class Recorder(QObject):
    """Collects what the user does between start() and stop()."""

    changed = pyqtSignal()                 # something was recorded (for the live counter)
    projectChanged = pyqtSignal(str)       # recording paused because another project was opened

    KEEP = 500   # passive tracker: items kept (oldest dropped)

    def __init__(self, parent=None, passive=False):
        super().__init__(parent)
        # passive: the background tracker that runs while QGIS is open, for tracing layers
        # back later. It never pauses and keeps only the most recent items.
        self.passive = passive
        self.recording = False
        self.paused = False
        self.started: QDateTime | None = None
        self.layer_snapshot: dict = {}  # layer id -> (source, name)
        self.extents: list = []         # (source, name, extent, crs authid) of layers seen
        self.edits: list = []           # ManualEdit
        self.copies: list = []          # SavedCopy
        self.extra_steps: list = []     # batch rows and iterate runs captured by the hooks
        self.pauses: list = []          # (last history id before pausing, last history id when resuming)
        self.project_path = ""
        self.project_switches: list = []  # (ms, new project path)
        self._pause_start = None
        self._watched: dict = {}
        self._committing: dict = {}     # layer id -> the edit of the save in progress
        self._recent: dict = {}
        self._signatures: dict = {}
        self._last_entry_id = 0
        self._hooks: list = []
        self._batch = None
        self._batch_count = 0
        self._ignore_ids: set = set()   # layers open before recording, or added while paused
        self._joins: dict = {}          # layer id -> the joins it has (Layer Properties › Joins)
        self._made_count = 0
        self._last_made: dict = {}      # (kind, key) -> (step, newest history entry when it was made)
        self._dialog_watcher = None
        self.notes: dict = {}

    # -- lifecycle --------------------------------------------------------------

    def start(self):
        self.layer_snapshot, self.extents, self.edits, self.copies = {}, [], [], []
        self.extra_steps, self.pauses, self.project_switches = [], [], []
        self.notes = {}   # entry id -> a note added while recording
        self.paused, self._pause_start = False, None
        now = QDateTime.currentDateTime()
        self.started = now.addSecs(-1)
        earlier = QgsGui.historyProviderRegistry().queryEntries(now.addSecs(-3600), now.addSecs(1), PROVIDER_ID)
        self._last_entry_id = max([e.id for e in earlier], default=0)
        self.recording = True
        self._recent = _recent_expressions()
        project = QgsProject.instance()
        self.project_path = project.fileName()
        project.layersAdded.connect(self._on_layers_added)
        project.layersWillBeRemoved.connect(self._layers_removed)
        project.cleared.connect(self._on_project_cleared)
        project.readProject.connect(self._on_project_read)
        project.fileNameChanged.connect(self._on_file_name_changed)
        self._ignore_ids = set()
        for layer in project.mapLayers().values():
            self._ignore_ids.add(layer.id())
            self.layer_snapshot[layer.id()] = (layer.source(), layer.name())
            self._watch(layer)
            self._remember_signature(layer)
            self._remember_extent(layer)
        self._install_hooks()

    def stop(self) -> list[Step]:
        if self.recording:
            project = QgsProject.instance()
            for signal, slot in ((project.layersAdded, self._on_layers_added),
                                 (project.layersWillBeRemoved, self._layers_removed),
                                 (project.cleared, self._on_project_cleared),
                                 (project.readProject, self._on_project_read),
                                 (project.fileNameChanged, self._on_file_name_changed)):
                try:
                    signal.disconnect(slot)
                except TypeError:
                    pass
            for connections in self._watched.values():
                for signal, slot in connections:
                    try:
                        signal.disconnect(slot)
                    except (TypeError, RuntimeError):
                        pass
            self._watched = {}
            self._remove_hooks()
            if self.paused:
                self.resume()
        steps = self.steps()
        self.recording = False
        return steps

    def _newest_entry_id(self) -> int:
        entries = QgsGui.historyProviderRegistry().queryEntries(
            self.started, QDateTime.currentDateTime().addSecs(1), PROVIDER_ID)
        return max([e.id for e in entries], default=self._last_entry_id)

    def pause(self):
        """Runs logged while paused are left out (matched by history entry number, not time)."""
        if self.recording and not self.paused:
            self.paused, self._pause_start = True, self._newest_entry_id()
            self.changed.emit()

    def resume(self):
        if self.recording and self.paused:
            self.pauses.append((self._pause_start, self._newest_entry_id()))
            self.paused, self._pause_start = False, None
            self.changed.emit()

    def _in_pause(self, entry_id: int) -> bool:
        spans = list(self.pauses)
        if self.paused and self._pause_start is not None:
            spans.append((self._pause_start, 10 ** 12))
        return any(a < entry_id <= b for a, b in spans)

    def steps(self) -> list[Step]:
        if self.started is None:
            return []
        steps = steps_from_history(self.started, QDateTime.currentDateTime().addSecs(1))
        steps = [s for s in steps if s.entry_id > self._last_entry_id and not self._in_pause(s.entry_id)]
        steps += [s for s in self.extra_steps]
        steps.sort(key=lambda s: (s.msecs, s.entry_id if s.entry_id > 0 else 0))
        for s in steps:
            s.project = self.project_at(s.msecs)
        return steps

    def project_at(self, ms: int) -> str:
        path = self.project_path
        for when, new_path in self.project_switches:
            if ms >= when:
                path = new_path
        return path

    def count(self) -> tuple[int, int]:
        """(steps, hand edits) recorded so far, for the live counter."""
        if not self.recording or self.started is None:
            return 0, 0
        return len(self.steps()), len(self.edits)

    # -- projects ---------------------------------------------------------------

    def _on_file_name_changed(self, *_args):
        """Saving an unsaved project keeps it the same project."""
        path = QgsProject.instance().fileName()
        if self.project_switches:
            when, old = self.project_switches[-1]
            if old == "" and path:
                self.project_switches[-1] = (when, path)
        elif self.project_path == "" and path:
            self.project_path = path
        self.changed.emit()

    def _on_project_cleared(self):
        self._project_switch("")

    def _on_project_read(self, *_args):
        self._project_switch(QgsProject.instance().fileName())

    def _project_switch(self, path: str):
        now = _now_ms()
        # opening a project clears the old one first: one switch, with the new project's path
        if self.project_switches and now - self.project_switches[-1][0] < 3000:
            self.project_switches[-1] = (self.project_switches[-1][0], path)
            return
        self.project_switches.append((now, path))
        if not self.paused and not self.passive:
            self.pause()
            self.projectChanged.emit(path)
        # the new project's layers need watching too
        for layer in QgsProject.instance().mapLayers().values():
            self._watch(layer)
            self._remember_signature(layer)
            self._remember_extent(layer)

    # -- Processing hooks (batch window, iterate button) -------------------------

    def _install_hooks(self):
        try:
            from processing.gui import BatchAlgorithmDialog as batch_module
            cls = batch_module.BatchAlgorithmDialog
            recorder = self
            original_execute = getattr(cls, "execute")
            original_handle = getattr(cls, "handleAlgorithmResults")

            def execute(dialog, parameters):
                try:
                    if recorder.recording and not recorder.paused:
                        recorder._batch_started(dialog, parameters)
                except Exception as error:   # safe to carry on: noted in the log
                    log.ignored(error)
                return original_execute(dialog, parameters)

            def handle(dialog, algorithm, context, feedback, parameters):
                try:
                    if recorder.recording and not recorder.paused:
                        recorder._batch_row_done(algorithm, context, parameters)
                except Exception as error:   # safe to carry on: noted in the log
                    log.ignored(error)
                return original_handle(dialog, algorithm, context, feedback, parameters)

            for name, func, original in (("execute", execute, original_execute),
                                         ("handleAlgorithmResults", handle, original_handle)):
                self._hooks.append((cls, name, original, name in cls.__dict__))
                setattr(cls, name, func)
        except Exception as error:   # safe to carry on: noted in the log
            log.ignored(error)
        try:
            from processing.gui import AlgorithmDialog as dialog_module
            original_iter = dialog_module.executeIterating
            recorder = self

            def execute_iterating(alg, parameters, param_to_iter, context, feedback):
                ok = original_iter(alg, parameters, param_to_iter, context, feedback)
                try:
                    if ok and recorder.recording and not recorder.paused:
                        recorder._iterate_done(alg, parameters, param_to_iter, context)
                except Exception as error:   # safe to carry on: noted in the log
                    log.ignored(error)
                return ok

            self._hooks.append((dialog_module, "executeIterating", original_iter, True))
            dialog_module.executeIterating = execute_iterating
        except Exception as error:   # safe to carry on: noted in the log
            log.ignored(error)
        # the Raster Calculator window (Raster menu) isn't a Processing tool: watch for it
        if self._dialog_watcher is None and QApplication.instance() is not None:
            self._dialog_watcher = _DialogWatcher(self)
            QApplication.instance().focusChanged.connect(self._dialog_watcher.focus_changed)

    def _remove_hooks(self):
        if self._dialog_watcher is not None:
            try:
                QApplication.instance().focusChanged.disconnect(self._dialog_watcher.focus_changed)
            except (TypeError, RuntimeError):
                pass
            self._dialog_watcher = None
        for owner, name, original, had_own in reversed(self._hooks):
            try:
                if had_own:
                    setattr(owner, name, original)
                else:
                    delattr(owner, name)
            except Exception as error:   # safe to carry on: noted in the log
                log.ignored(error)
        self._hooks = []

    def _batch_started(self, dialog, rows):
        self._batch_count += 1
        alg = dialog.algorithm()
        context = dialog.processingContext()
        captured = []
        for params in rows:
            captured.append({"inputs": dict(alg.asMap(params, context).get("inputs", {})),
                             "python": alg.asPythonCommand(params, context)})
        self._batch = {"id": f"batch{self._batch_count}", "alg_id": alg.id(), "rows": captured, "next": 0}

    def _batch_row_done(self, algorithm, context, parameters):
        batch = self._batch
        if batch is None:
            return
        index = batch["next"]
        batch["next"] += 1
        row = batch["rows"][index] if index < len(batch["rows"]) else {
            "inputs": dict(algorithm.asMap(parameters, context).get("inputs", {})), "python": ""}
        results = self._results_from(algorithm, context, parameters)
        step = make_step(BATCH_IDS - self._next_extra(), QDateTime.currentDateTime(), batch["alg_id"],
                         row["inputs"], results, f"batch row {index + 1} of {len(batch['rows'])}")
        step.python_command = row["python"]
        step.batch_id = batch["id"]
        self.extra_steps.append(step)
        self._trim()
        self.changed.emit()

    def _iterate_done(self, alg, parameters, param_to_iter, context):
        inputs = dict(alg.asMap(parameters, context).get("inputs", {}))
        step = make_step(ITERATE_IDS - self._next_extra(), QDateTime.currentDateTime(), alg.id(), inputs, {},
                         "run once per feature")
        layer = inputs.get(param_to_iter, "")
        step.note = (f"Run with “Iterate over this layer” on {os.path.basename(str(layer)) or 'a layer'}: "
                     "one result per feature. A recipe step runs once on the whole layer; to repeat the "
                     "per-feature runs, run the recipe as a batch.")
        step.python_command = alg.asPythonCommand(parameters, context)
        self.extra_steps.append(step)
        self._trim()
        self.changed.emit()

    @staticmethod
    def _results_from(algorithm, context, parameters) -> dict:
        results = {}
        loaded = {}
        try:
            for layer_id, details in context.layersToLoadOnCompletion().items():
                loaded[getattr(details, "outputName", "")] = layer_id
        except Exception as error:   # safe to carry on: noted in the log
            log.ignored(error)
        for d in algorithm.destinationParameterDefinitions():
            name = d.name()
            value = parameters.get(name)
            if name in loaded:
                results[name] = loaded[name]
            elif isinstance(value, QgsProcessingOutputLayerDefinition):
                results[name] = value.sink.staticValue()
            elif isinstance(value, str):
                results[name] = value
        return results

    # -- layers added -----------------------------------------------------------

    def _next_extra(self) -> int:
        """Numbers for batch rows and iterate runs: unique even after old ones are dropped."""
        self._extra_count = getattr(self, "_extra_count", 0) + 1
        return self._extra_count

    def clear_notes(self):
        """Forget everything noted so far (keeps running)."""
        self.edits, self.copies, self.extra_steps, self.extents = [], [], [], []
        self.layer_snapshot, self._signatures = {}, {}
        self.changed.emit()

    def _layers_removed(self, layer_ids):
        """The background tracker forgets its connections and records for removed layers
        (it keeps their name and source: steps may still refer to them)."""
        for lid in layer_ids:
            for signal, slot in self._watched.pop(lid, []):
                try:
                    signal.disconnect(slot)
                except (TypeError, RuntimeError):
                    pass
            if self.passive:
                self._signatures.pop(lid, None)

    def _trim(self):
        if self.passive:
            if len(self._signatures) > self.KEEP:
                for key in list(self._signatures)[:len(self._signatures) - self.KEEP]:
                    del self._signatures[key]
            for name in ("edits", "copies", "extra_steps", "extents"):
                items = getattr(self, name)
                if len(items) > self.KEEP:
                    del items[:len(items) - self.KEEP]
            if len(self.layer_snapshot) > 5 * self.KEEP:
                for key in list(self.layer_snapshot)[:len(self.layer_snapshot) - 5 * self.KEEP]:
                    del self.layer_snapshot[key]

    def _remember_signature(self, layer):
        if isinstance(layer, QgsVectorLayer) and layer.isValid():
            self._signatures[layer.id()] = (_layer_signature(layer), layer.source(), layer.name())

    def _remember_extent(self, layer):
        try:
            if layer.isValid():
                self.extents.append((layer.source(), layer.name(), _extent_of(layer), layer.crs().authid()))
        except Exception as error:   # safe to carry on: noted in the log
            log.ignored(error)

    def _on_layers_added(self, layers):
        for layer in layers:
            # keep the first source seen: reopening a project re-adds layers with new sources
            if self.paused and layer.id() not in self.layer_snapshot:
                self._ignore_ids.add(layer.id())
            self.layer_snapshot.setdefault(layer.id(), (layer.source(), layer.name()))
            if not self.paused:
                self._check_saved_copy(layer)
                if layer.providerType() == "virtual":
                    self._virtual_added(layer)
            self._watch(layer)
            self._remember_signature(layer)
            self._remember_extent(layer)
        self._trim()
        self.changed.emit()

    # -- filters, joins, the Raster Calculator window, virtual layers ------------

    def _add_made(self, kind, key, alg_id, inputs, results, label, note=""):
        """A step for something QGIS doesn't log as a Processing run."""
        base = {"filter": FILTER_IDS, "join": JOIN_IDS, "rastercalc": RASTERCALC_IDS, "virtual": VIRTUAL_IDS}[kind]
        self._made_count += 1
        step = make_step(base - self._made_count, QDateTime.currentDateTime(), alg_id, inputs, results, label)
        if step.alg is None:
            return None
        step.note = note
        self.extra_steps.append(step)
        self._last_made[(kind, key)] = (step, self._newest_entry_id() if self.started is not None else 0)
        self._trim()
        self.changed.emit()
        return step

    def _undo_made(self, kind, key, why):
        """A filter or join was changed or removed again: drop its step if nothing ran
        since (just trying things out), otherwise say so on the step."""
        made = self._last_made.pop((kind, key), None)
        if made is None:
            return
        step, entry = made
        newest = self._newest_entry_id() if self.started is not None else 0
        if newest <= entry and step in self.extra_steps:
            self.extra_steps.remove(step)
        else:
            step.note = (step.note + "; " if step.note else "") + why
        self.changed.emit()

    def _filter_changed(self, layer_id):
        if not self.recording or self.paused:
            return
        layer = QgsProject.instance().mapLayer(layer_id)
        if not isinstance(layer, QgsVectorLayer):
            return
        self._undo_made("filter", layer_id, "the filter was changed or removed afterwards: later steps may have "
                                            "used the whole layer")
        subset = layer.subsetString().strip()
        if not subset:
            return
        source = _without_subset(layer.source())
        note = ("" if not QgsExpression(subset).hasParserError() else
                "the filter is written in the data source's own query language: check the expression")
        self._add_made("filter", layer_id, "native:extractbyexpression",
                       {"INPUT": source, "EXPRESSION": subset, "OUTPUT": "TEMPORARY_OUTPUT"},
                       {"OUTPUT": source}, "layer filter", note)

    def _fields_updated(self, layer_id):
        layer = QgsProject.instance().mapLayer(layer_id)
        if not isinstance(layer, QgsVectorLayer):
            return
        keys, old = _join_keys(layer), self._joins.get(layer_id, set())
        self._joins[layer_id] = keys
        if not self.recording or self.paused:
            return
        for key in old - keys:
            self._undo_made("join", (layer_id,) + key, "the join was removed afterwards")
        for key in sorted(keys - old, key=str):
            join_id, join_field, target_field, prefix, subset = key
            join_layer = QgsProject.instance().mapLayer(join_id)
            if join_layer is None:
                continue
            source = _without_subset(layer.source())
            self._add_made("join", (layer_id,) + key, "native:joinattributestable",
                           {"INPUT": source, "FIELD": target_field, "INPUT_2": join_layer.source(),
                            "FIELD_2": join_field, "FIELDS_TO_COPY": list(subset), "METHOD": 1,
                            "DISCARD_NONMATCHING": False, "PREFIX": prefix if prefix else f"{join_layer.name()}_",
                            "OUTPUT": "TEMPORARY_OUTPUT"},
                           {"OUTPUT": source}, "join in Layer Properties")

    def _virtual_added(self, layer):
        """A virtual layer (an SQL query over other layers) becomes an Execute SQL step."""
        try:
            from qgis.core import QgsVirtualLayerDefinition
            definition = QgsVirtualLayerDefinition.fromUrl(QUrl(layer.source()))
        except Exception:
            return
        query = definition.query().strip()
        if not query:
            return
        project = QgsProject.instance()
        sources, names = [], []
        for sl in definition.sourceLayers():
            known = [sl.name()]
            if sl.isReferenced():
                ref = project.mapLayer(sl.reference())
                sources.append(ref.source() if ref is not None else sl.reference())
                if ref is not None:
                    known.append(ref.name())   # a table can be called by the layer's name too
            else:
                sources.append(sl.source())
            names.append(known)
        # Execute SQL calls its tables input1, input2, …
        pairs = sorted(((name, i) for i, known in enumerate(names, start=1) for name in known if name),
                       key=lambda x: -len(x[0]))
        for name, i in pairs:
            query = re.sub(r'(?<![\w"])"?' + re.escape(name) + r'"?(?![\w"])', f"input{i}", query)
        self._add_made("virtual", layer.id(), "qgis:executesql",
                       {"INPUT_DATASOURCES": sources, "INPUT_QUERY": query, "INPUT_UID_FIELD": "",
                        "INPUT_GEOMETRY_FIELD": "", "INPUT_GEOMETRY_TYPE": 0, "OUTPUT": "TEMPORARY_OUTPUT"},
                       {"OUTPUT": layer.source()}, "virtual layer",
                       "made from a virtual layer: its tables are called input1, input2, … here; check the query")

    def _raster_calc(self, dialog):
        """The Raster Calculator window was run: a Processing 'Raster calculator' step."""
        if not self.recording or self.paused:
            return
        from qgis.PyQt.QtWidgets import QCheckBox, QLineEdit, QSpinBox, QTextEdit

        def child(cls, name):
            return dialog.findChild(cls, name)

        text = child(QTextEdit, "mExpressionTextEdit")
        expression = text.toPlainText().strip() if text is not None else ""
        if not expression:
            return
        project = QgsProject.instance()
        names = list(dict.fromkeys(re.findall(r'"([^"]+?)@\d+"', expression)))
        layers = [layer.source() for name in names for layer in project.mapLayersByName(name)[:1]]
        try:
            xmin, xmax, ymin, ymax = (float(child(QLineEdit, n).text().replace(",", "."))
                                      for n in ("mXMinLineEdit", "mXMaxLineEdit", "mYMinLineEdit", "mYMaxLineEdit"))
            columns = child(QSpinBox, "mNColumnsSpinBox").value()
        except Exception:
            xmin = xmax = ymin = ymax = None
            columns = 0
        crs_widget = dialog.findChild(QObject, "mCrsSelector")
        authid = crs_widget.crs().authid() if crs_widget is not None and hasattr(crs_widget, "crs") else ""
        inputs = {"LAYERS": layers, "EXPRESSION": expression}
        if xmin is not None:
            inputs["EXTENT"] = f"{xmin},{xmax},{ymin},{ymax}" + (f" [{authid}]" if authid else "")
            for name in names:   # the extent of one of its layers (the window's usual choice)
                for layer in project.mapLayersByName(name)[:1]:
                    e = layer.extent()
                    if all(abs(a - b) < 1e-6 * max(1.0, abs(b)) for a, b in
                           ((xmin, e.xMinimum()), (xmax, e.xMaximum()), (ymin, e.yMinimum()), (ymax, e.yMaximum()))):
                        inputs["EXTENT"] = layer.source()
                        break
            if columns:
                inputs["CELL_SIZE"] = (xmax - xmin) / columns
        if authid:
            inputs["CRS"] = authid
        virtual = child(QCheckBox, "mUseVirtualProviderCheckBox")
        if virtual is not None and virtual.isChecked():
            name_edit = child(QLineEdit, "mVirtualLayerName")
            inputs["LAYER_NAME"] = name_edit.text() if name_edit is not None else ""
            self._add_made("rastercalc", id(dialog), "native:virtualrastercalc", inputs,
                           {"OUTPUT": inputs["LAYER_NAME"]}, "Raster Calculator window")
            return
        output = dialog.findChild(QObject, "mOutputLayer")
        path = output.filePath() if output is not None and hasattr(output, "filePath") else ""
        if not path:
            return
        inputs["OUTPUT"] = path
        self._add_made("rastercalc", path, "native:rastercalc", inputs, {"OUTPUT": path}, "Raster Calculator window")

    def unexplained_layers(self, steps: list) -> list:
        """Names of layers created while recording that no recorded step or copy explains."""
        from .builder import norm_source, same_source  # avoid import cycle
        explained = []
        for s in steps:
            for value in (s.results or {}).values():
                if isinstance(value, str):
                    explained.append(norm_source(value))
                    if value in self.layer_snapshot:
                        explained.append(norm_source(self.layer_snapshot[value][0]))
        explained += [norm_source(c.copy_path) for c in self.copies]
        names = []
        for layer_id, (source, name) in self.layer_snapshot.items():
            if layer_id in self._ignore_ids:
                continue
            key = norm_source(source)
            if any(same_source(key, k) for k in explained) or layer_id in [v for s in steps for v in (s.results or {}).values()]:
                continue
            new = source.startswith(("memory", "Point", "LineString", "Polygon", "Multi", "None"))
            if not new:
                try:
                    new = os.path.getmtime(source.split("|")[0]) * 1000 >= self.started.toMSecsSinceEpoch()
                except OSError:
                    new = False
            if new:
                names.append(name)
        return names

    def _check_saved_copy(self, layer):
        """A new file, created while recording, that matches an existing layer."""
        if not isinstance(layer, QgsVectorLayer) or layer.providerType() != "ogr":
            return
        path = layer.source().split("|")[0]
        try:
            created = os.path.getmtime(path) * 1000 >= self.started.toMSecsSinceEpoch()
        except OSError:
            return
        if not created:
            return
        signature = _layer_signature(layer)
        matches = [(lid, src, name) for lid, (sig, src, name) in self._signatures.items()
                   if sig == signature and src.split("|")[0] != path]
        if matches:
            _lid, src, name = matches[-1]  # most recently added match
            self.copies.append(SavedCopy(QDateTime.currentDateTime(), src, name, layer.source(), layer.name()))

    # -- manual edits -----------------------------------------------------------

    def _watch(self, layer):
        if not isinstance(layer, QgsVectorLayer) or layer.id() in self._watched:
            return
        lid = layer.id()
        handlers = [
            (layer.committedAttributeValuesChanges, lambda _id, changes, lid=lid: self._values_changed(lid, changes)),
            (layer.committedGeometriesChanges, lambda _id, changes, lid=lid: self._edit(lid, "geometries", len(changes))),
            (layer.committedFeaturesAdded, lambda _id, feats, lid=lid: self._edit(lid, "added", len(feats))),
            (layer.committedFeaturesRemoved, lambda _id, ids, lid=lid: self._edit(lid, "removed", len(ids))),
            (layer.committedAttributesAdded, lambda _id, attrs, lid=lid: self._fields_added(lid, attrs)),
            (layer.committedAttributesDeleted, lambda _id, attrs, lid=lid: self._edit(lid, "fields_removed", len(attrs))),
            # one save = one edit, however long writing it takes (big layers can take minutes)
            (layer.beforeCommitChanges, lambda *_a, lid=lid: self._committing.__setitem__(lid, None)),
            (layer.afterCommitChanges, lambda *_a, lid=lid: self._committing.pop(lid, None)),
            # a layer filter, and joins added in Layer Properties
            (layer.subsetStringChanged, lambda *_a, lid=lid: self._filter_changed(lid)),
            (layer.updatedFields, lambda *_a, lid=lid: self._fields_updated(lid)),
        ]
        self._joins[lid] = _join_keys(layer)
        for signal, slot in handlers:
            signal.connect(slot)
        self._watched[lid] = handlers

    def _values_changed(self, layer_id, changes):
        edit = self._edit(layer_id, "values", sum(len(v) for v in changes.values()))
        layer = QgsProject.instance().mapLayer(layer_id)
        if edit is None or layer is None:
            return
        fields = layer.fields()
        indexes = set()
        for attrs in changes.values():
            indexes |= set(attrs)
        # each changed field on its own: one save may hold several Field Calculator runs
        for index in sorted(indexes):
            if not 0 <= index < fields.count():
                continue
            name = fields.at(index).name()
            edit.changed_fields.add(name)
            only_this = {fid: {index: attrs[index]} for fid, attrs in changes.items() if index in attrs}
            text, partial = self._matching_expression(layer, index, only_this, edit.expression)
            if text:
                edit.expressions[name] = text
                if partial:
                    edit.partial.add(name)

    def _matching_expression(self, layer, index, changes, first_guess=""):
        """(expression, partial): a recently used expression that reproduces every saved value of
        the changed field, compared after the field's own rounding. partial: the expression would
        also have changed features that were left as they were (an update of selected features)."""
        target = layer.fields().at(index)
        field_name = target.name()
        candidates = [first_guess] if first_guess else []
        for entry in reversed(BAR_EXPRESSIONS):        # the attribute table's quick calculation bar
            if entry["field"] in ("", field_name) and entry["expression"] not in candidates:
                candidates.append(entry["expression"])
        for values in _recent_expressions().values():
            candidates += [v for v in values[:15] if v not in candidates]
        keep_guess = ""
        for text in candidates[:50]:
            expression = QgsExpression(text)
            if expression.hasParserError():
                continue
            if field_name in expression.referencedColumns():
                # an expression using the changed field itself can't be checked after the change
                if text == first_guess or any(e["expression"] == text for e in BAR_EXPRESSIONS[-3:]):
                    keep_guess = keep_guess or text
                continue
            context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
            expression.prepare(context)
            if not all(_same_value(_as_stored(expression, context, layer.getFeature(fid), target), attrs[index])
                       for fid, attrs in list(changes.items())[:500]):
                continue
            # features the save did not touch: would the expression have changed them too?
            partial = False
            for n, feature in enumerate(layer.getFeatures()):
                if n >= 300:
                    break
                if feature.id() in changes:
                    continue
                if not _same_value(_as_stored(expression, context, feature, target), feature.attribute(index)):
                    partial = True
                    break
            return text, partial
        return keep_guess, False

    def _fields_added(self, layer_id, fields):
        edit = self._edit(layer_id, "fields_added", len(fields))
        if edit:
            for f in fields:
                ftype = f.type()
                edit.added_fields.append((f.name(), f.typeName(), f.length(), f.precision(),
                                          getattr(ftype, "value", ftype)))

    def _edit(self, layer_id: str, kind: str, count: int):
        if self.paused:
            return None
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return None
        now = QDateTime.currentDateTime()
        last = self.edits[-1] if self.edits else None
        committing = getattr(self, "_committing", {})
        # one save emits several signals: group them into one edit (by the save itself, or,
        # if QGIS didn't announce the save, by time)
        if committing.get(layer_id) is not None:
            last = committing[layer_id]     # the edit of this save
        if committing.get(layer_id) is not None or (layer_id not in committing and last and last.layer_id == layer_id
                                                    and last.timestamp.msecsTo(now) < 2000):
            last.changes[kind] = last.changes.get(kind, 0) + count
            edit = last
        else:
            edit = ManualEdit(now, layer_id, layer.name(), layer.source(), {kind: count})
            self.edits.append(edit)
            if layer_id in committing:
                committing[layer_id] = edit
            self._trim()
            self.changed.emit()
        # A new entry in a recent-expressions list since the last check is the first
        # guess for the expression used; _values_changed then checks it against the data.
        recent = _recent_expressions()
        for key, values in recent.items():
            if values and values[:1] != self._recent.get(key, [])[:1]:
                edit.expression = values[0]
        self._recent = recent
        self._remember_signature(layer)
        return edit
