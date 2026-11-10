"""Choosing a recipe's symbol: QGIS's own SVG browser with fill and outline colours and a
live preview, all in one window.

QGIS's symbols take their colours from a style ("param(fill)", "param(outline)"); some say
which colour to use by default ("param(fill) #000"). The colours start from that default,
otherwise a grey fill and a black outline. The chosen colours are filled in and saved as a
copy, with the recipe card's small green gear in the corner unless that's switched off.
"""

from __future__ import annotations

import os
import re

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QByteArray, QRectF, QSize, Qt
from qgis.PyQt.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from qgis.PyQt.QtSvg import QSvgRenderer
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QTabWidget,
    QVBoxLayout,
)

from ..i18n import tr

DEFAULT_FILL, DEFAULT_OUTLINE = "#B9BDC4", "#1E1F22"   # grey and black, like the recipe card icon
GEAR_KEY = "souschef/symbol_gear"
# the recipe card's green gear (icons/recipe.svg), drawn in a 48 × 48 box's bottom-right corner
GEAR = ('<circle cx="316" cy="316" r="130" fill="#FFFFFF"/>'
        '<polygon points="327.8,196.6 350.8,201.2 359.0,235.6 373.9,245.5 408.8,239.9 421.8,259.4 403.3,289.5 406.8,307.1 '
        '435.4,327.8 430.8,350.8 396.4,359.0 386.5,373.9 392.1,408.8 372.6,421.8 342.5,403.3 324.9,406.8 304.2,435.4 '
        '281.2,430.8 273.0,396.4 258.1,386.5 223.2,392.1 210.2,372.6 228.7,342.5 225.2,324.9 196.6,304.2 201.2,281.2 '
        '235.6,273.0 245.5,258.1 239.9,223.2 259.4,210.2 289.5,228.7 307.1,225.2" fill="#5DAA2B" stroke="#1E1F22" '
        'stroke-width="17.6" stroke-linejoin="round"/>'
        '<circle cx="316" cy="316" r="43.2" fill="#FFFFFF" stroke="#1E1F22" stroke-width="17.6"/>')
COOKING_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), "symbols", "cooking")
PARAM_RE = re.compile(r"param\((fill|outline|stroke)\)\s*(#[0-9A-Fa-f]{3,8}|[a-zA-Z]+)?")


def colour_params(path: str) -> dict:
    """{"fill": QColor | None, "outline": QColor | None} for the colours a symbol takes from a
    style (None: it takes it, but says no default). Empty when it has its own colours."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return {}
    found = {}
    for kind, default in PARAM_RE.findall(text):
        kind = "outline" if kind == "stroke" else kind
        if kind not in found or (found[kind] is None and default):
            colour = QColor(default) if default else None
            found[kind] = colour if colour is not None and colour.isValid() else None
    return found


def default_colours(path: str):
    """(fill, outline): the symbol's own defaults, otherwise a grey fill and a black outline."""
    params = colour_params(path)
    return (params.get("fill") or QColor(DEFAULT_FILL), params.get("outline") or QColor(DEFAULT_OUTLINE))


def with_gear(content: bytes) -> bytes:
    """The symbol with the recipe card's small green gear over its bottom-right corner, sized to
    the symbol's own drawing area (its viewBox, else its width and height)."""
    text = content.decode("utf-8", errors="replace")
    end = text.rfind("</svg>")
    tag = re.search(r"<svg\b[^>]*>", text)
    if end < 0 or tag is None:
        return content
    box = re.search(r'viewBox="\s*([-\d.eE]+)[\s,]+([-\d.eE]+)[\s,]+([\d.eE]+)[\s,]+([\d.eE]+)', tag.group(0))
    if box:
        x, y, w, h = (float(v) for v in box.groups())
    else:
        sizes = [re.search(rf'\s{name}="([\d.]+)', tag.group(0)) for name in ("width", "height")]
        if not all(sizes):
            return content
        x, y, w, h = 0.0, 0.0, float(sizes[0].group(1)), float(sizes[1].group(1))
    side = min(w, h)
    scale = side / 48.0
    left, top = x + w - side + 15.2 * scale, y + h - side + 15.2 * scale
    group = f'<g transform="translate({left:.3f},{top:.3f}) scale({0.075 * scale:.5f})">{GEAR}</g>'
    return (text[:end] + group + text[end:]).encode("utf-8")


def svg_with_colours(path: str, fill: QColor, outline: QColor, size: int = 64) -> bytes:
    """The symbol with its style colours filled in."""
    if not colour_params(path):
        with open(path, "rb") as fh:
            return fh.read()
    content = QgsApplication.svgCache().svgContent(path, size, fill, outline, 1, 1)
    content = bytes(content[0] if isinstance(content, tuple) else content)
    if not content.strip().startswith((b"<svg", b"<?xml")):
        with open(path, "rb") as fh:
            content = fh.read()
    return content


def save_coloured_copy(path: str, fill: QColor, outline: QColor, gear: bool = True) -> str:
    """A copy with the colours filled in, in Sous Chef's symbols folder (saved recipes keep
    their own copy next to the recipe)."""
    from ..core import cleanup
    folder = cleanup.symbols_folder()
    os.makedirs(folder, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    target = os.path.join(folder, f"{base}_{fill.name()[1:]}_{outline.name()[1:]}{'_gear' if gear else ''}.svg")
    content = svg_with_colours(path, fill, outline)
    try:
        with open(target, "wb") as fh:
            fh.write(with_gear(content) if gear else content)
    except OSError:
        return path
    return target


class SymbolDialog(QDialog):
    """QGIS's SVG browser on the left; colours and a large preview on the right."""

    def __init__(self, start_path="", fill=None, outline=None, parent=None):
        super().__init__(parent)
        from qgis.gui import QgsColorButton, QgsSvgSelectorWidget
        self.setWindowTitle(tr("Symbol for the recipe"))
        self.resize(940, 620)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.selector = QgsSvgSelectorWidget(self)
        tabs = QTabWidget()
        tabs.addTab(self._cooking_list(), tr("Cooking"))
        tabs.addTab(self.selector, tr("QGIS library"))
        if start_path and not start_path.startswith(COOKING_FOLDER):
            tabs.setCurrentIndex(1)
        row.addWidget(tabs, 3)
        side = QFrame()
        side.setMinimumWidth(220)
        sl = QVBoxLayout(side)
        self.preview = QLabel()
        self.preview.setFixedSize(160, 160)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("background: #FFFFFF; border: 1px solid #C8C8C8; border-radius: 6px;")
        sl.addWidget(QLabel(tr("<b>Preview</b>")))
        sl.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)
        self.small = QLabel()
        self.small.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sl.addWidget(self.small)
        sl.addSpacing(10)
        self.fill_button, self.outline_button = QgsColorButton(), QgsColorButton()
        for label, button in (("Fill", self.fill_button), ("Outline", self.outline_button)):
            button.setAllowOpacity(False)
            button.colorChanged.connect(self._update_preview)
            line = QHBoxLayout()
            line.addWidget(QLabel(label))
            line.addWidget(button, 1)
            sl.addLayout(line)
        from qgis.core import QgsSettings
        from qgis.PyQt.QtWidgets import QCheckBox
        self.gear_box = QCheckBox(tr("Add the recipe card's green gear"))
        self.gear_box.setToolTip(tr("A small green gear in the corner, as on Sous Chef's recipe card, so the "
                                    "recipe stands out from QGIS's own tools"))
        self.gear_box.setChecked(QgsSettings().value(GEAR_KEY, True, type=bool))
        self.gear_box.toggled.connect(self._update_preview)
        sl.addWidget(self.gear_box)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #6B7178;")
        sl.addWidget(self.note)
        sl.addStretch(1)
        row.addWidget(side, 1)
        lay.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.path = ""
        self.selector.svgSelected.connect(self._symbol_chosen)
        if start_path:
            self.selector.setSvgPath(start_path)
            self._symbol_chosen(start_path, fill, outline)
        else:
            self._update_preview()

    def _cooking_list(self):
        """Sous Chef's own cooking symbols, recoloured with the colours below."""
        self.cooking = QListWidget()
        self.cooking.setViewMode(QListView.ViewMode.IconMode)
        self.cooking.setIconSize(QSize(48, 48))
        self.cooking.setGridSize(QSize(92, 82))
        self.cooking.setResizeMode(QListView.ResizeMode.Adjust)
        self.cooking.setMovement(QListView.Movement.Static)
        self.cooking.setSpacing(4)
        for name in sorted(os.listdir(COOKING_FOLDER)) if os.path.isdir(COOKING_FOLDER) else []:
            if not name.endswith(".svg"):
                continue
            path = os.path.join(COOKING_FOLDER, name)
            fill, outline = default_colours(path)
            renderer = QSvgRenderer(QByteArray(svg_with_colours(path, fill, outline)))
            image = QImage(96, 96, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(painter, QRectF(0, 0, 96, 96))
            painter.end()
            item = QListWidgetItem(QIcon(QPixmap.fromImage(image)), name[:-4].replace("_", " "))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(name[:-4].replace("_", " "))
            self.cooking.addItem(item)
        self.cooking.currentItemChanged.connect(self._cooking_chosen)
        return self.cooking

    def _cooking_chosen(self, item, _previous=None):
        if item is None:
            return
        keep = bool(self.path) and bool(colour_params(self.path))   # keep the colours already chosen
        self._symbol_chosen(item.data(Qt.ItemDataRole.UserRole), self.fill_button.color() if keep else None,
                            self.outline_button.color() if keep else None)

    def _symbol_chosen(self, path, fill=None, outline=None):
        self.path = path if path and os.path.isfile(path) else ""
        params = colour_params(self.path) if self.path else {}
        default_fill, default_outline = default_colours(self.path) if self.path else (QColor(DEFAULT_FILL),
                                                                                      QColor(DEFAULT_OUTLINE))
        for button, colour in ((self.fill_button, fill or default_fill), (self.outline_button, outline or default_outline)):
            button.blockSignals(True)
            button.setColor(colour)
            button.blockSignals(False)
        self.fill_button.setEnabled("fill" in params)
        self.outline_button.setEnabled("outline" in params)
        if not self.path:
            self.note.setText(tr("Choose a symbol on the left, or a file with the … button below the list."))
        elif params:
            self.note.setText(tr("This symbol takes its colours from a style: choose them here. "
                              "They start from the symbol's own default, otherwise grey and black."))
        else:
            self.note.setText(tr("This symbol has its own colours."))
        self._update_preview()

    def _update_preview(self, *_args):
        if not self.path:
            self.preview.setPixmap(QPixmap())
            self.small.setText("")
            return
        content = svg_with_colours(self.path, self.fill_button.color(), self.outline_button.color())
        if self.gear_box.isChecked():
            content = with_gear(content)
        renderer = QSvgRenderer(QByteArray(content))
        renderer.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)

        def picture(size):
            image = QImage(size, size, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(painter, QRectF(0, 0, size, size))
            painter.end()
            return QPixmap.fromImage(image)

        self.preview.setPixmap(picture(128))
        self.small.setPixmap(picture(24))
        self.small.setToolTip(tr("As in the Processing Toolbox"))

    def gear(self) -> bool:
        return self.gear_box.isChecked()

    def done(self, result):
        from qgis.core import QgsSettings
        QgsSettings().setValue(GEAR_KEY, self.gear_box.isChecked())
        super().done(result)

    def fill(self):
        return self.fill_button.color()

    def outline(self):
        return self.outline_button.color()
