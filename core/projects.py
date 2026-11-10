"""Project names for messages and review notes."""

from __future__ import annotations

import os


def project_label(path: str | None) -> str:
    if path is None:
        return "unknown project"
    if not path:
        return "an unsaved project"
    return os.path.basename(path)
