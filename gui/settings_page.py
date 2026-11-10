"""Sous Chef's page in QGIS's Settings › Options: background tracking, where things are
saved, tips, and clearing temporary data, all in one place."""

from __future__ import annotations

import os

from qgis.core import QgsSettings
from qgis.gui import QgsFileWidget, QgsOptionsPageWidget, QgsOptionsWidgetFactory
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..i18n import tr
from . import icons


class SettingsPage(QgsOptionsPageWidget):
    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        s = QgsSettings()
        lay = QVBoxLayout(self)

        tracking = QGroupBox(tr("Background tracking"))
        tl = QVBoxLayout(tracking)
        self.tracking = QCheckBox(tr("Note Save As, Field Calculator, batch, iterate and other runs QGIS doesn't log"))
        self.tracking.setChecked(s.value("souschef/tracking", True, type=bool))
        hint = QLabel(tr("Needed for “Create Recipe from This Layer…” to find those steps. Kept in memory only "
                         "(the last 500 of each) and gone when QGIS closes."))
        hint.setWordWrap(True)
        tl.addWidget(self.tracking)
        tl.addWidget(hint)
        clear = QPushButton(tr("Clear Sous Chef's Temporary Data…"))
        clear.clicked.connect(plugin.clear_temporary_data)
        tl.addWidget(clear)
        lay.addWidget(tracking)

        places = QGroupBox(tr("Where things are saved"))
        form = QFormLayout(places)
        from ..provider import toolbox_folder
        row = QHBoxLayout()
        folder = QLabel(toolbox_folder())
        folder.setWordWrap(True)
        open_folder = QPushButton(tr("Open"))
        open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(toolbox_folder())))
        row.addWidget(folder, 1)
        row.addWidget(open_folder)
        form.addRow(tr("Recipes"), row)
        self.image_folder = QgsFileWidget()
        self.image_folder.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)
        self.image_folder.setFilePath(s.value("souschef/image_folder", "") or os.path.expanduser("~"))
        form.addRow(tr("Exports (images, animations, recipes) and shared files"), self.image_folder)
        lay.addWidget(places)

        from . import video
        videos = QGroupBox(tr("MP4 videos"))
        vl = QFormLayout(videos)
        self.ffmpeg = QgsFileWidget()
        self.ffmpeg.setStorageMode(QgsFileWidget.StorageMode.GetFile)
        self.ffmpeg.setFilePath(s.value(video.KEY, ""))
        found = video.find_ffmpeg()
        self.ffmpeg.lineEdit().setPlaceholderText(found or tr("Not found"))
        vl.addRow(tr("ffmpeg program"), self.ffmpeg)
        note = QLabel(tr("Animations can be saved as MP4 with ffmpeg, a free program "
                         "(<a href='https://ffmpeg.org/download.html'>ffmpeg.org</a>). Leave this empty to use the one "
                         "Sous Chef finds; GIFs don't need it."))
        note.setWordWrap(True)
        note.setOpenExternalLinks(True)
        vl.addRow(note)
        lay.addWidget(videos)

        tips = QGroupBox(tr("Help"))
        hl = QVBoxLayout(tips)
        self.tips = QCheckBox(tr("Show the tutorial the next time QGIS starts"))
        self.tips.setChecked(not s.value("souschef/welcome_shown", False, type=bool))
        hl.addWidget(self.tips)
        from . import tutorial
        tour = QPushButton(tr("Tutorial…"))
        tour.clicked.connect(lambda: tutorial.show(self))
        hl.addWidget(tour)
        guide = QPushButton(tr("How Sous Chef Works…"))
        guide.clicked.connect(plugin.show_help)
        hl.addWidget(guide)
        lay.addWidget(tips)
        lay.addStretch(1)

    def apply(self):
        s = QgsSettings()
        s.setValue("souschef/image_folder", self.image_folder.filePath())
        from . import video
        s.setValue(video.KEY, self.ffmpeg.filePath().strip())
        s.setValue("souschef/welcome_shown", not self.tips.isChecked())
        self.plugin.tracking_action.setChecked(self.tracking.isChecked())   # starts or stops the tracker


class SettingsFactory(QgsOptionsWidgetFactory):
    def __init__(self, plugin):
        super().__init__("Sous Chef", icons.plugin_icon())
        self.plugin = plugin

    def createWidget(self, parent):
        return SettingsPage(self.plugin, parent)
