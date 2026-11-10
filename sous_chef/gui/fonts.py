"""Fonts for the images, animations and recipes.

Each design names the fonts that suit it on Windows, macOS and Linux, and uses the first one
installed. Only the Pilgrimage's blackletter title font is bundled (sous_chef/fonts, SIL Open
Font License). The Export window can replace the title font and the text font of any design,
for other alphabets or for taste.
"""

from __future__ import annotations
from ..core import log

import base64
import os
import re

FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fonts")
SYSTEM = "'Segoe UI', 'Helvetica Neue', Helvetica, Arial, sans-serif"

SANS = ["Segoe UI", "Avenir Next", "Helvetica Neue", "Ubuntu", "Noto Sans", "DejaVu Sans", "Arial"]
LIGHT = ["Calibri", "Avenir Next", "Helvetica Neue", "Source Sans Pro", "Noto Sans", "DejaVu Sans", "Arial"]
CONDENSED = ["Bahnschrift", "DIN Condensed", "DIN Alternate", "Avenir Next Condensed", "Roboto Condensed",
             "Ubuntu Condensed", "DejaVu Sans Condensed", "Arial Narrow"]
HUMANIST = ["Candara", "Optima", "Avenir Next", "Segoe UI", "Noto Sans", "DejaVu Sans", "Arial"]
SERIF = ["Palatino Linotype", "Book Antiqua", "Palatino", "Hoefler Text", "URW Palladio L", "P052", "Georgia",
         "DejaVu Serif", "Times New Roman"]
MONO = ["Courier New", "Courier", "Nimbus Mono PS", "Liberation Mono", "DejaVu Sans Mono"]
BLACKLETTER = "UnifrakturMaguntia"

# image styles and animations -> the fonts that suit them (None: the system font)
FOR = {
    "boxes": None,
    "cards": SANS,
    "minimal": LIGHT,
    "metro": CONDENSED,
    "swimlanes": LIGHT,
    "data": SANS,
    "storyboard": HUMANIST,
    "summary": SANS,
    "mindmap": SANS,
    "river": CONDENSED,
    "board": ["Segoe Print", "Bradley Hand", "Chalkboard SE", "Comic Sans MS", "Noto Sans", "DejaVu Sans"],
    "comic": ["Comic Sans MS", "Chalkboard SE", "Comic Neue", "Segoe Print", "DejaVu Sans"],
    "kitchen": HUMANIST,
    "assembly": CONDENSED,
    "board_game": HUMANIST,
    "camera_tour": SANS,
    "pilgrimage": SERIF,
    "map_story": SANS,
    "map_metro": CONDENSED,
    "metro_ride": CONDENSED,
    "map_pilgrimage": SERIF,
    "before_after": SANS,
    "typewriter": MONO,
}
_FILES = {}     # bundled family -> [font files]
_LOADED = []
_INSTALLED = set()


def load():
    """Registers the bundled font with Qt (once)."""
    if _LOADED:
        return
    _LOADED.append(True)
    try:
        from qgis.PyQt.QtGui import QFontDatabase
    except ImportError:
        return
    for name in sorted(os.listdir(FOLDER)) if os.path.isdir(FOLDER) else []:
        if not name.lower().endswith((".ttf", ".otf")):
            continue
        path = os.path.join(FOLDER, name)
        index = QFontDatabase.addApplicationFont(path)
        for family in QFontDatabase.applicationFontFamilies(index) if index >= 0 else []:
            _FILES.setdefault(family, []).append(path)


def installed() -> set:
    if not _INSTALLED:
        load()
        try:
            from qgis.PyQt.QtGui import QFontDatabase
            try:
                _INSTALLED.update(QFontDatabase.families())          # Qt 6
            except TypeError:
                _INSTALLED.update(QFontDatabase().families())        # Qt 5
        except Exception as error:   # safe to carry on: noted in the log
            log.ignored(error)
    return _INSTALLED


def first_installed(candidates):
    """The first of ``candidates`` installed on this computer, or None."""
    have = installed()
    return next((f for f in candidates or [] if f in have), None)


def family_for(key, themed=True, override=""):
    """The font family for an image style or animation, or None for the system font.
    ``override``: a family the user chose in the Export window."""
    if override:
        return override
    if not themed:
        return None
    return first_installed(FOR.get(key))


def title_family(key, themed=True, override=""):
    """The title's font: the user's choice, the blackletter for the pilgrimage, otherwise
    the design's own font."""
    if override:
        return override
    if themed and key in ("pilgrimage", "map_pilgrimage"):
        load()
        return BLACKLETTER if BLACKLETTER in _FILES else first_installed(SERIF)
    return family_for(key, themed)


def css(family) -> str:
    """The font-family for an SVG: the bare family name. Qt's SVG renderer doesn't find a
    family of several words when it's part of a list."""
    return family if family else SYSTEM


def embed(svg: str) -> str:
    """The SVG with the bundled font written into it (@font-face) when it uses it, so it looks
    the same on computers that don't have it. Only for saved SVG files: Qt ignores it."""
    load()
    used = [f for f in _FILES if f'font-family="{f}' in svg or f"'{f}'" in svg]
    if not used:
        return svg
    rules = []
    for family in used:
        for path in _FILES[family]:
            with open(path, "rb") as fh:
                data = base64.b64encode(fh.read()).decode()
            rules.append(f"@font-face {{ font-family: '{family}'; src: url(data:font/ttf;base64,{data}) "
                         "format('truetype'); }")
    style = "<defs><style>" + " ".join(rules) + "</style></defs>"
    tag = re.search(r"<svg\b[^>]*>", svg)
    return svg[:tag.end()] + style + svg[tag.end():] if tag else svg
