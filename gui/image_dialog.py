"""Turning images and pages into files: SVG backgrounds, PNG pictures, PDF images and PDF
pages. Used by the Export window."""

from __future__ import annotations

import re

from qgis.PyQt.QtCore import QByteArray, QMarginsF, QRectF, QSizeF, Qt, QUrl
from qgis.PyQt.QtGui import QColor, QImage, QPageSize, QPainter, QPdfWriter, QTextDocument
from qgis.PyQt.QtSvg import QSvgRenderer

DETAILS = [("general", "General workflow: tools and kinds of data"),
           ("named", "This run: named layers and steps"),
           ("specific", "This run: also settings that differ from the defaults")]
FORMATS = [("svg", "SVG image"), ("png", "PNG image, opaque background"),
           ("png-clear", "PNG image, transparent background"), ("pdf", "PDF image"),
           ("cookbook-pdf", "Cookbook recipe (fun): PDF")]
EXTENSIONS = {"svg": ".svg", "png": ".png", "png-clear": ".png", "pdf": ".pdf", "cookbook-pdf": ".pdf"}
MAP_FORMATS = ("cookbook-pdf",)
KEY = "souschef/image/"


# ------------------------------------------------------------ converters

def with_background(svg: str, background) -> str:
    """The SVG with its background colour (None: transparent)."""
    svg = re.sub(r'<rect class="background"[^>]*/>', "", svg, count=1)
    if background is None:
        return svg
    size = _size(svg)
    rect = (f'<rect class="background" x="0" y="0" width="{size.width():.0f}" height="{size.height():.0f}" '
            f'fill="{background}"/>')
    end = svg.index(">", svg.index("<svg")) + 1
    return svg[:end] + rect + svg[end:]


def to_slide(svg: str) -> str:
    """The drawing centred on a 16:9 page (for presentations), the space around it filled
    with the drawing's own background."""
    size = _size(svg)
    w, h = size.width(), size.height()
    if w <= 0 or h <= 0:
        return svg
    if w / h > 16 / 9:
        new_w, new_h = w, w * 9 / 16
    else:
        new_w, new_h = h * 16 / 9, h
    pad_x, pad_y = (new_w - w) / 2, (new_h - h) / 2
    bg = re.search(r'<rect class="background"[^>]*fill="([^"]+)"', svg)
    start = svg.index(">", svg.index("<svg")) + 1
    end = svg.rindex("</svg>")
    head = re.sub(r'\swidth="[^"]*"', f' width="{new_w:.0f}"', svg[:start], count=1)
    head = re.sub(r'\sheight="[^"]*"', f' height="{new_h:.0f}"', head, count=1)
    head = re.sub(r'viewBox="[^"]*"', f'viewBox="0 0 {new_w:.0f} {new_h:.0f}"', head, count=1)
    page = (f'<rect class="background" x="0" y="0" width="{new_w:.0f}" height="{new_h:.0f}" fill="{bg.group(1)}"/>'
            if bg else "")
    return head + page + f'<g transform="translate({pad_x:.1f},{pad_y:.1f})">' + svg[start:end] + "</g></svg>"


def _size(svg: str) -> QSizeF:
    m = re.search(r'viewBox="\s*[-\d.]+[ ,]+[-\d.]+[ ,]+([\d.]+)[ ,]+([\d.]+)', svg)
    if m:
        return QSizeF(float(m.group(1)), float(m.group(2)))
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    return QSizeF(renderer.defaultSize())


def to_image(svg: str, scale: float = 1.0) -> QImage:
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    size = _size(svg)
    image = QImage(max(int(size.width() * scale), 1), max(int(size.height() * scale), 1),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    image.setDotsPerMeterX(int(3780 * scale))   # 96 dpi × scale, so it prints at its size
    image.setDotsPerMeterY(int(3780 * scale))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter, QRectF(0, 0, image.width(), image.height()))
    painter.end()
    return image


def save_pdf(svg, path: str):
    """A PDF the size of the image; text and lines stay sharp at any zoom. ``svg`` can be a
    list: one page each."""
    pages = svg if isinstance(svg, (list, tuple)) else [svg]
    size = _size(pages[0])
    writer = QPdfWriter(path)
    writer.setResolution(72)
    writer.setPageSize(QPageSize(size, QPageSize.Unit.Point))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))
    painter = QPainter(writer)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for k, page in enumerate(pages):
        if k:
            size = _size(page)
            writer.setPageSize(QPageSize(size, QPageSize.Unit.Point))
            writer.newPage()
        QSvgRenderer(QByteArray(page.encode("utf-8"))).render(painter, QRectF(0, 0, size.width(), size.height()))
    painter.end()


def recipe_pdf(html: str, path: str, images: dict | None = None):
    """``images``: {resource name used in the page: QImage}."""
    document = QTextDocument()
    for name, image in (images or {}).items():
        document.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), image)
    document.setHtml(html)
    writer = QPdfWriter(path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(18, 18, 18, 18))
    printer = getattr(document, "print", None) or getattr(document, "print_")
    printer(writer)


def _on_checkerboard(image: QImage) -> QImage:
    out = QImage(image.size(), QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(out)
    tile = 8
    for y in range(0, out.height(), tile):
        for x in range(0, out.width(), tile):
            painter.fillRect(x, y, tile, tile, QColor("#FFFFFF" if (x // tile + y // tile) % 2 else "#E4E4E4"))
    painter.drawImage(0, 0, image)
    painter.end()
    return out
