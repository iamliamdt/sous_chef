"""The Typewriter animation: the written recipe typed out on a sheet of paper, a step at a
time, with a snapshot of each step's map pinned beside it."""

from __future__ import annotations

from qgis.PyQt.QtCore import QPointF, QRectF, QSize, Qt
from qgis.PyQt.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen

from . import fonts, snapshots, written
from .animation import Animation, _ease, size_of, watermark

DESK, PAPER, TYPE_INK, RED_LINE = QColor("#4A4038"), QColor("#FBF7EE"), QColor("#24211E"), QColor("#E4A0A0")
THEME = dict(muted="#E8DFD2")


class Typewriter(Animation):
    FIXED_LAYOUT = True
    LINE = 22

    def type_font(self, size=14, bold=False):
        f = QFont(fonts.family_for("typewriter", True, self.options.font) or "Courier New")
        f.setStyleHint(QFont.StyleHint.TypeWriter)
        f.setPixelSize(size)
        f.setBold(bold)
        return f

    def prepare(self):
        fonts.load()
        options = self.options
        self.H = int(round(self.W * 9 / 16))
        self.paper = QRectF(28, 22, self.W - 56, self.H - 56)
        self.photo_w = int(min(190, self.W * 0.26))
        text_w = self.paper.width() - self.photo_w - 90
        recipe = written.build(self.controller, options.detail, True, options.title)
        fm = QFontMetricsF(self.type_font())
        self.lines = []   # (text, bold, size, step index or None)

        def add(text, step=None, bold=False, size=14, indent=""):
            words, line = text.split(), ""
            first = True
            for word in words:
                trial = (line + " " + word).strip()
                if fm.horizontalAdvance(("" if first else indent) + trial) > text_w and line:
                    self.lines.append((("" if first else indent) + line, bold, size, step))
                    first, line = False, word
                else:
                    line = trial
            self.lines.append((("" if first else indent) + line, bold, size, step))

        add(recipe["title"].upper(), bold=True, size=18)
        self.lines.append(("", False, 14, None))
        if recipe["ingredients"]:
            add("INGREDIENTS", bold=True)
            for x in recipe["ingredients"]:
                add("- " + x, indent="  ")
            self.lines.append(("", False, 14, None))
        add("METHOD", bold=True)
        for k, s in enumerate(recipe["method"]):
            number = f"{k + 1}. "
            add(number + s["text"], step=k, indent=" " * len(number))
            if s["note"]:
                add("   (" + s["note"] + ")", step=k, indent="    ")
        self.steps_n = len(recipe["method"])
        self.numbers = [s.get("number") for s in recipe["method"]]
        # the snapshots pinned beside the page
        found, used, self._hold = snapshots.result_layers(self.controller)
        size = QSize(self.photo_w - 20, int((self.photo_w - 20) * 0.72))
        background = snapshots.background_layers() if options.basemaps else []
        self.photos = {}
        for k, number in enumerate(self.numbers):
            layer = found.get(number)
            try:
                self.photos[k] = self.sharp(snapshots.render(layer, used.get(number, []), background, self.px(size))) \
                    if layer else None
            except Exception:
                self.photos[k] = None
        if snapshots.is_general(options):   # General detail: no real maps pinned beside the page
            self.photos = {k: None for k in self.photos}
        self.visible = int((self.paper.height() - 50) // self.LINE)

    # ------------------------------------------------------------ a frame
    def compose(self, typed: int, scroll: float, photo: int | None, photo_in: float, cursor=True) -> QImage:
        """``typed``: how many characters are on the page so far."""
        image = self.frame_image()
        image.fill(DESK)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        r = self.paper
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawRect(r.translated(4, 5))
        p.setBrush(PAPER)
        p.drawRect(r)
        p.setPen(QPen(RED_LINE, 1.2))
        p.drawLine(QPointF(r.left() + 46, r.top()), QPointF(r.left() + 46, r.bottom()))
        p.save()
        p.setClipRect(r.adjusted(0, 14, 0, -14))
        x0, y = r.left() + 60, r.top() + 40 - scroll * self.LINE
        left = typed
        cursor_at = None
        for text, bold, size, _step in self.lines:
            if left <= 0:
                break
            shown = text[:left]
            left -= len(text) + 1
            p.setFont(self.type_font(size, bold))
            p.setPen(TYPE_INK)
            p.drawText(QPointF(x0, y), shown)
            cursor_at = QPointF(x0 + QFontMetricsF(self.type_font(size, bold)).horizontalAdvance(shown), y)
            y += self.LINE if size <= 14 else self.LINE + 6
        if cursor and cursor_at is not None:
            p.fillRect(QRectF(cursor_at.x() + 1, cursor_at.y() - 13, 8, 16), QColor(36, 33, 30, 200))
        p.restore()
        # the snapshot of the step being typed, pinned at the right
        shown = [k for k in range(self.steps_n) if photo is not None and k <= photo and self.photos.get(k) is not None]
        img_h = next((size_of(self.photos[k])[1] for k in shown), 100) + 44
        room = r.height() - 40
        step_y = img_h + 10 if len(shown) * (img_h + 10) <= room else (room - img_h) / max(len(shown) - 1, 1)
        for slot, k in enumerate(shown):   # each step's snapshot stays pinned; many of them overlap like a pile
            img = self.photos[k]
            fade = _ease(photo_in) if k == photo else 1.0
            if fade <= 0:
                continue
            p.save()
            p.setOpacity(fade)
            cx = r.right() - self.photo_w / 2 - 14 + (6 if slot % 2 else -4)
            cy = r.top() + 20 + img_h / 2 + slot * step_y + (20 * (1 - fade) if k == photo else 0)
            p.translate(cx, cy)
            p.rotate(3 if k % 2 else -3)
            img_w, img_h2 = size_of(img)
            frame = QRectF(-img_w / 2 - 10, -img_h2 / 2 - 10, img_w + 20, img_h2 + 44)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 60))
            p.drawRect(frame.translated(3, 4))
            p.setBrush(QColor("#FFFFFF"))
            p.drawRect(frame)
            p.drawImage(QRectF(-img_w / 2, -img_h2 / 2, img_w, img_h2), img)
            p.setPen(TYPE_INK)
            f = QFont(fonts.first_installed(["Segoe Print", "Bradley Hand", "Comic Sans MS"]) or "") \
                if self.options.fonts else QFont()
            f.setPixelSize(15)
            p.setFont(f)
            p.drawText(QRectF(frame.left(), frame.bottom() - 30, frame.width(), 24), Qt.AlignmentFlag.AlignCenter,
                       f"step {k + 1}")
            p.setBrush(QColor("#C0392B"))   # the pin
            p.setPen(QPen(QColor("#7B241C"), 1))
            p.drawEllipse(QPointF(0, frame.top() + 6), 5, 5)
            p.restore()
        watermark(p, self.W - 6, self.H - 4, THEME)
        p.end()
        return image

    # ------------------------------------------------------------ typing
    def _segments(self):
        """[(characters, seconds, step or None)]: the heading and ingredients, then each step."""
        segments, current, count = [], "head", 0
        for text, _b, _s, step in self.lines:
            key = step if step is not None else ("head" if current == "head" else "tail")
            if key != current and count:
                segments.append((count, current))
                count = 0
            current = key
            count += len(text) + 1
        if count:
            segments.append((count, current))
        options = self.options
        return [(n, (options.draw * 1.2 if key == "head" else options.draw * 0.6 if key == "tail" else options.draw), key)
                for n, key in segments]

    def estimate(self):
        return sum(self.n(sec) + self.n(self.options.fade) for _n, sec, _k in self._segments()) + 10

    def keyframes(self):
        total = sum(len(t) + 1 for t, _b, _s, _st in self.lines)
        return [self.compose(total // 2, 0, 0, 1.0), self.compose(total, max(0, len(self.lines) - self.visible), 0, 1.0)]

    def frames(self):
        options = self.options
        line_of, k = [], 0
        for i, (text, _b, _s, _st) in enumerate(self.lines):
            line_of.append((k, k + len(text) + 1, i))
            k += len(text) + 1
        typed, scroll, photo, photo_in = 0, 0.0, None, 0.0
        first = self.compose(0, 0, None, 0)
        yield first, 60
        last = first
        frame_no = 0
        for count, seconds, key in self._segments():
            if isinstance(key, int) and key != photo:
                photo, photo_in = key, 0.0
            steps = self.n(seconds)
            start = typed
            for s in range(1, steps + 1):
                frame_no += 1
                typed = start + round(count * s / steps)
                line = next((i for a, b, i in line_of if a <= typed < b), len(self.lines) - 1)
                target = max(0.0, line - self.visible * 0.7)
                scroll += (target - scroll) * 0.35
                if photo is not None:
                    photo_in = min(1.0, photo_in + 1.0 / max(self.n(options.fade), 1))
                last = self.compose(typed, scroll, photo, photo_in, cursor=True)
                yield last, self.delay
            if isinstance(key, int):   # a pause after each step, the cursor blinking
                blinks = max(1, int(options.pause / 0.5))
                for b in range(blinks):
                    last = self.compose(typed, scroll, photo, 1.0, cursor=b % 2 == 1)
                    yield last, 50
        last = self.compose(typed, scroll, photo, 1.0, cursor=False)
        yield from self.end_frames(last, first)
