"""A small animated-GIF writer: one shared palette, only the changed part of each frame stored,
unchanged pixels transparent, and frames that don't change merged into a longer delay."""
import struct

import numpy as np
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QImage, qRgb

TRANSPARENT = 255


def _median_cut(pixels: np.ndarray, count: int) -> list:
    """``count`` colours for an (N, 3) array of RGB pixels."""
    boxes = [pixels]
    while len(boxes) < count:
        i = max(range(len(boxes)), key=lambda j: (len(boxes[j]) > 1) * len(boxes[j])
                * float(np.ptp(boxes[j], axis=0).max() if len(boxes[j]) else 0))
        box = boxes[i]
        if len(box) < 2:
            break
        channel = int(np.ptp(box, axis=0).argmax())
        box = box[box[:, channel].argsort()]
        half = len(box) // 2
        boxes[i:i + 1] = [box[:half], box[half:]]
    return [tuple(int(v) for v in b.mean(axis=0)) for b in boxes if len(b)]


def palette_for(images, count=255) -> list:
    samples = []
    for image in images:
        a = _array(image.convertToFormat(QImage.Format.Format_RGB32))
        rgb = a[:, :, [2, 1, 0]].reshape(-1, 3)
        samples.append(rgb[:: max(1, len(rgb) // 60000)])
    pixels = np.concatenate(samples).astype(np.int32)
    # flat areas (backgrounds, fills) keep their exact colours
    keys, counts = np.unique(pixels, axis=0, return_counts=True)
    common = keys[np.argsort(-counts)][: count // 3]
    common = common[counts[np.argsort(-counts)][: count // 3] >= max(len(pixels) * 0.002, 4)]
    # the rest is shared out over every distinct shade, however small its area, so a small
    # coloured chip keeps its colour rather than being swamped by the map
    shades = np.unique(pixels >> 3, axis=0) * 8 + 4
    rest = _median_cut(shades, count - len(common)) if count > len(common) else []
    return [tuple(int(v) for v in c) for c in common] + rest


def _array(image: QImage) -> np.ndarray:
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    a = np.frombuffer(ptr, np.uint8).reshape(image.height(), image.bytesPerLine())
    if image.format() == QImage.Format.Format_Indexed8:
        return a[:, : image.width()].copy()
    return a[:, : image.width() * 4].reshape(image.height(), image.width(), 4).copy()


def _lzw(data: bytes, min_size=8) -> bytes:
    clear, end = 1 << min_size, (1 << min_size) + 1
    out, bits, nbits = bytearray(), 0, 0
    size, next_code, table = min_size + 1, end + 1, {}

    def emit(code):
        nonlocal bits, nbits
        bits |= code << nbits
        nbits += size
        while nbits >= 8:
            out.append(bits & 0xFF)
            bits >>= 8
            nbits -= 8

    emit(clear)
    it = iter(data)
    w = next(it, None)
    if w is None:
        emit(end)
    else:
        for c in it:
            key = (w << 8) | c
            code = table.get(key)
            if code is not None:
                w = code
                continue
            emit(w)
            if next_code < 4096:
                table[key] = next_code
                next_code += 1
                if next_code > (1 << size) and size < 12:
                    size += 1
            else:
                emit(clear)
                table, size, next_code = {}, min_size + 1, end + 1
            w = c
        emit(w)
        emit(end)
    if nbits:
        out.append(bits & 0xFF)
    blocks = bytearray([min_size])
    for i in range(0, len(out), 255):
        chunk = out[i:i + 255]
        blocks += bytes([len(chunk)]) + chunk
    return bytes(blocks + b"\x00")


class GifWriter:
    def __init__(self, path, width, height, palette, loop=True):
        self.f = open(path, "wb")
        self.w, self.h = width, height
        colours = list(palette)[:255] + [(0, 0, 0)] * (256 - min(len(palette), 255))
        self.table = [qRgb(*c) for c in colours[:255]]
        self.f.write(b"GIF89a" + struct.pack("<HHBBB", width, height, 0xF7, 0, 0))
        self.f.write(bytes(v for c in colours for v in c))
        if loop:
            self.f.write(b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00")
        self.prev, self.pending = None, None

    def _index(self, image: QImage) -> np.ndarray:
        indexed = image.convertToFormat(QImage.Format.Format_Indexed8, self.table,
                                        Qt.ImageConversionFlag.ThresholdDither | Qt.ImageConversionFlag.AvoidDither)
        return _array(indexed)

    def add(self, image: QImage, delay_cs: int):
        idx = self._index(image)
        if self.prev is None:
            self._flush()
            self.pending = [(0, 0, idx), delay_cs]
            self.prev = idx
            return
        changed = idx != self.prev
        if not changed.any():
            self.pending[1] += delay_cs
            return
        ys, xs = np.nonzero(changed)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        sub = idx[y0:y1, x0:x1].copy()
        sub[~changed[y0:y1, x0:x1]] = TRANSPARENT
        self._flush()
        self.pending = [(int(x0), int(y0), sub), delay_cs]
        self.prev = idx

    def _flush(self):
        if not self.pending:
            return
        (x, y, sub), delay = self.pending
        self.f.write(b"\x21\xF9\x04" + struct.pack("<BHBB", 0x05, min(delay, 65535), TRANSPARENT, 0))
        self.f.write(b"\x2C" + struct.pack("<HHHHB", x, y, sub.shape[1], sub.shape[0], 0))
        self.f.write(_lzw(sub.tobytes()))
        self.pending = None

    def close(self):
        self._flush()
        self.f.write(b"\x3B")
        self.f.close()
