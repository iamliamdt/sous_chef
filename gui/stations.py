"""Animations with a station for each tool along a scene the camera follows: the Kitchen (pots
on a stove, ingredients dropped in, results served on plates) and the Assembly line (machines
along a conveyor belt, the data riding from one to the next)."""

from __future__ import annotations

import math
import random

from qgis.PyQt.QtCore import QPointF, QRectF, QSize, Qt
from qgis.PyQt.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen

from . import fonts, snapshots
from .animation import Animation, Flow, _ease, watermark

INK = QColor("#2B2622")


class Stations(Animation):
    FIXED_LAYOUT = True
    SPACING = 230
    ENDINGS = ["Done!"]

    def font(self, size, bold=False):
        family = fonts.family_for(self.options.kind, self.options.fonts, self.options.font)
        f = QFont(family) if family else QFont()
        f.setPixelSize(int(size))
        f.setBold(bold)
        return f

    def prepare(self):
        fonts.load()
        options = self.options
        self.flow = Flow(self.controller, options.detail)
        self.n_steps = len(self.flow.steps)
        found, used, self._hold = snapshots.result_layers(self.controller)
        background = snapshots.background_layers() if options.basemaps else []
        size = QSize(150, 100)
        self.thumbs = {}
        for i, step_plan in enumerate(self.flow.steps):
            layer = found.get(step_plan.number)
            try:
                image = self.sharp(snapshots.render(layer, used.get(step_plan.number, []), background,
                                                    self.px(size))) if layer else None
            except Exception:
                image = None
            self.thumbs[i] = image or self.sharp(snapshots.placeholder(self.px(size), "no map"))
        self.input_thumbs = {}
        for uid, layer in snapshots.starting_layers(self.controller).items():
            try:
                self.input_thumbs[uid] = self.sharp(snapshots.render(layer, (), background, self.px(size)))
            except Exception:
                self.input_thumbs[uid] = None
        big = QSize(320, 210)   # the maps shown large: on plates, on crates
        self.big = {}
        for i, step_plan in enumerate(self.flow.steps):
            layer = found.get(step_plan.number)
            try:
                image = self.sharp(snapshots.render(layer, used.get(step_plan.number, []), background,
                                                    self.px(big))) if layer else None
            except Exception:
                image = None
            self.big[i] = image or self.sharp(snapshots.placeholder(self.px(big), "no map"))
        try:   # the finished result, for the last crate: the final outputs (or the one chosen)
            choice = getattr(options, "final_output", 0)
            self.final_big = self.sharp(snapshots.final_map(self.controller, found, self.px(big), background, choice=choice))
        except Exception:
            self.final_big = None
        if snapshots.is_general(options):   # General detail: the kinds of data, no real maps
            self.thumbs = {i: self.sharp(snapshots.kind_card(snapshots.step_kind(step_plan), self.px(size))) for i,
                           step_plan in enumerate(self.flow.steps)}
            self.big = {i: self.sharp(snapshots.kind_card(snapshots.step_kind(step_plan), self.px(big))) for i,
                        step_plan in enumerate(self.flow.steps)}
            self.input_thumbs = {uid: self.sharp(snapshots.kind_card(input_plan.layer_kind, self.px(size))) for uid,
                                 input_plan in self.controller.plan.inputs.items()}
            self.final_big = None
        self.spacing = max(self.SPACING, (self.W - 260) / max(self.n_steps, 1))
        self.x0 = 190.0
        self.title = self.title_text()
        self.ending = random.choice(self.ENDINGS)   # a different closing line each time  # nosec B311
        self.background = self.scenery()

    # -- the scene
    def x_of(self, i: float) -> float:
        return self.x0 + i * self.spacing

    def world_width(self):
        return self.x_of(self.n_steps - 1) + 260

    def offset_for(self, focus_x: float) -> float:
        if self.world_width() <= self.W:
            return (self.W - self.world_width()) / 2 + 40
        return min(0.0, max(self.W - self.world_width(), self.W / 2 - focus_x))

    def label(self, i):
        return self.flow.tool(i, self.options.numbers)

    def uses(self, i):
        """The names of what step ``i`` uses: starting layers and earlier results."""
        step_plan = self.flow.steps[i]
        index = {s.number: k for k, s in enumerate(self.flow.steps)}
        out = []
        for param in step_plan.params:
            if param.kind not in ("input", "link"):
                continue
            for src in param.sources:
                if src[0] == "link":
                    k = index.get(src[1])
                    if k is not None:
                        out.append(("result", k, self.flow.result_of(k) or "result"))
                else:
                    out.append(("input", src[1], self.flow.input_names.get(src[1], "layer")))
        return out

    def plate(self, p, centre: QPointF, image: QImage, scale=1.0):
        """A map served on a plate (kitchen) or shown on a crate label."""
        r = 46 * scale
        p.setPen(QPen(QColor("#B9B2A6"), 1.5))
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(centre, r * 1.25, r * 1.25 * 0.62)
        clip = QPainterPath()
        clip.addEllipse(centre, r, r * 0.62)
        p.save()
        p.setClipPath(clip)
        p.drawImage(QRectF(centre.x() - r, centre.y() - r * 0.62, 2 * r, 1.24 * r), image)
        p.restore()
        p.setPen(QPen(QColor("#D8D2C6"), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(centre, r, r * 0.62)

    def tag(self, p, centre: QPointF, text: str, alpha=1.0, colour="#FFFDF5"):
        f = self.font(13, True)
        fm = QFontMetricsF(f)
        text = fm.elidedText(text, Qt.TextElideMode.ElideRight, self.spacing - 24)
        w = fm.horizontalAdvance(text) + 22
        r = QRectF(centre.x() - w / 2, centre.y(), w, 26)
        p.setOpacity(alpha)
        p.setPen(QPen(INK, 1.2))
        p.setBrush(QColor(colour))
        p.drawRoundedRect(r, 6, 6)
        p.setFont(f)
        p.setPen(INK)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
        p.setOpacity(1.0)

    def heading(self, p, sub=""):
        f = self.font(24, True)
        p.setFont(f)
        p.setPen(INK)
        r = QRectF(22, 12, self.W - 44, 34)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 215))
        fm = QFontMetricsF(f)
        title = fm.elidedText(self.title, Qt.TextElideMode.ElideRight, self.W - 80)
        p.drawRoundedRect(QRectF(16, 10, fm.horizontalAdvance(title) + 28, 40 + (20 if sub else 0)), 10, 10)
        p.setPen(INK)
        p.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        if sub:
            p.setFont(self.font(14))
            p.drawText(QRectF(22, 44, self.W - 44, 20), Qt.AlignmentFlag.AlignLeft, sub)

    def ending_line(self):
        return self.ending

    def finish_text(self, p, alpha):
        p.setOpacity(_ease(alpha))
        f = self.font(34, True)
        p.setFont(f)
        r = QRectF(0, self.H * 0.06 + 50, self.W, 50)
        p.setPen(QColor(0, 0, 0, 90))
        p.drawText(r.translated(2, 3), Qt.AlignmentFlag.AlignCenter, self.ending_line())
        p.setPen(QColor("#C0392B"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.ending_line())
        p.setOpacity(1.0)

    def frame(self, focus_x, draw):
        """A frame: the scenery and ``draw(p)`` in world coordinates, the camera on ``focus_x``."""
        image = QImage(self.background)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.save()
        p.translate(self.offset_for(focus_x), 0)
        draw(p)
        p.restore()
        return image, p

    def finish(self, image, p, ending=0.0, sub=""):
        self.heading(p, sub)
        if ending > 0:
            self.finish_text(p, ending)
        watermark(p, self.W, self.H, dict(muted="#5A524A"))
        p.end()
        return image


# ---------------------------------------------------------------- kitchen

POTS = ("pot", "pan", "oven", "bowl", "wok")


class Kitchen(Stations):
    SPACING = 330
    ENDINGS = ["Bon appétit!", "Dinner is served!", "Enjoy!", "Order up!", "Chef's kiss!", "Ready to serve!",
               "Tuck in!", "Served hot!", "Compliments to the chef!", "Come and get it!"]

    def scenery(self) -> QImage:
        W, H = self.W, self.H
        image = self.frame_image()
        image.fill(QColor("#F4F1EA"))
        p = QPainter(image)
        tile = 36
        p.setPen(QPen(QColor("#DCD6CB"), 1))
        for x in range(0, W + tile, tile):   # wall tiles
            p.drawLine(QPointF(x, 0), QPointF(x, H * 0.62))
        for y in range(0, int(H * 0.62) + tile, tile):
            p.drawLine(QPointF(0, y), QPointF(W, y))
        self.shelf_y = H * 0.24
        p.fillRect(QRectF(0, self.shelf_y, W, 10), QColor("#8B6B4A"))   # the shelf
        self.counter_y = H * 0.62
        p.fillRect(QRectF(0, self.counter_y, W, H - self.counter_y), QColor("#B88B5E"))   # the counter
        p.fillRect(QRectF(0, self.counter_y, W, 14), QColor("#5E5B57"))                  # the stove top's edge
        self.plate_scale = max(1.0, min(2.1, (H - self.counter_y - 66) / (46 * 0.62 * 1.25 * 2)))
        rnd = random.Random(3)  # nosec B311: drawing variety, not security
        p.setPen(QPen(QColor(120, 80, 40, 60), 1))
        for _ in range(60):   # wood grain
            y = rnd.uniform(self.counter_y + 20, H)
            p.drawLine(QPointF(0, y), QPointF(W, y + rnd.uniform(-4, 4)))
        p.end()
        return image

    def pot(self, p, i, x, lid=0.0, bubbling=0.0, done=False):
        y = self.counter_y
        kind = POTS[i % len(POTS)]
        p.setPen(QPen(INK, 2))
        if bubbling > 0 and kind != "oven":   # the flame underneath (an oven heats inside)
            for k in range(5):
                fx = x - 30 + k * 15
                h = 10 + 6 * math.sin(bubbling * 9 + k)
                path = QPainterPath(QPointF(fx - 5, y + 2))
                path.quadTo(QPointF(fx, y - h - 4), QPointF(fx + 5, y + 2))
                p.setBrush(QColor("#F39C12"))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawPath(path)
            p.setPen(QPen(INK, 2))
        colour = QColor("#7F8C8D") if kind != "oven" else QColor("#C0392B")
        if kind == "pot":
            body = QRectF(x - 46, y - 74, 92, 70)
            p.setBrush(colour)
            p.drawRoundedRect(body, 8, 8)
            p.drawLine(QPointF(x - 58, y - 56), QPointF(x - 46, y - 56))
            p.drawLine(QPointF(x + 46, y - 56), QPointF(x + 58, y - 56))
            p.save()
            p.translate(x, y - 76 - 4 * abs(math.sin(lid * 14)) * (1 if lid else 0))
            p.rotate(4 * math.sin(lid * 11))
            p.setBrush(colour.lighter(115))
            p.drawRoundedRect(QRectF(-50, -8, 100, 10), 4, 4)
            p.drawEllipse(QPointF(0, -12), 6, 5)
            p.restore()
        elif kind == "pan":   # a frying pan seen from a little above: rim, dish, long handle
            handle = QPainterPath(QPointF(x + 50, y - 26))
            handle.lineTo(x + 122, y - 40)
            handle.quadTo(QPointF(x + 130, y - 34), QPointF(x + 123, y - 28))
            handle.lineTo(x + 52, y - 16)
            handle.closeSubpath()
            p.setBrush(QColor("#2E2E2E"))
            p.drawPath(handle)
            p.setBrush(QColor("#F4F1EA"))
            p.drawEllipse(QPointF(x + 114, y - 35), 3.2, 3.2)          # the hanging hole
            side = QPainterPath(QPointF(x - 60, y - 26))
            side.quadTo(QPointF(x - 56, y - 2), QPointF(x - 40, y - 2))
            side.lineTo(x + 40, y - 2)
            side.quadTo(QPointF(x + 56, y - 2), QPointF(x + 60, y - 26))
            side.closeSubpath()
            p.setBrush(QColor("#3B3B3B"))
            p.drawPath(side)
            p.setBrush(QColor("#4A4A4A"))
            p.drawEllipse(QPointF(x, y - 26), 60, 13)                 # the rim
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#262626"))
            p.drawEllipse(QPointF(x, y - 25), 52, 9.5)                # the dish
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawEllipse(QPointF(x - 18, y - 28), 16, 3)             # a shine
            if bubbling > 0:   # sizzle
                p.setPen(QPen(QColor(255, 220, 150, 200), 1.6))
                for k in range(4):
                    sx = x - 30 + k * 20
                    p.drawLine(QPointF(sx, y - 38 - 4 * math.sin(bubbling * 30 + k)), QPointF(sx + 4, y - 46))
            p.setPen(QPen(INK, 2))
        elif kind == "oven":
            p.setBrush(QColor("#ECECEC"))
            p.drawRoundedRect(QRectF(x - 56, y - 120, 112, 116), 8, 8)
            if bubbling > 0:   # lit inside, the light pulsing
                pulse = 0.75 + 0.25 * math.sin(bubbling * 18)
                glow = QColor(int(245 * pulse), int(176 * pulse), int(65 * pulse))
            else:
                glow = QColor("#3D3D3D")
            p.setBrush(glow)
            p.drawRoundedRect(QRectF(x - 42, y - 86, 84, 66), 6, 6)
            if bubbling > 0:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 240, 200, 90))
                p.drawEllipse(QPointF(x, y - 53), 30, 18)
                p.setPen(QPen(INK, 2))
            p.drawLine(QPointF(x - 30, y - 92), QPointF(x + 30, y - 92))   # the door handle
            p.setBrush(QColor("#555555"))
            for k in range(3):
                p.drawEllipse(QPointF(x - 30 + k * 30, y - 106), 5, 5)
            p.setBrush(QColor("#2ECC71") if bubbling > 0 else QColor("#7F8C8D"))   # the power light
            p.drawEllipse(QPointF(x + 44, y - 106), 3.5, 3.5)
        elif kind == "bowl":
            path = QPainterPath(QPointF(x - 56, y - 50))
            path.quadTo(QPointF(x, y + 26), QPointF(x + 56, y - 50))
            path.closeSubpath()
            p.setBrush(QColor("#E59866"))
            p.drawPath(path)
        else:   # wok
            path = QPainterPath(QPointF(x - 62, y - 44))
            path.quadTo(QPointF(x, y + 12), QPointF(x + 62, y - 44))
            path.closeSubpath()
            p.setBrush(QColor("#34495E"))
            p.drawPath(path)
        if bubbling > 0:   # steam (from the top of the oven door, or out of the pot)
            top = y - 124 if kind == "oven" else y - 90
            for k in range(4):
                phase = (bubbling * 2.2 + k * 0.25) % 1.0
                sx = x - 22 + k * 15 + 6 * math.sin(phase * 6 + k)
                sy = top - phase * 70
                c = QColor(255, 255, 255, int(170 * (1 - phase)))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(c)
                p.drawEllipse(QPointF(sx, sy), 9 + 8 * phase, 9 + 8 * phase)
        if done:
            p.setPen(QPen(QColor("#27AE60"), 3))
            p.drawLine(QPointF(x + 30, y - 100), QPointF(x + 38, y - 92))
            p.drawLine(QPointF(x + 38, y - 92), QPointF(x + 54, y - 112))

    def jar(self, p, at: QPointF, text: str):
        f = self.font(11, True)
        w = min(QFontMetricsF(f).horizontalAdvance(text) + 18, 120)
        r = QRectF(at.x() - w / 2, at.y() - 34, w, 34)
        p.setPen(QPen(INK, 1.4))
        p.setBrush(QColor(225, 240, 250, 230))
        p.drawRoundedRect(r, 6, 6)
        p.setBrush(QColor("#C0392B"))
        p.drawRect(QRectF(r.x() + 4, r.y() - 6, r.width() - 8, 7))
        p.setFont(f)
        p.setPen(INK)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, QFontMetricsF(f).elidedText(text, Qt.TextElideMode.ElideRight, w - 8))

    def scene(self, p, upto, current=None, cooking=0.0, flying=(), plated=()):
        for i in range(self.n_steps):
            x = self.x_of(i)
            self.pot(p, i, x, lid=cooking if i == current else 0.0, bubbling=cooking if i == current else 0.0,
                     done=i < upto)
            self.tag(p, QPointF(x, self.counter_y + 20), self.label(i), 1.0 if i <= upto else 0.5)
            if i in plated:   # served large, on the counter in front
                a = plated[i]
                p.setOpacity(_ease(a))
                scale = self.plate_scale
                centre_y = self.counter_y + 52 + 46 * scale * 0.62 * 1.25
                self.plate(p, QPointF(x + 30 * (1 - _ease(a)), centre_y), self.big[i], scale)
                p.setOpacity(1.0)
        for at, text in flying:
            self.jar(p, at, text)

    def estimate(self):
        options = self.options
        return self.n_steps * (self.n(0.5) + self.n(options.draw) + self.n(options.fade) + 1) + self.n(options.fade) + 8

    def keyframes(self):
        plated = {i: 1.0 for i in range(self.n_steps)}
        image, p = self.frame(self.x_of(0), lambda p: self.scene(p, self.n_steps, plated=plated))
        return [self.finish(image, p, 1.0)]

    def frames(self):
        options = self.options
        plated, focus = {}, self.x_of(0) - 120
        first = None
        for i in range(self.n_steps):
            x = self.x_of(i)
            steps = self.n(0.5)   # the camera moves to the next pot
            for k in range(1, steps + 1):
                f = focus + (x - focus) * _ease(k / steps)
                image, p = self.frame(f, lambda p: self.scene(p, i, plated=plated))
                img = self.finish(image, p, sub=self.label(i))
                if first is None:
                    first = img
                yield img, self.delay
            focus = x
            items = self.uses(i)[:3] or [("input", "", "ingredients")]
            steps = self.n(options.draw * 0.4)   # the ingredients drop in
            for k in range(1, steps + 1):
                s = k / steps
                flying = []
                for j, (_kind, _ref, name) in enumerate(items):
                    sx, sy = x - 110 + j * 40, self.shelf_y - 2
                    ex, ey = x, self.counter_y - 70
                    fx = sx + (ex - sx) * s
                    fy = sy + (ey - sy) * s - 70 * math.sin(math.pi * s)
                    if s < 0.95:
                        flying.append((QPointF(fx, fy), name))
                image, p = self.frame(focus, lambda p, fl=flying: self.scene(p, i, i, flying=fl, plated=plated))
                yield self.finish(image, p, sub=self.label(i)), self.delay
            steps = self.n(options.draw * 0.6)   # it cooks
            for k in range(1, steps + 1):
                image, p = self.frame(focus, lambda p, c=k / steps: self.scene(p, i, i, cooking=c + 0.01, plated=plated))
                yield self.finish(image, p, sub=self.label(i)), self.delay
            steps = self.n(options.fade)   # and it's served
            for k in range(1, steps + 1):
                plated[i] = k / steps
                image, p = self.frame(focus, lambda p: self.scene(p, i + 1, plated=plated))
                last = self.finish(image, p, sub=self.label(i))
                yield last, self.delay
            yield last, int(options.pause * 100)
        steps = self.n(options.fade)
        for k in range(1, steps + 1):
            image, p = self.frame(focus, lambda p: self.scene(p, self.n_steps, plated=plated))
            last = self.finish(image, p, ending=k / steps, sub=self.flow.result_name)
            yield last, self.delay
        yield from self.end_frames(last, first)


# ---------------------------------------------------------------- assembly line

class AssemblyLine(Stations):
    SPACING = 400
    ENDINGS = ["Shipped!", "Quality checked!", "Production complete!", "Ready for dispatch!", "All boxed up!",
               "Order fulfilled!"]

    def scenery(self) -> QImage:
        W, H = self.W, self.H
        image = self.frame_image()
        image.fill(QColor("#D5D8DC"))
        p = QPainter(image)
        p.setPen(QPen(QColor("#BFC4C9"), 2))
        for x in range(0, W, 120):   # wall panels
            p.drawLine(QPointF(x, 0), QPointF(x, H))
        self.belt_y = H * 0.70
        self.crate_w = min(196.0, self.spacing * 0.5, H * 0.27)
        self.crate_h = self.crate_w * 0.64
        p.fillRect(QRectF(0, self.belt_y + 18, W, H - self.belt_y), QColor("#9AA3AB"))   # the floor
        p.end()
        return image

    def belt(self, p, shift):
        y = self.belt_y
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#2C3E50"))
        p.drawRoundedRect(QRectF(-200, y, self.world_width() + 400, 18), 9, 9)
        p.setPen(QPen(QColor("#5D6D7E"), 2))
        x = -200 + (shift % 30)
        while x < self.world_width() + 200:
            p.drawLine(QPointF(x, y + 3), QPointF(x + 10, y + 15))
            x += 30

    def feeder_path(self, i):
        """A side conveyor coming in at an angle from the upper left, to the machine's mouth."""
        x, y = self.x_of(i), self.belt_y
        return QPointF(x - self.spacing * 0.62, y - self.crate_h * 2.6), QPointF(x - self.crate_w * 0.55, y - 8)

    def feeder(self, p, i, shift):
        a, b = self.feeder_path(i)
        p.setPen(QPen(QColor("#2C3E50"), 16, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(a, b)
        p.setPen(QPen(QColor("#5D6D7E"), 2))
        length = math.hypot(b.x() - a.x(), b.y() - a.y())
        ux, uy = (b.x() - a.x()) / length, (b.y() - a.y()) / length
        d = shift % 24
        while d < length:
            c = QPointF(a.x() + ux * d, a.y() + uy * d)
            p.drawLine(QPointF(c.x() - uy * 6, c.y() + ux * 6), QPointF(c.x() + uy * 6, c.y() - ux * 6))
            d += 24
        p.setPen(QPen(QColor("#7F8C8D"), 3))   # its legs
        p.drawLine(a, QPointF(a.x(), self.belt_y + 18))

    def machine(self, p, i, x, working=0.0, done=False):
        y = self.belt_y
        shake = 2 * math.sin(working * 40) if 0 < working < 1 else 0
        w, h = self.crate_w * 1.35, self.crate_h * 2.3
        body = QRectF(x - w / 2 + shake, y - h - 6, w, h)
        p.setPen(QPen(INK, 2))
        p.setBrush(QColor("#F4D03F") if not done else QColor("#F7DC6F"))
        p.drawRoundedRect(body, 12, 12)
        p.setBrush(QColor("#34495E"))
        p.drawRect(QRectF(x - self.crate_w * 0.58 + shake, y - self.crate_h - 16, self.crate_w * 1.16, self.crate_h + 10))
        lamp = QColor("#E74C3C") if 0 < working < 1 and int(working * 10) % 2 == 0 else (
            QColor("#2ECC71") if done else QColor("#7F8C8D"))
        p.setBrush(lamp)
        p.drawEllipse(QPointF(body.right() - 20, body.top() + 20), 8, 8)
        p.save()   # a turning gear
        p.translate(body.left() + 30, body.top() + 32)
        p.rotate(working * 360)
        p.setBrush(QColor("#95A5A6"))
        for k in range(8):
            p.rotate(45)
            p.drawRect(QRectF(-3.5, -20, 7, 9))
        p.drawEllipse(QPointF(0, 0), 14, 14)
        p.setBrush(QColor("#F4D03F"))
        p.drawEllipse(QPointF(0, 0), 5, 5)
        p.restore()
        self.tag(p, QPointF(x, body.top() - 32), self.label(i), 1.0, "#FDFEFE")

    def crate(self, p, at: QPointF, image: QImage | None, text=""):
        w, h = self.crate_w, self.crate_h
        r = QRectF(at.x() - w / 2, at.y() - h, w, h)
        p.setPen(QPen(QColor("#6E4B2A"), 2.5))
        p.setBrush(QColor("#C68E52"))
        p.drawRect(r)
        inner = r.adjusted(9, 9, -9, -9)
        if image is not None:
            p.drawImage(inner, image)
        p.setPen(QPen(QColor("#6E4B2A"), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(inner)
        if text:
            self.tag(p, QPointF(at.x(), at.y() + 26), text, 1.0)

    def spotlight(self, p, at: QPointF, a):
        """Light falling on a finished crate."""
        w = self.crate_w
        cone = QPainterPath(QPointF(at.x() - 18, 0))
        cone.lineTo(at.x() + 18, 0)
        cone.lineTo(at.x() + w * 0.75, at.y() + 4)
        cone.lineTo(at.x() - w * 0.75, at.y() + 4)
        cone.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 244, 190, int(90 * a)))
        p.drawPath(cone)

    def scene(self, p, upto, shift=0.0, working=None, w=0.0, crate=None, joining=(), spot=0.0):
        self.belt(p, shift)
        for i in range(self.n_steps):
            if self.joins(i):
                self.feeder(p, i, shift if i == working else 0.0)
        for at, image, text in joining:   # new layers arriving on their side conveyors
            self.crate(p, at, image, text)
        for i in range(self.n_steps):
            self.machine(p, i, self.x_of(i), w if i == working else 0.0, i < upto)
        if crate is not None:
            at, image, text = crate
            if spot > 0:
                self.spotlight(p, at, spot)
            self.crate(p, at, image, text)

    def joins(self, i):
        """The starting layers that join the line at machine ``i`` (not the one it starts with)."""
        first = self.flow.inputs[0] if self.flow.inputs else None
        return [(ref, name) for kind, ref, name in self.uses(i) if kind == "input" and ref != first]

    def estimate(self):
        options = self.options
        return self.n_steps * (self.n(options.draw) + self.n(options.fade) + 1) + self.n(options.fade) + 8

    def keyframes(self):
        n = self.n_steps
        at = QPointF(self.x_of(n - 1) + self.spacing * 0.45, self.belt_y)
        cargo = self.final_big or self.big.get(n - 1)
        image, p = self.frame(at.x(), lambda p: self.scene(p, n, crate=(at, cargo, self.flow.result_name)))
        return [self.finish(image, p, 1.0)]

    def frames(self):
        options = self.options
        first_input = self.flow.inputs[0] if self.flow.inputs else None
        cargo = self.input_thumbs.get(first_input)
        x = self.x_of(0) - self.spacing * 0.55
        shift = 0.0
        first = last = None
        for i in range(self.n_steps):
            entry, exit_ = self.x_of(i) - 10, self.x_of(i) + self.spacing * 0.45
            joining = self.joins(i)
            steps = self.n(options.draw * 0.45)   # to the machine; a joining layer glides down its own conveyor
            start = x
            for k in range(1, steps + 1):
                s = _ease(k / steps)
                x = start + (entry - start) * s
                shift += 6
                arriving = []
                for ref, name in joining[:1]:
                    a, b = self.feeder_path(i)
                    at = QPointF(a.x() + (b.x() - a.x()) * s, a.y() + (b.y() - a.y()) * s + self.crate_h * 0.5)
                    arriving.append((at, self.input_thumbs.get(ref), name))
                image, p = self.frame(x, lambda p, cx=x, sh=shift, ar=arriving: self.scene(
                    p, i, sh, crate=(QPointF(cx, self.belt_y), cargo, ""), joining=ar))
                last = self.finish(image, p, sub=self.label(i))
                if first is None:
                    first = last
                yield last, self.delay
            steps = self.n(options.draw * 0.35)   # it's worked on
            for k in range(1, steps + 1):
                image, p = self.frame(x, lambda p, s=k / steps: self.scene(p, i, shift, i, s))
                yield self.finish(image, p, sub=self.label(i)), self.delay
            cargo = self.big.get(i)
            steps = self.n(options.fade)   # and comes out changed, into the light
            for k in range(1, steps + 1):
                x = entry + (exit_ - entry) * _ease(k / steps)
                shift += 6
                image, p = self.frame(x, lambda p, cx=x, sh=shift, s=k / steps: self.scene(
                    p, i + 1, sh, crate=(QPointF(cx, self.belt_y), cargo, self.flow.result_of(i)), spot=s))
                last = self.finish(image, p, sub=self.label(i))
                yield last, self.delay
            yield last, int(max(options.pause, 1.0) * 100)
        cargo = self.final_big or cargo   # the finished result (all the final outputs, or the one chosen)
        steps = self.n(options.fade)
        for k in range(1, steps + 1):
            image, p = self.frame(x, lambda p: self.scene(p, self.n_steps, shift, crate=(
                QPointF(x, self.belt_y), cargo, self.flow.result_name), spot=1.0))
            last = self.finish(image, p, ending=k / steps)
            yield last, self.delay
        yield from self.end_frames(last, first)
