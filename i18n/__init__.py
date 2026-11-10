"""Translations. Interface text goes through tr(); a translation is a Qt .qm file named
sous_chef_<language>.qm in this folder (e.g. sous_chef_fr.qm), made with Qt Linguist."""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import QCoreApplication, QLocale, QTranslator

CONTEXT = "SousChef"
_translator = None


def tr(text: str) -> str:
    return QCoreApplication.translate(CONTEXT, text)


def install():
    """Load the translation for QGIS's language, if there is one."""
    global _translator
    from qgis.core import QgsSettings
    locale = QgsSettings().value("locale/userLocale", "") or QLocale.system().name()
    folder = os.path.dirname(__file__)
    for name in (locale, locale[:2]):
        path = os.path.join(folder, f"sous_chef_{name}.qm")
        if name and os.path.isfile(path):
            translator = QTranslator()
            if translator.load(path):
                QCoreApplication.installTranslator(translator)
                _translator = translator
            return


def uninstall():
    global _translator
    if _translator is not None:
        QCoreApplication.removeTranslator(_translator)
        _translator = None
