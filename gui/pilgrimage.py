"""The Pilgrimage animation: a knight on horseback rides a winding road from the village
(the starting layers) past a medieval building for each tool, to the cathedral (the
result), on old paper inside an illuminated border."""

from __future__ import annotations

import math
import random

from qgis.PyQt.QtCore import QPointF, QRectF, QSize, Qt
from qgis.PyQt.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)

from . import fonts, icons, snapshots
from .animation import Animation, Flow, MapAnimation, MapMaker, MetroStrip, _blend, _ease, size_of, watermark  # noqa: F401

INK = QColor("#3B2412")
FADED = QColor("#8C7350")
RED = QColor("#8B1E1E")
BRIGHT_RED = QColor("#B3261E")
BLUE = QColor("#1F3A93")
GOLD = QColor("#C9A227")
LIGHT_GOLD = QColor("#E8C547")
GREEN = QColor("#3E6B2E")
STONE = QColor("#CFC4AE")
STONE_DARK = QColor("#8E8371")
ROOF = QColor("#7A2E1E")
PAPER = QColor("#EEDDB5")
ROAD = QColor("#E2C990")
ROAD_EDGE = QColor("#B08E55")


ENDINGS = ["Here endeth the recipe", "Huzzah, the work is done!", "Thus the quest is complete",
           "And so the deed was done", "So it is written", "The journey's end is reached",
           "Rejoice, the task is finished!", "Well met, the work is done"]


def path_text(p: QPainter, rect: QRectF, text: str, font: QFont, colour: QColor, align="center"):
    """Text drawn as filled outlines, with the font's letter substitutions off: the same on
    every system (some draw this blackletter font's substitutions wrongly and drop letters)."""
    font = QFont(font)
    font.setStyleStrategy(QFont.StyleStrategy.PreferNoShaping)
    font.setKerning(False)
    path = QPainterPath()
    path.addText(0, 0, font, text)
    box = path.boundingRect()
    fm = QFontMetricsF(font)
    if align == "center":
        x = rect.center().x() - box.width() / 2 - box.x()
    else:
        x = rect.left() - box.x() * 0
    y = rect.center().y() + (fm.ascent() - fm.descent()) / 2
    p.save()
    p.translate(x, y)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(colour)
    p.drawPath(path)
    p.restore()


def roman(n: int) -> str:
    out = ""
    for value, letters in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
                           (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out += letters
            n -= value
    return out


class Pilgrimage(Animation):
    FIXED_LAYOUT = True
    THEME = dict(muted="#7A5C3A")
    clip_top = 6      # how far above the road area things may be drawn (the heading is above it)
    reach_up = 0

    def font(self, kind, size):
        """kind: "title" (blackletter, unless another font was chosen), "label" (small
        capitals) or "text"."""
        options = self.options
        if kind == "title":
            family = fonts.title_family("pilgrimage", options.fonts, options.title_font)
        else:
            family = fonts.family_for("pilgrimage", options.fonts, options.font)
        f = QFont(family) if family else QFont()
        f.setPixelSize(int(size))
        if kind == "label":
            f.setCapitalization(QFont.Capitalization.SmallCaps)
        return f

    # ------------------------------------------------------------ setting up
    def prepare(self):
        fonts.load()
        options = self.options
        self.flow = Flow(self.controller, options.detail)
        self.H = int(round(self.W * 9 / 16))
        self.m = 14
        self.n_stops = len(self.flow.steps)
        self.title = self.title_text()
        # the page: the heading at the top, a clear band at the bottom for the closing words
        inner = QRectF(self.m + 12, self.m + 104, self.W - 2 * (self.m + 12), self.H - (self.m + 104) - self.m - 92)
        self.spacing = max(150.0, (inner.width() - 160) / max(self.n_stops + 1, 1))
        # small maps of each result
        found, used, self._hold = snapshots.result_layers(self.controller)
        tw = int(min(120, self.spacing - 20))
        size = QSize(tw, int(tw * 0.66))
        background = snapshots.background_layers() if options.basemaps else []
        self.thumbs = {}
        for i, step_plan in enumerate(self.flow.steps):
            layer = found.get(step_plan.number)
            try:
                image = self.sharp(snapshots.render(layer, used.get(step_plan.number, []), background, self.px(size))) \
                    if layer else None
            except Exception:
                image = None
            self.thumbs[i] = self._aged(image) if image is not None else self._no_map(size)
        self.layout(inner)
        # the result, shown large on the last page
        self.final_map = None
        fw, fh = int(self.W - 2 * (self.m + 60)), int(self.H - (self.m + 104) - self.m - 110)
        try:   # the final outputs (or the one chosen), tiles when far apart
            final_size = self.px(QSize(max(fw, 200), max(fh, 120)))
            self.final_map = self.sharp(snapshots.final_map(self.controller, found, final_size, background,
                                                            choice=getattr(options, "final_output", 0)))
        except Exception:
            self.final_map = None
        if snapshots.is_general(options):   # General detail: no real maps at the stops or at the end
            self.thumbs, self.thumb_at, self.final_map = {}, {}, None
        self.inset = None
        if options.overview:
            from . import overview
            maps = MapMaker(list(snapshots.starting_layers(self.controller).values()) + list(found.values()), 10, 10)
            crs = maps.settings.destinationCrs()
            w = int(min(170, self.W * 0.2))
            self.inset = overview.inset([(maps.settings.extent(), crs, True)], QSize(w, w // 2),
                                        dict(bg="#F3E6C4", tint="#E9D6A8", muted="#7A5C3A", box="#8B1E1E",
                                             zoom=options.overview_zoom))
        self.background = self._parchment()

    # ------------------------------------------------------------ where everything goes
    def _places(self):
        """The village (where the road starts) and the hamlets that join it: one for each
        starting layer, or one for a group of similar layers used together ("6 point layers")."""
        flow = self.flow
        self.places = []   # [(name, index of the first step that uses it)]
        for uids in snapshots.input_groups(self.controller.plan):
            uids = [u for u in uids if u in flow.input_names]
            if not uids:
                continue
            if len(uids) == 1:
                name = flow.input_names[uids[0]]
            else:
                word = icons.layer_icon(self.controller.plan.inputs[uids[0]].layer_kind)[1]
                name = f"{len(uids)} {word}s"
            self.places.append((name, min(flow.first_use.get(u, 0) for u in uids)))
        self.places.sort(key=lambda item: item[1])
        self.village_name = self.places[0][0] if self.places else ""

    def layout(self, area: QRectF, amp=0.16, mid=0.56, hamlets=True):
        """The road through ``area``, and a place for every hamlet and small map where it
        doesn't cover a building, a banner or the road."""
        self.area = area
        n = self.n_stops
        self.spacing = max(150.0, (area.width() - 160) / max(n + 1, 1))
        a = area.height() * amp
        y_mid = area.top() + area.height() * mid
        self._places()
        first = self.village_name
        start = max(60.0, self._banner_rect(QPointF(0, 0), first).width() / 2 + 14) if first else 60.0
        self.points = [QPointF(area.left() + start + (i + 1) * self.spacing, y_mid + a * math.sin((i + 1) * 1.3))
                       for i in range(-1, n + 1)]
        self.road = QPainterPath(self.points[0])
        lengths = [0.0]
        for p0, p1 in zip(self.points, self.points[1:]):
            dx = (p1.x() - p0.x()) * 0.45
            segment = QPainterPath(p0)
            segment.cubicTo(QPointF(p0.x() + dx, p0.y()), QPointF(p1.x() - dx, p1.y()), p1)
            self.road.cubicTo(QPointF(p0.x() + dx, p0.y()), QPointF(p1.x() - dx, p1.y()), p1)
            lengths.append(lengths[-1] + segment.length())
        total = lengths[-1] or 1.0
        self.stop_pc = [length / total for length in lengths]
        self.total_w = self.points[-1].x() + 100 - area.left()
        taken = []
        steps = max(int(self.road.length() / 12), 2)
        for k in range(steps + 1):   # the road itself
            pt = self.road.pointAtPercent(k / steps)
            taken.append(QRectF(pt.x() - 6, pt.y() - 9, 12, 18))
        labels = [self.village_name] + \
                 [self.label(i) for i in range(n)] + [self.flow.result_name]
        for k, pt in enumerate(self.points):
            taken.append(QRectF(pt.x() - 36, pt.y() - 82, 72, 78))                     # the building
            taken.append(self._banner_rect(QPointF(pt.x(), pt.y() + 12), labels[k]))   # its banner

        def free(rect):
            return all(not rect.intersects(r) for r in taken)

        def inside(rect):
            return rect.top() >= area.top() - self.reach_up and rect.bottom() <= area.bottom() + 12

        self.hamlets = []
        at_stop = {}
        for name, i in (self.places[1:] if hamlets else []):
            stop = self.points[i + 1]
            j = at_stop.get(i, 0)   # several hamlets joining one stop spread out along the road
            at_stop[i] = j + 1
            first = (-1 if stop.y() > y_mid else 1) * (1 if j % 2 == 0 else -1)
            choice = None
            for dx in (0.68, 0.95, 0.45, 1.2, 0.3):
                x = max(stop.x() - self.spacing * (dx + 0.22 * (j // 2)), area.left() + 46)
                for frac in (0.38, 0.48, 0.3, 0.56, 0.22, 0.64):
                    for side in (first, -first):
                        hy = stop.y() + side * area.height() * frac
                        rect = QRectF(x - 22, hy - 26, 44, 30).united(self._banner_rect(QPointF(x, hy + 8), name))
                        if inside(rect) and free(rect):
                            choice = (x, hy)
                            break
                    if choice is not None:
                        break
                if choice is not None:
                    break
            if choice is None:   # no free room: as near as fits inside the picture
                x = max(stop.x() - self.spacing * 0.68, area.left() + 46)
                choice = (x, min(max(stop.y() + first * area.height() * 0.32, area.top() + 30), area.bottom() - 20))
            x, hy = choice
            taken.append(QRectF(x - 22, hy - 26, 44, 30).united(self._banner_rect(QPointF(x, hy + 8), name)))
            self.hamlets.append((name, QPointF(x, hy), i))
        self.thumb_at = {}
        for i in range(n):
            thumb = self.thumbs.get(i)
            if thumb is None:
                continue
            pt = self.points[i + 1]
            options = []
            for scale in (1.0, 0.8, 0.62):   # smaller rather than none, when room is short
                tw, th = size_of(thumb)[0] * scale, size_of(thumb)[1] * scale
                options += [QRectF(pt.x() - tw / 2, pt.y() - 92 - th, tw, th), QRectF(pt.x() - tw / 2, pt.y() + 50, tw, th),
                            QRectF(pt.x() - tw / 2, pt.y() - 92 - th - 24, tw, th),
                            QRectF(pt.x() - tw / 2, pt.y() + 74, tw, th)]
            best = next((r for r in options if inside(r) and free(r.adjusted(-5, -5, 5, 5))), None)
            if best is None:   # the least covered place on the page, or no map at this stop
                def overlap(r):
                    return sum(max(0.0, r.intersected(t).width() * r.intersected(t).height()) for t in taken)
                fits = [r for r in options if inside(r)] or options
                best = min(fits, key=overlap)
            taken.append(best.adjusted(-5, -5, 5, 5))
            self.thumb_at[i] = best

    def _banner_rect(self, centre: QPointF, text: str) -> QRectF:
        fm = QFontMetricsF(self.font("label", 13))
        w = min(fm.horizontalAdvance(text), self.spacing - 18) + 26
        return QRectF(centre.x() - w / 2 - 11, centre.y(), w + 22, 25)

    def _no_map(self, size: QSize) -> QImage:
        """A blank parchment card for a step whose result can't be drawn."""
        image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#F3E6C4"))
        p = QPainter(image)
        p.setPen(FADED)
        p.setFont(self.font("text", 11))
        p.drawText(QRectF(image.rect()), Qt.AlignmentFlag.AlignCenter, "no map")
        p.end()
        return image

    def _aged(self, image: QImage) -> QImage:
        """The map toned like old ink on paper."""
        out = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
        out.setDevicePixelRatio(image.devicePixelRatio())   # a sharp map stays sharp
        out.fill(QColor("#F3E6C4"))
        p = QPainter(out)
        p.setOpacity(0.85)
        p.drawImage(0, 0, image)
        p.setOpacity(1.0)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        p.fillRect(out.rect(), QColor(222, 196, 140))
        p.end()
        return out

    # ------------------------------------------------------------ the page
    def _parchment(self) -> QImage:
        W, H = self.W, self.H
        image = self.frame_image()
        image.fill(PAPER)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rnd = random.Random(11)  # nosec B311: drawing variety, not security
        for _ in range(46):   # stains
            c = QPointF(rnd.uniform(0, W), rnd.uniform(0, H))
            r = rnd.uniform(30, 140)
            g = QRadialGradient(c, r)
            tone = QColor(170, 128, 70)
            tone.setAlphaF(rnd.uniform(0.04, 0.10))
            g.setColorAt(0, tone)
            tone.setAlphaF(0)
            g.setColorAt(1, tone)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(c, r, r * rnd.uniform(0.6, 1.0))
        p.setPen(QPen(QColor(120, 90, 50, 22), 1))
        for _ in range(420):  # fibres
            x, y = rnd.uniform(0, W), rnd.uniform(0, H)
            a = rnd.uniform(0, math.pi)
            length = rnd.uniform(4, 14)
            p.drawLine(QPointF(x, y), QPointF(x + length * math.cos(a), y + length * math.sin(a)))
        g = QRadialGradient(QPointF(W / 2, H / 2), max(W, H) * 0.7)   # darker towards the edges
        g.setColorAt(0.55, QColor(120, 80, 30, 0))
        g.setColorAt(1.0, QColor(120, 80, 30, 95))
        p.fillRect(QRectF(0, 0, W, H), g)
        self._border(p)
        self._heading(p)
        if getattr(self, "inset", None) is not None:   # the overview map, in the heading's corner
            x, y = self.W - self.m - 26 - self.inset.width(), self.m + 22
            p.setPen(QPen(GOLD, 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawImage(QPointF(x, y), self.inset)
            p.drawRect(QRectF(x - 2, y - 2, self.inset.width() + 4, self.inset.height() + 4))
        p.end()
        return image

    def _border(self, p: QPainter):
        W, H, m = self.W, self.H, self.m
        outer = QRectF(m, m, W - 2 * m, H - 2 * m)
        inner = outer.adjusted(9, 9, -9, -9)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(GOLD, 3))
        p.drawRect(outer)
        p.setPen(QPen(RED, 1.6))
        p.drawRect(inner)
        # a vine running round the band between the rules, with leaves and coloured dots
        band = outer.adjusted(4.5, 4.5, -4.5, -4.5)
        sides = [(band.topLeft(), band.topRight()), (band.topRight(), band.bottomRight()),
                 (band.bottomRight(), band.bottomLeft()), (band.bottomLeft(), band.topLeft())]
        dots = [RED, BLUE, GOLD]
        k = 0
        for a, b in sides:
            length = math.hypot(b.x() - a.x(), b.y() - a.y())
            ux, uy = (b.x() - a.x()) / length, (b.y() - a.y()) / length
            nx, ny = -uy, ux
            vine = QPainterPath(a)
            steps = int(length / 4)
            for s in range(1, steps + 1):
                t = s * 4
                w = 2.2 * math.sin(t / 9)
                vine.lineTo(a.x() + ux * t + nx * w, a.y() + uy * t + ny * w)
            p.setPen(QPen(GREEN, 1.1))
            p.drawPath(vine)
            t = 26.0
            while t < length - 20:
                w = 2.2 * math.sin(t / 9)
                cx, cy = a.x() + ux * t + nx * w, a.y() + uy * t + ny * w
                p.save()
                p.translate(cx, cy)
                p.rotate(math.degrees(math.atan2(uy, ux)) + (35 if k % 2 else -35))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(GREEN)
                p.drawEllipse(QPointF(3, 0), 3.2, 1.4)
                p.restore()
                p.setBrush(dots[k % 3])
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(cx - ux * 9, cy - uy * 9), 1.4, 1.4)
                k += 1
                t += 18
        for c in (outer.topLeft(), outer.topRight(), outer.bottomLeft(), outer.bottomRight()):   # corner pieces
            r = QRectF(c.x() - 10, c.y() - 10, 20, 20)
            p.setPen(QPen(GOLD, 1.8))
            p.setBrush(BLUE)
            p.drawRect(r)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(BRIGHT_RED)
            for dx, dy in ((0, -3.5), (0, 3.5), (-3.5, 0), (3.5, 0)):
                p.drawEllipse(QPointF(c.x() + dx, c.y() + dy), 3, 3)
            p.setBrush(LIGHT_GOLD)
            p.drawEllipse(c, 2, 2)

    def _heading(self, p: QPainter):
        m = self.m
        title = self.title or "Recipe"
        first, rest = title[0], title[1:]
        box = QRectF(m + 26, m + 24, 58, 58)
        p.setPen(QPen(GOLD, 2.5))
        p.setBrush(BLUE)
        p.drawRect(box)
        p.setPen(QPen(LIGHT_GOLD, 0.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(box.adjusted(4, 4, -4, -4))
        for dx, dy in ((7, 7), (51, 7), (7, 51), (51, 51)):   # small flowers in the corners
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(BRIGHT_RED)
            p.drawEllipse(QPointF(box.x() + dx, box.y() + dy), 2.2, 2.2)
        p.setPen(LIGHT_GOLD)
        path_text(p, box.adjusted(0, 2, 0, 0), first.upper(), self.font("title", 48), LIGHT_GOLD)
        x = box.right() + 8
        size = 34
        fm = QFontMetricsF(self.font("title", size))
        room = self.W - x - m - 30 - (self.inset.width() + 16 if getattr(self, "inset", None) is not None else 0)
        while size > 18 and fm.horizontalAdvance(rest) > room:
            size -= 2
            fm = QFontMetricsF(self.font("title", size))
        p.setPen(INK)
        path_text(p, QRectF(x, box.y() + 12, room, 34), fm.elidedText(rest, Qt.TextElideMode.ElideRight, room),
                  self.font("title", size), INK, align="left")
        n = self.n_stops
        sub = f"A pilgrimage in {n} stop{'s' if n != 1 else ''}, from the village to the cathedral"
        chosen = getattr(self.options, "subtitle", "auto")
        if chosen != "auto":   # the user's own subtitle, or none
            sub = chosen
        if not sub:
            return
        p.setPen(FADED)
        p.setFont(self.font("text", 15))
        italic = p.font()
        italic.setItalic(True)
        p.setFont(italic)
        p.drawText(QPointF(x + 2, box.y() + 60), sub)

    # ------------------------------------------------------------ buildings
    def _building(self, p: QPainter, i: int, base: QPointF, flag: float):
        kind = ("castle", "church", "keep", "abbey", "watchtower", "gatehouse")[i % 6]
        getattr(self, "_" + kind)(p, base)
        if flag > 0:   # a pennant raised on arrival
            top = self._top
            p.setPen(QPen(INK, 1.4))
            p.drawLine(QPointF(top.x(), top.y()), QPointF(top.x(), top.y() - 16 * flag))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(BRIGHT_RED)
            y = top.y() - 16 * flag
            p.drawPolygon([QPointF(top.x(), y), QPointF(top.x() + 13 * flag, y + 3.5), QPointF(top.x(), y + 7)])

    def _stone(self, p, path, colour=None):
        p.setPen(QPen(INK, 1.4))
        p.setBrush(colour or STONE)
        p.drawPath(path)

    def _crenellated(self, x, y, w, h, merlons=3):
        path = QPainterPath(QPointF(x, y + h))
        path.lineTo(x, y)
        mw = w / (merlons * 2 - 1)
        for k in range(merlons * 2 - 1):
            xx = x + k * mw
            if k % 2 == 0:
                path.lineTo(xx, y)
                path.lineTo(xx + mw, y)
            else:
                path.lineTo(xx, y + 4)
                path.lineTo(xx + mw, y + 4)
                path.lineTo(xx + mw, y)
        path.lineTo(x + w, y + h)
        path.closeSubpath()
        return path

    def _door(self, p, x, y, w=7, h=10):
        door = QPainterPath(QPointF(x - w / 2, y))
        door.lineTo(x - w / 2, y - h + w / 2)
        door.arcTo(QRectF(x - w / 2, y - h, w, w), 180, -180)
        door.lineTo(x + w / 2, y)
        door.closeSubpath()
        p.setPen(QPen(INK, 1.2))
        p.setBrush(QColor("#4A2E18"))
        p.drawPath(door)

    def _window(self, p, x, y):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(INK)
        p.drawRoundedRect(QRectF(x - 1.5, y - 3, 3, 6), 1.5, 1.5)

    def _castle(self, p, b):
        x, y = b.x(), b.y()
        self._stone(p, self._crenellated(x - 20, y - 26, 40, 26, 4))
        self._stone(p, self._crenellated(x - 30, y - 40, 14, 40, 2))
        self._stone(p, self._crenellated(x + 16, y - 40, 14, 40, 2))
        self._door(p, x, y, 9, 13)
        for wx in (x - 23, x + 23):
            self._window(p, wx, y - 28)
        self._top = QPointF(x - 23, y - 40)

    def _church(self, p, b):
        x, y = b.x(), b.y()
        nave = QPainterPath(QPointF(x - 22, y))
        nave.lineTo(x - 22, y - 18)
        nave.lineTo(x - 4, y - 18)
        nave.lineTo(x - 4, y)
        nave.closeSubpath()
        self._stone(p, nave)
        roof = QPainterPath(QPointF(x - 25, y - 18))
        roof.lineTo(x - 13, y - 28)
        roof.lineTo(x - 1, y - 18)
        roof.closeSubpath()
        self._stone(p, roof, ROOF)
        tower = QPainterPath(QPointF(x - 4, y))
        tower.lineTo(x - 4, y - 32)
        tower.lineTo(x + 12, y - 32)
        tower.lineTo(x + 12, y)
        tower.closeSubpath()
        self._stone(p, tower)
        spire = QPainterPath(QPointF(x - 6, y - 32))
        spire.lineTo(x + 4, y - 52)
        spire.lineTo(x + 14, y - 32)
        spire.closeSubpath()
        self._stone(p, spire, ROOF)
        p.setPen(QPen(GOLD, 1.6))
        p.drawLine(QPointF(x + 4, y - 52), QPointF(x + 4, y - 59))
        p.drawLine(QPointF(x + 1.5, y - 56.5), QPointF(x + 6.5, y - 56.5))
        self._door(p, x + 4, y, 7, 11)
        self._window(p, x - 13, y - 9)
        self._top = QPointF(x - 13, y - 28)

    def _keep(self, p, b):
        x, y = b.x(), b.y()
        self._stone(p, self._crenellated(x - 15, y - 42, 30, 42, 3))
        for wy in (y - 30, y - 18):
            self._window(p, x, wy)
        self._door(p, x, y, 8, 10)
        mound = QPainterPath(QPointF(x - 26, y + 2))
        mound.quadTo(QPointF(x, y - 6), QPointF(x + 26, y + 2))
        p.setPen(QPen(GREEN, 2))
        p.drawPath(mound)
        self._top = QPointF(x, y - 42)

    def _abbey(self, p, b):
        x, y = b.x(), b.y()
        body = QPainterPath(QPointF(x - 26, y))
        body.lineTo(x - 26, y - 20)
        body.lineTo(x + 26, y - 20)
        body.lineTo(x + 26, y)
        body.closeSubpath()
        self._stone(p, body)
        roof = QPainterPath(QPointF(x - 29, y - 20))
        roof.lineTo(x - 22, y - 30)
        roof.lineTo(x + 22, y - 30)
        roof.lineTo(x + 29, y - 20)
        roof.closeSubpath()
        self._stone(p, roof, ROOF)
        bell = QPainterPath(QPointF(x - 5, y - 30))
        bell.lineTo(x - 5, y - 42)
        bell.lineTo(x, y - 47)
        bell.lineTo(x + 5, y - 42)
        bell.lineTo(x + 5, y - 30)
        bell.closeSubpath()
        self._stone(p, bell)
        for wx in (x - 17, x - 8, x + 8, x + 17):
            self._window(p, wx, y - 11)
        self._door(p, x, y, 8, 12)
        self._top = QPointF(x, y - 47)

    def _watchtower(self, p, b):
        x, y = b.x(), b.y()
        shaft = QPainterPath(QPointF(x - 9, y))
        shaft.lineTo(x - 7, y - 40)
        shaft.lineTo(x + 7, y - 40)
        shaft.lineTo(x + 9, y)
        shaft.closeSubpath()
        self._stone(p, shaft)
        cap = QPainterPath(QPointF(x - 12, y - 40))
        cap.lineTo(x, y - 56)
        cap.lineTo(x + 12, y - 40)
        cap.closeSubpath()
        self._stone(p, cap, BLUE.lighter(130))
        self._window(p, x, y - 30)
        self._door(p, x, y, 6, 9)
        self._top = QPointF(x, y - 56)

    def _gatehouse(self, p, b):
        x, y = b.x(), b.y()
        self._stone(p, self._crenellated(x - 28, y - 22, 56, 22, 5))
        self._stone(p, self._crenellated(x - 12, y - 36, 24, 36, 3))
        arch = QPainterPath(QPointF(x - 7, y))
        arch.lineTo(x - 7, y - 12)
        arch.arcTo(QRectF(x - 7, y - 19, 14, 14), 180, -180)
        arch.lineTo(x + 7, y)
        arch.closeSubpath()
        p.setPen(QPen(INK, 1.2))
        p.setBrush(QColor("#2E1C0E"))
        p.drawPath(arch)
        p.setPen(QPen(QColor("#9A8A6A"), 1))
        for gx in (x - 4, x, x + 4):   # the portcullis
            p.drawLine(QPointF(gx, y - 17), QPointF(gx, y - 1))
        self._top = QPointF(x, y - 36)

    def _cottage(self, p, x, y, scale=1.0):
        w, h = 14 * scale, 10 * scale
        wall = QPainterPath(QPointF(x - w / 2, y))
        wall.lineTo(x - w / 2, y - h)
        wall.lineTo(x + w / 2, y - h)
        wall.lineTo(x + w / 2, y)
        wall.closeSubpath()
        self._stone(p, wall, QColor("#E9DCC0"))
        roof = QPainterPath(QPointF(x - w / 2 - 3, y - h))
        roof.lineTo(x, y - h - 9 * scale)
        roof.lineTo(x + w / 2 + 3, y - h)
        roof.closeSubpath()
        self._stone(p, roof, QColor("#B98A3E"))
        self._window(p, x, y - h / 2)

    def _cathedral(self, p, b):
        x, y = b.x(), b.y()
        body = QPainterPath(QPointF(x - 22, y))
        body.lineTo(x - 22, y - 34)
        body.lineTo(x, y - 48)
        body.lineTo(x + 22, y - 34)
        body.lineTo(x + 22, y)
        body.closeSubpath()
        self._stone(p, body)
        for tx in (x - 32, x + 22):
            tower = QPainterPath(QPointF(tx, y))
            tower.lineTo(tx, y - 52)
            tower.lineTo(tx + 10, y - 52)
            tower.lineTo(tx + 10, y)
            tower.closeSubpath()
            self._stone(p, tower)
            spire = QPainterPath(QPointF(tx - 2, y - 52))
            spire.lineTo(tx + 5, y - 70)
            spire.lineTo(tx + 12, y - 52)
            spire.closeSubpath()
            self._stone(p, spire, ROOF)
        p.setPen(QPen(INK, 1.2))   # the rose window
        p.setBrush(BLUE)
        p.drawEllipse(QPointF(x, y - 30), 7, 7)
        p.setPen(QPen(GOLD, 1))
        for a in range(0, 360, 45):
            p.drawLine(QPointF(x, y - 30), QPointF(x + 7 * math.cos(math.radians(a)), y - 30 + 7 * math.sin(math.radians(a))))
        p.setPen(QPen(GOLD, 1.8))
        p.drawLine(QPointF(x, y - 48), QPointF(x, y - 57))
        p.drawLine(QPointF(x - 3, y - 53.5), QPointF(x + 3, y - 53.5))
        self._door(p, x, y, 11, 16)
        self._top = QPointF(x - 27, y - 70)

    # ------------------------------------------------------------ the knight
    def _knight(self, p: QPainter, at: QPointF, phase: float, slope: float, moving: bool, victory=0.0):
        s = self.W / 760 * 0.95
        bounce = -abs(math.sin(phase)) * 3.5 if moving else 0.0
        rock = (math.sin(phase) * 3.5 if moving else 0.0) + max(-10.0, min(10.0, slope))
        p.save()
        p.translate(at.x(), at.y() + bounce * s)
        p.rotate(rock)
        p.scale(s, s)
        if victory > 0:   # the horse rears up on its hind legs
            p.translate(-12, 0)
            p.rotate(-24 * _ease(victory))
            p.translate(12, 0)
        horse, dark = QColor("#F2EBDD"), QColor("#4A3424")
        pen = QPen(dark, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        def leg(x, offset, front):
            swing = math.sin(phase + offset) * (0.55 if moving else 0.0)
            if front and victory > 0:   # front legs tucked up as it rears
                swing = -1.1 * _ease(victory)
            knee = QPointF(x + 7 * math.sin(swing), -15 + 7 * math.cos(swing))
            bend = swing * (0.8 if front else -0.6)
            foot = QPointF(knee.x() + 7 * math.sin(bend), knee.y() + 7 * math.cos(bend))
            p.setPen(QPen(dark, 4.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawPolyline([QPointF(x, -18), knee, foot])
            p.setPen(QPen(horse, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawPolyline([QPointF(x, -18), knee, foot])

        leg(-12, math.pi, False)   # the far legs first
        leg(12, 0, True)
        tail = QPainterPath(QPointF(-19, -26))
        tail.cubicTo(QPointF(-27, -26), QPointF(-28, -18 + 2 * math.sin(phase * 2)), QPointF(-25, -12))
        p.setPen(QPen(dark, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(tail)
        p.setPen(pen)
        p.setBrush(horse)
        p.drawEllipse(QPointF(0, -24), 20, 8.5)   # body
        neck = QPainterPath(QPointF(12, -29))
        neck.lineTo(22, -43)
        neck.lineTo(28, -40)
        neck.lineTo(19, -22)
        neck.closeSubpath()
        p.drawPath(neck)
        p.save()
        p.translate(29, -40)
        p.rotate(32)
        p.drawEllipse(QPointF(0, 0), 7.5, 4)        # head
        p.restore()
        p.setBrush(dark)
        p.drawPolygon([QPointF(22, -44), QPointF(23, -49), QPointF(25.5, -44)])   # ear
        p.drawEllipse(QPointF(29.5, -42), 0.9, 0.9)                                  # eye
        # the caparison: a red cloth with a gold hem
        cloth = QPainterPath(QPointF(-18, -27))
        cloth.lineTo(10, -27)
        cloth.lineTo(13, -16)
        for k in range(6):
            cloth.lineTo(13 - (k + 0.5) * 5.2, -12 if k % 2 else -14)
        cloth.lineTo(-19, -16)
        cloth.closeSubpath()
        p.setPen(QPen(GOLD, 1.4))
        p.setBrush(BRIGHT_RED)
        p.drawPath(cloth)
        p.setBrush(LIGHT_GOLD)
        p.setPen(Qt.PenStyle.NoPen)
        for k in range(3):
            p.drawEllipse(QPointF(-10 + k * 8, -21), 1.3, 1.3)
        # the knight
        p.setPen(pen)
        p.setBrush(QColor("#B9C0C8"))
        p.drawRect(QRectF(-1, -28, 4, 10))                          # leg
        p.setBrush(QColor("#FFFFFF"))
        body = QPainterPath(QPointF(-6, -30))
        body.lineTo(-4, -46)
        body.lineTo(8, -46)
        body.lineTo(9, -30)
        body.closeSubpath()
        p.drawPath(body)                                             # surcoat
        p.setPen(QPen(BRIGHT_RED, 2))
        p.drawLine(QPointF(2, -45), QPointF(2, -32))
        p.drawLine(QPointF(-3.5, -40), QPointF(7.5, -40))
        p.setPen(pen)
        p.setBrush(QColor("#B9C0C8"))
        p.drawRoundedRect(QRectF(-3, -56, 10, 10), 2, 2)             # helm
        p.setPen(QPen(dark, 1.2))
        p.drawLine(QPointF(1, -52), QPointF(7, -52))                  # its eye slit
        plume = QPainterPath(QPointF(2, -56))
        plume.cubicTo(QPointF(-2, -63), QPointF(-9, -62 + math.sin(phase) * 1.5), QPointF(-12, -57))
        p.setPen(QPen(BRIGHT_RED, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(plume)
        p.save()   # the lance, raised in triumph at the end
        p.translate(-8, -34)
        p.rotate(-52 * _ease(victory))
        p.translate(8, 34)
        self._lance(p, phase + victory * 6, dark)
        p.restore()
        self._shield(p)
        p.restore()

    def _lance(self, p, phase, dark):
        """The lance, with a pennant that flutters."""
        tip = QPointF(44, -64)
        p.setPen(QPen(QColor("#6B4423"), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(-8, -34), tip)
        wave = math.sin(phase * 1.7) * 2.5
        flag = QPainterPath(QPointF(36, -60))
        flag.cubicTo(QPointF(30, -64 + wave), QPointF(26, -58 - wave), QPointF(20, -60 + wave))
        flag.lineTo(26, -55)
        flag.lineTo(34.5, -55.5)
        flag.closeSubpath()
        p.setPen(QPen(dark, 0.8))
        p.setBrush(BLUE)
        p.drawPath(flag)

    def _shield(self, p):
        shield = QPainterPath(QPointF(-6, -43))
        shield.lineTo(4, -43)
        shield.lineTo(4, -36)
        shield.quadTo(QPointF(4, -31), QPointF(-1, -29))
        shield.quadTo(QPointF(-6, -31), QPointF(-6, -36))
        shield.closeSubpath()
        p.setPen(QPen(GOLD, 1.3))
        p.setBrush(BLUE)
        p.drawPath(shield)
        p.setPen(QPen(LIGHT_GOLD, 1.4))
        p.drawLine(QPointF(-1, -42), QPointF(-1, -31))
        p.drawLine(QPointF(-5, -38.5), QPointF(3, -38.5))

    # ------------------------------------------------------------ a frame
    def _banner(self, p: QPainter, centre: QPointF, text: str, alpha: float):
        f = self.font("label", 13)
        fm = QFontMetricsF(f)
        text = fm.elidedText(text, Qt.TextElideMode.ElideRight, self.spacing - 18)
        w = fm.horizontalAdvance(text) + 26
        r = QRectF(centre.x() - w / 2, centre.y(), w, 20)
        p.setOpacity(alpha)
        for side in (-1, 1):   # the folded ends
            x = r.left() if side < 0 else r.right()
            tail = QPainterPath(QPointF(x, r.top() + 4))
            tail.lineTo(x + side * 10, r.top() + 4)
            tail.lineTo(x + side * 6, r.top() + 13)
            tail.lineTo(x + side * 10, r.bottom() + 4)
            tail.lineTo(x, r.bottom() + 4)
            tail.closeSubpath()
            p.setPen(QPen(INK, 1))
            p.setBrush(QColor("#D9C392"))
            p.drawPath(tail)
        p.setPen(QPen(INK, 1))
        p.setBrush(QColor("#F3E6C4"))
        p.drawRect(r)
        p.setPen(INK)
        p.setFont(f)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        p.setOpacity(1.0)

    def label(self, i):
        step_plan = self.flow.steps[i]
        name = step_plan.step.alg.displayName() if step_plan.step.alg else step_plan.step.label
        text = f"{roman(step_plan.number)}. {name}" if self.options.numbers else name
        part = self.flow.part_of.get(step_plan.number, 0)
        if self.flow.n_parts > 1 and part > 0 and (i == 0 or self.flow.part_of.get(self.flow.steps[i - 1].number) != part):
            text = f"Part {roman(part + 1)} · " + text   # where the road reaches a new part of the work
        return text

    def draw_scene(self, p: QPainter, pc: float, reached: dict, phase=0.0, moving=False, knight_on=True, victory=0.0):
        """The road, the buildings, the small maps and the knight, for ``pc`` (the knight's
        place along the road) and ``reached`` ({stop: how far its arrival has got, 0–1})."""
        area = self.area
        knight = self.road.pointAtPercent(min(max(pc, 0.0), 1.0))
        offset = 0.0
        if self.total_w > area.width():
            offset = min(0.0, max(area.width() - self.total_w, area.center().x() - knight.x()))
        p.save()
        p.setClipRect(area.adjusted(-6, -self.clip_top, 6, 44))
        p.translate(offset, 0)
        for width, colour in ((15, ROAD_EDGE), (11, ROAD)):
            p.setPen(QPen(colour, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(self.road)
        p.setPen(QPen(QColor(150, 115, 65, 160), 1.4, Qt.PenStyle.DashLine))
        p.drawPath(self.road)
        for name, at, i in self.hamlets:
            stop = self.points[i + 1]
            side = QPainterPath(at)
            side.cubicTo(QPointF(at.x() + self.spacing * 0.35, at.y()), QPointF(stop.x() - 20, stop.y()), stop)
            for width, colour in ((10, ROAD_EDGE), (7, ROAD)):
                p.setPen(QPen(colour, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawPath(side)
            self._cottage(p, at.x() - 8, at.y() - 2, 0.9)
            self._cottage(p, at.x() + 8, at.y() + 1, 0.8)
            self._banner(p, QPointF(at.x(), at.y() + 8), name, 1.0)
        v = self.points[0]
        for dx, dy, sc in ((-14, -6, 1.0), (4, -10, 1.1), (18, -4, 0.9)):
            self._cottage(p, v.x() + dx, v.y() + dy, sc)
        if self.village_name:
            self._banner(p, QPointF(v.x(), v.y() + 12), self.village_name, 1.0)
        for i in range(self.n_stops):
            at = self.points[i + 1]
            got = reached.get(i, 0.0)
            p.setOpacity(0.38 + 0.62 * got)
            self._building(p, i, QPointF(at.x(), at.y() - 7), got)
            p.setOpacity(1.0)
            self._banner(p, QPointF(at.x(), at.y() + 12), self.label(i), 0.45 + 0.55 * got)
            thumb, r = self.thumbs.get(i), self.thumb_at.get(i)
            if thumb is not None and r is not None and got > 0:
                p.setOpacity(_ease(got))
                p.setPen(QPen(GOLD, 3))
                p.setBrush(QColor("#F3E6C4"))
                p.drawRect(r.adjusted(-4, -4, 4, 4))
                p.drawImage(r, thumb)
                p.setPen(QPen(INK, 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(r.adjusted(-4, -4, 4, 4))
                p.setOpacity(1.0)
        end = self.points[-1]
        p.setOpacity(0.38 + 0.62 * reached.get(self.n_stops, 0.0))
        self._cathedral(p, QPointF(end.x(), end.y() - 7))
        p.setOpacity(1.0)
        self._banner(p, QPointF(end.x(), end.y() + 12), self.flow.result_name, 1.0)
        slope = -self.road.angleAtPercent(min(max(pc, 0.0), 1.0))
        if slope < -180:
            slope += 360
        if knight_on:
            self._knight(p, QPointF(knight.x(), knight.y() + 4), phase, slope, moving, victory)
        p.restore()

    def compose(self, pc: float, reached: dict, phase=0.0, moving=False, ending=0.0, knight_on=True,
                victory=0.0) -> QImage:
        image = QImage(self.background)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.draw_scene(p, pc, reached, phase, moving, knight_on, victory)
        if ending > 0:   # in its own band at the bottom, clear of the road
            p.setOpacity(_ease(ending))
            path_text(p, QRectF(0, self.H - self.m - 70, self.W, 36), self.ending_line(), self.font("title", 28), RED)
            p.setOpacity(1.0)
        watermark(p, self.W - self.m - 26, self.H - self.m - 18, self.THEME)   # inside the border
        p.end()
        return image

    def final_page(self) -> QImage:
        """The last page: the result's map, large, in a gold frame, with its name."""
        image = QImage(self.background)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self.final_map is not None:
            img = self.final_map
            iw, ih = size_of(img)
            r = QRectF((self.W - iw) / 2, self.m + 108, iw, ih)
            p.setPen(QPen(GOLD, 4))
            p.setBrush(QColor("#F3E6C4"))
            p.drawRect(r.adjusted(-6, -6, 6, 6))
            p.drawImage(r, img)
            p.setPen(QPen(INK, 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r.adjusted(-9, -9, 9, 9))
            self._banner(p, QPointF(self.W / 2, r.bottom() + 16), self.flow.result_name, 1.0)
        watermark(p, self.W - self.m - 26, self.H - self.m - 18, self.THEME)
        p.end()
        return image

    def ending_line(self) -> str:
        """One of several closing lines, the same each time for the same recipe."""
        import zlib
        return ENDINGS[zlib.crc32((self.title or "").encode("utf-8")) % len(ENDINGS)]

    # ------------------------------------------------------------ the journey
    def keyframes(self):
        everything = {i: 1.0 for i in range(self.n_stops + 1)}
        frames = [self.compose(0.0, {}), self.compose(1.0, everything, ending=1.0)]
        return frames + ([self.final_page()] if self.final_map is not None else [])

    def estimate(self):
        options = self.options
        return (self.n_stops + 1) * (self.n(options.draw * 0.7) + self.n(options.fade) + 1) + self.n(options.fade) + 4

    def frames(self):
        options = self.options
        reached = {}
        first = self.compose(0.0, reached)
        yield first, int(options.pause * 100) + 60
        phase, pc = 0.0, 0.0
        last = first
        for stop in range(self.n_stops + 1):
            target = self.stop_pc[stop + 1]
            steps = self.n(options.draw * 0.7)
            for k in range(1, steps + 1):
                phase += 0.95
                here = pc + (target - pc) * _ease(k / steps)
                last = self.compose(here, reached, phase, moving=True)
                yield last, self.delay
            pc = target
            steps = self.n(options.fade)
            for k in range(1, steps + 1):
                reached[stop] = k / steps
                last = self.compose(pc, reached, 0.0)
                yield last, self.delay
            if stop < self.n_stops:
                yield last, int(options.pause * 100)
        steps = self.n(0.9)   # victory: the horse rears and the sword goes up
        for k in range(1, steps + 1):
            last = self.compose(pc, reached, 0.0, victory=k / steps)
            yield last, self.delay
        steps = self.n(options.fade)
        for k in range(1, steps + 1):
            last = self.compose(pc, reached, 0.0, ending=k / steps, victory=1.0)
            yield last, self.delay
        yield last, int(max(options.pause, 1.0) * 100)
        if self.final_map is not None:   # and the result itself, to finish on
            page = self.final_page()
            steps = self.n(options.fade)
            for k in range(1, steps + 1):
                yield _blend(last, page, k / steps), self.delay
            last = page
        yield from self.end_frames(last, first)


# ---------------------------------------------------------------- map pilgrimage

class PilgrimStrip(MetroStrip):
    """The pilgrimage road as the strip under a map: the knight rides from building to building
    as the map shows each step."""
    victory = True   # the map story animates the horse rearing at the end

    def __init__(self, scene, height):
        self.scene, self.height, self.n = scene, height, scene.n_stops

    def draw(self, p, top, pos, current, thumbs=None, shown=(), victory=None):
        sc = self.scene
        x = min(max(pos + 1, 0.0), sc.n_stops + 1.0)
        lo = int(math.floor(x))
        hi = min(lo + 1, sc.n_stops + 1)
        frac = x - lo
        pc = sc.stop_pc[lo] + (sc.stop_pc[hi] - sc.stop_pc[lo]) * frac
        reached = {i: 1.0 for i in range(sc.n_stops + 1) if pos >= i - 0.001}
        if victory is None:
            victory = 1.0 if pos >= sc.n_stops - 0.001 else 0.0
        sc.draw_scene(p, pc, reached, phase=x * 9.0, moving=0.001 < frac < 0.999, victory=victory)


class MapPilgrimage(MapAnimation):
    """The map story on old paper: the map in a gold frame, the knight's road underneath."""

    def prepare(self):
        super().prepare()
        fonts.load()
        options = self.options
        from . import images
        self.t = dict(images.THEMES["parchment"])
        m, W = 14, self.W
        self.mw = W - 2 * (m + 18)
        header, strip_h = m + 104, 158
        self.H = int(round(W * 9 / 16))
        self.map_h = max(120, self.H - (header + 18 + strip_h + 26 + m))   # 16:9 at the layout width
        self.H = header + self.map_h + 18 + strip_h + 26 + m
        self.map_rect = QRectF(m + 18, header, self.mw, self.map_h)
        self.build_makers(self.mw, self.map_h)
        self.map_bg = QColor("#F6ECD2")
        scene = Pilgrimage(self.controller, options)
        scene.ratio = self.ratio   # its parchment is this animation's background, at the same size
        scene.W, scene.H, scene.m = W, self.H, m
        scene.flow, scene.n_stops, scene.title, scene.thumbs = self.flow, len(self.flow.steps), self.title_text(), {}
        scene.clip_top = scene.reach_up = 100
        road_y = header + self.map_h + 18 + 98
        scene.layout(QRectF(m + 26, road_y - 16, W - 2 * (m + 26), 32), amp=0.4, mid=0.5, hamlets=False)
        self.scene = scene
        self.background = scene._parchment()
        self.strip = PilgrimStrip(scene, strip_h)

    def map_area(self) -> QRectF:
        return self.map_rect.adjusted(-7, -7, 7, 7)

    def compose(self, layers, caption=None, state=None, draw_more=None, legend=None):
        W, H, strip = self.W, self.H, self.strip
        self.W, self.H, self.strip, self._no_mark = self.mw, self.map_h, None, True
        try:
            inner = MapAnimation.compose(self, layers, caption, None, draw_more, legend)
        finally:
            self.W, self.H, self.strip, self._no_mark = W, H, strip, False
        image = QImage(self.background)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        r = self.map_rect
        p.drawImage(r.topLeft(), inner)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(GOLD, 3))
        p.drawRect(r.adjusted(-3, -3, 3, 3))
        p.setPen(QPen(INK, 1))
        p.drawRect(r.adjusted(-6, -6, 6, 6))
        if state is not None:
            self.strip.draw(p, 0, state["pos"], state.get("station"), victory=state.get("victory"))
        watermark(p, self.W - 40, self.H - 32, Pilgrimage.THEME)   # inside the border
        p.end()
        return image
