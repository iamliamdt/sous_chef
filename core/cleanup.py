"""Temporary files Sous Chef makes, and removing them.

Kept on purpose: models, their styles folders, symbols and .bak backups in the
models folder; images the user saves.
"""

from __future__ import annotations

import glob
import os
import shutil
import tempfile

from qgis.core import QgsApplication

TEST_RUN_PREFIX = "sous_chef_test_"


def plugin_folder() -> str:
    return os.path.join(QgsApplication.qgisSettingsDirPath(), "sous_chef")


def symbols_folder() -> str:
    return os.path.join(plugin_folder(), "symbols")


def remove_test_runs() -> int:
    """Folders left by Taste Test (the recipe and its results)."""
    count = 0
    for folder in glob.glob(os.path.join(tempfile.gettempdir(), TEST_RUN_PREFIX + "*")):
        shutil.rmtree(folder, ignore_errors=True)
        count += 1
    return count


def remove_symbol_copies(keep=()) -> int:
    """Coloured copies of QGIS symbols. Saved models keep their own copy, so only the
    one chosen in an open review window (``keep``) is still needed."""
    keep = {os.path.abspath(k) for k in keep if k}
    count = 0
    for path in glob.glob(os.path.join(symbols_folder(), "*.svg")):
        if os.path.abspath(path) not in keep:
            try:
                os.remove(path)
                count += 1
            except OSError:
                pass
    return count


def at_startup() -> None:
    remove_test_runs()
    remove_symbol_copies()
