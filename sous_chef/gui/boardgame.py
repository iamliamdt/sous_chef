"""The Board game animation: a token hops along a winding board, a square for each tool with
plain squares between. A die shows how far the next tool is; landing on it shows the step's
map on a card."""

from __future__ import annotations

import math
import random
import zlib

from qgis.PyQt.QtCore import QPointF, QRectF, QSize, Qt
from qgis.PyQt.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen

from . import fonts, images, snapshots
from .animation import Animation, Flow, _ease, _alg_icon_image, size_of, watermark

INK = QColor("#2B2622")
ENDINGS = ["You win!", "You win! The recipe is complete", "Game over: the recipe is cooked!",
           "Victory! Every step played", "Winner, winner, map for dinner!", "Champion of the map!"]


class BoardGame(Animation):
    FIXED_LAYOUT = True

    def font(self, size, bold=False):
        family = fonts.family_for("board_game", self.options.fonts, self.options.font)
        f = QFont(family) if family else QFont()
        f.setPixelSize(int(size))
        f.setBold(bold)
        return f

    def prepare(self):
        fonts.load()
        options = self.options
        self.flow = Flow(self.controller, options.detail)
        n = len(self.flow.steps)
        self.title = self.title_text()
        rnd = random.Random(zlib.crc32(self.title.encode("utf-8")))  # nosec B311: drawing variety, not security
        # the path: START, then two to four plain squares before each tool, then FINISH
        self.tool_at, pos = {}, 0
        for i in range(n):
            pos += rnd.randint(1, 5)
            self.tool_at[i] = pos
        self.finish_at = pos + 2
        total = self.finish_at + 1
        self.cols = 8 if total > 16 else 6
        rows = math.ceil(total / self.cols)
        top, bottom = 84, 46   # room at the bottom for the "Made with Sous Chef" mark
        self.sq = min((self.W - 90) / self.cols, (self.H - top - bottom) / rows) * 0.92
        board_w = self.cols * self.sq
        self.ox = (self.W - board_w) / 2
        self.oy = top + ((self.H - top - bottom) - rows * self.sq) / 2
        self.total = total
        # each square a little off the grid and turned a touch, so they don't line up like a chessboard
        self.nudge = [(rnd.uniform(-0.09, 0.09) * self.sq, rnd.uniform(-0.09, 0.09) * self.sq, rnd.uniform(-5, 5))
                      for _k in range(total)]
        found, used, self._hold = snapshots.result_layers(self.controller)
        background = snapshots.background_layers() if options.basemaps else []
        size = QSize(300, 190)
        self.maps = {}
        for i, step_plan in enumerate(self.flow.steps):
            layer = found.get(step_plan.number)
            try:
                image = self.sharp(snapshots.render(layer, used.get(step_plan.number, []), background,
                                                    self.px(size))) if layer else None
            except Exception:
                image = None
            self.maps[i] = image or self.sharp(snapshots.placeholder(self.px(size), "no map"))
        self.t = images.THEMES.get(options.theme, images.THEMES["standard"])
        try:   # the finished result: the final outputs (or the one chosen), tiles when far apart
            final_size = self.px(QSize(int(self.W * 0.56), int(self.H * 0.56)))
            self.final_map = self.sharp(snapshots.final_map(self.controller, found, final_size, background,
                                                            choice=getattr(options, "final_output", 0)))
        except Exception:
            self.final_map = None
        if snapshots.is_general(options):   # General detail: the kinds of data, no real maps
            self.maps = {i: self.sharp(snapshots.kind_card(snapshots.step_kind(step_plan), self.px(size))) for i,
                         step_plan in enumerate(self.flow.steps)}
            self.final_map = None
        self.ending = random.choice(ENDINGS)  # nosec B311: drawing variety, not security
        self.background = self.board()

    def centre(self, k: float) -> QPointF:
        """The middle of square ``k`` (fractions in between), winding back and forth."""
        k = min(max(k, 0.0), self.total - 1.0)
        a, b = int(math.floor(k)), min(int(math.floor(k)) + 1, self.total - 1)

        def at(i):
            row, col = divmod(i, self.cols)
            if row % 2:
                col = self.cols - 1 - col
            dx, dy, _turn = self.nudge[i]
            return QPointF(self.ox + (col + 0.5) * self.sq + dx, self.oy + (row + 0.5) * self.sq + dy)
        pa, pb = at(a), at(b)
        f = k - a
        return QPointF(pa.x() + (pb.x() - pa.x()) * f, pa.y() + (pb.y() - pa.y()) * f)

    def board(self) -> QImage:
        image = self.frame_image()
        image.fill(QColor("#2E6B4E"))
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rows = math.ceil(self.total / self.cols)
        frame = QRectF(self.ox - 16, self.oy - 16, self.cols * self.sq + 32, rows * self.sq + 32)
        p.setPen(QPen(QColor("#5D3A1A"), 6))
        p.setBrush(QColor("#F3E9D2"))
        p.drawRoundedRect(frame, 14, 14)
        tools = {v: i for i, v in self.tool_at.items()}
        palette = ["#F5B7B1", "#AED6F1", "#A9DFBF", "#F9E79F", "#D7BDE2", "#F5CBA7"]
        for k in range(1, self.total):   # dotted steps from square to square
            a, b = self.centre(k - 1), self.centre(k)
            p.setPen(QPen(QColor("#C9B79C"), 2, Qt.PenStyle.DotLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(a, b)
        side = self.sq * 0.8
        for k in range(self.total):
            c = self.centre(k)
            p.save()
            p.translate(c)
            p.rotate(self.nudge[k][2])
            p.translate(-c.x(), -c.y())
            r = QRectF(c.x() - side / 2, c.y() - side / 2, side, side)
            if k == 0 or k == self.finish_at:
                fill = "#F4D03F"
            elif k in tools:
                fill = palette[tools[k] % len(palette)]
            else:
                fill = "#FFFDF7"
            p.setPen(QPen(QColor("#8B6B4A"), 1.5))
            p.setBrush(QColor(fill))
            p.drawRoundedRect(r, 8, 8)
            p.setPen(INK)
            if k == 0 or k == self.finish_at:
                p.setFont(self.font(max(11, self.sq * 0.16), True))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, "START" if k == 0 else "FINISH")
            elif k in tools:
                i = tools[k]
                icon = _alg_icon_image(self.flow.steps[i].step.alg, int(self.sq * 0.32))
                if icon is not None:
                    p.drawImage(QPointF(c.x() - icon.width() / 2, r.top() + 4), icon)
                p.setFont(self.font(max(9, self.sq * 0.12), True))
                text = self.flow.tool(i, self.options.numbers)
                p.drawText(r.adjusted(3, self.sq * 0.34, -3, -2),
                           int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
                           QFontMetricsF(p.font()).elidedText(text, Qt.TextElideMode.ElideRight, (side - 8) * 2))
            else:
                p.setPen(QPen(QColor("#C9B79C"), 1))
                p.drawEllipse(c, 3, 3)
            p.restore()
        p.setFont(self.font(24, True))
        p.setPen(QColor("#FDFEFE"))
        p.drawText(QRectF(24, 18, self.W - 200, 40), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetricsF(p.font()).elidedText(self.title, Qt.TextElideMode.ElideRight, self.W - 220))
        p.end()
        return image

    def token(self, p, at: QPointF, lift=0.0):
        s = self.sq / 90
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawEllipse(QPointF(at.x(), at.y() + 18 * s), 16 * s * (1 - lift * 0.3), 6 * s)
        y = at.y() - lift * 34 * s
        body = QPainterPath(QPointF(at.x() - 15 * s, y + 18 * s))
        body.quadTo(QPointF(at.x(), y - 30 * s), QPointF(at.x() + 15 * s, y + 18 * s))
        body.closeSubpath()
        p.setPen(QPen(QColor("#7B241C"), 1.5))
        p.setBrush(QColor("#C0392B"))
        p.drawPath(body)
        p.drawEllipse(QPointF(at.x(), y - 14 * s), 9 * s, 9 * s)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 140))
        p.drawEllipse(QPointF(at.x() - 3 * s, y - 17 * s), 2.5 * s, 2.5 * s)

    def die(self, p, value, spin=0.0):
        size = 54
        r = QRectF(self.W - size - 30, 16, size, size)
        p.save()
        p.translate(r.center())
        p.rotate(spin * 360)
        p.setPen(QPen(INK, 2))
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(QRectF(-size / 2, -size / 2, size, size), 9, 9)
        p.setBrush(INK)
        spots = {1: [(0, 0)], 2: [(-1, -1), (1, 1)], 3: [(-1, -1), (0, 0), (1, 1)], 4: [(-1, -1), (1, -1), (-1, 1), (1, 1)],
                 5: [(-1, -1), (1, -1), (0, 0), (-1, 1), (1, 1)], 6: [(-1, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (1, 1)]}
        for dx, dy in spots.get(max(1, min(6, value)), []):
            p.drawEllipse(QPointF(dx * 14, dy * 14), 4.5, 4.5)
        p.restore()

    def card(self, p, i, alpha):
        image = self.maps[i]
        iw, ih = size_of(image)
        w, h = iw + 40, ih + 70
        a = _ease(alpha)
        r = QRectF(self.W / 2 - w / 2, self.H / 2 - h / 2 + 20 * (1 - a), w, h)
        p.setOpacity(a)
        p.setPen(QPen(INK, 2))
        p.setBrush(QColor("#FFFDF7"))
        p.drawRoundedRect(r, 14, 14)
        p.setFont(self.font(17, True))
        p.setPen(INK)
        p.drawText(QRectF(r.x(), r.y() + 10, w, 26), Qt.AlignmentFlag.AlignCenter,
                   QFontMetricsF(p.font()).elidedText(self.flow.tool(i, self.options.numbers), Qt.TextElideMode.ElideRight,
                                                      w - 20))
        p.drawImage(QRectF(r.x() + 20, r.y() + 44, iw, ih), image)
        result = self.flow.result_of(i)
        if result:
            p.setFont(self.font(13))
            p.drawText(QRectF(r.x(), r.bottom() - 24, w, 20), Qt.AlignmentFlag.AlignCenter, "→ " + result)
        p.setOpacity(1.0)

    def final_card(self, p, a):
        """The result's map, large, on a card: the prize."""
        image = self.final_map
        iw, ih = size_of(image)
        w, h = iw + 40, ih + 76
        r = QRectF(self.W / 2 - w / 2, self.H / 2 - h / 2 + 16, w, h)
        p.setOpacity(_ease(a))
        p.setPen(QPen(QColor("#5D3A1A"), 4))
        p.setBrush(QColor("#FFFDF7"))
        p.drawRoundedRect(r, 16, 16)
        p.drawImage(QRectF(r.x() + 20, r.y() + 20, iw, ih), image)
        p.setFont(self.font(20, True))
        p.setPen(INK)
        p.drawText(QRectF(r.x(), r.bottom() - 46, w, 30), Qt.AlignmentFlag.AlignCenter,
                   QFontMetricsF(p.font()).elidedText(self.flow.result_name, Qt.TextElideMode.ElideRight, w - 30))
        p.setOpacity(1.0)

    def compose(self, k, lift=0.0, die=None, spin=0.0, card=None, card_a=0.0, done=(), ending=0.0, confetti=0.0,
                final=0.0):
        image = QImage(self.background)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        for i in done:   # a tick on each square played
            c = self.centre(self.tool_at[i])
            p.setPen(QPen(QColor("#1E8449"), 3))
            p.drawLine(QPointF(c.x() + self.sq * 0.18, c.y() + self.sq * 0.28),
                       QPointF(c.x() + self.sq * 0.26, c.y() + self.sq * 0.36))
            p.drawLine(QPointF(c.x() + self.sq * 0.26, c.y() + self.sq * 0.36),
                       QPointF(c.x() + self.sq * 0.42, c.y() + self.sq * 0.16))
        self.token(p, self.centre(k), lift)
        if die is not None:
            self.die(p, die, spin)
        if card is not None:
            self.card(p, card, card_a)
        if final > 0 and self.final_map is not None:
            self.final_card(p, final)
        if confetti > 0:
            rnd = random.Random(4)  # nosec B311: drawing variety, not security
            for _ in range(90):
                x = rnd.uniform(0, self.W)
                y = (rnd.uniform(-self.H, 0) + confetti * self.H * 1.4) % (self.H + 20)
                p.save()
                p.translate(x, y)
                p.rotate(rnd.uniform(0, 360) + confetti * 400)
                colour = rnd.choice(["#E74C3C", "#F1C40F", "#2ECC71", "#3498DB", "#9B59B6"])
                p.fillRect(QRectF(-4, -2, 8, 4), QColor(colour))
                p.restore()
        if ending > 0:
            p.setOpacity(_ease(ending))
            p.setFont(self.font(32, True))
            line = self.ending
            r = QRectF(0, self.H * 0.42, self.W, 50)
            p.setPen(QColor(0, 0, 0, 120))
            p.drawText(r.translated(2, 3), Qt.AlignmentFlag.AlignCenter, line)
            p.setPen(QColor("#F4D03F"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, line)
            p.setOpacity(1.0)
        watermark(p, self.W, self.H, dict(muted="#D5E8DC"))
        p.end()
        return image

    def estimate(self):
        options = self.options
        return self.total * self.n(0.18) + len(self.tool_at) * (self.n(0.6) + 2 * self.n(options.fade) + 1) + 20

    def keyframes(self):
        return [self.compose(self.finish_at, done=list(self.tool_at), ending=1.0, confetti=0.4),
                self.compose(0, die=3, card=0 if self.tool_at else None, card_a=1.0)]

    def frames(self):
        options = self.options
        first = self.compose(0)
        yield first, int(options.pause * 100) + 40
        pos, done, last = 0, [], first
        targets = [(i, self.tool_at[i]) for i in sorted(self.tool_at)] + [(None, self.finish_at)]
        for i, target in targets:
            value = target - pos
            for k in range(self.n(0.6)):   # roll the die
                last = self.compose(pos, die=random.Random(k + pos).randint(1, 6), spin=k / self.n(0.6), done=done)  # nosec B311
                yield last, self.delay
            last = self.compose(pos, die=value, done=done)
            yield last, 40
            hop = self.n(0.18)
            for square in range(pos, target):   # hop square by square
                for k in range(1, hop + 1):
                    f = k / hop
                    last = self.compose(square + _ease(f), lift=math.sin(math.pi * f), die=value, done=done)
                    yield last, self.delay
            pos = target
            if i is None:
                break
            steps = self.n(options.fade)
            for k in range(1, steps + 1):   # the step's card
                last = self.compose(pos, die=value, card=i, card_a=k / steps, done=done)
                yield last, self.delay
            yield last, int(options.pause * 100) + 60
            done.append(i)
            for k in range(steps, 0, -1):
                last = self.compose(pos, die=value, card=i, card_a=(k - 1) / steps, done=done)
                yield last, self.delay
        steps = self.n(1.2)   # you win!
        for k in range(1, steps + 1):
            last = self.compose(pos, done=done, ending=min(1.0, 2 * k / steps), confetti=k / steps * 0.5)
            yield last, self.delay
        if self.final_map is not None:   # the words go, the result's map arrives, the confetti keeps falling
            steps = self.n(max(options.fade, 0.6))
            for k in range(1, steps + 1):
                f = k / steps
                last = self.compose(pos, done=done, ending=1 - f, final=f, confetti=0.5 + f * 0.3)
                yield last, self.delay
            steps = self.n(1.4)
            for k in range(1, steps + 1):
                last = self.compose(pos, done=done, final=1.0, confetti=0.8 + k / steps * 0.6)
                yield last, self.delay
            last = self.compose(pos, done=done, final=1.0)
        yield from self.end_frames(last, first)
