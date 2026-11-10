"""How was this layer made? Trace a layer back through the Processing history (and what
the background tracker caught: Save As, Field Calculator, batch and iterate runs) to the
layers it started from.

Only the steps that lead to the chosen layers are kept, so runs in other projects,
earlier attempts and unrelated work drop out by themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QDateTime

from .builder import Context, match_extent, norm_source, same_source
from .history import steps_from_history
from .prepare import prepare_steps
from . import log


@dataclass
class Lineage:
    steps: list                                   # the steps that lead to the layers, oldest first
    edits: list = field(default_factory=list)     # hand edits of layers along the way (not repeatable)
    snapshot: dict = field(default_factory=dict)  # layer id -> (source, name)
    extents: list = field(default_factory=list)
    notes: list = field(default_factory=list)     # what could not be traced, ambiguities
    untraced: list = field(default_factory=list)  # names of chosen layers no step made
    targets: list = field(default_factory=list)   # names of the chosen layers that were traced


def _strings(value):
    """Every text inside a value (lists and maps included): layer ids, sources, paths."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    return []


def _keys(value, snapshot, project):
    """Ways a layer reference may be written: its id, its source, the source it had."""
    keys = [(value, None)]
    source = norm_source(value)
    if source:
        keys.append(source)
    layer = project.mapLayer(value)
    if layer is not None:
        keys.append(norm_source(layer.source()))
    if value in snapshot:
        keys.append(norm_source(snapshot[value][0]))
    return [k for k in keys if k]


def _in_place(step) -> bool:
    """A tool whose result is its own input (selections, spatial index, …): makes no layer.
    The Field Calculator (a made step) changes its layer in place but does count."""
    if step.synthetic:
        return False
    results = set(_strings(step.results or {}))
    # only what the step read: a result saved to a file is also listed among its settings,
    # as the output it was asked to write, and that doesn't make it an in-place edit
    inputs = set()
    for name, value in step.inputs.items():
        d = step.alg.parameterDefinition(name) if step.alg is not None else None
        if d is not None and d.isDestination():
            continue
        inputs |= set(_strings(value))
    return bool(results) and results <= inputs


def trace(layers, tracker=None, project=None) -> Lineage:
    """The steps that made ``layers``, back to the layers they started from."""
    project = project or QgsProject.instance()
    snapshot = dict(tracker.layer_snapshot) if tracker is not None else {}
    for layer in project.mapLayers().values():
        snapshot.setdefault(layer.id(), (layer.source(), layer.name()))
    extents = list(tracker.extents) if tracker is not None else []
    for layer in project.mapLayers().values():
        try:
            e = layer.extent()
            extents.append((layer.source(), layer.name(), (e.xMinimum(), e.xMaximum(), e.yMinimum(), e.yMaximum()),
                            layer.crs().authid()))
        except Exception as error:   # safe to carry on: noted in the log
            log.ignored(error)

    raw = steps_from_history(QDateTime(), QDateTime.currentDateTime().addSecs(1))
    if tracker is not None:
        raw += list(tracker.extra_steps)
    raw.sort(key=lambda s: (s.msecs, s.entry_id if s.entry_id > 0 else 0))
    edits = list(tracker.edits) if tracker is not None else []
    copies = list(tracker.copies) if tracker is not None else []
    steps, _reasons, converted = prepare_steps(raw, edits, copies)
    steps = [s for s in steps if s.ok and not _in_place(s)]

    # where each result can be found again
    made = []   # (index, keys)
    for i, s in enumerate(steps):
        keys = []
        for value in _strings(s.results or {}):
            keys += _keys(value, snapshot, project)
        made.append((i, keys))

    def producers(keys, before):
        found = [i for i, ks in made if i < before and any(same_source(a, b) for a in keys for b in ks)]
        return found

    lineage = Lineage([], snapshot=snapshot, extents=extents)
    chosen, todo = set(), []
    for layer in layers:
        keys = _keys(layer.id(), snapshot, project) + [norm_source(layer.source())]
        found = producers(keys, len(steps))
        if not found:
            lineage.untraced.append(layer.name())
            continue
        if len(found) > 1:
            lineage.notes.append(f"'{layer.name()}' was written by {len(found)} runs; the latest is used.")
        todo.append(found[-1])
        lineage.targets.append(layer.name())
    while todo:
        i = todo.pop()
        if i in chosen:
            continue
        chosen.add(i)
        step = steps[i]
        for name, value in step.inputs.items():
            d = step.alg.parameterDefinition(name)
            if d is None or d.isDestination():
                continue
            texts = _strings(value)
            if d.type() == "extent":
                # an extent "calculated from" a layer: follow that layer
                match = match_extent(value, Context(project, snapshot, extents)) if isinstance(value, str) else None
                texts = [match[0]] if match else []
            for text in texts:
                found = producers(_keys(text, snapshot, project), i)
                if found:
                    todo.append(found[-1])

    lineage.steps = [steps[i] for i in sorted(chosen)]
    # hand edits (not Field Calculator ones) of layers the steps used: shown as warnings
    used = [norm_source(t) for s in lineage.steps for t in _strings(s.inputs)]
    used += [norm_source(snapshot[t][0]) for s in lineage.steps for t in _strings(s.inputs) if t in snapshot]
    lineage.edits = [e for i, e in enumerate(edits) if i not in converted
                     and any(same_source(norm_source(e.source), k) for k in used if k)]
    if lineage.untraced:
        lineage.notes.append(
            "No logged step made " + ", ".join(f"'{n}'" for n in lineage.untraced) + ". QGIS logs Processing "
            "tools; Save As, the Field Calculator and batch runs are only known since the plugin was loaded.")
    return lineage


def diagnose(layers, project=None, count=8) -> str:
    """What tracing compared, for a report when a layer can't be traced: the layer's id and
    source, and the most recent Processing runs QGIS logged, with their results."""
    project = project or QgsProject.instance()
    from qgis.core import Qgis
    lines = [f"QGIS {Qgis.version()}"]
    for layer in layers:
        lines.append(f"Layer '{layer.name()}': id {layer.id()} · provider {layer.providerType()} · source {layer.source()}")
        lines.append(f"  compared as: {_keys(layer.id(), {}, project) + [norm_source(layer.source())]}")
    raw = steps_from_history(QDateTime(), QDateTime.currentDateTime().addSecs(1))
    lines.append(f"Processing history: {len(raw)} run(s) readable; the last {min(count, len(raw))}:")
    for step in raw[-count:]:
        lines.append(f"  {step.time_text} {step.alg_id} · tool available: {step.alg is not None} · "
                     f"results: {step.results}")
    return "\n".join(lines)
