"""MP4 video of an animation, made with ffmpeg when it's on the computer. QGIS has no video
encoder of its own; Sous Chef doesn't bundle one (it would make the plugin many times bigger).
The frames are made exactly as for the GIF and streamed to ffmpeg one at a time, each repeated
for as long as it shows, so the timing matches the GIF; but a video has every colour, not 256."""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404: runs only ffmpeg, with a fixed list of arguments and no shell
import sys
import tempfile

from qgis.core import QgsApplication, QgsSettings
from qgis.PyQt.QtGui import QImage

KEY = "souschef/ffmpeg"
_ENCODER = {}


def _candidates():
    yield (QgsSettings().value(KEY, "") or "").strip()
    yield shutil.which("ffmpeg") or ""
    exe = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
    prefix = QgsApplication.prefixPath() or ""
    for folder in (os.path.join(prefix, "bin"), os.path.join(prefix, "..", "bin"), os.path.join(prefix, "..", "..", "bin")):
        yield os.path.join(folder, exe)
    if sys.platform.startswith("win"):
        for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:"):
            yield os.path.join(root, "ffmpeg", "bin", exe)
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            yield os.path.join(local, "Microsoft", "WinGet", "Links", exe)
    else:
        for folder in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/opt/local/bin"):
            yield os.path.join(folder, exe)


def find_ffmpeg() -> str:
    """The ffmpeg program to use: the one set in Sous Chef's settings, else one on the PATH or in
    a usual place (QGIS's own bin folder, Homebrew…); "" when there is none."""
    for path in _candidates():
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return os.path.normpath(path)
    return ""


def _quiet():
    """No console window popping up on Windows."""
    return {"creationflags": 0x08000000} if sys.platform.startswith("win") else {}


def encoder(ffmpeg: str) -> str:
    """The best H.264 encoder this ffmpeg has (some builds leave libx264 out), else MPEG-4."""
    if ffmpeg in _ENCODER:
        return _ENCODER[ffmpeg]
    try:
        listed = subprocess.run([ffmpeg, "-hide_banner", "-encoders"],  # nosec B603: ffmpeg, checked, no shell
                                capture_output=True, text=True, timeout=20, **_quiet()).stdout
    except (OSError, subprocess.SubprocessError):
        listed = ""
    names = {line.split()[1] for line in listed.splitlines() if len(line.split()) > 1 and line.startswith(" V")}
    choice = next((e for e in ("libx264", "libopenh264", "h264_mf", "h264_videotoolbox", "mpeg4") if e in names),
                  "libx264")
    _ENCODER[ffmpeg] = choice
    return choice


def write_mp4(controller, opts, path: str, progress=None, ffmpeg: str = "") -> int:
    """Makes the animation and writes it as an MP4 video; returns the number of frames made.
    ``progress(done, total)`` returns False to stop (the unfinished file is removed)."""
    from .animation import Cancelled, make
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg:
        raise OSError("ffmpeg wasn't found. Install it, or set where it is in Settings › Options › Sous Chef.")
    anim = make(controller, opts)
    fps = max(int(opts.fps), 10)
    codec = encoder(ffmpeg)
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{anim.out_W}x{anim.out_H}", "-r", str(fps), "-i", "-",
               "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", codec, "-pix_fmt", "yuv420p"]
    if codec == "libx264":
        command += ["-crf", "20", "-preset", "medium", "-tune", "animation"]
    elif codec == "mpeg4":
        command += ["-q:v", "3"]
    else:
        command += ["-b:v", "4M"]
    command += ["-movflags", "+faststart", path]
    log = tempfile.TemporaryFile()
    process = subprocess.Popen(command,  # nosec B603: ffmpeg (an executable file found or set by the user), no shell
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log, **_quiet())
    total, done, written, clock = anim.estimate(), 0, 0, 0.0
    ok = False
    try:
        for image, delay in anim.frames():
            if image.width() != anim.out_W or image.height() != anim.out_H:
                image = image.scaled(anim.out_W, anim.out_H)
            frame = image.convertToFormat(QImage.Format.Format_RGB32)   # bytes in B, G, R, A order
            bits = frame.constBits()
            bits.setsize(frame.sizeInBytes())
            data = bytes(bits)
            clock += delay / 100.0   # GIF delays are in hundredths of a second
            repeat = max(int(round(clock * fps)) - written, 0)
            for _k in range(repeat):
                process.stdin.write(data)
            written += repeat
            done += 1
            if progress is not None and progress(done, max(total, done + 1)) is False:
                raise Cancelled()
        ok = True
    except BrokenPipeError:
        pass   # ffmpeg stopped: its message is reported below
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        code = process.wait()
        log.seek(0)
        message = log.read().decode("utf-8", "replace").strip()
        log.close()
        if not ok or code != 0:
            try:
                os.remove(path)
            except OSError:
                pass
    if code != 0:
        raise OSError(f"ffmpeg couldn't make the video: {message.splitlines()[-1] if message else code}")
    return done


def estimate_kb(seconds: float, width: int, height: int) -> float:
    """A rough size: these drawings are mostly flat colour, which compresses very well."""
    return max(seconds, 1.0) * width * height / 921600 * 120
