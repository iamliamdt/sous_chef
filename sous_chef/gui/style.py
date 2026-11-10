"""Colours shared by the review window's views, in a light and a dark version that follow
QGIS's theme. Images saved from the window always use the light colours."""

from contextlib import contextmanager


INK = "#1F1F1F"
MUTED = "#5C5C5C"
LAYER = {"bg": "#FFF4C7", "border": "#B8901C", "tag": "#8A6A0A", "text": "#6B4E00"}
SETTING = {"bg": "#EFE7FA", "border": "#7A4FB5", "tag": "#6A40A8", "text": "#4B2A7A"}
OUTPUT = {"bg": "#DFF0D0", "border": "#4E8A2E", "text": "#2F6A1A"}
STEP = {"bg": "#FFFFFF", "border": "#7A7F85"}
LEFT_OUT = {"bg": "#F2F2F2", "border": "#8A8A8A"}
WARN = {"bg": "#FDEBD3", "text": "#6B3A00"}
INFO = {"bg": "#E3ECF7", "text": "#1F4E89"}
SELECT = "#1F4E89"
ACCENT = "#3F6F1F"
PAGE = "#FFFFFF"           # side panel and run-dialog pages
CANVAS = "#FAFAFA"         # flowchart background
LINE = "#5C6670"           # flowchart lines
QUIET = "#8A8A8A"          # default settings, things not saved
SHADOW = "#ECECEC"         # the stacked look of a group
FRAME = {"bg": "#F4F4F4", "border": "#A8A8A8", "title": "#DCDCDC", "side": "#F7F7F7", "rule": "#D6D6D6",
         "field": "#FFFFFF"}

LIGHT = {k: v for k, v in dict(globals()).items() if k.isupper()}
DARK = dict(
    INK="#E8EAED", MUTED="#A9AFB6",
    LAYER={"bg": "#4A3E12", "border": "#C9A23A", "tag": "#9C7A16", "text": "#F3DC8A"},
    SETTING={"bg": "#3A2E52", "border": "#A782E0", "tag": "#6A40A8", "text": "#DCCAF8"},
    OUTPUT={"bg": "#24401B", "border": "#6FB04A", "text": "#BFE3A8"},
    STEP={"bg": "#2C3034", "border": "#8A9096"}, LEFT_OUT={"bg": "#33363A", "border": "#80858B"},
    WARN={"bg": "#4A3216", "text": "#F6C98F"}, INFO={"bg": "#1F3350", "text": "#B4CEF2"},
    SELECT="#7FB0F0", ACCENT="#5E9E3A", PAGE="#25282C", CANVAS="#1E2124", LINE="#9AA3AD", QUIET="#8D9399",
    SHADOW="#3A3E43", FRAME={"bg": "#2B2E32", "border": "#5A6066", "title": "#3A3E43", "side": "#2F3337",
                             "rule": "#4A4F55", "field": "#1E2124"})


def is_dark() -> bool:
    """QGIS is using a dark theme (its window colour is dark)."""
    try:
        from qgis.PyQt.QtGui import QPalette
        from qgis.PyQt.QtWidgets import QApplication
        return QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
    except Exception:
        return False


def apply_theme(dark=None):
    """Use the dark colours when QGIS has a dark theme (or as asked)."""
    globals().update(DARK if (is_dark() if dark is None else dark) else LIGHT)


@contextmanager
def light():
    """Light colours for a while (images saved from the window), then back."""
    before = {k: globals()[k] for k in LIGHT}
    globals().update(LIGHT)
    try:
        yield
    finally:
        globals().update(before)


def tag_html(kind: str) -> str:
    colour = LAYER["tag"] if kind == "LAYER" else SETTING["tag"]
    return (f'<span style="background-color:{colour}; color:#FFFFFF; font-size:9px; font-weight:bold;">'
            f'&nbsp;{kind}&nbsp;</span>')


def chip_css(kind: str) -> str:
    c = LAYER if kind == "layer" else SETTING
    return (f"QPushButton {{ color: {c['text']}; background: {c['bg']}; border: 1px solid {c['border']}; "
            f"border-radius: 11px; padding: 2px 10px; font-weight: bold; }}")
