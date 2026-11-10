"""Small widgets for the Export window: segmented buttons, collapsible sections, a colour
scheme picker with swatches, and a gallery of thumbnails with favourites."""

from __future__ import annotations

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr

ACCENT = "#2F6DB5"
CSS = f"""
QToolButton[seg="1"] {{ border: 1px solid #B9BDC2; background: palette(base); padding: 3px 11px; margin: 0; }}
QToolButton[seg="1"]:checked {{ background: {ACCENT}; color: white; border-color: {ACCENT}; }}
QToolButton[seg="1"]:disabled {{ color: #A0A4A8; }}
QToolButton[first="1"] {{ border-top-left-radius: 6px; border-bottom-left-radius: 6px; }}
QToolButton[last="1"] {{ border-top-right-radius: 6px; border-bottom-right-radius: 6px; }}
QToolButton[mode="1"] {{ padding: 6px 18px; font-size: 13px; }}
QToolButton[mode="1"]:checked {{ font-weight: 600; }}
QListWidget#gallery {{ border: 1px solid #D5D8DC; border-radius: 8px; }}
QListWidget#gallery::item {{ border: 2px solid transparent; border-radius: 6px; padding: 2px; }}
QListWidget#gallery::item:selected {{ border: 2px solid {ACCENT}; background: rgba(47,109,181,0.12); color: palette(text); }}
QLabel#estimate {{ background: #FFF4D6; border: 1px solid #E8C870; border-radius: 10px; padding: 3px 10px;
                   color: #5A4300; }}
"""


class Segmented(QWidget):
    """A row of joined toggle buttons: one choice from a few."""
    changed = pyqtSignal()

    def __init__(self, items, current=None, mode=False, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for i, (value, label) in enumerate(items):
            b = QToolButton()
            b.setText(tr(label))
            b.setCheckable(True)
            b.setProperty("seg", "1")
            b.setProperty("first", "1" if i == 0 else "0")
            b.setProperty("last", "1" if i == len(items) - 1 else "0")
            if mode:
                b.setProperty("mode", "1")
            self.group.addButton(b)
            self.buttons[value] = b
            lay.addWidget(b)
            b.toggled.connect(lambda on: on and self.changed.emit())
        lay.addStretch(1)
        self.set(current if current in self.buttons else items[0][0])

    def value(self):
        return next((v for v, b in self.buttons.items() if b.isChecked()), None)

    def set(self, value):
        value = next((v for v in self.buttons if str(v) == str(value)), None)
        if value is not None:
            self.buttons[value].setChecked(True)


class Section(QWidget):
    """A header that opens and closes, with form rows underneath."""

    def __init__(self, title, key, open_=True, note=""):
        super().__init__()
        self.key = "souschef/export/section_" + key
        self.title, self.note = tr(title), tr(note)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 4)
        v.setSpacing(4)
        self.head = QToolButton()
        self.head.setStyleSheet("QToolButton { border: none; font-weight: 600; padding: 3px 0; }")
        self.head.setCursor(Qt.CursorShape.PointingHandCursor)
        self.head.clicked.connect(self.toggle)
        v.addWidget(self.head)
        self.body = QWidget()
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(14, 0, 0, 2)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(6)
        self.grid.setColumnStretch(1, 1)
        v.addWidget(self.body)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #E0E2E5;")
        v.addWidget(line)
        self.rows = []
        self.set_open(QgsSettings().value(self.key, open_, type=bool))

    def add(self, widget, label=None):
        r = len(self.rows)
        lab = None
        if label:
            lab = QLabel(tr(label))
            self.grid.addWidget(lab, r, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.grid.addWidget(widget, r, 1)
        else:
            self.grid.addWidget(widget, r, 0, 1, 2)
        self.rows.append((widget, lab))
        return widget

    def show_row(self, widget, visible):
        for w, lab in self.rows:
            if w is widget:
                w.setVisible(visible)
                if lab is not None:
                    lab.setVisible(visible)

    def refresh_visible(self):
        """The section hides itself when none of its rows apply."""
        self.setVisible(any(not w.isHidden() for w, _l in self.rows))

    def set_open(self, on):
        self.body.setVisible(on)
        self.head.setText(("▾  " if on else "▸  ") + self.title + (f"   ({self.note})" if self.note and not on else ""))
        QgsSettings().setValue(self.key, on)

    def toggle(self):
        self.set_open(not self.body.isVisible())


def colour_picker(themes, current):
    """The colour schemes, each with a small swatch of its colours."""
    from . import images
    box = QComboBox()
    box.setIconSize(QSize(36, 14))
    for key, label in themes:
        t = images.THEMES.get(key, images.THEMES["standard"])
        img = QImage(36, 14, QImage.Format.Format_ARGB32)
        img.fill(QColor(t["bg"]))
        p = QPainter(img)
        for i, c in enumerate((t["input"][0], t["tint"], t["output"][0])):
            p.fillRect(2 + i * 11, 2, 10, 10, QColor(c))
        p.setPen(QColor("#9AA0A6"))
        p.drawRect(0, 0, 35, 13)
        p.end()
        box.addItem(QIcon(QPixmap.fromImage(img)), tr(label), key)
    i = box.findData(current)
    box.setCurrentIndex(max(i, 0))
    return box


def _card(image: QImage | None, w: int, h: int, text="") -> QIcon:
    card = QImage(w, h, QImage.Format.Format_ARGB32)
    card.fill(QColor("#FFFFFF"))
    p = QPainter(card)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if image is not None and not image.isNull():
        ratio = image.width() / max(image.height(), 1)
        if ratio > 1.9 or ratio < 0.9:   # very wide or tall: a readable part from the top left
            s = max(w / image.width(), h / image.height()) * 1.15
            p.drawImage(QRectF(0, 0, w, h), image, QRectF(0, 0, w / s, h / s))
        else:
            s = min(w / image.width(), h / image.height())
            iw, ih = image.width() * s, image.height() * s
            p.drawImage(QRectF((w - iw) / 2, (h - ih) / 2, iw, ih), image)
    else:
        p.fillRect(0, 0, w, h, QColor("#F1F2F4"))
        p.setPen(QColor("#9AA0A6"))
        p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, text)
    p.setPen(QColor("#D0D3D6"))
    p.drawRect(0, 0, w - 1, h - 1)
    p.end()
    return QIcon(QPixmap.fromImage(card))


class Gallery(QWidget):
    """A scrolling row of thumbnails drawn from the user's own workflow. Favourites (right-
    click, or the star button) come first."""
    chosen = pyqtSignal(str)

    TW, TH = 132, 84

    def __init__(self, category, items, thumbnail, current=None):
        """``items``: [(key, name, description)]; ``thumbnail(key)`` -> QImage or None, called
        a little at a time after the window opens."""
        super().__init__()
        self.category, self.items, self.thumbnail = category, items, thumbnail
        self.about_of = {k: d for k, _n, d in items}
        self.name_of = {k: n for k, n, _d in items}
        self.settings_key = f"souschef/export/favourites_{category}"
        stored = QgsSettings().value(self.settings_key, "") or ""
        self.favourites = [k for k in str(stored).split(",") if k in self.name_of]
        self.icons = {}
        self.unavailable = {}   # key -> why it can't be chosen now
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        self.list = QListWidget()
        self.list.setObjectName("gallery")
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setFlow(QListView.Flow.LeftToRight)
        self.list.setWrapping(False)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setIconSize(QSize(self.TW, self.TH))
        self.list.setGridSize(QSize(self.TW + 18, self.TH + 34))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setFixedHeight(self.TH + 34 + 18)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.currentItemChanged.connect(self._current_changed)
        v.addWidget(self.list)
        row = QHBoxLayout()
        self.about = QLabel()
        self.about.setWordWrap(True)
        self.about.setStyleSheet("color: #5F6670;")
        self.star = QToolButton()
        self.star.setCheckable(True)
        self.star.setToolTip(tr("Favourites come first in the gallery"))
        self.star.toggled.connect(self._star_toggled)
        row.addWidget(self.about, 1)
        row.addWidget(self.star)
        v.addLayout(row)
        self._fill(current)
        self._queue = [k for k, _n, _d in items]
        self._timer = QTimer(self)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self._next_thumbnail)

    def start(self):
        """Draws the thumbnails one at a time, so the window stays responsive."""
        if self._queue and not self._timer.isActive():
            self._timer.start()

    def _next_thumbnail(self):
        if not self._queue:
            self._timer.stop()
            return
        current = self.current()
        key = current if current in self._queue else self._queue[0]   # the chosen one first
        self._queue.remove(key)
        try:
            image = self.thumbnail(key)
        except Exception:
            image = None
        self.icons[key] = _card(image, self.TW, self.TH, tr("no preview"))
        item = self._item(key)
        if item is not None:
            item.setIcon(self.icons[key])

    def _order(self):
        favs = [k for k, _n, _d in self.items if k in self.favourites]
        return favs + [k for k, _n, _d in self.items if k not in self.favourites]

    def _fill(self, current):
        self.list.blockSignals(True)
        self.list.clear()
        for key in self._order():
            label = ("★ " if key in self.favourites else "") + tr(self.name_of[key])
            item = QListWidgetItem(self.icons.get(key) or _card(None, self.TW, self.TH, "…"), label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(tr(self.about_of[key]))
            if key in self.unavailable:   # greyed out, with the reason
                item.setFlags(item.flags() & ~(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable))
                item.setToolTip(self.unavailable[key])
            self.list.addItem(item)
        self.list.blockSignals(False)
        if current in self.unavailable:
            current = None
        item = self._item(current) or next((self.list.item(i) for i in range(self.list.count())
                                            if self.list.item(i).data(Qt.ItemDataRole.UserRole) not in self.unavailable),
                                           self.list.item(0))
        self.list.setCurrentItem(item)
        self._show_about()

    def set_unavailable(self, reasons: dict):
        """Grey out designs that can't be used with the current settings: {key: why}. When the
        chosen one becomes unavailable, the first available one is chosen instead."""
        if reasons == self.unavailable:
            return
        self.unavailable = dict(reasons)
        before = self.current()
        self._fill(before)
        if self.current() != before:
            self.chosen.emit(self.current() or "")

    def _item(self, key):
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == key:
                return self.list.item(i)
        return None

    def current(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def set_current(self, key):
        item = self._item(key)
        if item is not None:
            self.list.setCurrentItem(item)

    def _current_changed(self, *_args):
        self._show_about()
        self.chosen.emit(self.current() or "")

    def _show_about(self):
        key = self.current()
        if key is None:
            return
        self.about.setText(f"<b>{tr(self.name_of[key])}</b> — {tr(self.about_of[key])}")
        self.star.blockSignals(True)
        self.star.setChecked(key in self.favourites)
        self.star.setText("★ " + tr("Favourite") if key in self.favourites else "☆ " + tr("Add to favourites"))
        self.star.blockSignals(False)

    def _star_toggled(self, on):
        self.set_favourite(self.current(), on)

    def set_favourite(self, key, on):
        if key is None:
            return
        if on and key not in self.favourites:
            self.favourites.append(key)
        elif not on and key in self.favourites:
            self.favourites.remove(key)
        QgsSettings().setValue(self.settings_key, ",".join(self.favourites))
        self._fill(key)
        self.list.scrollToItem(self._item(key))

    def _menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        fav = key in self.favourites
        action = menu.addAction(tr("Remove from Favourites") if fav else tr("Add to Favourites"))
        action.triggered.connect(lambda: self.set_favourite(key, not fav))
        menu.exec(self.list.viewport().mapToGlobal(pos))
