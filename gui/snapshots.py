"""Small maps of each step's result, as the QGIS map draws it (the layer's own style), with
the step's inputs faint behind it. Used by the cookbook recipe and the
Storyboard image style.

A result is drawn from the layer in the project (or its file). When a result is gone (a
temporary layer that was removed), the recipe can be run again on its starting layers, as
the taste test does, with every intermediate result kept, and the maps drawn from that run.
"""

from __future__ import annotations

import copy
import os
import shutil
import tempfile

from qgis.core import (
    QgsCoordinateTransform,
    QgsMapLayer,
    QgsMapRendererSequentialJob,
    QgsMapSettings,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingUtils,
    QgsProject,
    QgsRectangle,
)
from qgis.PyQt.QtCore import QRectF, QSize, Qt
from qgis.PyQt.QtGui import QColor, QImage, QPainter

from ..core import builder, cleanup
from ..core import log

SIZE = QSize(300, 190)
_CACHE = {}


_OPENED = {}   # layers opened from their files (not in the project), kept while Sous Chef runs


def _open_file(source: str, name: str):
    """A layer opened straight from its file, when the file is still there (a layer saved to a
    GeoPackage, say, that's no longer in the project)."""
    key = builder.norm_source(source)
    if not key or not key[0] or not os.path.isfile(key[0]):
        return None
    uri = key[0] + (f"|layername={key[1]}" if key[1] else "")
    if uri in _OPENED:
        return _OPENED[uri]
    from qgis.core import QgsRasterLayer, QgsVectorLayer
    layer = QgsVectorLayer(uri, name or os.path.basename(key[0]), "ogr")
    if not layer.isValid():
        layer = QgsRasterLayer(key[0], name or os.path.basename(key[0]))
    if not layer.isValid():
        return None
    _OPENED[uri] = layer
    return layer


def _by_name(name: str, project):
    """The project's layer with this name, when exactly one has it."""
    if not name:
        return None
    matches = [layer for layer in project.mapLayers().values() if layer.name().lower() == name.lower() and layer.isSpatial()]
    return matches[0] if len(matches) == 1 else None


def find_layer(value, controller, project=None, name=""):
    """The layer a recorded value refers to: by layer id or source in the project; by the
    source it had when recorded; opened from its file if the file is still there; else the
    project's one layer with its name (``name``, or the name inside a layer id)."""
    project = project or QgsProject.instance()
    if not isinstance(value, str) or not value:
        return None
    layer = project.mapLayer(value)
    if layer is None:
        key = builder.norm_source(value)
        layer = builder._layer_for(key, project) if key else None
    recorded_source, recorded_name = controller.snapshot.get(value, ("", "")) if hasattr(controller, "snapshot") else ("", "")
    if layer is None and recorded_source:
        layer = builder._layer_for(builder.norm_source(recorded_source), project)
    if layer is None:
        layer = _open_file(value, name or recorded_name) or (_open_file(recorded_source, name or recorded_name)
                                                             if recorded_source else None)
    if layer is None:
        layer = _by_name(name or recorded_name or builder.readable_name(value), project)
    return layer


def _layer_of(value, controller, project):
    return find_layer(value, controller, project)


def _results(step_plan):
    return [param for param in step_plan.params if param.kind in ("output", "temp")]


def _inputs(step_plan, controller, project, found):
    """Layers the step used: recorded layers, or results of earlier steps."""
    out = []
    for param in step_plan.params:
        if param.kind not in ("input", "link"):
            continue
        for src in param.sources:
            if src[0] == "link":
                layer = found.get(src[1])
            else:
                input_plan = controller.plan.inputs.get(src[1])
                layer = input_layer(controller, input_plan) if input_plan else None
            if layer is not None and layer not in out:
                out.append(layer)
    return out


def _rerun(controller, missing_numbers):
    """Run the recipe again with every result kept; {(step number, output name): layer}, context."""
    project = QgsProject.instance()
    choices = copy.deepcopy(controller.choices)
    for step_plan in controller.plan.steps:
        for param in _results(step_plan):
            if param.kind == "temp":
                choices.roles[param.uid] = "save"
    plan = builder.analyse(controller.selected_steps(), choices, controller.context())
    folder = tempfile.mkdtemp(prefix=cleanup.TEST_RUN_PREFIX)
    try:
        model = builder.build_model(plan, "Snapshots", "", style_dir=os.path.join(folder, "styles"))
        path = os.path.join(folder, "snapshots.model3")
        builder.save_model(model, path)
        model.setSourceFilePath(path)
        params = {}
        for input_plan in plan.inputs.values():
            if not input_plan.key:
                continue
            layer = input_layer(controller, input_plan)
            if layer is None:
                return {}, None
            params[input_plan.key] = layer
        for d in model.parameterDefinitions():
            if d.isDestination():
                params[d.name()] = "TEMPORARY_OUTPUT"
            elif d.name() not in params:
                params[d.name()] = d.defaultValue()
        context = QgsProcessingContext()
        context.setProject(project)
        import processing
        results = processing.run(model, params, context=context, feedback=QgsProcessingFeedback())
        outputs = {d.description(): d.name() for d in model.outputDefinitions()}
        found = {}
        for step_plan, param in plan.outputs:
            name = outputs.get(param.display) or next((n for desc, n in outputs.items() if desc.startswith(param.display)), None)
            value = results.get(name) if name else None
            if isinstance(value, QgsMapLayer):
                layer = value
            else:
                layer = QgsProcessingUtils.mapLayerFromString(value, context) if isinstance(value, str) else None
            if layer is not None:
                found[(step_plan.number, param.name)] = layer
        return found, context
    except Exception:  # no maps rather than an error: the placeholders say so
        return {}, None
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _extent(layer, crs):
    extent = QgsRectangle(layer.extent())
    if layer.crs() != crs and layer.crs().isValid() and crs.isValid():
        try:
            extent = QgsCoordinateTransform(layer.crs(), crs, QgsProject.instance()).transformBoundingBox(extent)
        except Exception as error:   # safe to carry on: noted in the log
            log.ignored(error)
    return extent


def render(result, inputs=(), background=(), size: QSize = SIZE, fill="#FFFFFF",
           overview=None, extent=None, crs=None) -> QImage | None:
    """The result as the map draws it, zoomed to its extent, inputs faint behind it.
    ``result`` can be a list of layers, all drawn in full (several outputs, or a group of
    starting layers). Rasters fill the picture (cropped at the edges rather than leaving bands
    around them); ``extent``/``crs`` set the area instead (a shared extent)."""
    results = [lyr for lyr in (result if isinstance(result, (list, tuple)) else [result])
               if lyr is not None and lyr.isValid() and lyr.isSpatial()]
    if not results:
        return None
    crs = crs if crs is not None and crs.isValid() else results[0].crs()
    ratio = size.width() / size.height()
    if extent is None:
        extent = QgsRectangle()
        for layer in results:
            e = _extent(layer, crs)
            if e.isFinite() and not e.isNull():   # a single point has no size, but still counts
                extent.combineExtentWith(e)
        extent = sized(extent, crs)
        # the map's shape: a raster is cropped to fill it; anything else is widened, plus a margin
        cover = all(lyr.type() == QgsMapLayer.LayerType.RasterLayer for lyr in results)
        w, h = extent.width(), extent.height()
        if (w / h < ratio) != cover:
            w = h * ratio
        else:
            h = w / ratio
        margin = 0.5 if cover else 0.54
        c = extent.center()
        extent = QgsRectangle(c.x() - w * margin, c.y() - h * margin, c.x() + w * margin, c.y() + h * margin)

    def draw(layers):
        layers = [lyr for lyr in layers if lyr is not None and lyr.isValid()]
        if not layers:
            return None
        settings = QgsMapSettings()
        settings.setDestinationCrs(crs)
        settings.setExtent(extent)
        settings.setOutputSize(size)
        settings.setBackgroundColor(QColor(0, 0, 0, 0))
        settings.setLayers(layers)
        job = QgsMapRendererSequentialJob(settings)
        job.start()
        job.waitForFinished()
        return job.renderedImage()

    image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(fill) if fill else QColor(0, 0, 0, 0))
    painter = QPainter(image)
    for layers, opacity in ((list(background), 1.0), ([i for i in inputs if i not in results], 0.35),
                            (list(reversed(results)), 1.0)):
        drawn = draw(layers)
        if drawn is not None:
            painter.setOpacity(opacity)
            painter.drawImage(0, 0, drawn)
    if overview is not None:   # a small world map in the corner, with a box where this is
        from . import overview as ov
        painter.setOpacity(1.0)
        w = max(60, int(size.width() * 0.3))
        ov.place(painter, ov.inset([(extent, crs, True)], QSize(w, w // 2), overview), QRectF(image.rect()),
                 "bottom-right", 4)
    painter.end()
    return image


def sized(extent, crs):
    """An extent with some size: a single point gets an area around it (about 2 km, or 0.02°
    on a map in degrees); a straight line along an axis gets half its length the other way."""
    if extent.isNull():
        return QgsRectangle(-1, -1, 1, 1)
    w, h = extent.width(), extent.height()
    if w > 0 and h > 0:
        return extent
    least = max(w, h) * 0.5 or (0.02 if crs.isValid() and crs.isGeographic() else 2000.0)
    w, h = max(w, least), max(h, least)
    c = extent.center()
    return QgsRectangle(c.x() - w / 2, c.y() - h / 2, c.x() + w / 2, c.y() + h / 2)


def map_fill(theme: dict | None) -> str:
    """The colour behind a small map: white on light designs, a soft light grey on dark ones
    (the layers' own colours stay readable)."""
    bg = QColor((theme or {}).get("bg", "#FFFFFF"))
    if bg.lightness() < 110:
        return "#D9DDE2"
    paper = QColor((theme or {}).get("step", ("#FFFFFF",))[0])
    mix = QColor(int(paper.red() * 0.35 + 255 * 0.65), int(paper.green() * 0.35 + 255 * 0.65),
                 int(paper.blue() * 0.35 + 255 * 0.65))
    return mix.name()


def final_layers(controller, found: dict) -> list:
    """The recipe's results: the outputs marked final or kept, else the last step's result."""
    wanted = {step_plan.number for step_plan, param in controller.plan.outputs if param.role in ("final", "save")}
    layers = []
    for number in sorted(wanted):
        layer = found.get(number)
        if layer is not None and layer not in layers:
            layers.append(layer)
    if not layers and found:
        layers = [found[max(found)]]
    return layers


def visible_layers() -> list:
    """The project's visible layers (vector and raster), bottom first: the context behind a
    final map, as the user sees it."""
    root = QgsProject.instance().layerTreeRoot()
    return [node.layer() for node in reversed(root.findLayers())
            if node.isVisible() and node.layer() is not None and node.layer().isSpatial()]


def spread_out(layers, crs) -> bool:
    """True when the layers lie far apart: the map that fits them all is many times wider
    than the biggest of them (outputs in different parts of the world). A single point counts
    as a layer of no size."""
    boxes = [e for e in (_extent(layer, crs) for layer in layers) if e.isFinite() and not e.isNull()]
    if len(boxes) < 2:
        return False
    union = QgsRectangle(boxes[0])
    for e in boxes[1:]:
        union.combineExtentWith(e)

    def diagonal(e):
        return (e.width() ** 2 + e.height() ** 2) ** 0.5

    return diagonal(union) > 6 * max(max(diagonal(e) for e in boxes), 1e-9)


def tiles(layers, background, size: QSize, fill="#FFFFFF", gap=6) -> QImage | None:
    """Several results side by side (or in a grid), each zoomed to itself, with its name."""
    layers = [lyr for lyr in layers if lyr is not None and lyr.isValid() and lyr.isSpatial()]
    if not layers:
        return None
    n = len(layers)
    cols = n if n <= 3 else 2 if n == 4 else 3
    rows = -(-n // cols)
    tw = int((size.width() - gap * (cols - 1)) / cols)
    th = int((size.height() - gap * (rows - 1)) / rows)
    image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    for k, layer in enumerate(layers):
        x, y = (k % cols) * (tw + gap), (k // cols) * (th + gap)
        tile = render(layer, (), background, QSize(max(tw, 10), max(th, 10)), fill)
        if tile is None:
            continue
        p.drawImage(x, y, tile)
        label = QRectF(x + 6, y + th - 26, tw - 12, 20)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 215))
        font = p.font()
        font.setPixelSize(12)
        font.setBold(True)
        p.setFont(font)
        width = min(p.fontMetrics().horizontalAdvance(layer.name()) + 14, tw - 12)
        p.drawRoundedRect(QRectF(label.x(), label.y(), width, 20), 5, 5)
        p.setPen(QColor("#2B2B2B"))
        p.drawText(label.adjusted(7, 0, 0, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                   p.fontMetrics().elidedText(layer.name(), Qt.TextElideMode.ElideRight, int(width - 14)))
    p.end()
    return image


def final_map(controller, found: dict, size: QSize, background=(), fill="#FFFFFF", choice=0) -> QImage | None:
    """The finished result for an ending: the final outputs on the project's visible layers
    (faint), zoomed to the outputs; tiles side by side when they lie far apart. ``choice``: a
    step number to show only that step's result (0: all the final outputs)."""
    finals = final_layers(controller, found)
    if choice and found.get(choice) is not None:
        finals = [found[choice]]
    if not finals:
        return None
    project_crs = QgsProject.instance().crs()
    if spread_out(finals, project_crs if project_crs.isValid() else finals[0].crs()):
        return tiles(finals, list(background), size, fill)
    context = [layer for layer in visible_layers() if layer not in finals and layer not in background
               and layer.type() == QgsMapLayer.LayerType.VectorLayer]
    return render(finals, context, background, size, fill)


def output_choices(controller) -> list:
    """[(step number, name)] of the recipe's final outputs, for the "Shown at the end" menu."""
    out = []
    for step_plan, param in controller.plan.outputs:
        if param.role in ("final", "save") and all(n != step_plan.number for n, _ in out):
            out.append((step_plan.number, param.display or param.label))
    return out


def background_layers() -> list:
    """The project's visible raster and web layers (basemaps), bottom first."""
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    out = []
    for node in reversed(root.findLayers()):
        layer = node.layer()
        if node.isVisible() and layer is not None and layer.type() != QgsMapLayer.LayerType.VectorLayer:
            out.append(layer)
    return out


def result_layers(controller, rerun=True):
    """({step number: result layer}, {step number: [layers it used]}, keep): each step's
    result as a layer, made again by running the recipe where it's gone. Hold on to
    ``keep`` while using the layers (temporary layers of the run go with it)."""
    project = QgsProject.instance()
    plan = controller.plan
    found = {}        # step number -> result layer
    for step_plan in plan.steps:
        for param in _results(step_plan):
            layer = _layer_of((step_plan.step.results or {}).get(param.name), controller, project)
            if layer is not None and layer.isSpatial():
                found[step_plan.number] = layer
                break
    missing = [step_plan.number for step_plan in plan.steps if step_plan.number not in found and _results(step_plan)]
    context = None
    if missing and rerun:
        again, context = _rerun_once(controller, missing)
        for (number, _name), layer in again.items():
            if number in missing and layer.isSpatial():
                found.setdefault(number, layer)
    used = {step_plan.number: _inputs(step_plan, controller, project, found) for step_plan in plan.steps}
    return found, used, context


def starting_layers(controller) -> dict:
    """{input id: layer} for the layers the recipe starts from that are still around."""
    out = {}
    for uid, input_plan in controller.plan.inputs.items():
        layer = input_layer(controller, input_plan)
        if layer is not None and layer.isSpatial():
            out[uid] = layer
    return out


def collect(controller, with_background=False, rerun=True, overview=None, fill="#FFFFFF") -> dict:
    """{step number: QImage or None} for every step in the recipe. Cached per plan.
    ``overview``: a design's colours, to put a small world map in the corner of each map."""
    key = (id(controller.plan), bool(with_background), bool(rerun), str(sorted((overview or {}).items())), fill)
    if key in _CACHE:
        return _CACHE[key]
    _CACHE.clear()
    project = QgsProject.instance()
    plan = controller.plan
    found, _used, context = result_layers(controller, rerun)
    bg = background_layers() if with_background else []
    images = {}
    for step_plan in plan.steps:
        try:
            images[step_plan.number] = render(found.get(step_plan.number), _inputs(step_plan, controller, project, found), bg,
                                              fill=fill, overview=overview)
        except Exception:
            images[step_plan.number] = None
    del context   # temporary layers of the run go with it
    _CACHE[key] = images
    return images


def placeholder(size: QSize = SIZE, text="No map: the layer is no longer available") -> QImage:
    image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#F3F1EC"))
    painter = QPainter(image)
    painter.setPen(QColor("#8A8478"))
    painter.drawText(image.rect().adjusted(16, 0, -16, 0), int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                     text)
    painter.end()
    return image


def _rerun_once(controller, missing):
    """The recipe run again for its missing results, once per recipe: images and animations
    made from the same recipe share that run (each would otherwise run the whole recipe
    again). A change to the recipe makes a new plan, and so a new run."""
    plan = controller.plan
    if _RERUN.get("plan") is plan and _RERUN.get("missing") == tuple(missing):
        return _RERUN["again"], _RERUN["context"]
    again, context = _rerun(controller, missing)
    _RERUN.clear()
    _RERUN.update(plan=plan, missing=tuple(missing), again=again, context=context)   # keeps its layers alive
    return again, context


_RERUN = {}


def clear_cache():
    _CACHE.clear()
    _RERUN.clear()


def input_groups(plan, smallest=3) -> list:
    """The starting layers in groups: [[input uid, …], …] in the order they're first used.
    Layers of the same kind that go into the same setting of the same steps (the layers a
    Merge takes, say) form one group when there are ``smallest`` or more of them."""
    groups, order = {}, []
    for uid, input_plan in plan.inputs.items():
        key = (input_plan.layer_kind, tuple(sorted({(n, str(p)) for n, p, _v in input_plan.used_by})))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(uid)
    out = []
    for key in order:
        uids = groups[key]
        if len(uids) >= smallest:
            out.append(uids)
        else:
            out.extend([u] for u in uids)
    return out


def input_layer(controller, input_plan):
    """The layer a starting layer was recorded with, if it's still around (in the project, as
    a file, or by its name)."""
    project = QgsProject.instance()
    for _n, _p, value in input_plan.used_by:
        layer = find_layer(value, controller, project, input_plan.recorded)
        if layer is not None:
            return layer
    return None


def is_general(options) -> bool:
    """General detail: no real maps or places, only the kinds of data (for a workflow shown to
    others, or used as an example)."""
    return getattr(options, "detail", "") == "general"


def step_kind(step_plan) -> str:
    """The kind of layer a step makes (point, line, polygon, raster…)."""
    return next((param.layer_kind for param in step_plan.params if param.kind in ("output", "temp")), "layer") or "layer"


def kind_card(kind: str, size: QSize, fill="#FFFFFF") -> QImage:
    """A stand-in for a map with General detail: the kind of layer as its icon and a word."""
    from qgis.core import QgsApplication
    from qgis.PyQt.QtGui import QIcon
    from . import icons
    path, word = icons.layer_icon(kind or "layer")
    image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(fill))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    side = int(min(size.width(), size.height()) * 0.42)
    icon = QIcon(QgsApplication.iconPath(path))
    pixmap = icon.pixmap(side, side)
    painter.drawPixmap(int((size.width() - side) / 2), int(size.height() * 0.42 - side / 2), pixmap)
    font = painter.font()
    font.setPixelSize(max(10, int(size.height() * 0.11)))
    painter.setFont(font)
    painter.setPen(QColor("#6A6F75"))
    painter.drawText(QRectF(0, size.height() * 0.66, size.width(), size.height() * 0.3),
                     int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), word.capitalize())
    painter.end()
    return image
