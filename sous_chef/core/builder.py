"""Turn recorded steps into a general Processing model (.model3).

1. analyse(steps, choices, ctx) works out the data flow between the chosen steps:
   - a layer input that matches an earlier step's result becomes a link;
   - an extent that matches a layer's extent becomes a link to that layer;
   - any other layer becomes a model input (one per distinct layer), with a
     descriptive name ("Line layer for Buffer");
   - other settings stay fixed unless the user chose "ask when the model runs";
   - results nobody uses are final outputs, the others intermediate, unless the
     user chose otherwise.
2. build_model(plan, ...) creates the QgsProcessingModelAlgorithm.

Everything the user decides in the review window lives in a Choices object,
keyed by stable ids ("uids") so choices survive re-analysis:
  layer inputs     layer:<source>
  asked settings   set:<entry id>:<parameter>
  outputs          out:<entry id>:<parameter>
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsExpression,
    QgsMapLayer,
    QgsProcessing,
    QgsProcessingModelAlgorithm,
    QgsProcessingModelChildAlgorithm,
    QgsProcessingModelChildParameterSource,
    QgsProcessingModelOutput,
    QgsProcessingModelParameter,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterField,
    QgsProcessingParameterMapLayer,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsProcessingProvider,
    QgsProject,
    QgsProperty,
    QgsRectangle,
)
from qgis.PyQt.QtCore import QPointF

from .history import Step
from . import log

LAYER_TYPES = {"source", "vector", "raster", "layer", "mesh", "pointcloud", "vectortile",
               "annotation", "multilayer"}
EXPOSABLE_TYPES = {"number", "distance", "area", "volume", "duration", "scale", "string",
                   "expression", "enum", "boolean", "crs", "extent", "point", "geometry",
                   "field", "band", "datetime", "file", "color", "range", "matrix"}
STYLEABLE_OUTPUTS = {"sink", "vectorDestination", "rasterDestination", "vectorTileDestination",
                     "pointCloudDestination"}
VECTOR_LAYER_TYPES = {QgsProcessing.SourceType.TypeVectorAnyGeometry, QgsProcessing.SourceType.TypeVectorPoint,
                      QgsProcessing.SourceType.TypeVectorLine, QgsProcessing.SourceType.TypeVectorPolygon,
                      QgsProcessing.SourceType.TypeVector}
DATA_RENDERERS = {"categorizedSymbol", "graduatedSymbol", "RuleRenderer", "paletted", "singlebandpseudocolor"}
EXTENT_RE = re.compile(r"^\s*([-+\d.eE]+)\s*,\s*([-+\d.eE]+)\s*,\s*([-+\d.eE]+)\s*,\s*([-+\d.eE]+)\s*(?:\[(.*)\])?\s*$")


# ------------------------------------------------------------- source matching

def norm_source(value) -> tuple | None:
    """(file or uri, layername or None) used to compare layer references."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    for scheme in ("memory://", "virtual://"):   # how Processing names memory and virtual layers it was given
        if s.startswith(scheme):
            s = s[len(scheme):]
    main, _, rest = s.partition("|")
    match = re.search(r"layername=([^|]+)", rest)
    layername = match.group(1) if match else None
    if os.path.isabs(main) or re.match(r"^[A-Za-z]:[\\/]", main):
        main = os.path.normcase(os.path.normpath(main))
    return main, layername


def same_source(a, b) -> bool:
    return bool(a and b) and a[0] == b[0] and (a[1] is None or b[1] is None or a[1] == b[1])


def source_uid(key) -> str:
    return "layer:" + (key[0] if key else "") + ("|" + key[1] if key and key[1] else "")


def slug(text: str, fallback: str = "input") -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    if not s:
        s = fallback
    return s if not s[0].isdigit() else f"{fallback}_{s}"


# ------------------------------------------------------- choices and context

@dataclass
class Choices:
    """Everything the user decides in the review window."""
    ask: set = field(default_factory=set)            # (entry_id, parameter) asked when the model runs
    fixed: set = field(default_factory=set)          # (entry_id, parameter) the user set back to fixed
    names: dict = field(default_factory=dict)        # uid -> name shown when the model runs
    helps: dict = field(default_factory=dict)        # uid -> help text
    roles: dict = field(default_factory=dict)        # output uid -> final | intermediate | save
    unstyled: set = field(default_factory=set)       # output uids that use the default style
    edits_as_input: set = field(default_factory=set)  # indices of hand edits: ask for the edited layer
    batch_modes: dict = field(default_factory=dict)  # batch id -> rows | template
    notes: dict = field(default_factory=dict)        # entry_id -> a note on the step (a comment in the model)
    starts: set = field(default_factory=set)         # entry_ids of steps chosen as the recipe's starting point
    description: str = ""

    def asked(self, entry_id, param, default=False) -> bool:
        if (entry_id, param) in self.ask:
            return True
        if (entry_id, param) in self.fixed:
            return False
        return default


@dataclass
class Context:
    """What is known about the session the steps were recorded in."""
    project: QgsProject | None = None
    snapshot: dict = field(default_factory=dict)     # layer id -> (source, name)
    extents: list = field(default_factory=list)      # (source, name, (xmin, xmax, ymin, ymax), crs authid)
    edits: list = field(default_factory=list)        # ManualEdit
    converted_edits: set = field(default_factory=set)
    excluded_steps: list = field(default_factory=list)

    def __post_init__(self):
        self.project = self.project or QgsProject.instance()


# ------------------------------------------------------------------- the plan

@dataclass
class ParamPlan:
    name: str                  # algorithm parameter name
    label: str                 # its description, for the UI
    kind: str                  # link | input | static | output | temp
    value: object = None
    sources: list = field(default_factory=list)  # ("link", step no, output) or ("input", input uid)
    exposable: bool = False
    expose: bool = False
    detail: str = ""
    uid: str = ""              # asked settings and outputs
    display: str = ""          # name shown when the model runs
    default_display: str = ""
    help: str = ""
    style_layer_id: str = ""
    style_file: str = ""       # a .qml kept with a model that was opened (instead of a layer in the project)
    keep_style: bool = False
    role: str = ""             # outputs: final | intermediate | save
    shown_value: str = ""
    is_default: bool = False   # settings: the tool's own default value
    layer_kind: str = ""       # outputs: point | line | polygon | raster | table | layer
    unit: object = None        # distance settings: the unit (Qgis.DistanceUnit) when known
    column: str = ""           # columns: the recorded column name
    column_origin: tuple | None = None   # ("input", input uid, checked) or ("made", FIELD_NAME uid, step)
    column_note: str = ""      # where the column comes from, or why it can't be asked
    follows: str = ""          # uid of the asked new-column name this column follows
    format: str = ""           # results: file format of the recorded result


@dataclass
class StepPlan:
    step: Step
    number: int
    params: list = field(default_factory=list)
    output_labels: dict = field(default_factory=dict)
    note: str = ""             # the user's note on this step

    def param(self, name):
        return next((p for p in self.params if p.name == name), None)


@dataclass
class InputPlan:
    uid: str
    label: str                 # name shown when the model runs
    default_label: str
    help: str
    definition: QgsProcessingParameterDefinition
    recorded: str = ""         # layer it was recorded with
    accepts: str = ""
    used_by: list = field(default_factory=list)  # (step number, parameter, recorded value)
    key: str = ""              # model parameter name (set when building)
    layer_kind: str = ""       # point | line | polygon | raster | table | layer
    format: str = ""           # file format of the recorded layer, e.g. "GeoPackage (.gpkg)"


@dataclass
class ModelPlan:
    steps: list = field(default_factory=list)
    inputs: dict = field(default_factory=dict)   # uid -> InputPlan
    warnings: list = field(default_factory=list)
    groups: list = field(default_factory=list)   # lists of step numbers (repeated steps / batch rows)
    parts: list = field(default_factory=list)    # sets of step numbers not connected to each other
    links: list = field(default_factory=list)    # (from step no, to step no)
    description: str = ""

    @property
    def outputs(self) -> list:
        return [(step_plan, p) for step_plan in self.steps for p in step_plan.params
                if p.kind in ("output", "temp") and p.role in ("final", "save")]

    @property
    def asked(self) -> list:
        return [(step_plan, p) for step_plan in self.steps for p in step_plan.params if p.expose]

    def summary(self) -> str:
        outs = len(self.outputs)
        return (f"{len(self.inputs)} layer{'s' if len(self.inputs) != 1 else ''} + "
                f"{len(self.asked)} setting{'s' if len(self.asked) != 1 else ''} to enter · "
                f"{len(self.steps)} step{'s' if len(self.steps) != 1 else ''} · "
                f"{outs} output{'s' if outs != 1 else ''}")


# ------------------------------------------------------------ naming helpers

def _layer_for(key, project: QgsProject):
    for layer in project.mapLayers().values():
        if same_source(norm_source(layer.source()), key):
            return layer
    if key and key[1] is None and project.mapLayer(key[0]) is not None:   # a layer id (runs from scripts)
        return project.mapLayer(key[0])
    return None


def _recorded_name(key, value, project, snapshot) -> str:
    layer = _layer_for(key, project)
    if layer is not None:
        return layer.name()
    for source, name in snapshot.values():
        if same_source(norm_source(source), key):
            return name
    if key and key[1]:
        return key[1]
    main = key[0] if key else str(value)
    return readable_name(os.path.splitext(os.path.basename(main.split("?")[0]))[0]) or "a layer"


def _kind_of_layer(key, definition, project, value) -> tuple[str, str]:
    """('Line layer', 'line layers') from the recorded layer, else from what the tool accepts."""
    layer = _layer_for(key, project)
    if layer is None and isinstance(value, str) and value.startswith(("memory://", "Point", "LineString", "Polygon",
                                                                      "MultiPoint", "MultiLineString", "MultiPolygon")):
        uri = value[len("memory://"):] if value.startswith("memory://") else value
        geom = uri.split("?")[0].lower()
        for word, label in (("point", "Point"), ("line", "Line"), ("polygon", "Polygon")):
            if word in geom:
                return f"{label} layer", f"{label.lower()} layers"
    if layer is not None:
        if layer.type() == Qgis.LayerType.Raster:
            return "Raster layer", "raster layers"
        if layer.type() == Qgis.LayerType.Vector:
            gtype = layer.geometryType()
            names = {Qgis.GeometryType.Point: "Point", Qgis.GeometryType.Line: "Line", Qgis.GeometryType.Polygon: "Polygon"}
            label = names.get(gtype)
            if label:
                return f"{label} layer", f"{label.lower()} layers"
            return "Table", "tables (no geometry)"
    if definition is not None and definition.type() == "raster":
        return "Raster layer", "raster layers"
    types = []
    if definition is not None and hasattr(definition, "dataTypes"):
        types = list(definition.dataTypes())
    simple = {QgsProcessing.SourceType.TypeVectorPoint: "Point", QgsProcessing.SourceType.TypeVectorLine: "Line",
              QgsProcessing.SourceType.TypeVectorPolygon: "Polygon"}
    if len(types) == 1 and types[0] in simple:
        label = simple[types[0]]
        return f"{label} layer", f"{label.lower()} layers"
    return "Layer", "layers"


FIELD_CALCULATORS = {"native:fieldcalculator", "qgis:fieldcalculator"}
DROPS_COLUMNS = {"native:refactorfields", "native:aggregate", "native:retainfields", "native:deletecolumn",
                 "native:renametablefield", "qgis:refactorfields", "native:countpointsinpolygon"}


def _fields_of_input(input_plan, project, snapshot):
    """Column names of the layer an input was recorded with; None when it can't be checked."""
    for _n, _p, value in input_plan.used_by:
        if not isinstance(value, str):
            continue
        layer = _layer_for(norm_source(value), project)
        if layer is None and value in snapshot:
            layer = _layer_for(norm_source(snapshot[value][0]), project)
        if layer is not None and hasattr(layer, "fields"):
            return set(layer.fields().names())
    return None


def column_origin(plan, step_plan, layer_param, column, project, snapshot, depth=0):
    """Where ``column`` of the layer in ``layer_param`` of step ``sp`` comes from, followed back
    through the steps: (("input", uid, checked) | ("made", FIELD_NAME uid, step number) | None, note)."""
    lp = step_plan.param(layer_param)
    if lp is None or not lp.sources or depth > 30:
        return None, "its layer isn't known"
    origins = set()
    for src in lp.sources:
        if src[0] == "input":
            fields = _fields_of_input(plan.inputs[src[1]], project, snapshot)
            if fields is None or column in fields:
                origins.add(("input", src[1], fields is not None))
        elif src[0] == "link" and 0 < src[1] <= len(plan.steps):
            prev = plan.steps[src[1] - 1]
            if prev.step.alg_id in FIELD_CALCULATORS and prev.step.inputs.get("FIELD_NAME") == column:
                made = prev.param("FIELD_NAME")
                origins.add(("made", made.uid if made else "", prev.number))
                continue
            if prev.step.alg_id in DROPS_COLUMNS:
                continue
            result = (prev.step.results or {}).get(src[2])
            layer = project.mapLayer(result) if isinstance(result, str) else None
            if layer is None and isinstance(result, str) and result in snapshot:
                layer = _layer_for(norm_source(snapshot[result][0]), project)
            if layer is not None and hasattr(layer, "fields") and column not in layer.fields().names():
                continue   # that step's result doesn't have it
            for q in prev.params:
                if q.kind in ("input", "link"):
                    origin, _note = column_origin(plan, prev, q.name, column, project, snapshot, depth + 1)
                    if origin:
                        origins.add(origin)
    if len(origins) == 1:
        return origins.pop(), ""
    if len(origins) > 1:
        return None, "the column is in more than one of the layers the steps start from"
    return None, "the column couldn't be traced back to a layer the recipe asks for"


def _expression_columns(formula) -> list:
    if not isinstance(formula, str) or not formula:
        return []
    expression = QgsExpression(formula)
    if expression.hasParserError():
        return []
    return sorted(c for c in expression.referencedColumns() if c and not c.startswith("#"))


def _plan_columns(plan, choices, project, snapshot, default_asks):
    """Column settings, and the columns Field Calculator expressions read: can each be asked when
    the model runs (tied to the input layer it came from), or does it follow a column the model makes?"""

    def apply(step_plan, param, origin, note):
        param.column_origin = origin
        if origin and origin[0] == "input":
            param.exposable = True
            checked = "" if origin[2] else " (its columns couldn't be checked)"
            param.column_note = f"comes from the ingredient '{plan.inputs[origin[1]].label}'{checked}"
        elif origin and origin[0] == "made":
            param.exposable = False
            param.follows = origin[1]
            param.column_note = f"made by step {origin[2]}: follows that step's new column name"
        else:
            param.exposable = False
            param.column_note = f"stays fixed: {note}"
        param.expose = param.exposable and choices.asked(step_plan.step.entry_id, param.name,
                                                         (step_plan.step.entry_id, param.name) in default_asks)

    for step_plan in plan.steps:
        alg_name = step_plan.step.alg.displayName()
        for param in list(step_plan.params):
            if param.kind != "static":
                continue
            d = step_plan.step.alg.parameterDefinition(param.name)
            if d is None or d.type() != "field" or not isinstance(param.value, str) or not param.value:
                continue
            if hasattr(d, "allowMultiple") and d.allowMultiple():
                continue
            param.column = param.value
            origin, note = column_origin(plan, step_plan, d.parentLayerParameterName(), param.value, project, snapshot)
            apply(step_plan, param, origin, note)
        if step_plan.step.alg_id in FIELD_CALCULATORS:
            for col in _expression_columns(step_plan.step.inputs.get("FORMULA")):
                param = ParamPlan(f"FORMULA#{col}", f'Column "{col}" in the expression', "static", col, shown_value=col)
                param.uid = f"set:{step_plan.step.entry_id}:FORMULA#{col}"
                param.column = col
                param.default_display = f"Column for {col} ({alg_name}, step {step_plan.number})"
                param.display = choices.names.get(param.uid) or param.default_display
                param.help = choices.helps.get(param.uid, f"Recorded with the column {col}")
                param.detail = col
                origin, note = column_origin(plan, step_plan, "INPUT", col, project, snapshot)
                apply(step_plan, param, origin, note)
                step_plan.params.append(param)


def formula_expression(formula: str, replacements: dict) -> str:
    """A QGIS expression that builds ``formula`` when the model runs, with each column in
    ``replacements`` (column -> model parameter name) swapped for the column chosen there."""
    parts, i, n, text = [], 0, len(formula), ""

    def flush():
        nonlocal text
        if text:
            parts.append("'" + text.replace("'", "''") + "'")
            text = ""

    while i < n:
        ch = formula[i]
        if ch == "'":                                  # a text literal: copied as it is
            j = i + 1
            while j < n:
                if formula[j] == "'" and j + 1 < n and formula[j + 1] == "'":
                    j += 2
                    continue
                if formula[j] == "'":
                    break
                j += 1
            text += formula[i:j + 1]
            i = j + 1
            continue
        if ch == '"':                                  # a quoted column name
            j = formula.find('"', i + 1)
            j = n - 1 if j < 0 else j
            name = formula[i + 1:j]
            if name in replacements:
                flush()
                parts.append(f"'\"' || @{replacements[name]} || '\"'")
            else:
                text += formula[i:j + 1]
            i = j + 1
            continue
        match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", formula[i:])
        if match and (i == 0 or not (formula[i - 1].isalnum() or formula[i - 1] in "_@$")):
            word = match.group(0)
            after = formula[i + len(word):].lstrip()
            if word in replacements and not after.startswith("("):
                flush()
                parts.append(f"'\"' || @{replacements[word]} || '\"'")
            else:
                text += word
            i += len(word)
            continue
        text += ch
        i += 1
    flush()
    return " || ".join(parts) if parts else "''"


FORMAT_NAMES = {
    "gpkg": "GeoPackage", "shp": "Shapefile", "geojson": "GeoJSON", "json": "GeoJSON", "csv": "CSV",
    "kml": "KML", "kmz": "KMZ", "gml": "GML", "fgb": "FlatGeobuf", "parquet": "GeoParquet", "tab": "MapInfo TAB",
    "dxf": "DXF", "gpx": "GPX", "sqlite": "SpatiaLite", "xlsx": "Excel", "ods": "OpenDocument spreadsheet",
    "tif": "GeoTIFF", "tiff": "GeoTIFF", "vrt": "Virtual raster", "asc": "ASCII grid", "nc": "NetCDF",
    "jp2": "JPEG 2000", "png": "PNG", "jpg": "JPEG", "img": "ERDAS Imagine", "dem": "DEM", "txt": "text",
}
DRIVER_NAMES = {"GPKG": "GeoPackage", "ESRI Shapefile": "Shapefile", "GeoJSON": "GeoJSON", "CSV": "CSV",
                "FlatGeobuf": "FlatGeobuf", "KML": "KML", "LIBKML": "KML", "GML": "GML", "SQLite": "SpatiaLite",
                "MapInfo File": "MapInfo TAB", "Parquet": "GeoParquet", "XLSX": "Excel", "GPX": "GPX", "DXF": "DXF"}
PROVIDER_NAMES = {"memory": "temporary layer (not saved)", "postgres": "PostgreSQL/PostGIS database",
                  "spatialite": "SpatiaLite database", "delimitedtext": "delimited text (CSV)", "wms": "web map (WMS/WMTS)",
                  "wfs": "web features (WFS)", "arcgisfeatureserver": "ArcGIS feature service",
                  "virtual": "virtual layer", "xyzvectortiles": "vector tiles", "mssql": "SQL Server database",
                  "oracle": "Oracle database", "WFS": "web features (WFS)"}


def layer_format(layer, path: str = "") -> str:
    """The file format of a layer, e.g. "GeoPackage (.gpkg)" or "temporary layer (not saved)"."""
    provider = layer.providerType() if layer is not None else ""
    source = (layer.source() if layer is not None else "") or path or ""
    if provider in PROVIDER_NAMES:
        return PROVIDER_NAMES[provider]
    if not layer and (source.startswith(("memory", "Point", "LineString", "Polygon", "Multi", "None"))
                      or source == "TEMPORARY_OUTPUT"):
        return PROVIDER_NAMES["memory"]
    ext = os.path.splitext(source.split("|")[0].split("?")[0])[1].lstrip(".").lower()
    name = ""
    if layer is not None and provider == "ogr":
        try:
            name = DRIVER_NAMES.get(layer.dataProvider().storageType(), "")
        except Exception:
            name = ""
    name = name or FORMAT_NAMES.get(ext, ext.upper() if ext else "")
    if not name:
        return provider or ""
    return f"{name} (.{ext})" if ext and name.lower() != ext else name


def _output_kind(result, keys, d, project, snapshot) -> str:
    """point | line | polygon | raster | table | layer, for a step's result."""
    value = snapshot[result][0] if isinstance(result, str) and result in snapshot else result
    layer = project.mapLayer(result) if isinstance(result, str) else None
    key = norm_source(layer.source()) if layer is not None else (keys[0] if keys else None)
    if layer is not None or isinstance(value, str):
        kind, _ = _kind_of_layer(key, None, project, value)
        if kind != "Layer":
            return kind.split()[0].lower()
    if d.type() == "rasterDestination":
        return "raster"
    if hasattr(d, "dataType"):
        return {QgsProcessing.SourceType.TypeVectorPoint: "point", QgsProcessing.SourceType.TypeVectorLine: "line",
                QgsProcessing.SourceType.TypeVectorPolygon: "polygon",
                QgsProcessing.SourceType.TypeVector: "table"}.get(d.dataType(), "layer")
    return "layer"


def _input_default_name(kind: str, param_def, alg_name: str) -> str:
    if param_def is None or param_def.name() in ("INPUT", "LAYERS", "SOURCE"):
        return f"{kind} for {alg_name}"
    return f"{kind}, {param_def.description().lower()} for {alg_name}"


def _unit_enum(d, step, project, extents):
    """The distance unit of a setting: the tool's own unit, else the map units of the layer
    (or CRS) it applies to. None when unknown."""
    if d is None or not hasattr(d, "defaultUnit") or d.type() != "distance":
        return None
    try:
        own = d.defaultUnit()
        if own != Qgis.DistanceUnit.Unknown:
            return own
        if not hasattr(d, "parentParameterName") or step is None:
            return None
        parent = step.inputs.get(d.parentParameterName())
        parent = parent[0] if isinstance(parent, list) and parent else parent
        crs = None
        if isinstance(parent, QgsCoordinateReferenceSystem):
            crs = parent
        elif isinstance(parent, str) and parent and ":" in parent and "/" not in parent and "\\" not in parent:
            maybe = QgsCoordinateReferenceSystem(parent.split(" ")[0])   # a CRS setting, e.g. "EPSG:3857"
            crs = maybe if maybe.isValid() else None
        key = norm_source(parent) if parent and crs is None else None
        layer = _layer_for(key, project) if key else None
        if layer is not None:
            crs = layer.crs()
        elif key:
            for source, _n, _e, authid in extents:
                if authid and same_source(norm_source(source), key):
                    crs = QgsCoordinateReferenceSystem(authid)
                    break
        if crs is not None and crs.isValid():
            return crs.mapUnits()
    except Exception:
        return None
    return None


def _unit_for(d, step, project, extents) -> str:
    """Abbreviated unit for a numeric setting, e.g. "m"."""
    if d is None or not hasattr(d, "defaultUnit"):
        return ""
    try:
        from qgis.core import QgsUnitTypes
        unit = _unit_enum(d, step, project, extents)
        if unit is None:
            own = d.defaultUnit()
            text = QgsUnitTypes.toAbbreviatedString(own)
            return text if text and "unknown" not in text.lower() and text not in ("<unknown>", "?") else ""
        return QgsUnitTypes.toAbbreviatedString(unit)
    except Exception:
        return ""


def _show_value(d, value, unit: str = "") -> str:
    shown = value
    if d is not None and d.type() == "enum" and hasattr(d, "options"):
        opts = d.options()
        picks = value if isinstance(value, list) else [value]
        try:
            shown = ", ".join(opts[int(v)] for v in picks)
        except (ValueError, TypeError, IndexError):
            shown = value
    if isinstance(shown, bool):
        shown = "Yes" if shown else "No"
    if isinstance(shown, float) and shown.is_integer():
        shown = int(shown)
    if unit and isinstance(shown, (int, float)) and not isinstance(shown, bool):
        shown = f"{shown} {unit}"
    text = str(shown)
    return text if len(text) <= 60 else text[:57] + "…"


# ------------------------------------------------------------- extent matching

def parse_extent(value):
    if not isinstance(value, str):
        return None
    m = EXTENT_RE.match(value)
    if not m:
        return None
    try:
        xmin, xmax, ymin, ymax = (float(m.group(i)) for i in range(1, 5))
    except ValueError:
        return None
    return (xmin, xmax, ymin, ymax), (m.group(5) or "").strip()


def extent_candidates(ctx: Context) -> list:
    """(source, name, (xmin, xmax, ymin, ymax), crs authid) of every layer known."""
    out = list(ctx.extents)
    for layer in ctx.project.mapLayers().values():
        if layer.isValid():
            e = layer.extent()
            out.append((layer.source(), layer.name(), (e.xMinimum(), e.xMaximum(), e.yMinimum(), e.yMaximum()),
                        layer.crs().authid()))
    return out


def match_extent(value, ctx: Context):
    """The (source, name) of the layer whose extent the recorded extent equals, or None."""
    parsed = parse_extent(value)
    if not parsed:
        return None
    (xmin, xmax, ymin, ymax), crs = parsed
    size = max(abs(xmax - xmin), abs(ymax - ymin), 1e-9)
    tol = size * 1e-6
    for source, name, (a, b, c, d), lcrs in reversed(extent_candidates(ctx)):
        if crs and lcrs and crs != lcrs:
            try:
                ct = QgsCoordinateTransform(QgsCoordinateReferenceSystem(lcrs), QgsCoordinateReferenceSystem(crs),
                                            ctx.project.transformContext())
                r = ct.transformBoundingBox(QgsRectangle(a, c, b, d))
                a, b, c, d = r.xMinimum(), r.xMaximum(), r.yMinimum(), r.yMaximum()
            except Exception as error:   # safe to carry on: noted in the log
                log.ignored(error)
                continue
        if all(abs(p - q) <= tol for p, q in ((a, xmin), (b, xmax), (c, ymin), (d, ymax))):
            return source, name
    return None


# --------------------------------------------------------------- result keys

LAYER_ID_SUFFIX = re.compile(r"_[0-9a-fA-F]{8}(_[0-9a-fA-F]{4}){3}_[0-9a-fA-F]{12}$")


def readable_name(text: str) -> str:
    """A layer name from a layer id or file name: QGIS makes ids from the name and a long
    unique code ("Grid_834b1805_981e_4d50_b2c1_4fb…"), which isn't meant to be read."""
    return LAYER_ID_SUFFIX.sub("", text or "")


def _result_keys(value, project: QgsProject, snapshot: dict) -> tuple[list, str]:
    """Source keys a later step may use to refer to this result, and its name."""
    if not isinstance(value, str) or not value:
        return [], ""
    layer = project.mapLayer(value)
    if layer is not None:
        keys = [norm_source(layer.source()), (value, None)]
        if value in snapshot:  # source when it was made (it changes if the project is reloaded)
            keys.append(norm_source(snapshot[value][0]))
        return keys, layer.name()
    if value in snapshot:
        source, name = snapshot[value]
        return [norm_source(source), (value, None)], name
    key = norm_source(value)
    if value.startswith("souschef-"):
        return [key], ""
    layer = _layer_for(key, project)
    if layer is not None:
        return [key], layer.name()
    base = os.path.splitext(os.path.basename(value.split("|")[0].split("?")[0]))[0]
    return [key], readable_name(base)


def _single_layer_definition(item_def, key: str, label: str):
    layer_type = item_def.layerType() if hasattr(item_def, "layerType") else None
    if layer_type in VECTOR_LAYER_TYPES:
        return QgsProcessingParameterVectorLayer(key, label, [layer_type])
    if layer_type == QgsProcessing.SourceType.TypeRaster:
        return QgsProcessingParameterRasterLayer(key, label)
    return QgsProcessingParameterMapLayer(key, label)


# ------------------------------------------------------------------ analysis

def _batch_template_asks(steps: list) -> set:
    """For batches modelled as one step: settings that differed between the rows."""
    asks = set()
    by_batch = {}
    for s in steps:
        if s.batch_id:
            by_batch.setdefault(s.batch_id, []).append(s)
    for rows in by_batch.values():
        first = rows[0]
        if first.alg is None:
            continue
        for d in first.alg.parameterDefinitions():
            if d.isDestination() or d.type() in LAYER_TYPES:
                continue
            values = {str(r.inputs.get(d.name())) for r in rows}
            if len(values) > 1 and d.type() in EXPOSABLE_TYPES:
                asks.add((first.entry_id, d.name()))
    return asks


def apply_batch_modes(steps: list, choices: Choices) -> tuple[list, set, list]:
    """Steps after applying each batch's mode, extra default asks, and description notes."""
    kept, notes = [], []
    seen = set()
    asks = _batch_template_asks(steps)
    for s in steps:
        if not s.batch_id:
            kept.append(s)
            continue
        mode = choices.batch_modes.get(s.batch_id, "rows")
        if mode == "template":
            if s.batch_id in seen:
                continue
            seen.add(s.batch_id)
            rows = sum(1 for x in steps if x.batch_id == s.batch_id)
            notes.append(f"Recorded as a batch of {rows} runs of {s.alg.displayName() if s.alg else s.alg_id}: "
                         "to repeat that, right-click this recipe in the Recipe Book (Processing Toolbox) and choose "
                         "“Execute as Batch Process”.")
        kept.append(s)
    template_ids = {s.entry_id for s in kept if s.batch_id and choices.batch_modes.get(s.batch_id) == "template"}
    return kept, {a for a in asks if a[0] in template_ids}, notes


def analyse(steps: list, choices: Choices | None = None, ctx: Context | None = None) -> ModelPlan:
    """Plan a model from ``steps`` (the ones the user kept, in order)."""
    from .prepare import selected_only  # avoid import cycle

    choices = choices or Choices()
    ctx = ctx or Context()
    project, snapshot = ctx.project, ctx.snapshot
    steps, default_asks, batch_notes = apply_batch_modes([s for s in steps if s.ok], choices)

    excluded_keys = []
    for other in ctx.excluded_steps:
        own_inputs = [norm_source(v) for name, v in other.inputs.items() if isinstance(v, str)
                      and not (other.alg is not None and other.alg.parameterDefinition(name) is not None
                               and other.alg.parameterDefinition(name).isDestination())]   # what it read, not wrote
        if other.entry_id in choices.starts:   # left out on purpose: its result is where the recipe starts
            continue
        for value in (other.results or {}).values():
            for key in _result_keys(value, project, snapshot)[0]:
                if not any(same_source(key, k) for k in own_inputs):
                    excluded_keys.append(key)

    plan = ModelPlan()
    produced = []          # (source key, step number, output name, label)
    used = set()
    alg_counts = {}
    for s in steps:
        alg_counts[s.alg_id] = alg_counts.get(s.alg_id, 0) + 1

    def add_input(key, value, d, alg_name, number, param_name, hand_edit=None):
        uid = source_uid(key) + ("#edited" if hand_edit is not None else "")
        input_plan = plan.inputs.get(uid)
        if input_plan is None:
            recorded = _recorded_name(key, value, project, snapshot)
            kind, accepts = _kind_of_layer(key, d, project, value)
            default = _input_default_name(kind, d, alg_name)
            if hand_edit is not None:
                default = f"{kind} (edited by hand) for {alg_name}"
            if d is None or d.type() == "extent":
                definition = QgsProcessingParameterMapLayer("x", default)
            elif d.type() == "multilayer":
                definition = _single_layer_definition(d, "x", default)
            else:
                definition = d.clone()
            definition.setDefaultValue(None)
            definition.setFlags(definition.flags() & ~QgsProcessingParameterDefinition.Flag.FlagOptional)
            input_plan = InputPlan(uid, choices.names.get(uid) or default, default,
                                   choices.helps.get(uid, f"Recorded with: {recorded}"), definition, recorded, accepts)
            input_plan.layer_kind = kind.split()[0].split(",")[0].lower()
            input_plan.format = layer_format(_layer_for(key, project) or (project.mapLayer(value) if isinstance(value,
                                                                                                                str) else None),
                                             value if isinstance(value, str) else "")
            plan.inputs[uid] = input_plan
        input_plan.used_by.append((number, param_name, value))
        return input_plan

    for number, step in enumerate(steps, start=1):
        step_plan = StepPlan(step, number)
        step_plan.note = choices.notes.get(step.entry_id, "")
        plan.steps.append(step_plan)
        alg = step.alg
        alg_name = alg.displayName()
        repeated = alg_counts.get(step.alg_id, 0) > 1
        for d in alg.parameterDefinitions():
            name = d.name()
            if name not in step.inputs:
                continue
            value = step.inputs[name]
            unit = _unit_for(d, step, project, ctx.extents) if d.type() in ("distance", "area", "volume", "duration") else ""
            pp = ParamPlan(name, d.description(), "static", value, shown_value=_show_value(d, value, unit))
            if d.type() == "distance":
                pp.unit = _unit_enum(d, step, project, ctx.extents)
            default_value = d.defaultValue()
            pp.is_default = (value in (None, "") and default_value in (None, "")) or \
                _show_value(d, default_value, unit) == pp.shown_value

            # ---- results
            if d.isDestination():
                result = (step.results or {}).get(name)
                keys, label = _result_keys(result, project, snapshot)
                label = label or d.description()
                pp.kind = "output"
                pp.uid = f"out:{step.entry_id}:{name}"
                step_plan.output_labels[name] = label
                pp.default_display = label
                if isinstance(result, str) and project.mapLayer(result) is not None:
                    pp.style_layer_id = result
                elif keys:
                    match = _layer_for(keys[0], project)
                    pp.style_layer_id = match.id() if match else ""
                if d.type() not in STYLEABLE_OUTPUTS:
                    pp.style_layer_id = ""
                pp.style_file = step.opened.get("style_files", {}).get(name, "")
                pp.keep_style = bool(pp.style_layer_id or pp.style_file) and pp.uid not in choices.unstyled
                pp.layer_kind = _output_kind(result, keys, d, project, snapshot)
                result_layer = project.mapLayer(result) if isinstance(result, str) else None
                if result_layer is None and keys:
                    result_layer = _layer_for(keys[0], project)
                recorded_path = value if isinstance(value, str) and os.path.splitext(value.split("|")[0])[1] else ""
                pp.format = layer_format(result_layer, recorded_path or (snapshot.get(result, ("",))[0]
                                                                         if isinstance(result, str) else ""))
                for key in keys:
                    produced.append((key, number, name, label))
                step_plan.params.append(pp)
                continue

            # ---- layers (and extents taken from a layer)
            extent_layer = match_extent(value, ctx) if d.type() == "extent" else None
            if d.type() == "extent" and isinstance(value, str) and "souschef-" in value:
                extent_layer = (value, "")   # an opened model: the extent comes from a layer or a step
            if d.type() == "extent" and extent_layer is None and isinstance(value, str) and (
                    value in ctx.snapshot or any(same_source(p[0], norm_source(value)) for p in produced)
                    or any(same_source(norm_source(src), norm_source(value)) for src, _n in ctx.snapshot.values())):
                extent_layer = (value, "")   # a recipe's step whose extent is taken from a layer or a step
            if d.type() in LAYER_TYPES or extent_layer:
                if d.type() in LAYER_TYPES and name not in step.selected_handled and selected_only(step.python_command, name):
                    plan.warnings.append(
                        f"Step {number} ({alg_name}) used only the selected features of its "
                        f"'{d.description()}'. The recipe uses all features: add a Processing step such "
                        "as 'Extract by expression' if only some features should be used.")
                items = [extent_layer[0]] if extent_layer else (value if isinstance(value, list) else [value])
                descriptions = []
                for item in items:
                    key = norm_source(item)
                    link = next((p for p in reversed(produced) if same_source(p[0], key)), None)
                    hand_edit = next((e for i, e in enumerate(ctx.edits) if i in choices.edits_as_input
                                      and same_source(norm_source(e.source), key) and step.timestamp is not None
                                      and step.timestamp.addSecs(1) > e.timestamp), None)
                    if hand_edit is not None:
                        link = None
                    if link is not None:
                        pp.sources.append(("link", link[1], link[2]))
                        used.add((link[1], link[2]))
                        plan.links.append((link[1], number))
                        descriptions.append(f"{'extent of ' if extent_layer else ''}step {link[1]} result ({link[3]})")
                        continue
                    input_plan = add_input(key, item, None if extent_layer else d, alg_name, number, name, hand_edit)
                    pp.sources.append(("input", input_plan.uid))
                    descriptions.append(f"{'extent of ' if extent_layer else ''}ingredient '{input_plan.label}'")
                pp.kind = "link" if all(s[0] == "link" for s in pp.sources) else "input"
                pp.detail = "; ".join(descriptions)
                if extent_layer:
                    pp.detail += " (detected: the recorded extent matches this layer)"
                step_plan.params.append(pp)
                continue

            # ---- other settings: fixed, or asked when the model runs
            pp.exposable = d.type() in EXPOSABLE_TYPES
            if d.type() == "field":
                pp.exposable = False   # decided in _plan_columns, once the whole chain is known
            pp.expose = pp.exposable and choices.asked(step.entry_id, name, (step.entry_id, name) in default_asks)
            pp.uid = f"set:{step.entry_id}:{name}"
            default = f"{d.description()} for {alg_name}" + (f" (step {number})" if repeated else "")
            pp.default_display = default
            pp.display = choices.names.get(pp.uid) or default
            pp.help = choices.helps.get(pp.uid, "")
            pp.detail = pp.shown_value
            if d.type() == "extent" and value not in (None, "") and not pp.expose:
                plan.warnings.append(
                    f"Step {number} ({alg_name}): the extent was drawn or typed, not taken from a layer, so it "
                    "stays fixed to the original area. Choose “Ask when the recipe runs” to change it per run.")
            step_plan.params.append(pp)

    _plan_columns(plan, choices, project, snapshot, default_asks)

    # ---- result roles and names
    label_steps = {}
    for step_plan in plan.steps:
        for pp in step_plan.params:
            if pp.kind == "output":
                label_steps.setdefault(pp.default_display, []).append((step_plan, pp))
    for label, items in label_steps.items():
        if len(items) < 2:
            continue
        # same result name more than once: add what differs (e.g. "Buffered, 10 m")
        for step_plan, pp in items:
            differs = ""
            for d in step_plan.step.alg.parameterDefinitions():
                if d.isDestination() or d.type() in LAYER_TYPES:
                    continue
                values = {str(other.step.inputs.get(d.name())) for other, _p in items}
                if len(values) > 1:
                    param = step_plan.param(d.name())
                    differs = param.shown_value if param else _show_value(d, step_plan.step.inputs.get(d.name()))
                    break
            pp.default_display = f"{label}, {differs}" if differs else f"{label} (step {step_plan.number})"
    for step_plan in plan.steps:
        for pp in step_plan.params:
            if pp.kind != "output":
                continue
            if (step_plan.number, pp.name) in used:
                pp.kind = "temp"
            default_role = "intermediate" if pp.kind == "temp" else "final"
            if step_plan.step.alg_id == "native:savefeatures":
                default_role = "save" if pp.kind == "temp" else "final"   # the user chose to keep this layer
            pp.role = choices.roles.get(pp.uid) or default_role
            if pp.kind == "temp" and pp.role == "final":
                pp.role = "save"
            pp.display = choices.names.get(pp.uid) or pp.default_display
            pp.help = choices.helps.get(pp.uid, "")
            pp.detail = f"result: {pp.display}" + (" (used by a later step)" if pp.kind == "temp" else "")
            if pp.role in ("final", "save") and pp.keep_style:
                layer = project.mapLayer(pp.style_layer_id)
                renderer = layer.renderer() if isinstance(layer, QgsMapLayer) and hasattr(layer, "renderer") else None
                if renderer is not None and renderer.type() in DATA_RENDERERS:
                    plan.warnings.append(
                        f"The style of '{pp.display}' uses categories or classes from the recorded data. On other "
                        "data, some values may show without a colour or in the wrong class.")

    # ---- describe links with the results' final names
    names = {(step_plan.number,
              pp.name): pp.display for step_plan in plan.steps for pp in step_plan.params if pp.kind in ("output", "temp")}
    for step_plan in plan.steps:
        for pp in step_plan.params:
            if pp.kind not in ("input", "link"):
                continue
            extent = step_plan.step.alg.parameterDefinition(pp.name) is not None and \
                step_plan.step.alg.parameterDefinition(pp.name).type() == "extent"
            parts = []
            for src in pp.sources:
                if src[0] == "link":
                    parts.append(f"{'extent of ' if extent else ''}step {src[1]} result ({names.get((src[1], src[2]), src[2])})")
                else:
                    parts.append(f"{'extent of ' if extent else ''}ingredient '{plan.inputs[src[1]].label}'")
            pp.detail = "; ".join(parts) + (" (detected: the recorded extent matches this layer)" if extent else "")

    # ---- warnings about the recording
    for input_plan in plan.inputs.values():
        key = norm_source(input_plan.used_by[0][2])
        if any(same_source(key, k) for k in excluded_keys):
            plan.warnings.append(f"'{input_plan.recorded}' is the result of a step you left out, so it became an ingredient.")
    for index, edit in enumerate(ctx.edits):
        if index in ctx.converted_edits or index in choices.edits_as_input:
            continue
        edit_key = norm_source(edit.source)
        for step_plan in plan.steps:
            if step_plan.step.timestamp is None or step_plan.step.timestamp.addSecs(1) <= edit.timestamp:
                continue
            values = []
            for pp in step_plan.params:
                if pp.kind in ("input", "link"):
                    v = step_plan.step.inputs.get(pp.name)
                    values += v if isinstance(v, list) else [v]
            if any(same_source(norm_source(v), edit_key) for v in values):
                plan.warnings.append(
                    f"'{edit.layer_name}' was edited by hand at {edit.timestamp.toString('HH:mm:ss')} "
                    f"({edit.describe()}) before step {step_plan.number} used it. The recipe won't repeat that edit.")
                break
    if not plan.outputs:
        plan.warnings.append("The recipe has no results: choose “Final output” for at least one result.")
    # layers in different coordinate systems used together: QGIS reprojects on the fly while you
    # work, but some tools need the same CRS, or measure in the first layer's units
    crs_of = {}
    for source, _name, _extent, authid in ctx.extents:
        key = norm_source(source)
        if key and authid:
            crs_of[key] = authid
    for step_plan in plan.steps:
        found = {}
        for pp in step_plan.params:
            if pp.kind != "input":
                continue
            for src in pp.sources:
                input_plan = plan.inputs.get(src[1])
                key = norm_source(input_plan.used_by[0][2]) if input_plan and input_plan.used_by else None
                authid = next((a for k, a in crs_of.items() if same_source(k, key)), None)
                if authid:
                    found.setdefault(authid, input_plan.recorded or input_plan.label)
        if len(found) > 1:
            listed = ", ".join(f"'{name}' ({authid})" for authid, name in found.items())
            plan.warnings.append(
                f"Step {step_plan.number} ({step_plan.step.alg.displayName()}) used layers in different coordinate "
                f"systems: {listed}. "
                "Results can differ from what you saw on the map; consider a 'Reproject layer' step first.")

    plan.groups = find_groups(plan)
    plan.parts = find_parts(plan)
    if len(plan.parts) > 1:
        plan.warnings.append(f"The steps form {len(plan.parts)} separate parts that don't use each other's results. "
                             "You can create one recipe per part.")
    plan.description = choices.description
    plugins = plugins_needed([step_plan.step.alg for step_plan in plan.steps])
    if plugins:
        batch_notes = list(batch_notes) + [plugins_note(plugins)]
    for note in batch_notes:
        if note not in plan.description:
            plan.description = (plan.description + "\n\n" + note).strip()
    plan.warnings = list(dict.fromkeys(plan.warnings))
    return plan


# ------------------------------------------------------- groups and parts

def _layer_signature(step_plan: StepPlan) -> tuple:
    out = []
    for param in step_plan.params:
        if param.kind in ("input", "link"):
            out.append((param.name, tuple(param.sources)))
    return tuple(out)


def find_groups(plan: ModelPlan) -> list:
    """Step numbers of repeated steps: batch rows, or the same tool on the same layers."""
    groups, seen = [], set()
    for step_plan in plan.steps:
        if step_plan.number in seen:
            continue
        if step_plan.step.batch_id:
            members = [other.number for other in plan.steps if other.step.batch_id == step_plan.step.batch_id]
        else:
            members = [other.number for other in plan.steps
                       if not other.step.batch_id and other.step.alg_id == step_plan.step.alg_id
                       and _layer_signature(other) == _layer_signature(step_plan)]
        if len(members) > 1:
            groups.append(members)
            seen.update(members)
    return groups


def find_parts(plan: ModelPlan) -> list:
    """Sets of step numbers connected by links or shared layer inputs."""
    parent = {step_plan.number: step_plan.number for step_plan in plan.steps}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for a, b in plan.links:
        union(a, b)
    for input_plan in plan.inputs.values():
        numbers = [u[0] for u in input_plan.used_by]
        for n in numbers[1:]:
            union(numbers[0], n)
    parts = {}
    for step_plan in plan.steps:
        parts.setdefault(find(step_plan.number), set()).add(step_plan.number)
    return sorted(parts.values(), key=min)


def group_title(plan: ModelPlan, members: list) -> str:
    sps = [step_plan for step_plan in plan.steps if step_plan.number in members]
    first = sps[0]
    differs, what = [], ""
    for d in first.step.alg.parameterDefinitions():
        if d.isDestination() or d.type() in LAYER_TYPES:
            continue
        values = [step_plan.param(d.name()).shown_value if step_plan.param(d.name())
                  else _show_value(d, step_plan.step.inputs.get(d.name()))
                  for step_plan in sps]
        if len(set(values)) > 1:
            differs, what = values, d.description().lower()
            if d.type() in LONG_VALUE_TYPES or max(len(str(v)) for v in values) > 12:
                differs = []   # e.g. extents: listing them is no summary; say what differs instead
            break
    kind = "Batch: " if first.step.batch_id else ""
    text = f"{kind}{len(sps)} × {first.step.alg.displayName()}"
    if differs:
        return f"{text} ({' / '.join(differs)})"
    if what:
        return f"{text} (different {_plural(what)})"
    return text


LONG_VALUE_TYPES = {"extent", "geometry", "crs", "expression", "point", "matrix", "file", "folder"}


def _plural(words: str) -> str:
    """"extent" -> "extents", "grid crs" -> "grid crs" (left alone), "class" -> "classes"."""
    last = words.split()[-1] if words else words
    if not last or last.endswith("s") or last.upper() == last:
        return words
    return words + ("es" if last.endswith(("x", "ch", "sh")) else "s")


# --------------------------------------------------------------- redundancy

def find_redundant(steps: list, project: QgsProject | None = None, snapshot: dict | None = None) -> dict:
    """Steps that are probably not part of the final workflow: entry_id -> reason.

    - repeated: the same tool with the same settings on the same layers was run again later
    - earlier attempt: run again later with other settings, nothing used this result, and its
      result layer was removed from the project (results still in the project are kept on purpose)
    - discarded: the result layer was removed from the project and nothing used it
    """
    project = project or QgsProject.instance()
    snapshot = snapshot or {}
    ok = [s for s in steps if s.ok and not s.batch_id]
    reasons = {}

    def layer_inputs(step):
        out = {}
        for d in step.alg.parameterDefinitions():
            if d.type() in LAYER_TYPES and d.name() in step.inputs:
                v = step.inputs[d.name()]
                out[d.name()] = tuple(norm_source(x) for x in (v if isinstance(v, list) else [v]))
        return out

    def settings(step):
        return {k: str(v) for k, v in step.inputs.items()
                if not (step.alg.parameterDefinition(k) and (step.alg.parameterDefinition(k).isDestination()
                        or step.alg.parameterDefinition(k).type() in LAYER_TYPES))}

    def result_keys(step):
        keys = []
        for value in (step.results or {}).values():
            keys += _result_keys(value, project, snapshot)[0]
        return keys

    def result_used(i):
        keys = result_keys(ok[i])
        for later in ok[i + 1:]:
            for items in layer_inputs(later).values():
                if any(same_source(k, item) for k in keys for item in items):
                    return True
        return False

    def removed(step):
        """All of the step's result layers were in the project while recording and are gone now."""
        ids = [v for v in (step.results or {}).values() if isinstance(v, str)]
        seen = [v for v in ids if v in snapshot]
        if not seen:
            seen_sources = [v for v in ids if any(same_source(norm_source(src), norm_source(v)) for src, _n in snapshot.values())]
            return bool(seen_sources) and all(_layer_for(norm_source(v), project) is None for v in seen_sources)
        return all(project.mapLayer(v) is None for v in seen)

    for i, step in enumerate(ok):
        if result_used(i):
            continue
        for later in ok[i + 1:]:
            if later.alg_id != step.alg_id or layer_inputs(later) != layer_inputs(step):
                continue
            if settings(later) == settings(step):
                reasons[step.entry_id] = f"repeated: run again with the same settings at {later.time_text}"
            elif removed(step):
                reasons[step.entry_id] = (f"earlier attempt: run again with other settings at {later.time_text}, "
                                          "and its result was removed")
            break
        if step.entry_id not in reasons and removed(step):
            reasons[step.entry_id] = "discarded: its result layer was removed from the project"
    return reasons


# ------------------------------------------------------------------ building

def _static_value(value):
    """History stores some values as JSON maps (e.g. data-defined properties)."""
    if isinstance(value, dict) and "type" in value:
        prop = QgsProperty()
        if prop.loadVariant(value):
            return prop
    return value


def model_path(folder: str, name: str) -> str:
    """A free .model3 file name in ``folder``."""
    base = slug(name, "model")
    path, n = os.path.join(folder, f"{base}.model3"), 2
    while os.path.exists(path):
        path, n = os.path.join(folder, f"{base}_{n}.model3"), n + 1
    return path


def layer_columns(plan: ModelPlan) -> dict:
    """Step number -> column (1 + the deepest step it uses)."""
    depth = {}
    for step_plan in plan.steps:
        parents = [depth[a] for a, b in plan.links if b == step_plan.number and a in depth]
        depth[step_plan.number] = (max(parents) + 1) if parents else 1
    return depth


def build_model(plan: ModelPlan, name: str, group: str = "", style_dir: str | None = None,
                project: QgsProject | None = None, only_steps: set | None = None) -> QgsProcessingModelAlgorithm:
    """Build the model (only the steps in ``only_steps`` when given, e.g. one part).

    Outputs that keep their style get a 'Set layer style' step using a .qml file in
    ``style_dir`` (next to the .model3 file; found again via @model_folder)."""
    project = project or QgsProject.instance()
    model = QgsProcessingModelAlgorithm(name, group)
    steps = [step_plan for step_plan in plan.steps if only_steps is None or step_plan.number in only_steps]
    numbers = {step_plan.number for step_plan in steps}
    help_content = {}
    used_names = set()

    def unique(text, fallback):
        key = slug(text, fallback)
        base, n = key, 2
        while key in used_names:
            key, n = f"{base}_{n}", n + 1
        used_names.add(key)
        return key

    columns = layer_columns(plan)
    x_gap, y_gap = 330, 150
    row_in_column = {}

    # layer inputs used by these steps
    row = 0
    order = []   # layers first (in the order the steps use them), then each step's settings together
    geometry_types = {"point": QgsProcessing.SourceType.TypeVectorPoint, "line": QgsProcessing.SourceType.TypeVectorLine,
                      "polygon": QgsProcessing.SourceType.TypeVectorPolygon}
    first_use = sorted(plan.inputs.values(), key=lambda i: min((u[0] for u in i.used_by), default=0))
    for input_plan in first_use:
        if not any(u[0] in numbers for u in input_plan.used_by):
            continue
        input_plan.key = unique(input_plan.label, "layer")
        order.append(input_plan.key)
        definition = input_plan.definition.clone()
        if input_plan.layer_kind in geometry_types and hasattr(definition, "setDataTypes"):
            definition.setDataTypes([geometry_types[input_plan.layer_kind]])   # only layers of the recorded geometry
        definition.setName(input_plan.key)
        definition.setDescription(input_plan.label)
        if input_plan.help and hasattr(definition, "setHelp"):
            definition.setHelp(input_plan.help)
        if input_plan.help:
            help_content[input_plan.key] = input_plan.help
        component = QgsProcessingModelParameter(input_plan.key)
        component.setPosition(QPointF(120, 80 + row * 110))
        row += 1
        model.addModelParameter(definition, component)

    child_ids = {}
    output_names = set()
    asked_keys = {}   # setting uid -> model parameter name (columns follow new-column names through this)
    for step_plan in steps:
        child = QgsProcessingModelChildAlgorithm(step_plan.step.alg_id)
        child.generateChildId(model)
        child.setDescription(f"{step_plan.number}. {step_plan.step.label}")
        col = columns.get(step_plan.number, 1)
        r = row_in_column.get(col, 0)
        row_in_column[col] = r + 1
        position = QPointF(120 + col * x_gap, 80 + r * y_gap)
        child.setPosition(position)
        if step_plan.note:
            comment = child.comment()
            comment.setDescription(step_plan.note)
            comment.setPosition(position + QPointF(0, -70))
        alg = step_plan.step.alg
        outputs, style_steps = {}, []
        # columns a Field Calculator expression reads: asked, or following a new column's name
        replacements = {}
        for pp in step_plan.params:
            if "#" not in pp.name:
                continue
            if pp.expose and pp.column_origin and plan.inputs[pp.column_origin[1]].key:
                key = unique(pp.display, "column")
                definition = QgsProcessingParameterField(
                    key, pp.display, pp.column, plan.inputs[pp.column_origin[1]].key)
                if pp.help:
                    definition.setHelp(pp.help)
                    help_content[key] = pp.help
                component = QgsProcessingModelParameter(key)
                component.setPosition(QPointF(120, 80 + row * 110))
                row += 1
                model.addModelParameter(definition, component)
                asked_keys[pp.uid] = key
                order.append(key)
                replacements[pp.column] = key
            elif pp.follows in asked_keys:
                replacements[pp.column] = asked_keys[pp.follows]
        for pp in step_plan.params:
            if "#" in pp.name:
                continue   # not a setting of the tool itself (see above)
            d = alg.parameterDefinition(pp.name)
            if pp.kind in ("link", "input"):
                sources = []
                for src in pp.sources:
                    if src[0] == "link":
                        if src[1] in child_ids:
                            sources.append(QgsProcessingModelChildParameterSource.fromChildOutput(child_ids[src[1]], src[2]))
                    else:
                        sources.append(QgsProcessingModelChildParameterSource.fromModelParameter(plan.inputs[src[1]].key))
                child.addParameterSources(pp.name, sources)
            elif pp.kind in ("output", "temp"):
                if pp.role not in ("final", "save"):
                    continue
                out_name = pp.display or pp.label
                base, n = out_name, 2
                while out_name in output_names:
                    out_name, n = f"{base} {n}", n + 1
                output_names.add(out_name)
                out = QgsProcessingModelOutput(out_name, out_name)
                out.setChildOutputName(pp.name)
                out.setChildId(child.childId())
                default_file = _numbered_file(pp.value, out_name, project)
                if default_file:
                    # saved to a file when recorded: default to a new, numbered file next to it
                    # (e.g. buffer_copy_gpkg_1.gpkg), changeable in the model's window
                    out.setDefaultValue(default_file)
                outputs[out_name] = out
                layer = project.mapLayer(pp.style_layer_id) if pp.keep_style else None
                if style_dir and pp.keep_style and (layer is not None or os.path.isfile(pp.style_file)):
                    style_steps.append((layer if layer is not None else pp.style_file, out_name, pp.name))
            elif pp.expose:
                key = unique(pp.display, "setting")
                definition = d.clone()
                definition.setName(key)
                definition.setDescription(pp.display)
                definition.setDefaultValue(_static_value(pp.value))
                if pp.help and hasattr(definition, "setHelp"):
                    definition.setHelp(pp.help)
                if pp.help:
                    help_content[key] = pp.help
                if d.type() == "field" and pp.column_origin and pp.column_origin[0] == "input":
                    # the column list comes from the model input the column was traced back to
                    definition.setParentLayerParameterName(plan.inputs[pp.column_origin[1]].key)
                if d.type() == "distance":
                    # the tool's own "parent" (its input layer) is not a model input: point the unit at
                    # the model input it comes from, or fix the unit that was recorded
                    parent = step_plan.param(d.parentParameterName()) if hasattr(d, "parentParameterName") else None
                    if parent and parent.kind == "input" and len(parent.sources) == 1 and parent.sources[0][0] == "input" \
                            and plan.inputs[parent.sources[0][1]].key:
                        definition.setParentParameterName(plan.inputs[parent.sources[0][1]].key)
                    else:
                        definition.setParentParameterName("")
                        if pp.unit is not None:
                            definition.setDefaultUnit(pp.unit)
                order.append(key)
                asked_keys[pp.uid] = key
                component = QgsProcessingModelParameter(key)
                component.setPosition(QPointF(120, 80 + row * 110))
                row += 1
                model.addModelParameter(definition, component)
                child.addParameterSources(pp.name, [QgsProcessingModelChildParameterSource.fromModelParameter(key)])
            elif pp.follows in asked_keys:
                # a column an earlier step makes, whose name is asked: use that same name
                child.addParameterSources(pp.name, [QgsProcessingModelChildParameterSource.fromModelParameter(
                    asked_keys[pp.follows])])
            elif pp.name == "FORMULA" and replacements and isinstance(pp.value, str):
                # the expression is put together when the model runs, with the columns chosen there
                child.addParameterSources(pp.name, [QgsProcessingModelChildParameterSource.fromExpression(
                    formula_expression(pp.value, replacements))])
            else:
                child.addParameterSources(pp.name, [QgsProcessingModelChildParameterSource.fromStaticValue(
                    _static_value(pp.value))])
        child.setModelOutputs(outputs)
        child_ids[step_plan.number] = model.addChildAlgorithm(child)
        for layer, out_name, output_param in style_steps:
            _add_style_step(model, child_ids[step_plan.number], output_param, layer, out_name, position, style_dir)

    if order and hasattr(model, "setParameterOrder"):
        model.setParameterOrder(order)
    help_content["ALG_DESC"] = plan.description or (
        f"Generated by Sous Chef from {len(steps)} recorded step(s).")
    help_content["ALG_CREATOR"] = "Sous Chef"
    model.setHelpContent(help_content)
    return model


CORE_PROVIDERS = {"native", "qgis", "gdal", "grass", "grass7", "grass8", "3d", "pdal", "model", "script", "project"}
PLUGINS_NOTE = "Needs these plugins"


def general_title(plan) -> str:
    """A title that names the tools, not the layers or the project: for general images
    and cookbook recipes, which are about the workflow rather than one run of it."""
    names = list(dict.fromkeys(step_plan.step.alg.displayName() for step_plan in plan.steps if step_plan.step.alg is not None))
    if not names:
        return "Workflow"
    if len(names) == 1:
        return f"{names[0]} workflow"
    if len(names) <= 3:
        return f"{', '.join(names[:-1])} and {names[-1]} workflow"
    return f"Workflow: {', '.join(names[:3])} and {len(names) - 3} more"


def plugins_needed(algs) -> dict:
    """{plugin name: [tool names]} for tools that come from plugins rather than QGIS itself."""
    found = {}
    for alg in algs:
        if alg is None:
            continue
        provider = alg.provider()
        pid = provider.id() if provider is not None else alg.id().split(":")[0]
        if pid in CORE_PROVIDERS:
            continue
        if pid == "souschef":   # a recipe inside the recipe: it needs that recipe file as well
            name = "Sous Chef, with these recipes in its Recipe Book"
        else:
            name = _plugin_for_provider(pid) or (provider.name() if provider is not None else pid)
        tools = found.setdefault(name, [])
        if alg.displayName() not in tools:
            tools.append(alg.displayName())
    return found


def _plugin_for_provider(pid: str) -> str:
    """The name of the plugin that added the Processing provider ``pid``, when it can be found."""
    try:
        import qgis.utils
        for key, plugin in qgis.utils.plugins.items():
            for value in vars(plugin).values():
                if isinstance(value, QgsProcessingProvider) and value.id() == pid:
                    return qgis.utils.pluginMetadata(key, "name") or key
    except Exception as error:   # safe to carry on: noted in the log
        log.ignored(error)
    return ""


def plugins_note(plugins: dict) -> str:
    parts = [f"{name} (tools: {', '.join(tools)})" for name, tools in plugins.items()]
    return (f"{PLUGINS_NOTE} (install them with Plugins › Manage and Install Plugins): " + "; ".join(parts) + ".")


FILE_SCHEMES = ("memory:", "TEMPORARY_OUTPUT", "postgres:", "postgis:", "ogr:", "spatialite:")


def _numbered_file(recorded, name: str, project) -> str:
    """For a result recorded as a file: <folder>/<name>_<type>_<n>.<type> with the first free n.
    The folder is the recorded one, else the project's. Empty for temporary results."""
    if not isinstance(recorded, str) or not recorded or recorded.startswith(FILE_SCHEMES):
        return ""
    path = recorded.split("|")[0]
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    if not ext or not os.path.isabs(path):
        return ""
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        folder = project.homePath() if project and project.homePath() else os.path.expanduser("~")
    base = slug(name, "output")
    n = 1
    while os.path.exists(os.path.join(folder, f"{base}_{ext}_{n}.{ext}")):
        n += 1
    return os.path.join(folder, f"{base}_{ext}_{n}.{ext}")


def _add_style_step(model, child_id, output_param, layer, out_name, position, style_dir):
    os.makedirs(style_dir, exist_ok=True)
    file_name = f"{slug(out_name, 'output')}.qml"
    qml_path = os.path.join(style_dir, file_name)
    if isinstance(layer, str):   # a style kept with an opened model
        if os.path.abspath(layer) != os.path.abspath(qml_path):
            shutil.copyfile(layer, qml_path)
    else:
        _msg, ok = layer.saveNamedStyle(qml_path)
        if not ok and not os.path.exists(qml_path):
            return
    folder_name = os.path.basename(style_dir.rstrip("/\\"))
    style = QgsProcessingModelChildParameterSource.fromExpression(f"@model_folder || '/{folder_name}/{file_name}'")
    child = QgsProcessingModelChildAlgorithm("native:setlayerstyle")
    child.generateChildId(model)
    child.setDescription(f"Style: {out_name}")
    child.setPosition(position + QPointF(170, 75))
    child.addParameterSources("INPUT", [QgsProcessingModelChildParameterSource.fromChildOutput(child_id, output_param)])
    child.addParameterSources("STYLE", [style])
    model.addChildAlgorithm(child)


def save_model(model: QgsProcessingModelAlgorithm, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    model.setSourceFilePath(path)
    if not model.toFile(path):
        raise OSError(f"Could not write {path}")
    return path
