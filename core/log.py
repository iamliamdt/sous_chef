"""Errors Sous Chef recovered from by itself, noted in QGIS's Log Messages panel (the "Sous Chef"
tab), so they can be found later, for example for a problem report, without interrupting work."""

from __future__ import annotations

import os

from qgis.core import Qgis, QgsMessageLog

TAG = "Sous Chef"


def ignored(error: BaseException) -> None:
    """Note an error that was safe to carry on after, with where it happened."""
    trace = error.__traceback__
    while trace is not None and trace.tb_next is not None:
        trace = trace.tb_next
    where = f"{os.path.basename(trace.tb_frame.f_code.co_filename)}:{trace.tb_lineno} " if trace is not None else ""
    QgsMessageLog.logMessage(f"{where}{type(error).__name__}: {error}", TAG, Qgis.MessageLevel.Info)
