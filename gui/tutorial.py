"""A short tutorial: a few cards that show how Sous Chef is used, the first time it starts.
It can be opened again from How Sous Chef Works… and from Sous Chef's settings page."""

from __future__ import annotations

import os

from qgis.core import QgsApplication, QgsSettings
from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr

SEEN_KEY = "souschef/welcome_shown"
ICONS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons")


def _icon(name: str) -> QIcon:
    """A Sous Chef icon from the plugin's folder, or one of QGIS's own (a name starting with "/")."""
    if name.startswith("/"):
        return QgsApplication.getThemeIcon(name)
    return QIcon(os.path.join(ICONS, name))


# (icon, title, text)
CARDS = [
    ("sous_chef_128.png", "Welcome to Sous Chef",
     "Sous Chef turns what you do in QGIS into a <b>recipe</b>: an ordinary Processing model you can run again on "
     "other data, and share. This takes a minute; you can skip it and find it again under "
     "<i>How Sous Chef Works…</i>"),
    ("sous_chef_recording_128.png", "1. Record",
     "Press the <b>Record</b> button on the Sous Chef toolbar and work as usual: Processing tools, batches, the Field "
     "Calculator, Save As, filters, joins. Press it again to stop.<br><br>Already done the work? Right-click the layer "
     "it made and choose <b>Create Recipe from This Layer…</b>"),
    ("/mActionShowAllLayers.svg", "2. Review",
     "The review window shows your workflow as a flowchart or a list. Leave out steps you don't want, choose which "
     "settings the recipe should ask for, and name the layers it needs. Right-click a step to start the recipe from "
     "its result instead."),
    ("/processingAlgorithm.svg", "3. Taste Test",
     "<b>Taste Test</b> runs the recipe on the layers you recorded with and checks that it makes the same results. "
     "Nothing is added to your project."),
    ("recipe_128.png", "4. Add to the Recipe Book",
     "<b>Add to Recipe Book</b> saves it in the Processing Toolbox, ready to run like any other tool. <b>Share</b> "
     "makes a zip file or a Python script for other people; they don't need Sous Chef."),
    ("/mActionSaveMapAsImage.svg", "5. Images & Animations",
     "Show what the recipe does: flowchart pictures, animations (GIF or MP4) and written recipes, from a plain "
     "method for a report to a knight's pilgrimage. Enjoy cooking!"),
]


class Tutorial(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Sous Chef tutorial"))
        self.resize(560, 400)
        lay = QVBoxLayout(self)
        self.pages = QStackedWidget()
        for icon, title, text in CARDS:
            self.pages.addWidget(self._card(icon, tr(title), tr(text)))
        lay.addWidget(self.pages, 1)
        self.dots = QLabel()
        self.dots.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.dots)
        row = QHBoxLayout()
        self.again = QCheckBox(tr("Show this again when QGIS starts"))
        self.again.setChecked(False)
        row.addWidget(self.again)
        row.addStretch(1)
        self.skip = QPushButton(tr("Skip"))
        self.back = QPushButton(tr("Back"))
        self.next = QPushButton(tr("Next"))
        for button in (self.skip, self.back, self.next):
            button.setAutoDefault(False)
            row.addWidget(button)
        self.next.setDefault(True)
        self.skip.clicked.connect(self.accept)
        self.back.clicked.connect(lambda: self._go(-1))
        self.next.clicked.connect(lambda: self._go(1))
        lay.addLayout(row)
        self.finished.connect(self._remember)
        self._go(0)

    def _card(self, icon, title, text):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 16, 24, 8)
        picture = QLabel()
        picture.setPixmap(_icon(icon).pixmap(QSize(96, 96)))
        picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(picture)
        heading = QLabel(f"<h2>{title}</h2>")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(heading)
        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(body, 1)
        return page

    def _go(self, step):
        index = self.pages.currentIndex() + step
        if index >= self.pages.count():
            self.accept()
            return
        index = max(index, 0)
        self.pages.setCurrentIndex(index)
        last = index == self.pages.count() - 1
        self.back.setEnabled(index > 0)
        self.next.setText(tr("Done") if last else tr("Next"))
        self.skip.setVisible(not last)
        self.dots.setText("  ".join("●" if i == index else "○" for i in range(self.pages.count())))

    def _remember(self, _result):
        QgsSettings().setValue(SEEN_KEY, not self.again.isChecked())


_OPEN = []


def show(parent=None):
    """Open the tutorial beside QGIS (it doesn't block anything else)."""
    dialog = Tutorial(parent)
    dialog.setModal(False)
    _OPEN.append(dialog)   # kept alive while it's open
    dialog.finished.connect(lambda _r: _OPEN.remove(dialog) if dialog in _OPEN else None)
    dialog.show()
    dialog.raise_()
    return dialog
