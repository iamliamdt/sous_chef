"""Animated GIFs of a workflow: the steps play out one after another.

Kinds:
  map_story    the map on top, each step's result drawn in a bit at a time, and a flattened
               flowchart underneath that follows along
  map_metro    the same map, with a metro line underneath and a train riding from station
               to station
  metro_ride   the metro line on its own, with a small map popping up at each station
  build_up     an image style (cards, metro, …) drawn in from left to right, step by step
  spotlight    an image style with a spotlight moving from step to step
  data_flow    the data-centred image, with dots running along the lines into each tool
  storyboard   the Storyboard cards dealt out one at a time
  before_after the starting layers, then a wipe across to the result (and back)

Every frame carries the "Made with Sous Chef" mark. Frames are made one at a time and
written straight to the GIF, so long workflows don't fill the memory.
"""

from __future__ import annotations

import math
import os
import random
from dataclasses import dataclass

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsFeatureRequest,
    QgsField,
    QgsMapLayer,
    QgsMapRendererCustomPainterJob,
    QgsMapSettings,
    QgsProject,
    QgsRectangle,
    QgsSymbolLayerUtils,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QByteArray, QPointF, QRectF, QSize, Qt
from qgis.PyQt.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPixmap
from qgis.PyQt.QtSvg import QSvgRenderer

from ..i18n import tr
from . import fonts, icons, images, snapshots
from .gifwriter import GifWriter, palette_for
from ..core import log

KINDS = [
    ("map_story", "Map story", "The map on top, each step's result drawn in a bit at a time, with a flattened "
                               "flowchart underneath that follows along."),
    ("map_metro", "Map and metro ride", "The same map story, with a metro line underneath: a train rides from "
                                        "station (tool) to station."),
    ("metro_ride", "Metro ride", "The workflow as a metro line: each starting layer is a line, each tool a station, "
                                 "and a small map of the result pops up as the train arrives."),
    ("build_up", "Flowchart build-up", "An image style drawn in from left to right, a step at a time. No maps "
                                       "needed, so it's quick and suits general workflows."),
    ("spotlight", "Spotlight", "The whole image, with a spotlight moving from step to step."),
    ("data_flow", "Data flow", "The data-centred image: dots run along the lines into each tool, then its result "
                               "appears."),
    ("storyboard", "Storyboard deal", "The Storyboard cards, dealt out one at a time."),
    ("pilgrimage", "Pilgrimage", "A knight rides from castle to church to keep along a winding road, one building "
                                 "for each tool, on old paper inside an illuminated border. A small map of each "
                                 "result appears as the knight arrives."),
    ("map_pilgrimage", "Map pilgrimage", "The map story on old paper: each step's map in a gold frame, drawn in a "
                                         "bit at a time, while the knight rides from building to building underneath."),
    ("typewriter", "Typewriter", "The written recipe typed out on a sheet of paper, a step at a time, with a "
                                 "snapshot of each step's map pinned beside it."),
    ("camera_tour", "Camera tour", "A smooth pan and zoom across an image style, stopping on each step: easy to "
                                   "follow on a big screen, even for long workflows."),
    ("comic", "Comic strip", "The comic strip panels appear one at a time, speech bubbles and all."),
    ("kitchen", "Kitchen", "Each tool a pot, pan or oven on the stove: the ingredients drop in, it bubbles away, and "
                           "the result comes out on a plate with its map."),
    ("assembly", "Assembly line", "The data rides a conveyor belt from machine to machine, one machine per tool, "
                                  "coming out changed each time."),
    ("board_game", "Board game", "A token hops along a winding board, one square for each tool: roll the die, land on "
                                 "the square, see the step's map."),
    ("before_after", "Before and after", "The starting layers, then a wipe across to the result (and back when it "
                                         "loops). Good for slides and social media."),
]
MAP_KINDS = {"map_story", "map_metro", "map_pilgrimage", "before_after"}
ORDERS = [("auto", "Automatic (by kind of layer)"), ("random", "Random"), ("west_east", "West to east"),
          ("north_south", "North to south"), ("centre", "From the centre outwards"), ("wipe", "Wipe across"),
          ("fade", "Fade in")]
ACCENT = QColor("#E07A1F")
BIG_LAYER = 30000   # more features than this: a wipe instead of drawing them in a bit at a time


@dataclass
class AnimationOptions:
    kind: str = "map_story"
    width: int = 760
    fps: int = 12
    draw: float = 2.5        # seconds to draw each step
    pause: float = 1.6       # seconds on each finished step
    end: float = 4.0         # seconds on the final frame
    fade: float = 0.6        # seconds to fade from one step to the next
    loop: bool = True
    earlier: bool = False    # keep earlier results, faint
    basemaps: bool = False
    order: str = "auto"
    strip: str = "scroll"    # flowchart strip: "scroll" or "wrap"
    captions: bool = True
    legend: bool = True
    numbers: bool = True
    inputs: bool = True      # storyboard: show the starting layers
    style: str = "cards"     # build-up and spotlight
    detail: str = "named"
    theme: str = "standard"
    title: str = ""
    wipe: str = "across"     # before/after: "across", "down" or "fade"
    fonts: bool = True       # fonts to match the animation (off: the system font)
    font: str = ""           # the text font chosen in the Export window ("": the animation's own)
    title_font: str = ""     # the title font chosen in the Export window
    inputs_side: str = "top"  # storyboard deal: the starting layers "top" or "left"
    overview: bool = False   # a small world map with a box where the layers are
    overview_zoom: str = "world"  # or "region"
    subtitle: str = "auto"   # the pilgrimage's subtitle: "auto", your own, or "" for none
    corner: str = "top-right"  # where the overview map goes
    part: int = -1           # separate parts: -1 all of them, one after another; otherwise one part
    layout_width: int = 0    # fixed-layout animations: the width they're laid out at (0: BASE_WIDTH)
    final_output: int = 0    # what the ending shows: 0 all the final outputs, otherwise one step's result


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- shared drawing

def _ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


_FAMILY = [None]   # the font of the animation being made (None: the system font)
_TITLE = [None]    # its title font


def _font(size, bold=False, family=None):
    font = QFont(family or _FAMILY[0]) if (family or _FAMILY[0]) else QFont()
    font.setPixelSize(int(size))
    font.setBold(bold)
    return font


def _elide(text, size, width, bold=False):
    return QFontMetricsF(_font(size, bold)).elidedText(str(text), Qt.TextElideMode.ElideRight, max(width, 10))


_LOGO = {}


def _logo() -> QImage:
    if "img" not in _LOGO:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons", "sous_chef_32.png")
        _LOGO["img"] = QImage(path)
    return _LOGO["img"]


def watermark(p: QPainter, w: float, h: float, t):
    """The small "Made with Sous Chef" mark, bottom right."""
    label = "Made with Sous Chef"
    font = _font(10.5)
    tw = QFontMetricsF(font).horizontalAdvance(label)
    x, y = w - 14 - tw, h - 8
    p.save()
    p.setOpacity(0.65)
    p.drawImage(QRectF(x - 20, y - 13, 16, 16), _logo())
    p.setFont(font)
    p.setPen(QColor(t["muted"]))
    p.drawText(QPointF(x, y), label)
    p.restore()


def _alg_icon_image(alg, size: int):
    """A tool's own icon as a picture, or None."""
    try:
        icon = alg.icon() if alg is not None else None
        if icon is None or icon.isNull():
            return None
        return icon.pixmap(QSize(size, size)).toImage()
    except Exception:
        return None


def _blend(a: QImage, b: QImage, x: float) -> QImage:
    out = QImage(a)
    p = QPainter(out)
    p.setOpacity(_ease(x))
    p.drawImage(0, 0, b)
    p.end()
    return out


BASE_WIDTH = 854   # animations with a fixed layout are laid out at this width, then drawn at the chosen size


def _draw_part(painter: QPainter, target: QRectF, image: QImage, source: QRectF):
    """Draw part of an image, the part given in layout units (Qt reads it in the image's own
    pixels, which a sharper copy has more of)."""
    r = image.devicePixelRatio() or 1.0
    painter.drawImage(target, image, QRectF(source.x() * r, source.y() * r, source.width() * r, source.height() * r))


def size_of(image: QImage):
    """(width, height) of an image in layout units: a sharper copy (drawn at a bigger size)
    has a pixel ratio, and takes the same room as the plain one."""
    ratio = image.devicePixelRatio() or 1.0
    return image.width() / ratio, image.height() / ratio


class Animation:
    """Base: an animation knows its key frames (for the palette) and makes its frames.
    FIXED_LAYOUT animations are laid out at BASE_WIDTH and drawn ``ratio`` times bigger, so
    a bigger size shows the same picture, only sharper."""

    FIXED_LAYOUT = False

    def __init__(self, controller, opts: AnimationOptions):
        self.controller, self.options = controller, opts
        self.t = images.THEMES.get(opts.theme, images.THEMES["standard"])
        self.fps = max(4, int(opts.fps))
        self.delay = max(2, int(round(100 / self.fps)))
        self.W = int(opts.width)
        self.H = int(round(opts.width * 9 / 16))   # a standard 16:9 screen
        self.ratio = 1.0
        layout_width = int(getattr(opts, "layout_width", 0) or BASE_WIDTH)
        if self.FIXED_LAYOUT and opts.width != layout_width:
            self.ratio = opts.width / layout_width
            self.W, self.H = layout_width, int(round(layout_width * 9 / 16))

    @property
    def out_W(self) -> int:
        """The frames' width in pixels."""
        return int(round(self.W * self.ratio))

    @property
    def out_H(self) -> int:
        if self.H == int(round(self.W * 9 / 16)):   # a standard 16:9 frame
            return int(round(self.out_W * 9 / 16))
        return int(round(self.H * self.ratio))

    def frame_image(self, image_format=QImage.Format.Format_RGB32) -> QImage:
        """A blank frame at the chosen size, drawn on in layout units."""
        image = QImage(self.out_W, self.out_H, image_format)
        image.setDevicePixelRatio(self.ratio)
        return image

    def px(self, size: QSize) -> QSize:
        """A size in layout units, in pixels at the chosen size (for maps drawn sharp)."""
        return QSize(int(round(size.width() * self.ratio)), int(round(size.height() * self.ratio)))

    def sharp(self, image):
        """A map drawn at the chosen size, marked so it takes its layout size."""
        if image is not None and self.ratio != 1.0:
            image.setDevicePixelRatio(self.ratio)
        return image

    def title_text(self) -> str:
        if self.options.title.strip():
            return self.options.title.strip()
        from ..core import builder
        return builder.general_title(self.controller.plan) if self.options.detail == "general" else self.controller.model_name

    def n(self, seconds) -> int:
        return max(1, int(round(seconds * self.fps)))

    def prepare(self):
        pass

    def keyframes(self) -> list:
        return []

    def frames(self):
        """Yields (image, delay in 1/100 s)."""
        return iter(())

    def estimate(self) -> int:
        return 100

    def end_frames(self, last: QImage, first: QImage):
        """The pause on the final frame, and when looping, a fade back to the beginning."""
        yield last, int(self.options.end * 100)
        if self.options.loop and first is not None:
            for i in range(1, self.n(self.options.fade) + 1):
                yield _blend(last, first, i / self.n(self.options.fade)), self.delay


# ---------------------------------------------------------------- the workflow, flattened

class Flow:
    """The recipe as a sequence: steps in order, the layers each starts from, the results."""

    def __init__(self, controller, detail):
        plan = controller.plan
        parts = getattr(plan, "parts", None) or [set(step_plan.number for step_plan in plan.steps)]
        self.part_of = {n: k for k, part in enumerate(parts) for n in part}
        self.n_parts = len(parts)
        self.steps = sorted(plan.steps, key=lambda step_plan: (self.part_of.get(step_plan.number, 0), step_plan.number))
        self.detail = detail
        self.first_use = {}
        for i, step_plan in enumerate(self.steps):
            for param in step_plan.params:
                if param.kind == "input":
                    for src in param.sources:
                        if src[0] == "input":
                            self.first_use.setdefault(src[1], i)
        kinds = [icons.layer_icon(input_plan.layer_kind)[1] for input_plan in plan.inputs.values()]
        seen = {}
        self.input_names = {}
        for uid, input_plan in plan.inputs.items():
            if detail == "general":
                word = icons.layer_icon(input_plan.layer_kind)[1]
                seen[word] = seen.get(word, 0) + 1
                self.input_names[uid] = word.capitalize() + (f" {seen[word]}" if kinds.count(word) > 1 else "")
            else:
                self.input_names[uid] = input_plan.recorded or input_plan.label
        self.inputs = sorted(plan.inputs, key=lambda u: self.first_use.get(u, 0))
        self.input_part = {u: self.part_of.get(self.steps[i].number, 0) for u, i in self.first_use.items()}
        finals = [param for _sp, param in plan.outputs if param.role in ("final", "save")] or [param for _sp,
                                                                                               param in plan.outputs]
        if detail == "general":
            self.result_name = icons.layer_icon(finals[-1].layer_kind)[1].capitalize() if finals else "Result"
        else:
            self.result_name = finals[-1].default_display if finals else "Result"
        if len(finals) > 1:
            self.result_name += f" +{len(finals) - 1}"

    def tool(self, i, numbers=True):
        step_plan = self.steps[i]
        name = step_plan.step.alg.displayName() if step_plan.step.alg else step_plan.step.label
        return f"{step_plan.number}. {name}" if numbers else name

    def result_of(self, i):
        step_plan = self.steps[i]
        out = [param for param in step_plan.params if param.kind in ("output", "temp")]
        if not out:
            return ""
        if self.detail == "general":
            return icons.layer_icon(out[0].layer_kind)[1]
        return out[0].default_display


# ---------------------------------------------------------------- strips under the map

class FlowStrip:
    """A flattened flowchart: starting layer → tools in order → result, with the other
    starting layers above the tool that first uses them."""

    def __init__(self, flow: Flow, t, width, mode="scroll", numbers=True):
        self.f, self.t, self.W, self.mode = flow, t, width, mode
        fm = QFontMetricsF(_font(12, True))
        self.items = []   # (key, kind, label)
        main_inputs = [u for u in flow.inputs if flow.first_use.get(u, 0) == 0][:1]
        for u in main_inputs:
            self.items.append((f"in:{u}", "input", flow.input_names[u]))
        for i in range(len(flow.steps)):
            self.items.append((f"step:{i}", "tool", flow.tool(i, numbers)))
        self.items.append(("result", "output", flow.result_name))
        self.side = [(f"in:{u}", flow.input_names[u], flow.first_use.get(u, 0)) for u in flow.inputs
                     if f"in:{u}" not in {k for k, _kind, _l in self.items}]
        self.width_of = {k: min(fm.horizontalAdvance(label) + (30 if kind == "tool" else 24), 230)
                         for k, kind, label in self.items}
        sfm = QFontMetricsF(_font(11, True))
        self.side_w = {k: min(sfm.horizontalAdvance(label) + 22, 180) for k, label, _i in self.side}
        self.arrow = 26
        total = sum(self.width_of.values()) + self.arrow * (len(self.items) - 1)
        self.rows = [self.items]
        if mode == "wrap" and total > width - 32:
            self.rows, row, used = [], [], 0.0
            for item in self.items:
                w = self.width_of[item[0]]
                if row and used + self.arrow + w > width - 32:
                    self.rows.append(row)
                    row, used = [], 0.0
                used += (self.arrow if row else 0) + w
                row.append(item)
            self.rows.append(row)
        side_rows = set()
        for _k, _label, i in self.side:
            side_rows.add(next((r for r, row in enumerate(self.rows) if any(k == f"step:{i}" for k, _x, _y in row)), 0))
        self.row_top, y = [], 14.0
        for r in range(len(self.rows)):
            y += 40 if r in side_rows else 6
            self.row_top.append(y)
            y += 30 + 16
        self.height = int(y + 10)
        self.total = total

    def _positions(self, focus: float):
        """{key: QRectF} with the strip's top at 0."""
        rects = {}
        for r, row in enumerate(self.rows):
            widths = [self.width_of[k] for k, _kind, _l in row]
            row_w = sum(widths) + self.arrow * (len(row) - 1)
            if row_w <= self.W - 32:
                x = (self.W - row_w) / 2
            else:   # scroll: the focused item near the middle
                xs, acc = [], 0.0
                for w in widths:
                    xs.append(acc + w / 2)
                    acc += w + self.arrow
                lo, frac = int(math.floor(focus)), focus - math.floor(focus)
                lo = min(max(lo, 0), len(xs) - 1)
                hi = min(lo + 1, len(xs) - 1)
                cx = xs[lo] + (xs[hi] - xs[lo]) * frac
                x = min(16, max(self.W - 16 - row_w, self.W / 2 - cx))
            y = self.row_top[r] + 15
            for (key, _kind, _label), w in zip(row, widths):
                rects[key] = QRectF(x, y - 15, w, 30)
                x += w + self.arrow
        for key, _label, i in self.side:
            anchor = rects.get(f"step:{i}")
            if anchor is None:
                continue
            same = [k for k, _l, j in self.side if j == i]
            k = same.index(key)
            w = self.side_w[key]
            total = sum(self.side_w[s] + 8 for s in same) - 8
            x = anchor.center().x() - total / 2 + sum(self.side_w[s] + 8 for s in same[:k])
            rects[key] = QRectF(x, anchor.top() - 40, w, 24)
        return rects

    def focus_index(self, key):
        return next((i for i, (k, _kind, _l) in enumerate(self.items) if k == key), 0)

    def draw(self, p: QPainter, top: float, current: set, done: set, focus: float):
        t = self.t
        p.save()
        p.translate(0, top)
        rects = self._positions(focus)
        line = QColor(t["line"])

        def alpha(key):
            return 1.0 if key in current or key in done else 0.28

        for row in self.rows:
            for (a, _ka, _la), (b, _kb, _lb) in zip(row, row[1:]):
                ra, rb = rects[a], rects[b]
                p.setOpacity(min(alpha(a), alpha(b)))
                _arrow(p, QPointF(ra.right() + 3, ra.center().y()), QPointF(rb.left() - 4, rb.center().y()), line)
        for r in range(len(self.rows) - 1):   # wrapped: down to the next row
            a, b = rects[self.rows[r][-1][0]], rects[self.rows[r + 1][0][0]]
            p.setOpacity(min(alpha(self.rows[r][-1][0]), alpha(self.rows[r + 1][0][0])))
            path = QPainterPath(QPointF(a.center().x(), a.bottom() + 2))
            mid = a.bottom() + 9
            path.lineTo(a.center().x(), mid)
            path.lineTo(b.left() - 14, mid)
            path.lineTo(b.left() - 14, b.center().y())
            path.lineTo(b.left() - 4, b.center().y())
            p.setPen(QPen(line, 1.3, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        for key, _label, i in self.side:
            if key not in rects:
                continue
            ra, rs = rects[key], rects[f"step:{i}"]
            p.setOpacity(min(alpha(key), alpha(f"step:{i}")))
            _arrow(p, QPointF(ra.center().x(), ra.bottom() + 2), QPointF(rs.center().x(), rs.top() - 4), line)
        for key, kind, label in self.items + [(k, "input", lab) for k, lab, _i in self.side]:
            if key not in rects:
                continue
            r = rects[key]
            p.setOpacity(alpha(key))
            bg, edge = {"input": t["input"], "output": t["output"]}.get(kind, (t["tint"], t["step"][1]))
            radius = r.height() / 2 if kind == "tool" else 7
            if key in current:
                glow = QColor(ACCENT)
                for grow, a in ((7, 0.10), (4, 0.18)):
                    glow.setAlphaF(a)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(glow)
                    p.drawRoundedRect(r.adjusted(-grow, -grow, grow, grow), radius + grow, radius + grow)
            p.setBrush(QColor(bg))
            p.setPen(QPen(ACCENT if key in current else QColor(edge), 2.2 if key in current else 1.2))
            p.drawRoundedRect(r, radius, radius)
            p.setPen(QColor(t["ink"]))
            size = 11 if r.height() < 28 else 12
            p.setFont(_font(size, True))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, _elide(label, size, r.width() - 14, True))
        p.restore()


def _arrow(p, a, b, colour, head=7):
    p.setPen(QPen(colour, 1.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(a, b)
    angle = math.atan2(b.y() - a.y(), b.x() - a.x())
    tip = QPointF(b.x() + 3 * math.cos(angle), b.y() + 3 * math.sin(angle))
    left = QPointF(tip.x() - head * math.cos(angle - 0.45), tip.y() - head * math.sin(angle - 0.45))
    right = QPointF(tip.x() - head * math.cos(angle + 0.45), tip.y() - head * math.sin(angle + 0.45))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(colour)
    p.drawPolygon([tip, left, right])


class MetroStrip:
    """The workflow as a metro line: the first starting layer is the main line, the other
    starting layers branch in from below where they're first used, tools are stations and the
    result is the terminus. A train rides along it."""

    def __init__(self, flow: Flow, t, width, big=False, numbers=True):
        self.f, self.t, self.W, self.big = flow, t, width, big
        self.n = len(flow.steps)
        fm = QFontMetricsF(_font(12, True))
        self.end_w = min(fm.horizontalAdvance(flow.result_name) + 24, 200)
        room = width - 40 - self.end_w - 90
        self.spacing = max(150 if big else 118, room / max(self.n - 1 + 0.75, 1))
        self.labels = [flow.tool(i, numbers) for i in range(self.n)]
        self.colours = {u: QColor(t["metro"][i % len(t["metro"])]) for i, u in enumerate(flow.inputs)}
        self.branches = [u for u in flow.inputs[1:]]
        self.top_room = 150 if big else 38
        self.height = self.top_room + 34 + 26 * max(len(self.branches), 1) + (30 if big else 18)

    def x_of(self, pos: float, offset: float):
        return offset + 24 + (pos + 0.75) * self.spacing

    def offset_for(self, pos: float):
        total = 24 + (self.n - 1 + 0.75) * self.spacing + 90 + self.end_w + 20
        if total <= self.W:
            return (self.W - total) / 2
        return min(0.0, max(self.W - total, self.W / 2 - (24 + (pos + 0.75) * self.spacing)))

    def draw(self, p: QPainter, top: float, pos: float, current: int | None, thumbs=None, shown=()):
        """``pos``: where the train is (-1 the start, 0… the stations, n the terminus)."""
        t = self.t
        p.save()
        p.translate(0, top)
        off = self.offset_for(pos)
        y = self.top_room + 18
        main = self.colours.get(self.f.inputs[0], QColor(t["metro"][0])) if self.f.inputs else QColor(t["metro"][0])
        start_x, end_x = self.x_of(-0.75, off), self.x_of(self.n - 1, off) + 90
        width = 8 if self.big else 6
        # the main line: travelled part solid, the rest faint
        last_x = self.x_of(self.n - 1, off) if self.n else start_x
        if pos <= self.n - 1:
            train_x = self.x_of(pos, off)
        else:   # the last leg: from the last station to the terminus, and no further
            train_x = last_x + (end_x - last_x) * min(max(pos - (self.n - 1), 0.0), 1.0)
        for x0, x1, a in ((start_x, train_x, 1.0), (train_x, end_x, 0.3)):
            if x1 > x0:
                p.setOpacity(a)
                p.setPen(QPen(main, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(x0, y), QPointF(x1, y))
        # branches from below
        branch_trains = []
        for k, u in enumerate(self.branches):
            i = self.f.first_use.get(u, 0)
            sx = self.x_of(i, off)
            by = y + 26 * (k + 1)
            p.setOpacity(1.0 if pos >= i - 0.5 else 0.3)
            path = QPainterPath(QPointF(sx - self.spacing * 0.8, by))
            path.lineTo(sx - 26, by)
            path.quadTo(QPointF(sx, by), QPointF(sx, y))
            p.setPen(QPen(self.colours[u], width - 1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            self._terminus(p, QPointF(sx - self.spacing * 0.8, by), self.colours[u], self.f.input_names[u], below=True)
            if pos < i:   # its own train, timed to meet the main one at the joining station
                along = min(max(pos - (i - 1), 0.0), 1.0)
                branch_trains.append((path.pointAtPercent(along), self.colours[u]))
        p.setOpacity(1.0)
        if self.f.inputs:
            self._terminus(p, QPointF(start_x, y), main, self.f.input_names[self.f.inputs[0]],
                           below=not self.branches)
        # stations
        for i in range(self.n):
            x = self.x_of(i, off)
            reached = pos >= i - 0.01
            p.setOpacity(1.0 if reached else 0.35)
            r = 11 if (current == i) else 7
            if current == i:
                glow = QColor(ACCENT)
                for grow, a in ((9, 0.12), (5, 0.2)):
                    glow.setAlphaF(a)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(glow)
                    p.drawEllipse(QPointF(x, y), r + grow, r + grow)
            p.setBrush(QColor("#FFFFFF"))
            p.setPen(QPen(ACCENT if current == i else QColor(t["ink"]), 3 if current == i else 2.2))
            p.drawEllipse(QPointF(x, y), r, r)
            p.setPen(QColor(t["ink"]))
            size = 12 if self.big else 11
            p.setFont(_font(size, True))
            label = _elide(self.labels[i], size, self.spacing - 10, True)
            p.drawText(QRectF(x - self.spacing / 2, y - 34, self.spacing, 18), Qt.AlignmentFlag.AlignCenter, label)
            if thumbs is not None and i in shown and thumbs.get(i) is not None:
                image = thumbs[i]
                tw, th = size_of(image)
                box = QRectF(x - tw / 2 - 5, y - 44 - th - 10, tw + 10, th + 10)
                p.setPen(QPen(QColor(t["line"]), 1))
                p.setBrush(QColor("#FFFFFF"))
                p.drawRoundedRect(box, 8, 8)
                p.drawImage(QRectF(box.x() + 5, box.y() + 5, tw, th), image)
        # terminus: the result
        p.setOpacity(1.0 if pos >= self.n - 0.01 else 0.35)
        label = self.f.result_name
        fm = QFontMetricsF(_font(12, True))
        w = min(fm.horizontalAdvance(label) + 24, 200)
        r = QRectF(end_x, y - 14, w, 28)
        bg, edge = t["output"]
        p.setBrush(QColor(bg))
        p.setPen(QPen(ACCENT if pos >= self.n else QColor(edge), 2.2 if pos >= self.n else 1.4))
        p.drawRoundedRect(r, 8, 8)
        p.setPen(QColor(t["ink"]))
        p.setFont(_font(12, True))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, _elide(label, 12, w - 12, True))
        # the trains
        p.setOpacity(1.0)
        for at, colour in branch_trains:
            self._car(p, at.x(), at.y(), colour)
        self._car(p, min(train_x, end_x - 16), y, ACCENT)
        p.restore()

    def _car(self, p, x, y, colour):
        p.setPen(QPen(QColor("#FFFFFF"), 2))
        p.setBrush(QColor(colour))
        p.drawRoundedRect(QRectF(x - 15, y - 8, 30, 16), 8, 8)
        p.setBrush(QColor("#FFFFFF"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(x + 7, y - 1), 2.5, 2.5)

    def _terminus(self, p, at, colour, name, below=False):
        p.setPen(QPen(colour, 3))
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(at, 6, 6)
        p.setPen(QColor(self.t["muted"]))
        p.setFont(_font(10.5, True))
        text = _elide(name, 10.5, self.spacing * 0.9, True)
        if below:
            p.drawText(QRectF(at.x() - 10, at.y() + 9, self.spacing, 16), Qt.AlignmentFlag.AlignLeft, text)
        else:
            p.drawText(QRectF(at.x() - 10, at.y() + 10, self.spacing, 16), Qt.AlignmentFlag.AlignLeft, text)


# ---------------------------------------------------------------- maps

class MapMaker:
    """Draws layers onto the animation's map, whole or a part of their features."""

    def __init__(self, layers, width, height, basemaps=False, ratio=1.0):
        project = QgsProject.instance()
        self.ratio = ratio   # drawn this many times bigger (sharper), in the same layout size
        crs = project.crs()
        layers = [lyr for lyr in layers if lyr is not None and lyr.isValid() and lyr.isSpatial()]
        if not crs.isValid() and layers:
            crs = layers[0].crs()
        if not crs.isValid():
            crs = QgsCoordinateReferenceSystem("EPSG:4326")
        extent = QgsRectangle()
        for layer in layers:
            e = snapshots._extent(layer, crs)
            if e.isFinite() and not e.isNull():   # a single point has no size, but still counts
                extent.combineExtentWith(e)
        extent = snapshots.sized(extent, crs)
        self.settings = QgsMapSettings()
        self.settings.setDestinationCrs(crs)
        self.settings.setOutputSize(QSize(int(round(width * ratio)), int(round(height * ratio))))
        self.settings.setExtent(extent.buffered(max(extent.width(), extent.height()) * 0.03))
        self.settings.setBackgroundColor(QColor(0, 0, 0, 0))
        self.settings.setFlag(Qgis.MapSettingsFlag.Antialiasing, True)
        self.settings.setOutputDpi(96 * ratio)   # symbols and labels in proportion
        self.w, self.h = width, height
        self.background = self.draw(snapshots.background_layers()) if basemaps else None
        self._full = {}

    def draw(self, layers) -> QImage:
        size = self.settings.outputSize()
        image = QImage(size.width(), size.height(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        layers = [layer for layer in layers if layer is not None and layer.isValid()]
        if layers:
            self.settings.setLayers(layers)
            p = QPainter(image)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            job = QgsMapRendererCustomPainterJob(self.settings, p)
            job.renderSynchronously()
            p.end()
        image.setDevicePixelRatio(self.ratio)
        return image

    def full(self, layer) -> QImage:
        if layer.id() not in self._full:
            self._full[layer.id()] = self.draw([layer])
        return self._full[layer.id()]

    def ordered_copy(self, layer: QgsVectorLayer, order: str):
        """A copy of the layer with an "ord" field: the order its features appear in."""
        copy = layer.materialize(QgsFeatureRequest())
        if copy is None:
            return None, 0
        copy.setRenderer(layer.renderer().clone())
        copy.dataProvider().addAttributes([QgsField("souschef_ord", _int_type())])
        copy.updateFields()
        feats = list(copy.getFeatures())
        centre = self.settings.extent().center()
        rnd = random.Random(7)  # nosec B311: drawing variety, not security

        xf = None
        if layer.crs() != self.settings.destinationCrs():
            from qgis.core import QgsCoordinateTransform
            xf = QgsCoordinateTransform(layer.crs(), self.settings.destinationCrs(), QgsProject.instance())

        def key(ft):
            g = ft.geometry()
            if g is None or g.isEmpty():
                return 0.0
            if order == "random":
                return rnd.random()
            c = g.centroid().asPoint()
            if xf is not None:
                try:
                    c = xf.transform(c)
                except Exception as error:   # safe to carry on: noted in the log
                    log.ignored(error)
            if order == "west_east":
                return c.x() + 0.15 * c.y()
            if order == "north_south":
                return -c.y() + 0.15 * c.x()
            return math.hypot(c.x() - centre.x(), c.y() - centre.y())

        feats.sort(key=key)
        i = copy.fields().indexOf("souschef_ord")
        copy.dataProvider().changeAttributeValues({ft.id(): {i: n} for n, ft in enumerate(feats)})
        return copy, len(feats)


def _int_type():
    """The whole-number field type (QGIS 3.38 and newer name field types by QMetaType)."""
    from qgis.PyQt.QtCore import QMetaType
    return QMetaType.Type.Int


def _same_shapes(result, used) -> bool:
    """A result with the same features as a layer it used (counts, joins, field changes)."""
    if not isinstance(result, QgsVectorLayer):
        return False
    for layer in used:
        if (isinstance(layer, QgsVectorLayer) and layer.geometryType() == result.geometryType()
                and layer.featureCount() == result.featureCount() and layer.featureCount() > 0):
            a, b = layer.extent(), result.extent()
            if layer.crs() == result.crs() and abs(a.width() - b.width()) <= max(a.width(), 1e-9) * 0.01:
                return True
    return False


def reveal_order(layer, used, choice):
    if choice != "auto":
        if choice not in ("wipe", "fade") and not isinstance(layer, QgsVectorLayer):
            return "wipe"
        return choice
    if not isinstance(layer, QgsVectorLayer):
        return "wipe" if layer.type() == QgsMapLayer.LayerType.RasterLayer else "fade"
    if _same_shapes(layer, used):
        return "wipe"
    count = layer.featureCount()
    if count > BIG_LAYER:
        return "wipe"
    if count < 3:
        return "fade"
    kind = layer.geometryType()
    if kind == Qgis.GeometryType.Point:
        return "random"
    if kind == Qgis.GeometryType.Line:
        return "west_east"
    return "centre"


class MapAnimation(Animation):
    FIXED_LAYOUT = True
    """Map story, map and metro ride, and before/after."""

    def prepare(self):
        options = self.options
        self.flow = Flow(self.controller, options.detail)
        self.found, self.used, self._keep = snapshots.result_layers(self.controller)
        self.starts = snapshots.starting_layers(self.controller)
        if options.kind == "map_metro":
            self.strip = MetroStrip(self.flow, self.t, self.W, numbers=options.numbers)
        elif options.kind == "map_story":
            self.strip = FlowStrip(self.flow, self.t, self.W, options.strip, options.numbers)
        else:
            self.strip = None
        self.map_h = max(int(self.H * 0.5), self.H - (self.strip.height if self.strip else 0))
        self.H = self.map_h + (self.strip.height if self.strip else 0)   # 16:9, unless the strip needs more room
        self.build_makers(self.W, self.map_h)
        bg = QColor(self.t["bg"])
        self.map_bg = bg.darker(103) if bg.lightness() > 128 else bg.lighter(112)
        self.strip_bg = bg.darker(108) if bg.lightness() > 128 else bg.lighter(125)

    def build_makers(self, w, h):
        """A map of everything, and with separate parts, a map of each part (the animation
        zooms to each part in turn)."""
        options, flow = self.options, self.flow
        everything = list(self.starts.values()) + list(self.found.values())
        self.all_maps = self.maps = MapMaker(everything, w, h, options.basemaps, self.ratio)
        self.makers = []
        if flow.n_parts > 1 and options.kind != "before_after":
            for k in range(flow.n_parts):
                layers = [layer for n_, layer in self.found.items() if flow.part_of.get(n_) == k]
                layers += [layer for u, layer in self.starts.items() if flow.input_part.get(u) == k]
                self.makers.append(MapMaker(layers or everything, w, h, options.basemaps, self.ratio))
            self.maps = self.makers[0]

    def _overview(self, p: QPainter):
        """The small world map, with a box for each part and the current one highlighted."""
        from . import overview
        makers = self.makers or [self.maps]
        crs = self.maps.settings.destinationCrs()
        areas = [(m.settings.extent(), crs, m is self.maps or self.maps is self.all_maps) for m in makers]
        area = QRectF(0, 0, self.W, self.map_h - (26 if self.strip is None else 0))   # clear of the corner mark
        w = max(110, int(area.width() * 0.2))
        inset = overview.inset(areas, QSize(w, w // 2), dict(self.t, zoom=self.options.overview_zoom))
        overview.place(p, inset, area, self.options.corner, 12)

    # -- one frame
    def compose(self, layers, caption=None, state=None, draw_more=None, legend=None) -> QImage:
        image = self.frame_image()
        image.fill(self.map_bg)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self.maps.background is not None:
            p.drawImage(0, 0, self.maps.background)
        for img, opacity in layers:
            if img is not None and opacity > 0:
                p.setOpacity(opacity)
                p.drawImage(0, 0, img)
        p.setOpacity(1.0)
        if draw_more:
            draw_more(p)
        if legend and self.options.legend:
            self._legend(p, legend)
        if caption and self.options.captions:
            self._caption(p, *caption)
        if self.options.overview:
            self._overview(p)
        if self.strip is not None:
            p.fillRect(QRectF(0, self.map_h, self.W, self.H - self.map_h), self.strip_bg)
            p.setPen(QPen(QColor(self.t["line"]).lighter(160), 1))
            p.drawLine(QPointF(0, self.map_h + 0.5), QPointF(self.W, self.map_h + 0.5))
            if state is not None:
                if isinstance(self.strip, MetroStrip):
                    self.strip.draw(p, self.map_h, state["pos"], state.get("station"))
                else:
                    self.strip.draw(p, self.map_h, state["current"], state["done"], state["focus"])
        if not getattr(self, "_no_mark", False):
            watermark(p, self.W, self.H, self.t)
        p.end()
        return image

    def _caption(self, p, title, sub=""):
        fm, fs = QFontMetricsF(_font(16, True, _TITLE[0])), QFontMetricsF(_font(12))
        title = QFontMetricsF(_font(16, True, _TITLE[0])).elidedText(str(title), Qt.TextElideMode.ElideRight, self.W - 60)
        sub = _elide(sub, 12, self.W - 60)
        w = max(fm.horizontalAdvance(title), fs.horizontalAdvance(sub) if sub else 0) + 28
        r = QRectF(14, 14, w, 52 if sub else 34)
        bg = QColor(self.t["bg"])
        bg.setAlpha(232)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 9, 9)
        p.setPen(QColor(self.t["ink"]))
        p.setFont(_font(16, True, _TITLE[0]))
        p.drawText(QRectF(r.x() + 14, r.y() + 6, w, 22), Qt.AlignmentFlag.AlignLeft, title)
        if sub:
            p.setPen(QColor(self.t["muted"]))
            p.setFont(_font(12))
            p.drawText(QRectF(r.x() + 14, r.y() + 30, w, 18), Qt.AlignmentFlag.AlignLeft, sub)

    def _legend(self, p, layers):
        entries = []
        for layer in layers:
            renderer = layer.renderer() if isinstance(layer, QgsVectorLayer) else None
            if renderer is None:
                continue
            items = renderer.legendSymbolItems()
            for item in items[:8]:
                if item.symbol() is None:
                    continue
                pix = QgsSymbolLayerUtils.symbolPreviewPixmap(item.symbol(), QSize(16, 16))
                entries.append((pix, item.label() or layer.name()))
        if not entries:
            return
        fm = QFontMetricsF(_font(11))
        x0, y = 20, self.map_h - 14
        rows, row, used = [], [], 0.0
        for pix, label in entries[:12]:
            w = 24 + fm.horizontalAdvance(_elide(label, 11, 160)) + 16
            if row and used + w > self.W - 200:
                rows.append(row)
                row, used = [], 0.0
            row.append((pix, label, w))
            used += w
        rows.append(row)
        h = 22 * len(rows) + 8
        width = max(sum(w for _p, _l, w in r) for r in rows) + 12
        bg = QColor(self.t["bg"])
        bg.setAlpha(232)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(x0 - 8, y - h, width, h), 8, 8)
        for r, items in enumerate(rows):
            x = x0
            yy = y - h + 6 + r * 22
            for pix, label, w in items:
                p.drawPixmap(int(x), int(yy + 1), pix if isinstance(pix, QPixmap) else QPixmap.fromImage(pix))
                p.setPen(QColor(self.t["ink"]))
                p.setFont(_font(11))
                p.drawText(QPointF(x + 22, yy + 14), _elide(label, 11, 160))
                x += w

    # -- drawing a layer in
    def reveal(self, layer, used, context, caption, state, seconds, edge=True):
        """Frames drawing ``layer`` in over ``context`` [(image, opacity)]; returns the last."""
        order = reveal_order(layer, used, self.options.order)
        steps = self.n(seconds)
        full = self.maps.full(layer)
        last = None
        if order in ("random", "west_east", "north_south", "centre") and isinstance(layer, QgsVectorLayer):
            copy, count = self.maps.ordered_copy(layer, order)
            if copy is None or count == 0:
                order = "fade"
            else:
                acc = self.map_layer_image()
                acc.fill(Qt.GlobalColor.transparent)
                shown = 0
                for i in range(1, steps + 1):
                    upto = round(count * (i / steps) ** 1.15) if i < steps else count
                    if upto > shown:
                        copy.setSubsetString(f'"souschef_ord" >= {shown} AND "souschef_ord" < {upto}')
                        batch = self.maps.draw([copy])
                        painter = QPainter(acc)
                        painter.drawImage(0, 0, batch)
                        painter.end()
                        shown = upto
                    last = self.compose(context + [(acc if i < steps else full, 1.0)], caption, state)
                    yield last, self.delay
                return
        if order == "fade":
            for i in range(1, steps + 1):
                last = self.compose(context + [(full, _ease(i / steps))], caption, state)
                yield last, self.delay
            return
        # wipe: across, with an orange edge
        for i in range(1, steps + 1):
            x = self.W * _ease(i / steps)
            part = self.map_layer_image()
            part.fill(Qt.GlobalColor.transparent)
            painter = QPainter(part)
            _draw_part(painter, QRectF(0, 0, x, self.map_h), full, QRectF(0, 0, x, self.map_h))
            painter.end()

            def line(p, x=x, i=i):
                if edge and i < steps:
                    p.setPen(QPen(QColor(224, 122, 31, 210), 2.2))
                    p.drawLine(QPointF(x, 0), QPointF(x, self.map_h))
            last = self.compose(context + [(part, 1.0)], caption, state, line)
            yield last, self.delay

    def crossfade(self, a, b):
        n = self.n(self.options.fade)
        for i in range(1, n + 1):
            yield _blend(a, b, i / n), self.delay

    # -- the whole thing
    def _states(self):
        if self.strip is None:
            return lambda *_a, **_k: None
        flow = self.flow
        n = len(flow.steps)
        metro = isinstance(self.strip, MetroStrip)
        start_keys = {f"in:{u}" for u in flow.inputs}

        def state(i, done_upto, moving=None):
            if metro:
                return {"pos": (moving if moving is not None else i), "station": i if 0 <= i < n else None}
            if i < 0:
                current = start_keys
                focus = 0.0
            elif i >= n:
                current = {"result"}
                focus = float(len(self.strip.items) - 1)
            else:
                current = {f"step:{i}"} | {f"in:{u}" for u in flow.inputs if flow.first_use.get(u) == i
                                           and f"in:{u}" not in {k for k, _x, _y in self.strip.items}}
                focus = float(self.strip.focus_index(f"step:{i}"))
            if moving is not None and not metro:
                focus = moving
            done = set(start_keys) | {f"step:{j}" for j in range(min(done_upto, n))}
            if done_upto >= n:
                done |= {"result"}
            return {"current": current, "done": done, "focus": focus}
        return state

    def keyframes(self):
        """Frames with every colour the animation uses: maps, captions and the strip."""
        state = self._states()
        n = len(self.flow.steps)
        frames = []
        start_ctx = [(self.maps.full(lyr), 1.0) for lyr in self.starts.values()]
        frames.append(self.compose(start_ctx, ("Starting layers", "x"), state(-1, 0)))
        for i, step_plan in enumerate(self.flow.steps):
            layer = self.found.get(step_plan.number)
            frames.append(self.compose([(self.maps.full(lyr), 0.55) for lyr in self.used.get(step_plan.number, [])]
                                       + ([(self.maps.full(layer), 1.0)] if layer is not None else []),
                                       ("x", "x"), state(i, i)))
        finals = self._final_layers()
        frames.append(self.compose(self._final_stack(finals), ("x", "x"),
                                   state(n, n, n if isinstance(self.strip, MetroStrip) else None), legend=finals))
        return frames

    def estimate(self):
        options = self.options
        per = self.n(options.draw) + self.n(options.fade) + 1
        return self.n(options.draw) + (len(self.flow.steps) + 2) * per + self.n(options.fade)

    def map_layer_image(self) -> QImage:
        """A transparent image the size of the map area, drawn on in layout units."""
        image = QImage(self.out_W, int(round(self.map_h * self.ratio)), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(self.ratio)
        return image

    def map_area(self) -> QRectF:
        return QRectF(0, 0, self.W, self.map_h)

    def _blend_map(self, prev: QImage, frame: QImage, x: float) -> QImage:
        """``frame`` with the map still fading over from ``prev``; the strip stays sharp, so
        movement along it reads as movement, not a blur."""
        out = QImage(frame)
        p = QPainter(out)
        p.setOpacity(1 - _ease(x))
        area = self.map_area()
        _draw_part(p, area, prev, area)
        p.end()
        return out

    def frames(self):
        if self.options.kind == "before_after":
            yield from self._before_after()
            return
        options, flow = self.options, self.flow
        state = self._states()
        title = self.title_text()
        parts_on = bool(self.makers)
        n = len(flow.steps)
        metro = isinstance(self.strip, MetroStrip)
        first = prev = None
        current_part = None
        earlier = []
        start_of = dict(self.starts)
        for i, step_plan in enumerate(flow.steps):
            part = flow.part_of.get(step_plan.number, 0)
            if i == 0 or (parts_on and part != current_part):
                # the starting layers (of this part), one after another
                current_part = part
                if parts_on:
                    self.maps = self.makers[part]
                    earlier = []
                starts = [(u, lyr) for u, lyr in self.starts.items()
                          if not parts_on or flow.input_part.get(u, 0) == part]
                names = ", ".join(flow.input_names[u] for u, _l in starts)
                heading = tr("Part {k} of {n}").format(k=part + 1, n=flow.n_parts) if parts_on else tr("Starting layers")
                cap = (heading, names)
                st = state(i - 1 if i else -1, i)
                blank = self.compose([], cap, st)
                if prev is None:
                    first = blank
                    yield blank, 50
                else:
                    steps = self.n(options.fade)
                    for k in range(1, steps + 1):
                        yield self._blend_map(prev, blank, k / steps), self.delay
                ctx, last = [], blank
                for _uid, layer in starts:
                    for last, d in self.reveal(layer, [], list(ctx), cap, st, options.draw / max(len(starts), 1) + 0.4,
                                               edge=False):
                        yield last, d
                    ctx.append((self.maps.full(layer), 1.0))
                yield last, int(options.pause * 100)
                prev = last
            used = self.used.get(step_plan.number, []) or [start_of[u] for u in start_of if flow.first_use.get(u) == i]
            context = [(self.maps.full(lyr), 0.3) for lyr in earlier if options.earlier and lyr not in used]
            context += [(self.maps.full(lyr), 0.55) for lyr in used]
            result = self.found.get(step_plan.number)
            cap = (flow.tool(i, options.numbers), ("→ " + flow.result_of(i)) if flow.result_of(i) else "")
            base = self.compose(context, cap, state(i, i))
            # move along the strip (a ride takes a little longer) while the map fades over
            fade_n = self.n(options.fade)
            travel_n = max(fade_n, self.n(options.draw * 0.5)) if metro else fade_n
            for k in range(1, travel_n + 1):
                x = k / travel_n
                moving = None
                if metro:
                    moving = (i - 1) + _ease(x)
                elif isinstance(self.strip, FlowStrip):
                    a = self.strip.focus_index(f"step:{i - 1}") if i > 0 else 0
                    b = self.strip.focus_index(f"step:{i}")
                    moving = a + (b - a) * _ease(x)
                frame = self.compose(context, cap, state(i, i, moving))
                yield (self._blend_map(prev, frame, k / fade_n) if k < fade_n else frame), self.delay
            last = base
            if result is not None:
                for last, d in self.reveal(result, used, context, cap, state(i, i), options.draw):
                    yield last, d
                if result not in earlier:
                    earlier.append(result)
            else:
                yield base, self.delay
            last = self.compose(context + ([(self.maps.full(result), 1.0)] if result is not None else []), cap,
                                state(i, i + 1))
            yield last, int(options.pause * 100)
            prev = last
        # the result (all parts together)
        if parts_on:
            self.maps = self.all_maps
        finals = self._final_layers()
        layers = self._final_stack(finals)
        caption = (title, flow.result_name)
        final = self.compose(layers, caption, state(n, n, (n if metro else None)), legend=finals)
        fade_n = self.n(options.fade)
        travel_n = max(fade_n, self.n(options.draw * 0.5)) if metro else fade_n
        for k in range(1, travel_n + 1):   # the last ride, sharp, while the map fades to the result
            frame = self.compose(layers, caption, state(n, n, (n - 1) + _ease(k / travel_n)), legend=finals) \
                if metro else final
            yield (self._blend_map(prev, frame, k / fade_n) if k < fade_n else frame), self.delay
        if metro and getattr(self.strip, "victory", False):   # the knight's victory, at the end of the ride
            steps = self.n(0.9)
            for k in range(1, steps + 1):
                st = state(n, n, n)
                st["victory"] = k / steps
                yield self.compose(layers, caption, st, legend=finals), self.delay
        yield from self.end_frames(final, first)

    def _final_layers(self):
        """The final outputs, or the one chosen in "Shown at the end"."""
        choice = getattr(self.options, "final_output", 0)
        if choice and self.found.get(choice) is not None:
            return [self.found[choice]]
        return snapshots.final_layers(self.controller, self.found)

    def _final_stack(self, finals):
        """The ending's map: the outputs, zoomed to them, on the project's visible layers (faint);
        side-by-side tiles when they lie far apart. Switches self.maps to the ending's map."""
        if not finals:
            return []
        crs = self.all_maps.settings.destinationCrs()
        if snapshots.spread_out(finals, crs):
            bg = snapshots.background_layers() if self.options.basemaps else []
            tiles = self.sharp(snapshots.tiles(finals, bg, self.px(QSize(self.W - 24, self.map_h - 24)),
                                               snapshots.map_fill(self.t)))
            image = self.map_layer_image()
            image.fill(Qt.GlobalColor.transparent)
            if tiles is not None:
                p = QPainter(image)
                p.drawImage(12, 12, tiles)
                p.end()
            self.maps = self.all_maps
            return [(image, 1.0)]
        if getattr(self, "_end_maps", None) is None:
            self._end_maps = MapMaker(finals, self.W, self.map_h, self.options.basemaps, self.ratio)
        self.maps = self._end_maps
        context = [layer for layer in snapshots.visible_layers() if layer not in finals
                   and layer.type() == QgsMapLayer.LayerType.VectorLayer]
        stack = [(self.maps.draw(context), 0.35)] if context else []
        return stack + [(self.maps.full(layer), 1.0) for layer in finals]

    def _before_after(self):
        options = self.options
        title = self.title_text()
        finals = self._final_layers()
        before = [(self.maps.full(layer), 1.0) for layer in self.starts.values()]
        context = [layer for layer in snapshots.visible_layers() if layer not in finals
                   and layer.type() == QgsMapLayer.LayerType.VectorLayer]
        after = ([(self.maps.draw(context), 0.35)] if context else []) + [(self.maps.full(layer), 1.0) for layer in finals]
        b_img = self._layer_stack(before)
        a_img = self._layer_stack(after)
        cap_b, cap_a = (title, tr("Before: the starting layers")), (title, tr("After: ") + self.flow.result_name)
        first = self.compose([(b_img, 1.0)], cap_b)
        yield first, int(options.pause * 100) + 60

        def wipe(src, dst, cap, forward=True):
            steps = self.n(options.draw)
            last = None
            for i in range(1, steps + 1):
                x = _ease(i / steps)
                if options.wipe == "fade":
                    last = self.compose([(src, 1.0 - x), (dst, x)], cap)
                    yield last, self.delay
                    continue
                part = self.map_layer_image()
                part.fill(Qt.GlobalColor.transparent)
                painter = QPainter(part)
                if options.wipe == "down":
                    edge = self.map_h * x
                    r = QRectF(0, 0, self.W, edge)
                else:
                    edge = self.W * x
                    r = QRectF(0, 0, edge, self.map_h)
                _draw_part(painter, r, dst, r)
                painter.end()

                def line(p, edge=edge, i=i):
                    if i < steps:
                        p.setPen(QPen(QColor(224, 122, 31, 210), 2.2))
                        if options.wipe == "down":
                            p.drawLine(QPointF(0, edge), QPointF(self.W, edge))
                        else:
                            p.drawLine(QPointF(edge, 0), QPointF(edge, self.map_h))
                last = self.compose([(src, 1.0), (part, 1.0)], cap if x > 0.5 else None, None, line)
                yield last, self.delay

        yield from wipe(b_img, a_img, cap_a)
        final = self.compose([(a_img, 1.0)], cap_a, legend=finals)
        yield final, int(options.end * 100)
        if options.loop:
            yield from wipe(a_img, b_img, cap_b)

    def _layer_stack(self, layers):
        image = self.map_layer_image()
        image.fill(Qt.GlobalColor.transparent)
        p = QPainter(image)
        for img, opacity in layers:
            p.setOpacity(opacity)
            p.drawImage(0, 0, img)
        p.end()
        return image


# ---------------------------------------------------------------- metro ride on its own

class MetroRide(Animation):
    FIXED_LAYOUT = True

    def prepare(self):
        options = self.options
        self.flow = Flow(self.controller, options.detail)
        self.strip = MetroStrip(self.flow, self.t, self.W, big=True, numbers=options.numbers)
        self.H = max(self.H, int(self.strip.height + 120))
        found, used, self._keep = snapshots.result_layers(self.controller)
        size = QSize(int(min(150, self.strip.spacing - 16)), int(min(150, self.strip.spacing - 16) * 0.64))
        background = snapshots.background_layers() if options.basemaps else []
        self.thumbs = {}
        for i, step_plan in enumerate(self.flow.steps):
            layer = found.get(step_plan.number)
            try:
                self.thumbs[i] = self.sharp(snapshots.render(layer, used.get(step_plan.number, []), background,
                                                             self.px(size))) if layer else None
            except Exception:
                self.thumbs[i] = None
            if snapshots.is_general(options):   # General detail: the kinds of data, no real maps
                self.thumbs[i] = self.sharp(snapshots.kind_card(snapshots.step_kind(step_plan), self.px(size)))
        self.title = self.title_text()

    def compose(self, pos, station, shown):
        image = self.frame_image()
        image.fill(QColor(self.t["bg"]))
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setPen(QColor(self.t["ink"]))
        p.setFont(_font(20, True, _TITLE[0]))
        p.drawText(QPointF(24, 40), QFontMetricsF(_font(20, True, _TITLE[0])).elidedText(
            self.title, Qt.TextElideMode.ElideRight, self.W - 48))
        top = (self.H - self.strip.height) / 2 + 30
        self.strip.draw(p, top, pos, station, self.thumbs, shown)
        watermark(p, self.W, self.H, self.t)
        p.end()
        return image

    def keyframes(self):
        n = len(self.flow.steps)
        return [self.compose(-1, None, set()), self.compose(n, None, set(range(n)))]

    def estimate(self):
        return (len(self.flow.steps) + 1) * (self.n(self.options.draw * 0.6) + 2) + 4

    def frames(self):
        options, n = self.options, len(self.flow.steps)
        first = self.compose(-1, None, set())
        yield first, 80
        shown = set()
        pos = -1.0
        last = first
        for target in list(range(n)) + [n]:
            steps = self.n(options.draw * 0.6)
            for k in range(1, steps + 1):
                p = pos + (target - pos) * _ease(k / steps)
                last = self.compose(p, None, shown)
                yield last, self.delay
            pos = float(target)
            if target < n:
                shown.add(target)
                last = self.compose(pos, target, shown)
                yield last, int(options.pause * 100)
        yield from self.end_frames(last, first)


# ---------------------------------------------------------------- image styles

def _svg_image(svg: str, width: int, max_height: int):
    """The SVG drawn to fit ``width`` (and ``max_height``); the image and its scale."""
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    size = renderer.defaultSize()
    scale = width / max(size.width(), 1)
    if size.height() * scale > max_height:
        scale = max_height / max(size.height(), 1)
    w, h = int(size.width() * scale), int(size.height() * scale)
    image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    renderer.render(p, QRectF(0, 0, w, h))
    p.end()
    return image, scale


class ImageAnimation(Animation):
    """Build-up, spotlight, data flow and storyboard: an image style, animated."""

    def prepare(self):
        options = self.options
        style = {"data_flow": "data", "storyboard": "storyboard", "comic": "comic"}.get(options.kind, options.style)
        detail = options.detail
        if style == "data" and detail == "general":
            detail = "named"
        images.LAST_DOC = None
        image_options = images.ImageOptions(style, detail, options.theme, False, True, options.numbers,
                                            legend=options.legend, title=options.title,
                                            map_background=options.basemaps, inputs=options.inputs,
                                            fonts=options.fonts, font=options.font, title_font=options.title_font,
                                            inputs_side=options.inputs_side, overview=options.overview,
                                            overview_zoom=options.overview_zoom, part=options.part)
        svg = images.render_svg(self.controller, image_options)
        self.doc = images.LAST_DOC if style != "boxes" else None
        drawing, self.s = _svg_image(svg, self.W - 24, self.H - 24)   # fitted into the 16:9 frame
        self.ox, self.oy = (self.W - drawing.width()) / 2, (self.H - drawing.height()) / 2
        self.drawing_h = drawing.height()
        corner = drawing.pixelColor(2, 2)   # the drawing's own background, so covers and margins match it
        self.bg = corner if corner.alpha() == 255 else QColor(self.t["bg"])

        def framed(img):
            out = QImage(self.W, self.H, QImage.Format.Format_ARGB32_Premultiplied)
            out.fill(self.bg)
            painter = QPainter(out)
            painter.drawImage(QPointF(self.ox, self.oy), img)
            painter.end()
            return out
        self.full = framed(drawing)
        if options.kind == "camera_tour":   # a sharper copy to zoom into
            self.zoom = 2.6
            self.big, _s = _svg_image(svg, int(drawing.width() * self.zoom), int(drawing.height() * self.zoom))

    def to_px(self, r: QRectF) -> QRectF:
        dx, dy = self.doc.offset
        return QRectF((r.x() + dx) * self.s + self.ox, (r.y() + dy) * self.s + self.oy, r.width() * self.s,
                      r.height() * self.s)

    def canvas(self) -> QImage:
        image = QImage(self.W, self.H, QImage.Format.Format_RGB32)
        image.fill(self.bg)
        return image

    def keyframes(self):
        ghost = self.canvas()
        p = QPainter(ghost)
        p.setOpacity(0.15)
        p.drawImage(0, 0, self.full)
        p.end()
        whole = self.canvas()
        p = QPainter(whole)
        p.drawImage(0, 0, self.full)
        p.end()
        return [whole, ghost]

    def whole(self):
        return self.keyframes()[0]

    def head_and_foot(self):
        """The title band at the top and the mark at the bottom: always shown."""
        top = self.oy + ((self.doc.offset[1] - 34) * self.s if self.doc else 50 * self.s)
        bottom = self.oy + self.drawing_h
        return QRectF(0, 0, self.W, max(top, 0)), QRectF(0, bottom - 26 * self.s, self.W, self.H - bottom + 26 * self.s)

    def estimate(self):
        return 120

    def frames(self):
        kind = self.options.kind
        if kind == "build_up":
            yield from self._build_up()
        elif kind == "spotlight":
            yield from self._spotlight()
        elif kind == "data_flow":
            yield from self._data_flow()
        elif kind == "camera_tour":
            yield from self._camera()
        else:   # storyboard deal and comic strip
            yield from self._storyboard()

    # -- build-up: drawn in from left to right, pausing at each column
    def _columns(self):
        if self.doc is None or not self.doc.rects:
            n = max(len(self.controller.plan.steps), 1)
            return [self.W * i / (n + 1) for i in range(1, n + 2)]
        lefts = sorted(self.to_px(r).left() for r in self.doc.rects)
        stops = []
        for x in lefts:
            if not stops or x - stops[-1] > 24 * self.s:
                stops.append(x)
        rights = max(self.to_px(r).right() for r in self.doc.rects)
        return stops[1:] + [rights + 30]

    def _frame_reveal(self, x):
        image = self.canvas()
        p = QPainter(image)
        p.setOpacity(0.15)
        p.drawImage(0, 0, self.full)
        p.setOpacity(1.0)
        top, foot = self.head_and_foot()
        for r in (top, foot):
            p.drawImage(r, self.full, r)
        r = QRectF(0, 0, x, self.H)
        p.drawImage(r, self.full, r)
        feather = 26
        for k in range(4):   # a soft edge
            p.setOpacity(0.75 - k * 0.18)
            band = QRectF(x + k * feather / 4, 0, feather / 4, self.H)
            p.drawImage(band, self.full, band)
        p.end()
        return image

    def _build_up(self):
        options = self.options
        stops = self._columns()
        first = self._frame_reveal(0)
        yield first, 60
        x = 0.0
        last = first
        for stop in stops:
            steps = self.n(options.draw * 0.5)
            for k in range(1, steps + 1):
                last = self._frame_reveal(x + (stop - x) * _ease(k / steps))
                yield last, self.delay
            x = stop
            yield last, int(options.pause * 50)
        last = self.whole()
        yield from self.end_frames(last, first)

    # -- spotlight
    def _spot_rects(self):
        if self.doc is None or not self.doc.rects:
            n = max(len(self.controller.plan.steps), 1)
            w = self.W / (n + 1)
            return [QRectF(w * i, 0, w, self.H) for i in range(n + 1)]
        groups = []
        for r in sorted((self.to_px(r) for r in self.doc.rects), key=lambda r: r.left()):
            if groups and r.left() - groups[-1][0].left() < 24 * self.s:
                groups[-1][1] = groups[-1][1].united(r)
            else:
                groups.append([r, QRectF(r)])
        return [g[1].adjusted(-14, -14, 14, 14) for g in groups]

    def _spot(self, rect: QRectF, veil: float):
        image = self.canvas()
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawImage(0, 0, self.full)
        if veil > 0:
            shade = QColor(self.t["bg"])
            shade.setAlphaF(0.8 * veil)
            path = QPainterPath()
            path.addRect(QRectF(0, 0, self.W, self.H))
            hole = QPainterPath()
            hole.addRoundedRect(rect, 14, 14)
            p.fillPath(path.subtracted(hole), shade)
            p.setPen(QPen(QColor(224, 122, 31, int(220 * veil)), 2.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect, 14, 14)
        p.end()
        return image

    def _spotlight(self):
        options = self.options
        rects = self._spot_rects()
        first = self.whole()
        yield first, int(options.pause * 100)
        if not rects:
            yield from self.end_frames(first, None)
            return
        current = rects[0]
        steps = self.n(options.fade)
        for k in range(1, steps + 1):
            yield self._spot(current, k / steps), self.delay
        last = None
        for target in rects:
            steps = self.n(options.draw * 0.4)
            for k in range(1, steps + 1):
                x = _ease(k / steps)
                r = QRectF(current.x() + (target.x() - current.x()) * x, current.y() + (target.y() - current.y()) * x,
                           current.width() + (target.width() - current.width()) * x,
                           current.height() + (target.height() - current.height()) * x)
                last = self._spot(r, 1.0)
                yield last, self.delay
            current = target
            yield last, int(options.pause * 100)
        steps = self.n(options.fade)
        for k in range(1, steps + 1):
            last = self._spot(current, 1 - k / steps)
            yield last, self.delay
        yield from self.end_frames(self.whole(), first)

    # -- data flow
    def _data_flow(self):
        options = self.options
        marks = self.doc.marks if self.doc else {}
        boxes, depth, flows = marks.get("boxes", {}), marks.get("depth", {}), marks.get("flows", [])
        tools = marks.get("tools", set())
        if not boxes:
            yield from self._build_up()
            return

        def area(key):
            r = self.to_px(boxes[key])
            extra = 26 * self.s if key in tools else 0
            return r.adjusted(-6, -6, 6, 6 + extra)

        shown = {k: 1.0 for k in boxes if k.startswith("input:")}
        drawn = set()   # flows drawn in full
        path_px = []
        dx, dy = self.doc.offset
        for a, b, path in flows:
            px = QPainterPath()
            for i in range(41):
                pt = path.pointAtPercent(i / 40)
                q = QPointF((pt.x() + dx) * self.s + self.ox, (pt.y() + dy) * self.s + self.oy)
                if i == 0:
                    px.moveTo(q)
                else:
                    px.lineTo(q)
            path_px.append((a, b, px))

        def frame(moving=(), progress=0.0):
            image = self.canvas()
            p = QPainter(image)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setOpacity(0.15)
            p.drawImage(0, 0, self.full)
            top, foot = self.head_and_foot()
            p.setOpacity(1.0)
            for r in (top, foot):
                p.drawImage(r, self.full, r)
            line = QColor(self.t["line"])
            for a, b, px in path_px:
                if (a, b) in drawn:
                    p.setPen(QPen(line, 1.6 * self.s))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPath(px)
            for key, alpha in shown.items():
                if alpha > 0:
                    p.setOpacity(alpha)
                    r = area(key)
                    p.drawImage(r, self.full, r)
            p.setOpacity(1.0)
            for a, b, px in path_px:
                if (a, b) not in moving:
                    continue
                part = QPainterPath()
                for i in range(int(progress * 40) + 1):
                    pt = px.pointAtPercent(i / 40)
                    if i == 0:
                        part.moveTo(pt)
                    else:
                        part.lineTo(pt)
                p.setPen(QPen(line, 1.6 * self.s))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(part)
                for k in range(3):
                    f = progress - k * 0.08
                    if 0 <= f <= 1:
                        pt = px.pointAtPercent(f)
                        p.setPen(QPen(QColor("#FFFFFF"), 1.5))
                        p.setBrush(ACCENT)
                        p.drawEllipse(pt, 4.5 - k, 4.5 - k)
            p.end()
            return image

        first = frame()
        yield first, int(options.pause * 100)
        levels = sorted({depth[k] for k in tools})
        last = first
        for d in levels:
            for targets in ([k for k in tools if depth[k] == d],
                            [b for a, b, _p in flows if a in tools and depth.get(a) == d]):
                moving = {(a, b) for a, b, _p in flows if b in targets}
                steps = self.n(options.draw * 0.45)
                for k in range(1, steps + 1):
                    last = frame(moving, _ease(k / steps))
                    yield last, self.delay
                drawn.update(moving)
                steps = self.n(options.fade * 0.6)
                for k in range(1, steps + 1):
                    for key in targets:
                        shown[key] = k / steps
                    last = frame()
                    yield last, self.delay
            yield last, int(options.pause * 100)
        yield from self.end_frames(self.whole(), first)

    # -- camera tour: a smooth pan and zoom from step to step
    def _view(self, r: QRectF) -> QImage:
        """The part of the drawing inside ``r`` (frame coordinates), filling the frame."""
        image = self.canvas()
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        z = self.zoom
        src = QRectF((r.x() - self.ox) * z, (r.y() - self.oy) * z, r.width() * z, r.height() * z)
        p.drawImage(QRectF(0, 0, self.W, self.H), self.big, src)
        p.end()
        return image

    def _camera(self):
        options = self.options
        whole = QRectF(0, 0, self.W, self.H)
        stops = []
        for r in self._spot_rects():
            r = r.adjusted(-30, -30, 30, 30)
            w = max(r.width(), r.height() * 16 / 9, self.W / self.zoom)   # 16:9, and no closer than the copy is sharp
            h = w * 9 / 16
            c = r.center()
            stops.append(QRectF(c.x() - w / 2, c.y() - h / 2, w, h))
        first = self._view(whole)
        yield first, int(options.pause * 100) + 40
        current, last = whole, first
        for target in stops + [whole]:
            steps = self.n(options.draw * 0.5)
            for k in range(1, steps + 1):
                x = _ease(k / steps)
                r = QRectF(current.x() + (target.x() - current.x()) * x, current.y() + (target.y() - current.y()) * x,
                           current.width() + (target.width() - current.width()) * x,
                           current.height() + (target.height() - current.height()) * x)
                last = self._view(r)
                yield last, self.delay
            current = target
            if target is not whole:
                yield last, int(options.pause * 60)
        yield from self.end_frames(last, first)

    # -- storyboard: cards dealt one at a time
    def _storyboard(self):
        options = self.options
        cards = [(number, self.to_px(r)) for number, r in (self.doc.marks.get("cards", []) if self.doc else [])]
        if not cards:
            yield from self._build_up()
            return
        base = self.full

        def cover(i):
            """Card i with the arrow on to the next card: to its right, to its left (a row running
            back) or below it (down to the next row), so the arrow arrives with the card."""
            r = cards[i][1]
            if i + 1 < len(cards):
                nxt = cards[i + 1][1]
                if abs(nxt.top() - r.top()) < 2 and nxt.left() > r.right():
                    return r.adjusted(0, 0, nxt.left() - r.right(), 0)
                if abs(nxt.top() - r.top()) < 2 and nxt.right() < r.left():
                    return r.adjusted(nxt.right() - r.left(), 0, 0, 0)
                if nxt.top() > r.bottom() and abs(nxt.center().x() - r.center().x()) < r.width() / 2:
                    return r.adjusted(0, 0, 0, nxt.top() - r.bottom())
            return r

        def frame(dealt, moving=None, x=0.0):
            image = self.canvas()
            p = QPainter(image)
            p.drawImage(0, 0, base)
            for i in range(len(cards)):   # cards still to come are covered, exactly
                if i not in dealt:
                    p.fillRect(cover(i), self.bg)
            if moving is not None:
                r = cards[moving][1]
                p.setOpacity(_ease(x))
                p.drawImage(r.translated(0, 34 * (1 - _ease(x))), base, r)
                p.setOpacity(1.0)
            p.end()
            return image

        first = frame(set())
        yield first, int(options.pause * 100)
        dealt = set()
        last = first
        for i in range(len(cards)):
            steps = self.n(max(options.draw * 0.35, 0.3))
            for k in range(1, steps + 1):
                last = frame(dealt, i, k / steps)
                yield last, self.delay
            dealt.add(i)
            last = frame(dealt)
            yield last, int(options.pause * 100)
        yield from self.end_frames(self.whole(), first)


# ---------------------------------------------------------------- making it

def make(controller, opts: AnimationOptions) -> Animation:
    if opts.detail == "general" and opts.overview:   # General detail shows no real places
        import dataclasses
        opts = dataclasses.replace(opts, overview=False)
    _FAMILY[0] = fonts.family_for(opts.kind, opts.fonts, opts.font)
    _TITLE[0] = fonts.title_family(opts.kind, opts.fonts, opts.title_font) or _FAMILY[0]
    if opts.kind == "pilgrimage":
        from .pilgrimage import Pilgrimage
        anim = Pilgrimage(controller, opts)
    elif opts.kind == "map_pilgrimage":
        from .pilgrimage import MapPilgrimage
        anim = MapPilgrimage(controller, opts)
    elif opts.kind == "typewriter":
        from .typewriter import Typewriter
        anim = Typewriter(controller, opts)
    elif opts.kind in ("kitchen", "assembly"):
        from .stations import AssemblyLine, Kitchen
        anim = (Kitchen if opts.kind == "kitchen" else AssemblyLine)(controller, opts)
    elif opts.kind == "board_game":
        from .boardgame import BoardGame
        anim = BoardGame(controller, opts)
    elif opts.kind in MAP_KINDS:
        anim = MapAnimation(controller, opts)
    elif opts.kind == "metro_ride":
        anim = MetroRide(controller, opts)
    else:
        anim = ImageAnimation(controller, opts)
    anim.prepare()
    return anim


def rough_estimate(opts: AnimationOptions, n_steps: int):
    """(seconds, frames, kilobytes), roughly, without making the animation: for the Export
    window's live estimate."""
    fps, n = max(4, opts.fps), max(n_steps, 1)
    k = opts.kind
    if k == "before_after":
        seconds = opts.pause + 0.6 + opts.draw * (2 if opts.loop else 1) + opts.end
        frames = fps * opts.draw * (2 if opts.loop else 1) + 3
    elif k == "typewriter":
        seconds = opts.draw * (n + 1.8) + opts.pause * n + opts.end
        frames = fps * opts.draw * (n + 1.8) + 2 * opts.pause * n
    elif k in ("build_up", "spotlight", "data_flow", "storyboard", "comic", "camera_tour"):
        per = opts.draw * 0.5 + opts.pause * 0.6
        seconds = (n + 1) * per + opts.end + opts.pause
        frames = fps * (n + 1) * opts.draw * 0.5 + n + 4
    else:   # the map stories, the rides and the pilgrimage
        seconds = (n + 1) * (opts.draw + opts.pause + opts.fade) + opts.end + (opts.fade if opts.loop else 0)
        frames = fps * (n + 1) * (opts.draw + opts.fade) + n + 4
    per_frame = {"map_story": 5.5, "map_metro": 5.5, "map_pilgrimage": 6.5, "pilgrimage": 7.0, "typewriter": 6.0,
                 "before_after": 2.5, "kitchen": 4.0, "assembly": 4.0, "board_game": 3.5, "camera_tour": 6.0}.get(k, 2.0)
    kb = frames * per_frame * (opts.width * opts.width * 9 / 16) / (760 * 500)
    return seconds, int(frames), kb


def write_gif(controller, opts: AnimationOptions, path: str, progress=None) -> int:
    """Makes the animation and writes it as a GIF; returns the number of frames.
    ``progress(done, total)`` returns False to stop."""
    anim = make(controller, opts)
    keys = anim.keyframes()
    samples = list(keys) + [_blend(a, b, 0.5) for a, b in zip(keys, keys[1:]) if a.size() == b.size()]
    gif = GifWriter(path, anim.out_W, anim.out_H, palette_for(samples), loop=opts.loop)
    total, done = anim.estimate(), 0
    try:
        for image, delay in anim.frames():
            if image.width() != anim.out_W or image.height() != anim.out_H:
                image = image.scaled(anim.out_W, anim.out_H)
            gif.add(image, delay)
            done += 1
            if progress is not None and progress(done, max(total, done + 1)) is False:
                raise Cancelled()
    finally:
        gif.close()
    return done


def write_frames(controller, opts: AnimationOptions, folder: str, progress=None) -> int:
    """Every frame as a numbered PNG, plus timings.csv (milliseconds each frame shows)."""
    anim = make(controller, opts)
    os.makedirs(folder, exist_ok=True)
    total, done = anim.estimate(), 0
    with open(os.path.join(folder, "timings.csv"), "w", encoding="utf-8") as fh:
        fh.write("frame,milliseconds\n")
        for image, delay in anim.frames():
            done += 1
            name = f"frame_{done:04d}.png"
            image.save(os.path.join(folder, name))
            fh.write(f"{name},{delay * 10}\n")
            if progress is not None and progress(done, max(total, done + 1)) is False:
                raise Cancelled()
    return done
