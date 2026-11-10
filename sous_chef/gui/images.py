"""Workflow images in several styles, written as SVG (vector: sharp at any size).

Styles
- boxes:     the flowchart as on screen (see flow.svg_text)
- cards:     the same layout with larger, shadowed cards tinted by kind of tool
- minimal:   round tool icons with labels, thin curved lines, lots of white space
- metro:     a metro map: each input layer is a coloured line, tools are stations
- swimlanes: bands for input data, preparation, analysis and results
- data:      the data as the main shapes, with a preview of each layer's geometry,
             and the tools on the arrows between them (recorded run only)

Detail: "general" (tools and kinds of data) or "specific" (the recorded run's layers
and values). Themes: standard, colourblind (Okabe-Ito), greyscale, dark.

The layout comes from the flowchart (FlowView), so boxes moved by hand keep their place.
"""

from __future__ import annotations

import base64
import math
import os
import re
from dataclasses import dataclass, field

from qgis.core import QgsApplication, QgsProject
from qgis.PyQt.QtCore import QFile, QIODevice, QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QFont, QFontMetricsF, QPainterPath
from qgis.PyQt.QtWidgets import QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsSimpleTextItem

from ..core import builder
from . import fonts, icons
from .flow import FlowView, svg_text


STYLES = [
    ("boxes", "Boxes (as on screen)"),
    ("cards", "Cards"),
    ("minimal", "Clean minimal"),
    ("metro", "Metro map"),
    ("swimlanes", "Swimlanes"),
    ("data", "Data-centred (always shows the run's layers)"),
    ("storyboard", "Storyboard (a map of each step)"),
    ("summary", "Summary sheet (the workflow in numbers)"),
    ("mindmap", "Radial flowchart (the result in the middle)"),
    ("river", "Feature river (bands as wide as the layers)"),
    ("board", "Sticky-note board"),
    ("comic", "Comic strip"),
    ("pilgrimage", "Pilgrimage (a knight's journey on old paper)"),
]
THEME_NAMES = [("standard", "Standard"), ("colourblind", "Colour-blind safe"), ("greyscale", "Greyscale (print)"),
               ("dark", "Dark (slides)"), ("blueprint", "Blueprint"), ("parchment", "Parchment")]

THEMES = {
    "standard": dict(
        bg="#FFFFFF", ink="#22272B", muted="#6B7178", line="#5C6670", icon_bg="#FFFFFF",
        input=("#FFF4C7", "#B8901C"), setting=("#EFE7FA", "#7A4FB5"), step=("#FFFFFF", "#5C6670"),
        output=("#E3F2D9", "#4E8A3C"), left=("#F2F2F2", "#9A9A9A"),
        lanes=["#FFF9E6", "#F2F5F9", "#EEF3FB", "#EEF6EA"],
        metro=["#E3A21A", "#2F7EC1", "#C2410C", "#7A4FB5", "#0E9F6E", "#D6336C", "#0891B2", "#65A30D"],
        tints={"Vector geometry": "#EAF2FB", "Vector overlay": "#FBEDEA", "Vector creation": "#EEF7EC",
               "Vector general": "#F3F0FA", "Vector table": "#FFF6E5", "Vector selection": "#E9F7F6",
               "Raster analysis": "#FBEFF6", "Raster terrain analysis": "#F6F1E7"},
        tint="#F4F5F7", shadow=0.16, data="#2F7EC1"),
    "colourblind": dict(
        bg="#FFFFFF", ink="#1A1A1A", muted="#666666", line="#4D4D4D", icon_bg="#FFFFFF",
        input=("#FDEBC8", "#E69F00"), setting=("#F7E1EE", "#CC79A7"), step=("#FFFFFF", "#4D4D4D"),
        output=("#CDEEE4", "#009E73"), left=("#F0F0F0", "#999999"),
        lanes=["#FEF4E0", "#EAF4FB", "#E6F1F8", "#E4F4EE"],
        metro=["#E69F00", "#56B4E9", "#009E73", "#D55E00", "#0072B2", "#CC79A7", "#F0E442", "#000000"],
        tints={"Vector geometry": "#E3F1FB", "Vector overlay": "#FBE7DA", "Vector creation": "#DFF3EC",
               "Vector general": "#F6E6F0", "Vector table": "#FDF0D3", "Vector selection": "#DCEBF6"},
        tint="#F2F2F2", shadow=0.16, data="#0072B2"),
    "greyscale": dict(
        bg="#FFFFFF", ink="#111111", muted="#555555", line="#333333", icon_bg="#FFFFFF",
        input=("#F0F0F0", "#333333"), setting=("#E4E4E4", "#333333"), step=("#FFFFFF", "#222222"),
        output=("#D6D6D6", "#111111"), left=("#FAFAFA", "#999999"),
        lanes=["#F7F7F7", "#EFEFEF", "#F7F7F7", "#EFEFEF"],
        metro=["#111111", "#555555", "#888888", "#333333", "#777777", "#222222", "#999999", "#444444"],
        dashes=["", "8 4", "2 4", "12 4 2 4", "4 4", "16 6", "1 3", "6 2 2 2"],
        tints={}, tint="#F4F4F4", shadow=0.12, data="#333333"),
    "dark": dict(
        bg="#1D2125", ink="#E8EAED", muted="#9AA0A6", line="#AEB4BA", icon_bg="#F1F3F4",
        input=("#4A3F17", "#E2B93B"), setting=("#3A2D52", "#B89AE6"), step=("#2A2F35", "#8A939C"),
        output=("#23402A", "#7CC47F"), left=("#25292D", "#6B7178"),
        lanes=["#2B2716", "#23282E", "#212A33", "#1F2B22"],
        metro=["#F2B84B", "#5AA9E6", "#F07F4F", "#B89AE6", "#4CC38A", "#F06292", "#4DD0E1", "#A3D65C"],
        tints={"Vector geometry": "#23303D", "Vector overlay": "#3A2A27", "Vector creation": "#25352A",
               "Vector general": "#2F2A3D", "Vector table": "#3A3322", "Vector selection": "#21363A"},
        tint="#2A2F35", shadow=0.5, data="#5AA9E6"),
    "blueprint": dict(
        bg="#1F4E8C", ink="#FFFFFF", muted="#C3D6EE", line="#E3ECF7", icon_bg="#FFFFFF",
        input=("#2A5E9E", "#FFE9A8"), setting=("#2A5E9E", "#E2D2FA"), step=("#24589A", "#E3ECF7"),
        output=("#2A5E9E", "#C4F2CF"), left=("#1F4E8C", "#7FA3CF"),
        lanes=["#24578F", "#215394", "#24578F", "#215394"],
        metro=["#FFE9A8", "#FFFFFF", "#9FD8FF", "#E2D2FA", "#C4F2CF", "#FFC7B5", "#B5F1F5", "#F3F7A8"],
        tints={}, tint="#2A5E9E", shadow=0.0, data="#FFE9A8", grid="#3E6CA8"),
    "parchment": dict(
        bg="#F3E6C4", ink="#3B2412", muted="#7A5C3A", line="#6B4A2B", icon_bg="#FBF3DD",
        input=("#F6E2A9", "#8B5A2B"), setting=("#EFD9C9", "#7A2E1E"), step=("#FBF3DD", "#6B4A2B"),
        output=("#E2E7C2", "#4E6B2E"), left=("#EFE6D2", "#A8957A"),
        lanes=["#F7ECCF", "#EFE0BB", "#F7ECCF", "#EFE0BB"],
        metro=["#8B1E1E", "#1F3A93", "#4E6B2E", "#B07A1E", "#6B3A6B", "#2E6B6B", "#8B5A2B", "#3B2412"],
        tints={}, tint="#F6EBCF", shadow=0.1, data="#8B1E1E"),
}

PREPARATION = {"Vector general", "Vector selection", "Vector table", "Vector creation", "Raster tools", "Database",
               "Layer tools", "File tools", "Vector tiles", "Cartography", "GPS", "Raster creation"}


# ------------------------------------------------------------------ public

@dataclass
class ImageOptions:
    style: str = "cards"
    detail: str = "general"      # general | named | specific
    theme: str = "standard"
    left_out: bool = True
    notes: bool = True
    numbers: bool = True         # step numbers ("2. Buffer")
    background: bool = True      # False: transparent (PNG), so filled areas behind the drawing are left out
    legend: bool = True          # the key at the bottom (styles that have one)
    map_background: bool = False  # storyboard maps: also draw the project's visible basemaps
    title: str = ""              # the image's title; empty: the recipe's name, or a general title for general images
    spacing: float = 1.0         # room between columns: below 1 makes a long workflow narrower
    inputs: bool = False         # storyboard: also show the layers it starts from
    fonts: bool = True           # a font to match the style (off: the system font)
    font: str = ""               # the text font chosen in the Export window ("": the style's own)
    title_font: str = ""         # the title font chosen in the Export window
    inputs_side: str = "top"     # storyboard: the starting layers "top" or "left"
    overview: bool = False       # maps: a small world map with a box where the layers are
    overview_zoom: str = "world"  # or "region": the part of the world around the layers
    step_maps: bool = True       # pilgrimage: a small map at each building
    canvas: str = "slide"        # "slide": a 16:9 page for presentations; "fit": the drawing's own shape
    subtitle: str = "auto"       # pilgrimage: "auto", your own subtitle, or "" for none
    part: int = -1               # separate parts: -1 all, otherwise the part's index
    fold: bool = True            # storyboard: housekeeping steps become a note on the next card
    pages: str = "auto"          # storyboard: "auto" splits a long one over pages; "one" keeps one page
    page: int = 0                # storyboard: the page drawn


_OPT = ImageOptions()
_T = {}   # the colours of the image being drawn


def render_svg(controller, opts: ImageOptions) -> str:
    """The workflow image as SVG text."""
    global _OPT, _FAMILY, _TITLE_FAMILY
    if opts.detail == "general" and (opts.overview or opts.step_maps):   # General detail shows no real maps or places
        import dataclasses
        opts = dataclasses.replace(opts, overview=False, step_maps=False)
    _OPT = opts
    _FAMILY = fonts.family_for(opts.style, opts.fonts, opts.font)
    _TITLE_FAMILY = fonts.title_family(opts.style, opts.fonts, opts.title_font)
    detail = opts.detail
    t = THEMES.get("standard" if opts.style == "boxes" else opts.theme, THEMES["standard"])
    _T.clear()
    _T.update(t)
    if opts.style == "boxes":
        svg = svg_text(controller, detail, opts.left_out, opts.notes, opts.numbers, image_title(controller, detail),
                       opts.spacing)
    else:
        if opts.style == "data" and detail == "general":
            detail = "named"   # this style is about the actual layers
        if opts.style == "pilgrimage":   # drawn as a picture, with its own mark
            return _pilgrimage(controller, detail)
        if opts.style in ("mindmap", "river", "board", "comic"):
            from . import designs
            draw = {"mindmap": designs.mind_map, "river": designs.river, "board": designs.board,
                    "comic": designs.comic}[opts.style]
            return _watermark(draw(controller, detail, t, opts.left_out), t)
        draw = {"cards": _cards, "minimal": _minimal, "metro": _metro, "swimlanes": _swimlanes, "data": _data,
                "storyboard": _storyboard, "summary": _summary}[opts.style]
        svg = draw(controller, detail, t, opts.left_out)
    return _watermark(svg, t)   # always: a small "Made with Sous Chef" with the logo, bottom right


_LOGO = {}


def _logo_uri() -> str:
    if "uri" not in _LOGO:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons", "sous_chef_48.png")
        try:
            with open(path, "rb") as fh:
                _LOGO["uri"] = "data:image/png;base64," + base64.b64encode(fh.read()).decode()
        except OSError:
            _LOGO["uri"] = ""
    return _LOGO["uri"]


def _watermark(svg: str, theme) -> str:
    """Make the image 26 units taller and put a small logo and "Made with Sous Chef" in the
    bottom-right corner of that strip, faint, so it never sits on the drawing."""
    tag = re.search(r"<svg\b[^>]*>", svg)
    box = re.search(r'viewBox="\s*([-\d.]+)[ ,]+([-\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)', tag.group(0)) if tag else None
    if box is None:
        return svg
    x0, y0, w, h = (float(v) for v in box.groups())
    extra = 26
    new_tag = tag.group(0).replace(box.group(0), f'viewBox="{x0:g} {y0:g} {w:g} {h + extra:g}')
    height = re.search(r'\sheight="([\d.]+)([a-z%]*)"', new_tag)
    if height:
        value = float(height.group(1)) * (h + extra) / h
        new_tag = new_tag.replace(height.group(0), f' height="{value:g}{height.group(2)}"')
    svg = svg.replace(tag.group(0), new_tag, 1)
    svg = re.sub(r'(<rect class="background"[^>]*height=")([\d.]+)(")', lambda m: f"{m.group(1)}{h + extra:g}{m.group(3)}",
                 svg, count=1)
    label = "Made with Sous Chef"
    right = x0 + w - 24
    text_x = right - _width(label, 10.5)
    y = y0 + h + extra - 9
    mark = ('<g class="watermark" opacity="0.6">'
            + _image(_logo_uri(), text_x - 21, y - 13, 16)
            + _text(text_x, y, label, 10.5, theme["muted"], halo=_loose(theme)) + "</g>")
    end = svg.rindex("</svg>")
    return svg[:end] + mark + svg[end:]


def export(controller, style: str, detail: str, theme: str, path: str, left_out: bool, notes: bool = True,
           numbers: bool = True):
    """Write the workflow image to ``path`` as SVG."""
    text = render_svg(controller, ImageOptions(style, detail, theme, left_out, notes, numbers))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


# ------------------------------------------------------------------ nodes

@dataclass
class Node:
    key: str
    kind: str                 # input | step | output | left
    title: str
    sub: str = ""
    rows: list = field(default_factory=list)   # (label, value, asked, setting definition)
    layer_kind: str = ""
    alg: object = None
    layer: object = None
    count: int = 1            # steps in a group
    note: str = ""            # the user's note on the step


def _layer_of(value, controller):
    project = QgsProject.instance()
    if not isinstance(value, str) or not value:
        return None
    layer = project.mapLayer(value)
    if layer is None:
        key = builder.norm_source(value)
        layer = builder._layer_for(key, project) if key else None
    if layer is None and value in controller.snapshot:
        layer = builder._layer_for(builder.norm_source(controller.snapshot[value][0]), project)
    return layer


def _rows(sps, detail):
    """Settings shown with a step: only in the recorded run "with settings" version, and only
    those asked when the model runs or changed from the tool's default."""
    rows = []
    if detail != "specific":
        return rows
    for step_plan in sps[:1]:
        for p in step_plan.params:
            if p.kind != "static":
                continue
            d = step_plan.step.alg.parameterDefinition(p.name)
            if p.expose:
                rows.append((p.display, p.shown_value, True, d))
            elif not p.is_default:
                rows.append((p.label, p.shown_value, False, d))
    return rows


def _nodes(controller, view, detail) -> dict:
    c, plan = controller, controller.plan
    by_entry = {step_plan.step.entry_id: step_plan for step_plan in plan.steps}
    outputs = {param.uid: (step_plan,
                           param) for step_plan in plan.steps for param in step_plan.params if param.kind in ("output", "temp")}
    nodes = {}
    numbers = _OPT.numbers
    # general images: inputs show only their kind; same kinds get a number to tell them apart
    kinds = [icons.layer_icon(plan.inputs[k[6:]].layer_kind)[1] for k in view.nodes if k.startswith("input:")]
    seen_kinds = {}
    for key in view.nodes:
        if key.startswith("input:"):
            input_plan = plan.inputs[key[6:]]
            _icon, words = icons.layer_icon(input_plan.layer_kind)
            value = input_plan.used_by[0][2] if input_plan.used_by else None
            if detail == "general":
                seen_kinds[words] = seen_kinds.get(words, 0) + 1
                label = words.capitalize() + (f" {seen_kinds[words]}" if kinds.count(words) > 1 else "")
                node = Node(key, "input", label, "", layer_kind=input_plan.layer_kind)
            else:
                node = Node(key, "input", input_plan.recorded, words, layer_kind=input_plan.layer_kind)
            node.layer = _layer_of(value, c)
        elif key in outputs:
            step_plan, param = outputs[key]
            _icon, words = icons.layer_icon(param.layer_kind)
            if detail == "general":
                node = Node(key, "output", words.capitalize(), f"from {step_plan.step.alg.displayName()}",
                            layer_kind=param.layer_kind)
            else:
                where = f"step {step_plan.number}" if numbers else f"from {step_plan.step.alg.displayName()}"
                node = Node(key, "output", param.default_display, f"{words} · {where}", layer_kind=param.layer_kind)
            node.layer = QgsProject.instance().mapLayer(param.style_layer_id) if param.style_layer_id else None
        elif key.startswith("group:"):
            index = int(key[6:])
            sps = [step_plan for step_plan in plan.steps if c.group_of(step_plan.number) == index]
            name = sps[0].step.alg.displayName()
            if detail == "general":
                node = Node(key, "step", f"{len(sps)} × {name}", "repeated with different settings")
            else:
                title = builder.group_title(plan, plan.groups[index]) if detail == "specific" else f"{len(sps)} × {name}"
                node = Node(key, "step", title, ("steps " + ", ".join(str(step_plan.number) for step_plan in sps)) if numbers
                            else f"{len(sps)} runs")
            node.alg, node.count, node.rows = sps[0].step.alg, len(sps), []
        elif key.startswith("step:") and int(key[5:]) in by_entry:
            step_plan = by_entry[int(key[5:])]
            name = step_plan.step.alg.displayName()
            if detail == "general":
                node = Node(key, "step", name, step_plan.step.synthetic or "")
            else:
                node = Node(key, "step", f"{step_plan.number}. {name}" if numbers else name,
                            step_plan.step.time_text[-8:] if detail == "specific" else "")
            node.alg, node.rows = step_plan.step.alg, _rows([step_plan], detail)
            node.note = step_plan.note if _OPT.notes else ""
        elif key.startswith("step:"):
            step = c.step(int(key[5:]))
            if step is None:
                continue
            reason = c.reasons.get(step.entry_id)
            node = Node(key, "left", step.alg.displayName() if step.alg else step.label,
                        f"left out: {reason}" if reason else "left out of the recipe")
            node.alg = step.alg
        else:
            continue
        nodes[key] = node
    return nodes


def _view(controller, detail, left_out):
    view = FlowView(controller, mode="named" if detail == "named" else detail)
    view.left_out_override = left_out
    view.gap_x = max(48, int(view.gap_x * _OPT.spacing))
    view.rebuild()
    return view


# ------------------------------------------------------------------ helpers

def _esc(text) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


_METRICS = {}
_FAMILY = None   # the font of the image being drawn (None: the system font)
_TITLE_FAMILY = None


def _width(text, size, bold=False) -> float:
    key = (_FAMILY, size, bold)
    if key not in _METRICS:
        font = QFont(_FAMILY or "Arial")
        font.setPixelSize(int(size))
        font.setBold(bold)
        _METRICS[key] = QFontMetricsF(font)
    return _METRICS[key].horizontalAdvance(str(text)) * 1.04


def _pieces(word, width, size, bold) -> list:
    """A word too wide for the line, split after _ - . / where possible, otherwise anywhere."""
    if _width(word, size, bold) <= width:
        return [word]
    parts = [p for p in re.split(r"(?<=[_\-./])", word) if p]
    out, line = [], ""
    for part in parts:
        if _width(line + part, size, bold) <= width:
            line += part
            continue
        if line:
            out.append(line)
        line = ""
        for ch in part:   # a part that is still too wide on its own
            if line and _width(line + ch, size, bold) > width:
                out.append(line)
                line = ""
            line += ch
    if line:
        out.append(line)
    return out


def _wrap(text, width, size, bold=False, lines=2) -> list:
    """Lines no wider than ``width``. ``lines`` caps the number of lines (the last one then
    ends with …); None shows all of it."""
    width *= 0.92   # room for fonts wider than the one measured (the viewer's own font is used)
    words, out, line = [], [], ""
    for word in str(text).split():
        words += _pieces(word, width, size, bold)
    for word in words:
        trial = f"{line} {word}".strip()
        if _width(trial, size, bold) <= width or not line:
            line = trial
        else:
            out.append(line)
            line = word
    if line:
        out.append(line)
    if lines is not None and len(out) > lines:
        out = out[:lines]
        while out[-1] and _width(out[-1] + "…", size, bold) > width:
            out[-1] = out[-1][:-1]
        out[-1] += "…"
    return out or [""]


def _elide(text, width, size, bold=False) -> str:
    return _wrap(text, width, size, bold, 1)[0]


_ICON_CACHE = {}


def _icon_uri(path: str) -> str:
    if path not in _ICON_CACHE:
        f = QFile(path)
        data = b""
        if f.open(QIODevice.OpenModeFlag.ReadOnly):
            data = bytes(f.readAll())
            f.close()
        _ICON_CACHE[path] = "data:image/svg+xml;base64," + base64.b64encode(data).decode() if data else ""
    return _ICON_CACHE[path]


def _alg_icon(alg) -> str:
    path = alg.svgIconPath() if alg is not None else ""
    return _icon_uri(path or QgsApplication.iconPath("processingAlgorithm.svg"))


def _layer_icon(kind) -> str:
    return _icon_uri(QgsApplication.iconPath(icons.layer_icon(kind)[0]))


def _setting_icon(definition) -> str:
    return _icon_uri(QgsApplication.iconPath(icons.setting_icon(definition)[0]))


def _image(uri, x, y, size) -> str:
    return (f'<image href="{uri}" xlink:href="{uri}" x="{x:.1f}" y="{y:.1f}" width="{size}" height="{size}"/>'
            if uri else "")


def _arrow(x, y, angle, colour, length=10, width=8) -> str:
    """An arrowhead with its tip at (x, y), pointing along ``angle`` (degrees, 0 = right,
    counter-clockwise as in QPainterPath). Drawn as a shape: PNG and PDF export can't draw markers."""
    a = math.radians(angle)
    ux, uy = math.cos(a), -math.sin(a)
    bx, by = x - ux * length, y - uy * length
    px, py = -uy * width / 2, ux * width / 2
    return (f'<polygon points="{x:.1f},{y:.1f} {bx + px:.1f},{by + py:.1f} {bx - px:.1f},{by - py:.1f}" '
            f'fill="{colour}"/>')


def _shadow(x, y, w, h, rx, opacity) -> str:
    """A soft drop shadow made of two offset shapes (no SVG filter, which PNG/PDF can't draw)."""
    return (f'<rect x="{x - 1:.1f}" y="{y + 1:.1f}" width="{w + 2:.1f}" height="{h + 3:.1f}" rx="{rx + 1}" '
            f'fill="#000000" fill-opacity="{opacity * 0.35:.3f}"/>'
            f'<rect x="{x:.1f}" y="{y + 1.5:.1f}" width="{w:.1f}" height="{h + 1:.1f}" rx="{rx}" '
            f'fill="#000000" fill-opacity="{opacity * 0.6:.3f}"/>')


EXTENT_ICON = "mActionZoomToLayer.svg"


def _extent_mark(x, y, theme, words=False) -> str:
    """The small extent symbol by the end of a line from a layer whose extent is used."""
    out = _image(_icon_uri(QgsApplication.iconPath(EXTENT_ICON)), x, y, 14)
    if words:
        out += _text(x - 4, y + 11, "extent", 10, theme["muted"], italic=True, anchor="end", halo=theme["bg"])
    return out


def _text(x, y, text, size, fill, bold=False, anchor="start", italic=False, halo="", family="") -> str:
    """A label. With ``halo`` (the background colour) lines passing behind it stop short of the
    letters, so text never sits on top of a line."""
    weight = ' font-weight="600"' if bold else ""
    style = ' font-style="italic"' if italic else ""
    if halo and not _OPT.background and fill == _T.get("muted"):
        fill = _T["ink"]   # grey text is too faint on an unknown background
    face = f' font-family="{_esc(family)}"' if family else ""
    body = f'x="{x:.1f}" y="{y:.1f}" font-size="{size}"{weight}{style}{face} text-anchor="{anchor}">{_esc(text)}</text>'
    if halo:   # the halo is a copy underneath (paint-order isn't drawn by every renderer)
        # on a transparent image: a thin outline in a contrasting colour, so text stands out on any background
        width = "5" if _OPT.background else f"{max(1.3, size * 0.15):.1f}"
        return (f'<text fill="{halo}" stroke="{halo}" stroke-width="{width}" stroke-linejoin="round" {body}'
                f'<text fill="{fill}" {body}')
    return f'<text fill="{fill}" {body}'


def _loose(theme) -> str:
    """The outline colour for text that sits on the image's background: none on an opaque
    background, the background colour (contrasting with the text) on a transparent one."""
    return "" if _OPT.background else theme["bg"]


def _path_d(path) -> str:
    out, i, n = [], 0, path.elementCount()
    while i < n:
        e = path.elementAt(i)
        if e.isMoveTo():
            out.append(f"M{e.x:.1f},{e.y:.1f}")
        elif e.isLineTo():
            out.append(f"L{e.x:.1f},{e.y:.1f}")
        elif e.isCurveTo() and i + 2 < n:
            c2, p = path.elementAt(i + 1), path.elementAt(i + 2)
            out.append(f"C{e.x:.1f},{e.y:.1f} {c2.x:.1f},{c2.y:.1f} {p.x:.1f},{p.y:.1f}")
            i += 2
        i += 1
    return " ".join(out)


LAST_DOC = None   # the layout of the image drawn last (animations use it)


class _Doc:
    """Collects SVG parts and the drawing's bounds."""

    def __init__(self, theme):
        self.t, self.parts, self.x0, self.y0, self.x1, self.y1 = theme, [], 1e9, 1e9, -1e9, -1e9
        # where things are, for animations: every placed rectangle, and what a style marks
        self.rects, self.marks, self.offset, self.size = [], {}, (0.0, 0.0), (0.0, 0.0)

    def add(self, svg, rect=None):
        self.parts.append(svg)
        if rect is not None:
            self.grow(rect)
            self.rects.append(QRectF(rect))

    def grow(self, rect: QRectF):
        self.x0, self.y0 = min(self.x0, rect.left()), min(self.y0, rect.top())
        self.x1, self.y1 = max(self.x1, rect.right()), max(self.y1, rect.bottom())

    def svg(self, title, legend=None) -> str:
        t = self.t
        if self.x0 > self.x1:
            self.x0 = self.y0 = self.x1 = self.y1 = 0
        margin, head = 32, 52
        if not _OPT.legend:
            legend = None
        width = max(self.x1 - self.x0 + 2 * margin, 420)
        rows, x = [[]], margin
        for swatch, label in legend or []:
            w = 40 + _width(label, 11) * 1.08
            if rows[-1] and x + w > width - margin:
                rows.append([])
                x = margin
            rows[-1].append((swatch, label, x))
            x += w
            width = max(width, x + margin)
        legend_h = 20 + 20 * len(rows) if legend else 0
        height = self.y1 - self.y0 + 2 * margin + head + legend_h
        dx, dy = margin - self.x0, margin + head - self.y0
        global LAST_DOC
        self.offset, self.size, LAST_DOC = (dx, dy), (width, height), self
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{width:.0f}" height="{height:.0f}" '
            f'viewBox="0 0 {width:.0f} {height:.0f}" font-family="{fonts.css(_FAMILY)}">',
            f"<title>{_esc(title)}</title>",
            (f'<rect class="background" x="0" y="0" width="{width:.0f}" height="{height:.0f}" fill="{t["bg"]}"/>'
             if _OPT.background else ""),
            (_grid_lines(width, height, t["grid"]) if _OPT.background and t.get("grid") else ""),
            _text(margin, 40, title, 20, t["ink"], bold=True, halo=_loose(t),
                  family=_TITLE_FAMILY if _TITLE_FAMILY and _TITLE_FAMILY != _FAMILY else ""),
            f'<g transform="translate({dx:.1f},{dy:.1f})">', *self.parts, "</g>",
        ]
        if "overview" in self.marks:
            uri, ow, oh = self.marks["overview"]
            parts.append(f'<image href="{uri}" xlink:href="{uri}" x="{width - margin - ow:.0f}" y="{12:.0f}" '
                         f'width="{ow}" height="{oh}"/>')
        if legend:
            for i, row in enumerate(rows):
                y = height - 22 - (len(rows) - 1 - i) * 20
                for swatch, label, x in row:
                    parts.append(swatch(x, y - 5))
                    parts.append(_text(x + 22, y, label, 11, t["muted"], halo=_loose(t)))
        parts.append("</svg>")
        return "\n".join(parts)


def _grid_lines(width, height, colour) -> str:
    """The squared paper of a blueprint."""
    d = "".join(f"M{x} 0V{height:.0f}" for x in range(0, int(width) + 1, 20))
    d += "".join(f"M0 {y}H{width:.0f}" for y in range(0, int(height) + 1, 20))
    return f'<path d="{d}" stroke="{colour}" stroke-width="0.6" stroke-opacity="0.55" fill="none"/>'


def image_title(controller, detail) -> str:
    """The title written on an image: the one typed in the image window, otherwise the
    recipe's name, or for a general image (which names no layers) a title made from its tools."""
    if _OPT.title.strip():
        return _OPT.title.strip()
    return builder.general_title(controller.plan) if detail == "general" else controller.model_name


def _fill(theme, node):
    if node.kind == "input":
        return theme["input"]
    if node.kind == "output":
        return theme["output"]
    if node.kind == "left":
        return theme["left"]
    group = node.alg.group() if node.alg is not None else ""
    return (theme["tints"].get(group, theme["tint"]), theme["step"][1])


# ---------------------------------------------------- box layouts (shared)

def _relayout(view, sizes, lane_of=None, lane_names=None, gap=26, lane_pad=26):
    """Give the flowchart's boxes new sizes, keep their columns and order, route the lines
    again. With lanes, each node goes into its lane's band. Returns {lane: (top, bottom)}."""
    view._building = True
    for key, box in view.nodes.items():
        for child in box.childItems():
            child.setVisible(False)   # text and asked-settings boxes: drawn differently here
        w, h = sizes.get(key, (box.rect().width(), box.rect().height()))
        box.setRect(QRectF(0, 0, w, h))
    columns = {}
    for key, box in view.nodes.items():
        columns.setdefault(round(box.pos().x()), []).append(key)
    bands = {}
    if lane_of is None:
        for keys in columns.values():
            keys.sort(key=lambda k: view.nodes[k].pos().y())
            bottom = -1e9
            for k in keys:
                box = view.nodes[k]
                y = max(box.pos().y(), bottom + gap)
                box.setPos(box.pos().x(), y)
                bottom = y + box.rect().height()
    else:
        top = 0.0
        for lane in lane_names:
            height = 0.0
            for x, keys in columns.items():
                inside = sorted((k for k in keys if lane_of[k] == lane), key=lambda k: view.nodes[k].pos().y())
                y = top + lane_pad
                for k in inside:
                    view.nodes[k].setPos(x, y)
                    y += view.nodes[k].rect().height() + gap
                height = max(height, y - gap - top + lane_pad)
            if height > 0:
                bands[lane] = (top, top + max(height, 90))
                top += max(height, 90) + 6
    view._building = False
    view.reroute()
    return bands


def _edges_svg(view, theme, doc):
    for a, b, item in view.edges:
        if isinstance(item, QGraphicsPathItem):
            dashed = any(s[0] == a and s[1] == b for s in view.dashed)
            extent = not dashed and item.pen().style() == Qt.PenStyle.DotLine
            colour = theme["left"][1] if dashed else theme["line"]
            dash = ' stroke-dasharray="6 5"' if dashed else ' stroke-dasharray="0.1 4.5"' if extent else ""
            path = item.path()
            end = path.pointAtPercent(1)
            doc.add(f'<path d="{_path_d(path)}" fill="none" stroke="{colour}" stroke-width="{2.2 if extent else 1.8}"{dash} '
                    f'stroke-linecap="round" stroke-linejoin="round"/>'
                    + _arrow(end.x() + 1, end.y(), path.angleAtPercent(1), colour), path.boundingRect())
        elif isinstance(item, QGraphicsPixmapItem) and item.data(0) == "extent":
            p = item.pos()
            doc.add(_extent_mark(p.x(), p.y() + 2, theme))
        elif isinstance(item, QGraphicsSimpleTextItem):
            p = item.pos()
            doc.add(_text(p.x(), p.y() + 10, item.text(), 10, theme["muted"], italic=True, halo=theme["bg"]))


def _title_lines(node, width):
    """Long names wrap (up to three lines) instead of being cut off."""
    room = width - (64 if node.kind in ("input", "output") else 60)
    return _wrap(node.title, room, 14, True, lines=None)


def _card_size(node, width=236):
    extra = (len(_title_lines(node, width)) - 1) * 18
    if node.kind in ("input", "output"):
        return width, (62 if node.sub else 48) + extra
    rows = len(node.rows)
    notes = len(_wrap(node.note, width - 60, 11, lines=3)) * 15 + 4 if node.note else 0
    return width, 52 + extra + (18 if node.sub else 0) + rows * 19 + (6 if rows else 0) + notes


def _card_svg(node, box, theme, doc, width=236):
    r = QRectF(box.pos().x(), box.pos().y(), box.rect().width(), box.rect().height())
    bg, accent = _fill(theme, node)
    dashed = ' stroke-dasharray="6 4"' if node.kind == "left" else ""
    stroke = f' stroke="{accent}" stroke-width="1.4"{dashed}' if node.kind in ("left",) else \
        f' stroke="{accent}" stroke-opacity="0.35" stroke-width="1"'
    parts = [_shadow(r.x(), r.y(), r.width(), r.height(), 12, theme["shadow"]) if node.kind != "left" else "",
             f'<rect x="{r.x():.1f}" y="{r.y():.1f}" width="{r.width():.1f}" height="{r.height():.1f}" rx="12" '
             f'fill="{bg}"{stroke}/>']
    if node.count > 1:
        parts.insert(0, f'<rect x="{r.x() + 6:.1f}" y="{r.y() + 6:.1f}" width="{r.width():.1f}" '
                     f'height="{r.height():.1f}" rx="12" fill="{bg}" stroke="{accent}" stroke-opacity="0.35"/>')
    if node.kind in ("input", "output"):
        parts.append(f'<rect x="{r.x():.1f}" y="{r.y():.1f}" width="6" height="{r.height():.1f}" rx="3" fill="{accent}"/>')
        parts.append(f'<circle cx="{r.x() + 30:.1f}" cy="{r.center().y():.1f}" r="15" fill="{theme["icon_bg"]}"/>')
        parts.append(_image(_layer_icon(node.layer_kind), r.x() + 20, r.center().y() - 10, 20))
        lines = _title_lines(node, r.width())
        y = r.center().y() - (len(lines) - 1) * 9 + (5 if not node.sub else -4)
        for line in lines:
            parts.append(_text(r.x() + 54, y, line, 14, theme["ink"], True))
            y += 18
        if node.sub:
            parts.append(_text(r.x() + 54, y, _elide(node.sub, r.width() - 64, 11), 11, theme["muted"]))
    else:
        parts.append(f'<circle cx="{r.x() + 26:.1f}" cy="{r.y() + 26:.1f}" r="15" fill="{theme["icon_bg"]}"/>')
        parts.append(_image(_alg_icon(node.alg), r.x() + 16, r.y() + 16, 20))
        y = r.y() + 25
        for line in _title_lines(node, r.width()):
            parts.append(_text(r.x() + 50, y, line, 14, theme["ink"] if node.kind != "left" else theme["muted"], True))
            y += 18
        y -= 1
        if node.sub:
            parts.append(_text(r.x() + 50, y, _elide(node.sub, r.width() - 60, 11), 11, theme["muted"]))
            y += 18
        if node.note:
            for line in _wrap("✎ " + node.note, r.width() - 60, 11, lines=3):
                parts.append(_text(r.x() + 50, y, line, 11, theme["muted"], italic=True))
                y += 15
        y += 4
        for label, value, asked, d in node.rows:
            colour = theme["setting"][1] if asked else theme["ink"]
            if asked:
                parts.append(f'<rect x="{r.x() + 10:.1f}" y="{y - 13:.1f}" width="{r.width() - 20:.1f}" height="18" '
                             f'rx="5" fill="{theme["setting"][0]}"/>')
                parts.append(_image(_setting_icon(d), r.x() + 15, y - 11, 13))
            vw = _width(value, 11, True)
            parts.append(_text(r.x() + 32, y, _elide(label, r.width() - 50 - vw, 11), 11, colour))
            if value:
                parts.append(_text(r.right() - 14, y, value, 11, colour, True, "end"))
            y += 19
    doc.add("".join(parts), r)


def _cards(controller, detail, theme, left_out):
    view = _view(controller, detail, left_out)
    nodes = _nodes(controller, view, detail)
    _relayout(view, {k: _card_size(n) for k, n in nodes.items()}, gap=30)
    doc = _Doc(theme)
    _edges_svg(view, theme, doc)
    for key, node in nodes.items():
        _card_svg(node, view.nodes[key], theme, doc)
    view.deleteLater()
    return doc.svg(image_title(controller, detail),
                   None if detail == "general" else _legend(theme, ("input", "setting", "output")))


def _lane(node):
    if node.kind == "input":
        return "Input data"
    if node.kind == "output":
        return "Results"
    group = node.alg.group() if node.alg is not None else ""
    return "Preparation" if group in PREPARATION else "Analysis"


def _swimlanes(controller, detail, theme, left_out):
    view = _view(controller, detail, left_out)
    nodes = _nodes(controller, view, detail)
    names = ["Input data", "Preparation", "Analysis", "Results"]
    lane_of = {k: _lane(n) for k, n in nodes.items()}
    bands = _relayout(view, {k: _card_size(n) for k, n in nodes.items()}, lane_of, names, gap=22)
    doc = _Doc(theme)
    xs = [b.pos().x() for b in view.nodes.values()]
    left = min(xs, default=0) - 150
    right = max((b.pos().x() + b.rect().width() for b in view.nodes.values()), default=400) + 30
    for i, lane in enumerate(names):
        if lane not in bands:
            continue
        top, bottom = bands[lane]
        colour = theme["lanes"][i % len(theme["lanes"])]
        paint = (f'fill="{colour}"' if _OPT.background else
                 f'fill="none" stroke="{theme["muted"]}" stroke-opacity="0.45" stroke-width="1.2" stroke-dasharray="6 4"')
        doc.add(f'<rect x="{left:.1f}" y="{top:.1f}" width="{right - left:.1f}" height="{bottom - top:.1f}" '
                f'rx="14" {paint}/>', QRectF(left, top, right - left, bottom - top))
        doc.add(_text(left + 18, top + 28, lane, 15, theme["ink"], True, halo=_loose(theme)))
        hint = {"Input data": "layers to choose", "Preparation": "selecting, joining, saving, creating",
                "Analysis": "geometry, overlay, analysis", "Results": "what the workflow makes"}[lane]
        for j, line in enumerate(_wrap(hint, 120, 11, lines=3)):
            doc.add(_text(left + 18, top + 46 + j * 14, line, 11, theme["muted"], halo=_loose(theme)))
    _edges_svg(view, theme, doc)
    for key, node in nodes.items():
        _card_svg(node, view.nodes[key], theme, doc)
    view.deleteLater()
    return doc.svg(image_title(controller, detail))


def _legend(theme, kinds):
    items = []
    if "input" in kinds:
        items.append((lambda x, y: f'<rect x="{x}" y="{y - 6}" width="16" height="12" rx="3" fill="{theme["input"][0]}" '
                      f'stroke="{theme["input"][1]}"/>', "layer you choose"))
    if "setting" in kinds:
        items.append((lambda x, y: f'<rect x="{x}" y="{y - 6}" width="16" height="12" rx="3" fill="{theme["setting"][0]}" '
                      f'stroke="{theme["setting"][1]}"/>', "setting you enter"))
    if "step" in kinds:
        items.append((lambda x, y: f'<circle cx="{x + 8}" cy="{y}" r="7" fill="{theme["step"][0]}" stroke="{theme["step"][1]}" '
                      f'stroke-width="1.5"/>', "tool"))
    if "output" in kinds:
        items.append((lambda x, y: f'<rect x="{x}" y="{y - 6}" width="16" height="12" rx="6" fill="{theme["output"][0]}" '
                      f'stroke="{theme["output"][1]}"/>', "result"))
    items.append((lambda x, y: f'<line x1="{x}" y1="{y}" x2="{x + 16}" y2="{y}" stroke="{theme["left"][1]}" '
                  f'stroke-width="2" stroke-dasharray="4 3"/>', "left out of the recipe"))
    return items


# ------------------------------------------------------ grid layouts

def _grid(view, nodes, dx, dy):
    """Positions on a grid: the flowchart's columns, and each node level with the nodes it
    connects to where there is room (fewer crossings than keeping the flowchart's rows)."""
    xs = sorted({round(view.nodes[k].pos().x()) for k in nodes})
    col_of = {x: i for i, x in enumerate(xs)}
    columns = {}
    for k in nodes:
        columns.setdefault(col_of[round(view.nodes[k].pos().x())], []).append(k)
    edges = [(a, b) for a, b, _l in view.edge_specs if a in nodes and b in nodes]
    y = {}

    def place(keys, desired):
        """Each node level with the nodes it connects to, at least dy apart. Nodes with nothing
        to line up with keep their place (or the next free one)."""
        keys.sort(key=lambda k: (desired.get(k, y.get(k, 0.0)), view.nodes[k].pos().y()))
        placed, last = [], None
        for k in keys:
            want = desired.get(k, y.get(k))
            if want is None:
                want = last + dy if last is not None else 0.0
            v = want if last is None else max(want, last + dy)
            placed.append(v)
            last = v
        diffs = [p - desired[k] for k, p in zip(keys, placed) if k in desired]
        shift = sum(diffs) / len(diffs) if diffs else 0.0
        for k, p in zip(keys, placed):
            y[k] = p - shift

    order = sorted(columns)
    for k_i, k in enumerate(sorted(columns[order[0]], key=lambda k: view.nodes[k].pos().y())):
        y[k] = k_i * dy
    for _round in range(3):
        for col in order[1:]:
            desired = {}
            for k in columns[col]:
                parents = [y[a] for a, b in edges if b == k and a in y]
                if parents:
                    desired[k] = sum(parents) / len(parents)
            place(columns[col], desired)
        for col in reversed(order[:-1]):
            desired = {}
            for k in columns[col]:
                children = [y[b] for a, b in edges if a == k and b in y]
                if children:
                    desired[k] = sum(children) / len(children)
            place(columns[col], desired)
    top = min(y.values(), default=0)
    return {k: QPointF(col_of[round(view.nodes[k].pos().x())] * dx, y[k] - top) for k in nodes}


def _minimal(controller, detail, theme, left_out):
    view = _view(controller, detail, left_out)
    nodes = _nodes(controller, view, detail)
    dx = 210 * _OPT.spacing
    lw = min(180, dx - 24)   # labels no wider than the room between columns
    tallest = max((len(_wrap(n.title, lw, 12, True, lines=None)) for n in nodes.values()), default=1)
    pos = _grid(view, nodes, dx, 128 + 15 * max(tallest - 2, 0))   # room for long names
    doc = _Doc(theme)

    def _halo_text(*a, **k):
        return _text(*a, halo=theme['bg'], **k)

    radius = {k: (26 if n.kind in ("step", "left") else 19) for k, n in nodes.items()}
    marked = set()
    for a, b, _label in view.edge_specs:
        if a not in pos or b not in pos:
            continue
        p1, p2 = pos[a], pos[b]
        x1, x2 = p1.x() + radius[a] + 3, p2.x() - radius[b] - 5
        mid = (x2 - x1) / 2
        dashed = (a, b, _label) in view.dashed
        extent = _label == "extent" and not dashed
        colour = theme["left"][1] if dashed else theme["line"]
        dash = ' stroke-dasharray="5 5"' if dashed else ' stroke-dasharray="0.1 4.5" stroke-linecap="round"' if extent else ""
        d = f"M{x1:.1f},{p1.y():.1f} C{x1 + mid:.1f},{p1.y():.1f} {x2 - mid:.1f},{p2.y():.1f} {x2:.1f},{p2.y():.1f}"
        doc.add(f'<path d="{d}" fill="none" stroke="{colour}" stroke-opacity="0.75" stroke-width="{2 if extent else 1.4}"{dash}/>'
                + _arrow(x2 + 2, p2.y(), 0, colour, 8, 7))
        if extent and b not in marked:
            marked.add(b)
            doc.add(_extent_mark(x2 - 28, p2.y() - 18, theme, detail == "specific"))
    for key, node in nodes.items():
        p, r = pos[key], radius[key]
        bg, accent = _fill(theme, node) if node.kind != "step" else (theme["step"][0], theme["step"][1])
        dash = ' stroke-dasharray="4 3"' if node.kind == "left" else ""
        parts = []
        if node.count > 1:
            parts.append(f'<circle cx="{p.x() + 5:.1f}" cy="{p.y() - 4:.1f}" r="{r}" fill="{bg}" stroke="{accent}" '
                         f'stroke-opacity="0.5" stroke-width="1.2"/>')
        parts.append(f'<circle cx="{p.x():.1f}" cy="{p.y():.1f}" r="{r}" fill="{bg}" stroke="{accent}" '
                     f'stroke-width="1.6"{dash}/>')
        icon = _layer_icon(node.layer_kind) if node.kind in ("input", "output") else _alg_icon(node.alg)
        size = 22 if r > 20 else 18
        if theme["bg"] != "#FFFFFF" and node.kind in ("step", "left"):
            parts.append(f'<circle cx="{p.x():.1f}" cy="{p.y():.1f}" r="{size / 2 + 3}" fill="{theme["icon_bg"]}"/>')
        parts.append(_image(icon, p.x() - size / 2, p.y() - size / 2, size))
        y = p.y() + r + 18
        for line in _wrap(node.title, lw, 12, True, lines=None):
            parts.append(_halo_text(p.x(), y, line, 12, theme["ink"] if node.kind != "left" else theme["muted"], True, "middle"))
            y += 15
        sub = node.sub
        if node.kind in ("step",) and detail == "specific" and node.rows:
            sub = " · ".join(v for _l, v, _a, _d in node.rows if v)[:60]
        if sub:
            parts.append(_halo_text(p.x(), y, _elide(sub, lw, 10), 10, theme["muted"], anchor="middle"))
            y += 14
        asked = [label for label, _v, a, _d in node.rows if a]
        if asked:
            parts.append(_halo_text(p.x(), y, _elide("asks: " + ", ".join(asked), lw, 10), 10, theme["setting"][1], True,
                                    "middle"))
            y += 14
        if node.note:
            parts.append(_halo_text(p.x(), y, _elide("✎ " + node.note, lw + 10, 10), 10, theme["muted"], italic=True,
                                    anchor="middle"))
        doc.add("".join(parts), QRectF(p.x() - lw / 2 - 5, p.y() - r - 6, lw + 10, y - p.y() + r + 10))
    view.deleteLater()
    return doc.svg(image_title(controller, detail),
                   None if detail == "general" else _legend(theme, ("input", "step", "output")))


# ------------------------------------------------------------- metro

def _metro_path(p1, p2):
    """Horizontal, then 45°, then horizontal."""
    dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
    if abs(dy) < 0.5 or dx <= abs(dy):
        return f"M{p1.x():.1f},{p1.y():.1f} L{p2.x():.1f},{p2.y():.1f}"
    run = (dx - abs(dy)) / 2
    a = QPointF(p1.x() + run, p1.y())
    b = QPointF(p2.x() - run, p2.y())
    return f"M{p1.x():.1f},{p1.y():.1f} L{a.x():.1f},{a.y():.1f} L{b.x():.1f},{b.y():.1f} L{p2.x():.1f},{p2.y():.1f}"


def _metro(controller, detail, theme, left_out):
    view = _view(controller, detail, left_out)
    nodes = _nodes(controller, view, detail)
    tallest = max((len(_wrap(n.title, 160, 12, True, lines=None)) for n in nodes.values()
                   if n.kind in ("input", "output")), default=1)
    mdx = 190 * _OPT.spacing
    slw = min(175, mdx - 15)   # station labels no wider than the room between stations
    pos = _grid(view, nodes, mdx, 96 + 15 * max(tallest - 2, 0))   # room for long names
    forward = {}
    for a, b, _l in view.edge_specs:
        if (a, b, _l) not in view.dashed and _l != "extent":
            forward.setdefault(a, set()).add(b)
    inputs = [k for k, n in nodes.items() if n.kind == "input"]
    # a step that uses no layer (only an extent, say) starts a line of its own
    fed = {b for _a, bs in forward.items() for b in bs}
    starts = [k for k, n in nodes.items() if n.kind == "step" and k not in fed and forward.get(k)]
    reach = {}
    for k in inputs + starts:
        seen, todo = {k}, [k]
        while todo:
            for nxt in forward.get(todo.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        reach[k] = seen
    lines = inputs + starts
    colour = {k: theme["metro"][i % len(theme["metro"])] for i, k in enumerate(lines)}
    dashes = theme.get("dashes")
    dash_of = {k: (dashes[i % len(dashes)] if dashes else "") for i, k in enumerate(lines)}
    width, spacing = 6, 7
    doc = _Doc(theme)

    def _halo_text(*a, **k):
        return _text(*a, halo=theme['bg'], **k)

    lines_at = {k: [i for i in lines if k in reach[i]] for k in nodes}
    # lines
    marked = set()
    for a, b, label in view.edge_specs:
        if a not in pos or b not in pos:
            continue
        if label == "extent" and (a, b, label) not in view.dashed:
            if not any(a in reach[i] and b in reach[i] for i in lines):
                end = QPointF(pos[b].x() - 12, pos[b].y())
                doc.add(f'<path d="{_metro_path(pos[a], end)}" fill="none" stroke="{theme["line"]}" stroke-width="2.6" '
                        f'stroke-dasharray="0.1 5" stroke-linecap="round" stroke-linejoin="round"/>')
                if b not in marked:
                    marked.add(b)
                    doc.add(_extent_mark(end.x() - 26, end.y() + 4, theme, detail == "specific"))
            continue
        if (a, b, label) in view.dashed:
            doc.add(f'<path d="{_metro_path(pos[a], pos[b])}" fill="none" stroke="{theme["left"][1]}" stroke-width="2.5" '
                    f'stroke-dasharray="3 5" stroke-linecap="round"/>')
            continue
        through = [i for i in lines if a in reach[i] and b in reach[i]]
        for n, i in enumerate(through):
            off_a = (lines_at[a].index(i) - (len(lines_at[a]) - 1) / 2) * spacing if i in lines_at[a] else 0
            off_b = (lines_at[b].index(i) - (len(lines_at[b]) - 1) / 2) * spacing if i in lines_at[b] else 0
            p1, p2 = QPointF(pos[a].x(), pos[a].y() + off_a), QPointF(pos[b].x(), pos[b].y() + off_b)
            dash = f' stroke-dasharray="{dash_of[i]}"' if dash_of[i] else ""
            doc.add(f'<path d="{_metro_path(p1, p2)}" fill="none" stroke="{colour[i]}" stroke-width="{width}" '
                    f'stroke-linecap="round" stroke-linejoin="round"{dash}/>')
    # stations
    for key, node in nodes.items():
        p = pos[key]
        n = max(len(lines_at[key]), 1)
        h = 12 + (n - 1) * spacing
        if node.kind == "input":
            c = colour[key]
            doc.add(f'<circle cx="{p.x():.1f}" cy="{p.y():.1f}" r="10" fill="{c}" stroke="{theme["bg"]}" stroke-width="3"/>')
            lines = _wrap(node.title, 150, 12, True, lines=None)
            subs = _wrap(node.sub, 150, 10, lines=None) if node.sub else []
            y = p.y() + 4 - (len(lines) - 1) * 7.5 - 6.5 * len(subs)
            top = y - 12
            for line in lines:
                doc.add(_halo_text(p.x() - 18, y, line, 12, theme["ink"], True, "end"))
                y += 15
            for line in subs:
                doc.add(_halo_text(p.x() - 18, y, line, 10, theme["muted"], anchor="end"))
                y += 13
            doc.grow(QRectF(p.x() - 175, top, 190, y - top))
            continue
        if node.kind == "output":
            c = colour[lines_at[key][0]] if lines_at[key] else theme["line"]
            doc.add(f'<circle cx="{p.x():.1f}" cy="{p.y():.1f}" r="11" fill="{theme["bg"]}" stroke="{c}" stroke-width="5"/>')
            lines = _wrap(node.title, 160, 12, True, lines=None)
            subs = _wrap(node.sub, 160, 10, lines=None) if node.sub else []
            y = p.y() + 4 - (len(lines) - 1) * 7.5 - 6.5 * len(subs)
            top = y - 12
            for line in lines:
                doc.add(_halo_text(p.x() + 20, y, line, 12, theme["ink"], True))
                y += 15
            for line in subs:
                doc.add(_halo_text(p.x() + 20, y, line, 10, theme["muted"]))
                y += 13
            doc.grow(QRectF(p.x() - 14, top, 200, y - top))
            continue
        left = node.kind == "left"
        stroke = theme["left"][1] if left else theme["ink"]
        fill = theme["left"][0] if left else theme["bg"] if theme["bg"] != "#1D2125" else "#FFFFFF"
        dash = ' stroke-dasharray="3 3"' if left else ""
        doc.add(f'<rect x="{p.x() - 8:.1f}" y="{p.y() - h / 2 - 2:.1f}" width="16" height="{h + 4:.1f}" rx="8" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="2.6"{dash}/>')
        label_y = p.y() - h / 2 - 12
        sub = node.sub if detail == "general" or not node.rows else " · ".join(v for _l, v, _a, _d in node.rows if v)
        if sub:
            doc.add(_halo_text(p.x(), label_y, _elide(sub, slw - 5, 10), 10, theme["muted"], anchor="middle"))
            label_y -= 14
        lines = _wrap(node.title, slw, 12, True, lines=None)
        for line in reversed(lines):
            doc.add(_halo_text(p.x(), label_y, line, 12, theme["muted"] if left else theme["ink"], True, "middle"))
            label_y -= 15
        doc.grow(QRectF(p.x() - slw / 2 - 3, label_y - 2, slw + 6, p.y() + h - label_y + 20))
        if node.note:
            doc.add(_halo_text(p.x(), p.y() + h / 2 + (32 if any(a for _l, _v, a, _d in node.rows) else 18),
                               _elide("✎ " + node.note, slw - 5, 10), 10, theme["muted"], italic=True, anchor="middle"))
        asked = [label for label, _v, a, _d in node.rows if a]
        if asked:
            doc.add(_halo_text(p.x(), p.y() + h / 2 + 18, _elide("asks: " + ", ".join(asked), slw - 5, 10), 10,
                               theme["setting"][1], True, "middle"))
    view.deleteLater()
    if detail == "general":   # the inputs are named by kind and number on the map itself
        return doc.svg(image_title(controller, detail))
    legend = []
    for k in inputs:
        c, dash = colour[k], dash_of[k]
        dashed = f' stroke-dasharray="{dash}"' if dash else ""
        legend.append(((lambda x, y, c=c, dashed=dashed: f'<line x1="{x}" y1="{y}" x2="{x + 16}" y2="{y}" stroke="{c}" '
                        f'stroke-width="5" stroke-linecap="round"{dashed}/>'),
                       nodes[k].sub if detail == "general" else nodes[k].title))
    return doc.svg(image_title(controller, detail), legend)


# ------------------------------------------------------- data-centred

def _layer_picture(d, x, y, w, h, found) -> str:
    """The layer as QGIS draws it, in its own style, fitted into the card (an embedded picture,
    drawn at twice the size so it stays sharp)."""
    from qgis.PyQt.QtCore import QSize
    from . import snapshots
    from .cookbook import png_uri
    layer = d.get("layer") or found.get(d.get("number"))
    if layer is None:
        return ""
    try:
        image = snapshots.render(layer, (), [], QSize(int(w * 2), int(h * 2)), fill=None)
    except Exception:
        image = None
    if image is None:
        return ""
    uri = png_uri(image)
    return f'<image href="{uri}" xlink:href="{uri}" x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}"/>'


def _data(controller, detail, theme, left_out):
    c, plan = controller, controller.plan
    project = QgsProject.instance()
    data, tools, edges = {}, {}, []   # data key -> dict; tool key -> dict
    extent_edges = set()
    for uid, input_plan in plan.inputs.items():
        value = input_plan.used_by[0][2] if input_plan.used_by else None
        data[f"input:{uid}"] = dict(title=input_plan.recorded, sub=icons.layer_icon(input_plan.layer_kind)[1],
                                    kind=input_plan.layer_kind,
                                    layer=_layer_of(value, c), role="input")
    out_key = {}
    for step_plan in plan.steps:
        tkey = f"tool:{step_plan.number}"
        values = [v for _l, v, _a, _d in _rows([step_plan], detail) if v]
        tools[tkey] = dict(title=step_plan.step.alg.displayName(), sub=" · ".join(values), alg=step_plan.step.alg,
                           number=step_plan.number)
        for param in step_plan.params:
            if param.kind in ("link", "input"):
                d = step_plan.step.alg.parameterDefinition(param.name)
                for src in param.sources:
                    a = out_key.get((src[1], src[2])) if src[0] == "link" else f"input:{src[1]}"
                    edges.append((a, tkey))
                    if d is not None and d.type() == "extent":
                        extent_edges.add((a, tkey))
            elif param.kind in ("output", "temp"):
                key = param.uid
                out_key[(step_plan.number, param.name)] = key
                result = (step_plan.step.results or {}).get(param.name)
                layer = project.mapLayer(param.style_layer_id) if param.style_layer_id else _layer_of(result, c)
                role = {"final": "output", "save": "output"}.get(param.role, "intermediate")
                data[key] = dict(title=param.default_display, sub=icons.layer_icon(param.layer_kind)[1], kind=param.layer_kind,
                                 layer=layer, role=role, number=step_plan.number)
                edges.append((tkey, key))
    edges = list(dict.fromkeys((a, b) for a, b in edges if a and b))
    # a layer used both for its features and its extent: an ordinary line
    used_fully = {(a, b) for a, b in edges if (a, b) not in extent_edges}
    extent_edges -= used_fully
    # columns: data, tool, data, tool…
    depth = {k: 0 for k in data if k.startswith("input:")}
    for _ in range(len(tools) + len(data) + 2):
        for a, b in edges:
            if a in depth:
                depth[b] = max(depth.get(b, 0), depth[a] + 1)
    for k in list(data) + list(tools):
        depth.setdefault(k, 0 if k in data else 1)
    # each layer you start from sits just before the tool that first uses it, so its line
    # doesn't cross other cards to get there
    for k in data:
        if k.startswith("input:"):
            users = [depth[b] for a, b in edges if a == k and b in depth]
            if users:
                depth[k] = max(min(users) - 1, 0)
    columns = {}
    for k, d in depth.items():
        columns.setdefault(d, []).append(k)
    card_w, tool_w = 176, 170
    role_words = {"input": "input layer", "output": "result", "intermediate": "intermediate result"}
    title_lines = {k: _wrap(d["title"], card_w - 36, 12, True, lines=2) for k, d in data.items()}
    sub_lines = {k: _wrap(f"{d['sub']} · {role_words[d['role']]}", card_w - 18, 10, lines=2) for k, d in data.items()}
    # every card the same height, so cards in a row line up
    same_h = max((128 + 14 * len(title_lines[k]) + 12 * (len(sub_lines[k]) - 1) for k in data), default=142)
    card_h = {k: same_h for k in data}

    def tool_title(d):
        return f"{d['number']}. {d['title']}" if _OPT.numbers else d["title"]

    tool_lines = {k: _wrap(tool_title(d), tool_w - 50, 12, True, lines=2) for k, d in tools.items()}
    tool_h = {k: 32 if len(tool_lines[k]) == 1 else 46 for k in tools}
    x_of, x = {}, 0.0
    for col in sorted(columns):
        x_of[col] = x
        x += (card_w if col % 2 == 0 else tool_w) + 70 * _OPT.spacing
    height = {k: card_h[k] if k in data else tool_h[k] + 22 for k in depth}   # a tool's settings line goes below it
    ys = {}

    def place(keys, desired):
        """Each card level with what it connects to, pushed apart where they'd overlap."""
        keys.sort(key=lambda k: (desired.get(k, ys.get(k, 0.0)), k))
        placed, last = [], None
        for k in keys:
            want = desired.get(k, ys.get(k))
            if want is None:
                want = last[0] + (last[1] + height[k]) / 2 + 30 if last else 0.0
            if last is not None:
                want = max(want, last[0] + (last[1] + height[k]) / 2 + 30)
            placed.append(want)
            last = (want, height[k])
        diffs = [p - desired[k] for k, p in zip(keys, placed) if k in desired]
        shift = sum(diffs) / len(diffs) if diffs else 0.0
        for k, p in zip(keys, placed):
            ys[k] = p - shift

    order = sorted(columns)
    starting = [k for k in data if k.startswith("input:")]

    def fit(k, want):
        """The free place in k's column nearest ``want``."""
        others = [(ys[other], height[other]) for other in columns[depth[k]] if other != k and other in ys and other not in loose]
        tries = [want] + [y + s * ((h + height[k]) / 2 + 30) for y, h in others for s in (-1, 1)]

        def free(v):
            return all(abs(v - y) >= (h + height[k]) / 2 + 30 - 0.5 for y, h in others)
        return min((v for v in tries if free(v)), key=lambda v: abs(v - want))

    for k in starting:
        ys[k] = 0.0
    for _round in range(3):
        # the main line: each tool level with its first input, each result level with its tool
        loose = set(starting)
        for col in order:
            desired = {}
            for k in columns[col]:
                if k in loose:
                    continue
                parents = [ys[a] for a, b in edges if b == k and a in ys]
                if parents:
                    desired[k] = parents[0]
            place([k for k in columns[col] if k not in loose], desired)
        # results stacked up in a column take more room than their tools: each tool moves
        # level with its result, so its line runs straight
        for col in order:
            tool_keys = [k for k in columns[col] if k in tools]
            desired = {}
            for k in tool_keys:
                children = [ys[b] for a, b in edges if a == k and b in ys]
                if children:
                    desired[k] = children[0]
            if tool_keys:
                place(tool_keys, desired)
        # starting layers: level with the tools that use them, wherever there's room
        for k in starting:
            users = [ys[b] for a, b in edges if a == k and b in ys]
            ys[k] = fit(k, sum(users) / len(users) if users else 0.0)
            loose.discard(k)
    top = min((ys[k] - height[k] / 2 for k in ys), default=0.0)
    ys = {k: v - top for k, v in ys.items()}
    doc = _Doc(theme)

    def _halo_text(*a, **k):
        return _text(*a, halo=theme['bg'], **k)

    pill = {k: tool_w if len(tool_lines[k]) > 1 else min(tool_w, _width(tool_lines[k][0], 12, True) + 56) for k in tools}
    marked = set()
    centre = {k: QPointF(x_of[depth[k]] + (card_w if k in data else tool_w) / 2, ys[k]) for k in depth}
    half_w = {k: (card_w if k in data else pill[k]) / 2 for k in depth}
    half_h = {k: (card_h[k] if k in data else tool_h[k]) / 2 for k in depth}
    boxes = {k: QRectF(centre[k].x() - half_w[k], centre[k].y() - half_h[k], 2 * half_w[k], 2 * half_h[k]) for k in depth}
    for a, b in edges:
        p1 = QPointF(centre[a].x() + half_w[a], centre[a].y())
        p2 = QPointF(centre[b].x() - half_w[b] - 4, centre[b].y())
        mid = (p2.x() - p1.x()) / 2
        extent = (a, b) in extent_edges
        dash = ' stroke-dasharray="0.1 4.5" stroke-linecap="round"' if extent else ""
        # cards in between: go around them (above or below, whichever is nearer), never behind
        between = [r for k, r in boxes.items() if depth[a] < depth[k] < depth[b]]   # only columns in between
        low, high = min(p1.y(), p2.y()) - 8, max(p1.y(), p2.y()) + 8
        blocking = [r for r in between if r.top() < high and r.bottom() > low]
        if blocking:
            above = min(r.top() for r in between) - 18
            below = max(r.bottom() for r in between) + 18
            yc = above if abs(above - (p1.y() + p2.y()) / 2) <= abs(below - (p1.y() + p2.y()) / 2) else below
            x1, x2 = p1.x() + 34, p2.x() - 34
            d = (f"M{p1.x():.1f},{p1.y():.1f} C{p1.x() + 22:.1f},{p1.y():.1f} {p1.x() + 12:.1f},{yc:.1f} {x1:.1f},{yc:.1f} "
                 f"H{x2:.1f} C{p2.x() - 12:.1f},{yc:.1f} {p2.x() - 22:.1f},{p2.y():.1f} {p2.x():.1f},{p2.y():.1f}")
            route = QPainterPath(p1)
            route.cubicTo(QPointF(p1.x() + 22, p1.y()), QPointF(p1.x() + 12, yc), QPointF(x1, yc))
            route.lineTo(QPointF(x2, yc))
            route.cubicTo(QPointF(p2.x() - 12, yc), QPointF(p2.x() - 22, p2.y()), p2)
            top_y, bottom_y = min(yc, p1.y(), p2.y()), max(yc, p1.y(), p2.y())
            bounds = QRectF(p1.x(), top_y - 4, p2.x() - p1.x(), bottom_y - top_y + 8)
        else:
            d = (f"M{p1.x():.1f},{p1.y():.1f} C{p1.x() + mid:.1f},{p1.y():.1f} {p2.x() - mid:.1f},{p2.y():.1f} "
                 f"{p2.x():.1f},{p2.y():.1f}")
            route = QPainterPath(p1)
            route.cubicTo(QPointF(p1.x() + mid, p1.y()), QPointF(p2.x() - mid, p2.y()), p2)
            bounds = None
        doc.marks.setdefault("flows", []).append((a, b, route))
        doc.add(f'<path d="{d}" fill="none" stroke="{theme["line"]}" stroke-width="{2.2 if extent else 1.6}" '
                f'stroke-opacity="0.8"{dash}/>' + _arrow(p2.x() + 2, p2.y(), 0, theme["line"], 8, 7), bounds)
        if extent and b not in marked:
            marked.add(b)
            doc.add(_extent_mark(p2.x() - 26, p2.y() - 18, theme, detail == "specific"))
    from . import snapshots
    try:
        found, _used, doc.keep = snapshots.result_layers(controller)   # results that are gone are made again
    except Exception:
        found = {}
    doc.marks.update(boxes=boxes, depth=depth, data=set(data), tools=set(tools),
                     step_of={k: d["number"] for k, d in tools.items()})
    for k, d in data.items():
        p = centre[k]
        ch = card_h[k]
        r = QRectF(p.x() - card_w / 2, p.y() - ch / 2, card_w, ch)
        bg, accent = {"input": theme["input"], "output": theme["output"]}.get(d["role"], (theme["step"][0], theme["line"]))
        dash = ' stroke-dasharray="5 4"' if d["role"] == "intermediate" else ""
        parts = [_shadow(r.x(), r.y(), card_w, ch, 12, theme["shadow"]),
                 f'<rect x="{r.x():.1f}" y="{r.y():.1f}" width="{card_w}" height="{ch}" rx="12" fill="{bg}" '
                 f'stroke="{accent}" stroke-width="1.4"{dash}/>',
                 f'<rect x="{r.x() + 8:.1f}" y="{r.y() + 8:.1f}" width="{card_w - 16}" height="80" rx="8" fill="{theme["bg"]}"/>']
        thumb = _layer_picture(d, r.x() + 8, r.y() + 8, card_w - 16, 80, found)
        parts.append(thumb or _image(_layer_icon(d["kind"]), r.center().x() - 18, r.y() + 30, 36))
        parts.append(_image(_layer_icon(d["kind"]), r.x() + 10, r.y() + 96, 14))
        lines = title_lines[k]
        for i, line in enumerate(lines):
            parts.append(_text(r.x() + 28, r.y() + 107 + i * 14, line, 12, theme["ink"], True))
        for i, line in enumerate(sub_lines[k]):
            parts.append(_text(r.x() + 10, r.y() + 121 + (len(lines) - 1) * 14 + i * 12, line, 10, theme["muted"]))
        doc.add("".join(parts), r)
    for k, d in tools.items():
        p = centre[k]
        w, h = pill[k], tool_h[k]
        r = QRectF(p.x() - w / 2, p.y() - h / 2, w, h)
        parts = [f'<rect x="{r.x():.1f}" y="{r.y():.1f}" width="{w:.1f}" height="{h}" rx="16" '
                 f'fill="{theme["tints"].get(d["alg"].group(), theme["tint"])}" stroke="{theme["step"][1]}" stroke-width="1.2"/>',
                 f'<circle cx="{r.x() + 17:.1f}" cy="{p.y():.1f}" r="11" fill="{theme["icon_bg"]}"/>',
                 _image(_alg_icon(d["alg"]), r.x() + 9, p.y() - 8, 16)]
        for i, line in enumerate(tool_lines[k]):
            parts.append(_text(r.x() + 33, p.y() + 4 - 7 * (len(tool_lines[k]) - 1) + 14 * i, line, 12, theme["ink"], True))
        if d["sub"]:
            parts.append(_halo_text(p.x(), r.bottom() + 16, _elide(d["sub"], tool_w + 40, 10), 10, theme["muted"],
                                    anchor="middle"))
        doc.add("".join(parts), QRectF(r.x(), r.y(), w, h + 22))
    return doc.svg(image_title(controller, detail),
                   [(lambda x, y: f'<rect x="{x}" y="{y - 6}" width="16" height="12" rx="3" fill="{theme["input"][0]}" '
                     f'stroke="{theme["input"][1]}"/>', "input layer"),
                    (lambda x, y: f'<rect x="{x}" y="{y - 6}" width="16" height="12" rx="3" fill="{theme["step"][0]}" '
                     f'stroke="{theme["line"]}" stroke-dasharray="3 2"/>', "intermediate result"),
                    (lambda x, y: f'<rect x="{x}" y="{y - 6}" width="16" height="12" rx="3" fill="{theme["output"][0]}" '
                     f'stroke="{theme["output"][1]}"/>', "result"),
                    (lambda x, y: f'<rect x="{x}" y="{y - 6}" width="18" height="12" rx="6" fill="{theme["tint"]}" '
                     f'stroke="{theme["step"][1]}"/>', "tool")])


# ---------------------------------------------------------------- storyboard

# housekeeping steps: folded into the next card as a short note (the Storyboard's "Fold small steps")
FOLD = {"native:reprojectlayer": "reprojected", "gdal:warpreproject": "reprojected", "native:fixgeometries": "fixed",
        "native:mergevectorlayers": "merged", "gdal:merge": "merged", "native:createspatialindex": "indexed",
        "native:assignprojection": "CRS set", "native:multiparttosingleparts": "split into parts",
        "native:promotetomulti": "made multipart", "native:retainfields": "columns kept",
        "native:deletecolumn": "columns dropped", "native:renametablefield": "column renamed",
        "native:refactorfields": "columns tidied", "native:setlayerencoding": "encoding set",
        "gdal:translate": "converted", "native:savefeatures": "saved", "native:dropmzvalues": "Z/M dropped",
        "native:removenullgeometries": "empty geometries removed", "gdal:assignprojection": "CRS set"}


def fold_steps(plan):
    """({numbers of the folded steps}, {step number: [(folded step's number, what it did)]}).
    A housekeeping step is folded when a later step uses its result and it isn't a final
    output; what it did is noted on the steps that use it (passed on through folded chains)."""
    users = {}
    for step_plan in plan.steps:
        for param in step_plan.params:
            for src in param.sources:
                if src[0] == "link" and src[1] != step_plan.number:
                    users.setdefault(src[1], [])
                    if step_plan.number not in users[src[1]]:
                        users[src[1]].append(step_plan.number)
    finals = {step_plan.number for step_plan, param in plan.outputs if param.role in ("final", "save")}
    folded, notes = set(), {}
    for step_plan in plan.steps:
        try:
            verb = FOLD.get(step_plan.step.alg.id())
        except Exception:
            verb = None
        if verb and users.get(step_plan.number) and step_plan.number not in finals:
            folded.add(step_plan.number)
            for u in users[step_plan.number]:
                for item in notes.get(step_plan.number, []) + [(step_plan.number, verb)]:
                    if item not in notes.setdefault(u, []):
                        notes[u].append(item)
    return folded, notes


def _storyboard_cards(controller, detail):
    """[(step plan, title, caption, folded notes)] in reading order, with the Fold option."""
    plan = controller.plan
    folded, notes = fold_steps(plan) if _OPT.fold else (set(), {})
    cards = []
    for step_plan in plan.steps:
        if step_plan.number in folded:
            continue
        title = (f"{step_plan.number}. {step_plan.step.alg.displayName()}" if _OPT.numbers and detail != "general"
                 else step_plan.step.alg.displayName())
        results = [param for param in step_plan.params if param.kind in ("output", "temp")]
        if detail == "general":
            caption = icons.layer_icon(results[0].layer_kind)[1].capitalize() if results else ""
        else:
            caption = ", ".join(param.display for param in results)
        before = ""
        if notes.get(step_plan.number):
            words = [(f"{verb} ({n})" if _OPT.numbers and detail != "general" else verb)
                     for n, verb in sorted(notes[step_plan.number])]
            before = "↳ first " + ", ".join(words)
        cards.append((step_plan, title, caption, before))
    return cards


def storyboard_pages(controller, opts) -> int:
    """How many pages the storyboard takes: long ones go over several when "Split when crowded"."""
    global _OPT
    keep, _OPT = _OPT, opts
    try:
        n = len(_storyboard_cards(controller, opts.detail))
    finally:
        _OPT = keep
    most = 8 if opts.canvas == "slide" else 12
    return 1 if opts.pages != "auto" or n <= most else -(-n // most)


def render_pages(controller, opts) -> list:
    """The image as one SVG per page (only a long storyboard has more than one)."""
    if opts.style != "storyboard":
        return [render_svg(controller, opts)]
    import dataclasses
    n = storyboard_pages(controller, opts)
    return [render_svg(controller, dataclasses.replace(opts, page=k)) for k in range(n)]


def _storyboard(controller, detail, theme, left_out):
    """One card per step, in reading order: the tool, a small map of its result, its name.
    The rows wind back and forth to fill the page's shape; housekeeping steps fold into a note
    on the next card; a long storyboard goes over pages."""
    from . import snapshots
    from .cookbook import png_uri
    fill = snapshots.map_fill(theme)
    maps = snapshots.collect(controller, _OPT.map_background, fill=fill)
    doc = _Doc(theme)
    card_w, map_w, map_h = 316, 300, 190
    gap = 46 * min(_OPT.spacing, 1.0)
    cards = _storyboard_cards(controller, detail)
    pages = storyboard_pages(controller, _OPT)
    page = max(0, min(_OPT.page, pages - 1))
    if pages > 1:
        size = -(-len(cards) // pages)
        cards = cards[page * size:(page + 1) * size]
    heights = []
    for step_plan, title, caption, before in cards:
        lines = _wrap(title, card_w - 24, 13, True, lines=None)
        caps = _wrap(caption, card_w - 24, 11, lines=None) if caption else []
        note = _wrap("✎ " + step_plan.note, card_w - 24, 11, lines=3) if (step_plan.note and _OPT.notes) else []
        first = _wrap(before, card_w - 24, 11, lines=2) if before else []
        heights.append(16 + 17 * len(lines) + 15 * len(first) + 6 + map_h + 10 + 15 * len(caps) + 15 * len(note) + 10)
    inputs_on = _OPT.inputs and page == 0
    left = inputs_on and _OPT.inputs_side == "left"
    x0, column_h = 0.0, 0.0
    if left:   # a column of starting layers on the left, centred against the steps
        column_w, column_h = _storyboard_inputs(controller, detail, theme, doc, 0, column=True, measure=True)
        x0 = column_w + 40

    def size_for(per_row):
        rows = [max(heights[r:r + per_row]) for r in range(0, len(heights), per_row)] or [0]
        w = per_row * (card_w + gap) - gap
        top = _storyboard_inputs(controller, detail, theme, doc, w, measure=True) if inputs_on and not left else 0
        return x0 + w, max(sum(rows) + 34 * (len(rows) - 1), column_h) + top + 70

    if _OPT.spacing != 1.0 or not cards:   # chosen by hand: narrower, fewer cards a row
        per_row = 2 if _OPT.spacing < 0.8 else 4 if _OPT.spacing > 1.2 else 3
    elif _OPT.canvas == "slide":   # the number a row that comes closest to the page's 16:9
        best = None
        for per_row in range(1, min(len(cards), 6) + 1):
            w, h = size_for(per_row)
            score = abs(math.log((w / max(h, 1)) / (16 / 9)))
            if best is None or score < best[0] - 0.02:
                best = (score, per_row)
        per_row = best[1]
    else:
        per_row = min(3, len(cards))
    per_row = max(1, min(per_row, len(cards) or 1))
    y = 0.0
    if inputs_on and not left:
        y = _storyboard_inputs(controller, detail, theme, doc, per_row * (card_w + gap) - gap)
    first_part = len(doc.parts)
    rows = [cards[r:r + per_row] for r in range(0, len(cards), per_row)]
    for r, chunk in enumerate(rows):
        h = max(heights[r * per_row:r * per_row + len(chunk)])
        backwards = r % 2 == 1 and len(rows) > 1   # every other row runs right to left, like a winding path
        for i, (step_plan, title, caption, before) in enumerate(chunk):
            slot = (per_row - 1 - i) if backwards else i
            x = x0 + slot * (card_w + gap)
            doc.marks.setdefault("cards", []).append((step_plan.number, QRectF(x - 4, y - 4, card_w + 8, h + 10)))
            doc.add(_shadow(x, y, card_w, h, 12, theme["shadow"]) +
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{card_w}" height="{h:.1f}" rx="12" fill="{theme["step"][0]}" '
                    f'stroke="{theme["step"][1]}" stroke-opacity="0.35"/>', QRectF(x, y, card_w, h))
            ty = y + 26
            for line in _wrap(title, card_w - 24, 13, True, lines=None):
                doc.add(_text(x + 12, ty, line, 13, theme["ink"], True))
                ty += 17
            for line in (_wrap(before, card_w - 24, 11, lines=2) if before else []):
                doc.add(_text(x + 12, ty - 2, line, 11, theme["muted"], italic=True))
                ty += 15
            my = ty - 4
            image = maps.get(step_plan.number) or snapshots.placeholder()
            uri = png_uri(image)
            doc.add(f'<rect x="{x + 8:.1f}" y="{my:.1f}" width="{map_w}" height="{map_h}" rx="6" fill="{fill}" '
                    f'stroke="{theme["line"]}" stroke-opacity="0.25"/>'
                    f'<image href="{uri}" xlink:href="{uri}" x="{x + 8:.1f}" y="{my:.1f}" width="{map_w}" height="{map_h}"/>')
            cy = my + map_h + 18
            for line in (_wrap(caption, card_w - 24, 11, lines=None) if caption else []):
                doc.add(_text(x + 12, cy, line, 11, theme["muted"]))
                cy += 15
            if step_plan.note and _OPT.notes:
                for line in _wrap("✎ " + step_plan.note, card_w - 24, 11, lines=3):
                    doc.add(_text(x + 12, cy, line, 11, theme["muted"], italic=True))
                    cy += 15
            if i < len(chunk) - 1:   # an arrow to the next card, the way this row runs
                ay = y + h / 2
                if backwards:
                    ax = x - 8
                    doc.add(f'<path d="M{ax:.1f},{ay:.1f} H{ax - gap + 18:.1f}" stroke="{theme["line"]}" stroke-width="2"/>'
                            + _arrow(ax - gap + 14, ay, 180, theme["line"], 9, 8))
                else:
                    ax = x + card_w + 8
                    doc.add(f'<path d="M{ax:.1f},{ay:.1f} H{ax + gap - 18:.1f}" stroke="{theme["line"]}" stroke-width="2"/>'
                            + _arrow(ax + gap - 14, ay, 0, theme["line"], 9, 8))
            elif r < len(rows) - 1:   # down to the next row, which starts below this card
                ax = x + card_w / 2
                doc.add(f'<path d="M{ax:.1f},{y + h + 4:.1f} V{y + h + 22:.1f}" stroke="{theme["line"]}" stroke-width="2"/>'
                        + _arrow(ax, y + h + 28, -90, theme["line"], 9, 8))
        y += h + 34
    if not cards:
        doc.add(_text(0, 20, "No steps to show.", 13, theme["muted"]), QRectF(0, 0, 300, 30))
    if left:
        steps_h = max(y - 34, 0)
        if column_h > steps_h:   # the steps move down to the middle of the taller column
            dy = (column_h - steps_h) / 2
            doc.parts[first_part:] = [f'<g transform="translate(0,{dy:.1f})">'] + doc.parts[first_part:] + ["</g>"]
            doc.marks["cards"] = [(nb, r.translated(0, dy)) for nb, r in doc.marks.get("cards", [])]
            doc.grow(QRectF(x0, dy, 1, steps_h))
        top = max(0.0, (steps_h - column_h) / 2)
        _storyboard_inputs(controller, detail, theme, doc, 0, column=True, y0=top)
        doc.add(f'<path d="M{x0 - 22:.1f},{0:.1f} V{max(steps_h, column_h):.1f}" stroke="{theme["line"]}" '
                f'stroke-opacity="0.25" stroke-dasharray="3 4"/>')
    if _OPT.overview and page == 0:   # one small world map for the whole storyboard, in the title band
        _overview_mark(controller, doc, theme)
    title = image_title(controller, detail)
    if pages > 1:
        title += f"  ({page + 1}/{pages})"
    return doc.svg(title)


def _overview_mark(controller, doc, theme):
    """A small world map with a box where the recipe's layers are, top right in the title band."""
    from qgis.PyQt.QtCore import QSize
    from . import overview, snapshots
    from .cookbook import png_uri
    from .animation import MapMaker
    found, _used, doc.keep_overview = snapshots.result_layers(controller, rerun=False)
    maps = MapMaker(list(snapshots.starting_layers(controller).values()) + list(found.values()), 10, 10)
    inset = overview.inset([(maps.settings.extent(), maps.settings.destinationCrs(), True)], QSize(240, 120),
                           dict(theme, zoom=_OPT.overview_zoom))
    uri = png_uri(inset)
    doc.marks["overview"] = (uri, 120, 60)


def _group_names(controller, detail, uids):
    """A starting-layer card's title and its second line: one layer's name, or for a group
    "6 point layers" and "1-100 … 501-527"."""
    plan = controller.plan
    inps = [plan.inputs[u] for u in uids]
    word = icons.layer_icon(inps[0].layer_kind)[1]
    if len(inps) == 1:
        return None, word + " · starting layer"
    names = [i.recorded or i.label for i in inps]
    if detail == "general":
        return f"{len(inps)} {word}s", "starting layers, used together"
    return f"{len(inps)} {word}s", (f"{names[0]} … {names[-1]}" if len(names) > 2 else ", ".join(names))


def _storyboard_inputs(controller, detail, theme, doc, room, column=False, y0=0.0, measure=False):
    """Smaller cards for the layers the workflow starts from: above the steps (returns the
    height they take), or in a column on the left (returns its width and height; many layers
    make two narrower columns). Similar layers used together share one stacked card.
    ``measure``: only work out the size."""
    from qgis.PyQt.QtCore import QSize
    from . import snapshots
    from .cookbook import png_uri
    plan = controller.plan
    background = snapshots.background_layers() if _OPT.map_background else []
    fill = snapshots.map_fill(theme)
    groups = snapshots.input_groups(plan)
    w, map_w, map_h, gap = 220, 204, 128, 24
    per_row = max(1, int((room + gap) // (w + gap)))
    if column:
        two = len(groups) > 4
        w, map_w, map_h, gap = (150, 134, 86, 14) if two else (180, 164, 104, 18)
        per_row = 2 if two else 1
    names = {}
    if detail == "general":
        kinds = [icons.layer_icon(i.layer_kind)[1] for i in plan.inputs.values()]
        seen = {}
        for uid, input_plan in plan.inputs.items():
            word = icons.layer_icon(input_plan.layer_kind)[1]
            seen[word] = seen.get(word, 0) + 1
            names[uid] = word.capitalize() + (f" {seen[word]}" if kinds.count(word) > 1 else "")
    if not measure:
        doc.add(_text(0, y0 + 12, "Starting layers", 12, theme["muted"], True), QRectF(0, y0, 120, 16))
    y = y0 + 24.0
    for row in range(0, len(groups), per_row):
        chunk = groups[row:row + per_row]
        h = 0.0
        for i, uids in enumerate(chunk):
            x = i * (w + gap)
            group_title, second = _group_names(controller, detail, uids)
            input_plan = plan.inputs[uids[0]]
            title = group_title or names.get(uids[0]) or input_plan.recorded or input_plan.label
            lines = _wrap(title, w - 24, 12, True, lines=None)
            seconds = _wrap(second, w - 20, 10, lines=2)
            card_h = 12 + 15 * len(lines) + 6 + map_h + 12 + 13 * len(seconds)
            h = max(h, card_h + (8 if len(uids) > 1 else 0))
            if measure:
                continue
            layers = [snapshots.input_layer(controller, plan.inputs[u]) for u in uids]
            layers = [layer for layer in layers if layer is not None]
            image = snapshots.render(layers, (), background, QSize(map_w, map_h), fill) if layers else None
            uri = png_uri(image or snapshots.placeholder(QSize(map_w, map_h), "No map: the layer isn't in the project"))
            parts = []
            if len(uids) > 1:   # a little stack of cards: there are several layers in this one
                for k in (2, 1):
                    parts.append(f'<rect x="{x + 4 * k:.1f}" y="{y + 4 * k:.1f}" width="{w}" height="{card_h:.1f}" '
                                 f'rx="10" fill="{theme["input"][0]}" stroke="{theme["input"][1]}" stroke-width="1" '
                                 f'stroke-opacity="0.6"/>')
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{card_h:.1f}" rx="10" fill="{theme["input"][0]}" '
                         f'stroke="{theme["input"][1]}" stroke-width="1.2"/>')
            ty = y + 20
            for line in lines:
                parts.append(_text(x + 10, ty, line, 12, theme["ink"], True))
                ty += 15
            parts.append(f'<rect x="{x + 8:.1f}" y="{ty - 6:.1f}" width="{map_w}" height="{map_h}" fill="{fill}"/>'
                         f'<image href="{uri}" xlink:href="{uri}" x="{x + 8:.1f}" y="{ty - 6:.1f}" width="{map_w}" '
                         f'height="{map_h}"/>')
            for k, line in enumerate(seconds):
                parts.append(_text(x + 10, ty - 6 + map_h + 17 + 13 * k, line, 10, theme["muted"]))
            doc.add("".join(parts), QRectF(x, y, w + (8 if len(uids) > 1 else 0), card_h + (8 if len(uids) > 1 else 0)))
            doc.marks.setdefault("input_cards", []).append(QRectF(x - 2, y - 2, w + 4, card_h + 4))
        y += h + 20
    if column:
        return per_row * w + (per_row - 1) * gap + 8, y - y0
    return y + 20


# ---------------------------------------------------------------- summary sheet

def _summary(controller, detail, theme, left_out):
    """The workflow in numbers: a row of totals, the tools used most, and what it starts
    from and makes."""
    plan = controller.plan
    doc = _Doc(theme)
    W = 760
    steps = plan.steps
    finals = [param for _sp, param in plan.outputs if param.role in ("final", "save")] or [param for _sp, param in plan.outputs]
    tools = {}
    for step_plan in steps:
        name = step_plan.step.alg.displayName() if step_plan.step.alg else step_plan.step.label
        tools[name] = tools.get(name, 0) + 1
    groups = {}
    for step_plan in steps:
        g = (step_plan.step.alg.group() if step_plan.step.alg else "") or "Other"
        groups[g] = groups.get(g, 0) + 1
    totals = [(len(steps), "steps"), (len(tools), "different tools"), (len(plan.inputs), "layers to choose"),
              (len(plan.asked), "settings to enter"), (len(finals), "results"),
              (len(getattr(plan, "parts", None) or [1]), "separate parts" if len(getattr(plan, "parts", []) or []) > 1
               else "connected part")]
    tile_w, gap = (W - 5 * 12) / 6, 12
    for i, (value, label) in enumerate(totals):
        x = i * (tile_w + gap)
        doc.add(f'<rect x="{x:.1f}" y="0" width="{tile_w:.1f}" height="76" rx="10" fill="{theme["tint"]}" '
                f'stroke="{theme["step"][1]}" stroke-opacity="0.35"/>'
                + _text(x + tile_w / 2, 38, str(value), 26, theme["ink"], True, "middle")
                + _text(x + tile_w / 2, 60, label, 11, theme["muted"], anchor="middle"), QRectF(x, 0, tile_w, 76))

    def bars(x0, y0, width, title, counts):
        doc.add(_text(x0, y0, title, 13, theme["ink"], True), QRectF(x0, y0 - 14, width, 18))
        items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        if len(counts) > 8:
            items[-1] = ("Other", sum(v for _k, v in sorted(counts.items(), key=lambda kv: -kv[1])[7:]))
        top = max((v for _k, v in items), default=1)
        label_w = min(180, max((_width(k, 11) for k, _v in items), default=60) + 22)
        bar_room = width - label_w - 30
        y = y0 + 16
        for name, value in items:
            w = max(4.0, bar_room * value / top)
            doc.add(_text(x0 + label_w - 8, y + 11, _elide(name, label_w - 4, 11), 11, theme["ink"], anchor="end")
                    + f'<path d="M{x0 + label_w:.1f},{y + 2:.1f} H{x0 + label_w + w - 4:.1f} '
                    f'A4,4 0 0 1 {x0 + label_w + w:.1f},{y + 6:.1f} V{y + 10:.1f} '
                    f'A4,4 0 0 1 {x0 + label_w + w - 4:.1f},{y + 14:.1f} H{x0 + label_w:.1f} Z" fill="{theme["data"]}"/>'
                    + _text(x0 + label_w + w + 6, y + 12, str(value), 11, theme["muted"]),
                    QRectF(x0, y, width, 16))
            y += 22
        doc.add(f'<path d="M{x0 + label_w:.1f},{y0 + 14:.1f} V{y:.1f}" stroke="{theme["line"]}" stroke-width="1" '
                f'stroke-opacity="0.5"/>')
        return y

    y1 = bars(0, 112, W / 2 - 16, "Tools used", tools)
    y2 = bars(W / 2 + 16, 112, W / 2 - 16, "Kinds of tool", groups)
    y = max(y1, y2) + 30

    def listing(x0, title, rows, kind_colour):
        doc.add(_text(x0, y, title, 13, theme["ink"], True), QRectF(x0, y - 14, W / 2 - 16, 18))
        yy = y + 14
        for kind, text in rows[:10]:
            doc.add(f'<rect x="{x0:.1f}" y="{yy:.1f}" width="{W / 2 - 16:.1f}" height="24" rx="6" fill="{kind_colour[0]}" '
                    f'stroke="{kind_colour[1]}" stroke-width="1"/>'
                    + _image(_layer_icon(kind), x0 + 6, yy + 4, 16)
                    + _text(x0 + 28, yy + 16, _elide(text, W / 2 - 60, 11.5), 11.5, theme["ink"]),
                    QRectF(x0, yy, W / 2 - 16, 24))
            yy += 30
        return yy

    general = detail == "general"
    ins = [(i.layer_kind, icons.layer_icon(i.layer_kind)[1].capitalize() if general else (i.recorded or i.label))
           for i in plan.inputs.values()]
    outs = [(param.layer_kind, icons.layer_icon(param.layer_kind)[1].capitalize() if general else param.default_display)
            for param in finals]
    y3 = listing(0, "Starts from", ins, theme["input"])
    y4 = listing(W / 2 + 16, "Makes", outs, theme["output"])
    needs = builder.plugins_needed([step_plan.step.alg for step_plan in steps])
    note = ("Needs the " + ", ".join(needs) + (" plugin" if len(needs) == 1 else " plugins")) if needs \
        else "Needs only QGIS's own tools"
    doc.add(_text(0, max(y3, y4) + 22, note, 11.5, theme["muted"]), QRectF(0, max(y3, y4) + 8, W, 20))
    return doc.svg(image_title(controller, detail))


# ---------------------------------------------------------------- pilgrimage (still)

def _pilgrimage(controller, detail) -> str:
    """The Pilgrimage animation's last scene: every building reached, each with its small map,
    as one picture (drawn at 1.5× so it stays sharp)."""
    from . import animation
    from .cookbook import png_uri
    options = _OPT
    opts = animation.AnimationOptions(kind="pilgrimage", width=1200, layout_width=1200, detail=detail,
                                      numbers=options.numbers,
                                      title=options.title, basemaps=options.map_background, fonts=options.fonts,
                                      font=options.font, title_font=options.title_font, overview=options.overview,
                                      overview_zoom=options.overview_zoom, part=options.part, subtitle=options.subtitle)
    anim = animation.make(controller, opts)
    area = getattr(anim, "area", None)
    if area is not None and anim.total_w > area.width() + 4:   # a long road: a wider picture, so it all fits
        import dataclasses
        wider = int(opts.width * (anim.total_w + 40) / area.width()) + 40
        opts = dataclasses.replace(opts, width=wider, layout_width=wider)
        anim = animation.make(controller, opts)
    if not options.step_maps:
        anim.thumbs, anim.thumb_at = {}, {}
    everything = {i: 1.0 for i in range(anim.n_stops + 1)}
    image = anim.compose(1.0, everything, knight_on=False)   # a still: no rider, no closing line
    w, h = image.width() / 1.5, image.height() / 1.5
    uri = png_uri(image)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{w:.0f}" '
            f'height="{h:.0f}" viewBox="0 0 {w:.0f} {h:.0f}"><title>{_esc(image_title(controller, detail))}</title>'
            f'<rect class="background" x="0" y="0" width="{w:.0f}" height="{h:.0f}" fill="#EEDDB5"/>'
            f'<image href="{uri}" xlink:href="{uri}" x="0" y="0" width="{w:.0f}" height="{h:.0f}"/></svg>')
