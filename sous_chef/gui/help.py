"""The built-in guide: a short page on how Sous Chef works."""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout

from ..i18n import tr

PAGE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "help", "index.html")
_open = []


def show_help(parent=None):
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr("How Sous Chef works"))
    dialog.resize(640, 680)
    lay = QVBoxLayout(dialog)
    browser = QTextBrowser()
    browser.setOpenExternalLinks(True)
    browser.setSource(QUrl.fromLocalFile(PAGE))
    lay.addWidget(browser)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.close)
    from . import tutorial
    tour = buttons.addButton(tr("Tutorial…"), QDialogButtonBox.ButtonRole.HelpRole)
    tour.clicked.connect(lambda: tutorial.show(dialog))
    lay.addWidget(buttons)
    dialog.finished.connect(lambda _r: _open.remove(dialog) if dialog in _open else None)
    _open.append(dialog)   # keep it alive while it's open
    dialog.show()
    return dialog
