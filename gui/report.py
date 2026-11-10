"""Report a problem: a short report the user can read and change, then send as a GitHub issue
(opened in their browser, filled in) or copy. Nothing is sent by Sous Chef itself, and no
layers or data go into it: only versions, the last error and what was compared when tracing."""

from __future__ import annotations

import os
import platform
import traceback
from urllib.parse import quote

from qgis.core import Qgis
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)

from ..i18n import tr

_LAST = {"error": "", "where": ""}
URL_LIMIT = 6000   # longer reports are copied instead: browsers cut very long addresses


def remember(where: str, error) -> None:
    """Note an error Sous Chef showed, so a report can include it."""
    if isinstance(error, BaseException):
        text = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    else:
        text = str(error)
    _LAST.update(error=text.strip(), where=where)


def _metadata() -> dict:
    values = {}
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "metadata.txt")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if "=" in line and not line.startswith((" ", "\t")):
                    key, _sep, value = line.partition("=")
                    values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


def issues_url() -> str:
    """The plugin's GitHub issues page from metadata.txt; "" while it's still a placeholder."""
    url = _metadata().get("tracker", "")
    return url if url.startswith("https://github.com/") and "YOUR-ACCOUNT" not in url else ""


def report_text(details: str = "") -> str:
    meta = _metadata()
    lines = ["**What happened**", "", "(What were you doing, and what did you expect?)", "",
             "**About this computer**", "",
             f"- Sous Chef {meta.get('version', '?')}",
             f"- QGIS {Qgis.version()}",
             f"- {platform.system()} {platform.release()} ({platform.machine()})", ""]
    if _LAST["error"]:
        lines += [f"**Last error** ({_LAST['where']})", "", "```", _LAST["error"][-3000:], "```", ""]
    if details:
        lines += ["**Details**", "", "```", details.strip()[-3000:], "```", ""]
    return "\n".join(lines)


class ReportDialog(QDialog):
    def __init__(self, parent=None, details: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("Report a Problem"))
        self.resize(640, 520)
        lay = QVBoxLayout(self)
        intro = QLabel(tr("Describe what happened at the top. The rest is filled in for you: versions and the last "
                          "error. No layers or data are included. Nothing is sent until you choose to send it."))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.title = QLineEdit()
        self.title.setPlaceholderText(tr("A short title, e.g. “Taste test fails after a Field Calculator step”"))
        lay.addWidget(self.title)
        self.text = QPlainTextEdit(report_text(details))
        lay.addWidget(self.text, 1)
        buttons = QDialogButtonBox()
        self.url = issues_url()
        if self.url:
            send = buttons.addButton(tr("Open as a GitHub Issue…"), QDialogButtonBox.ButtonRole.AcceptRole)
            send.setToolTip(tr("Opens the plugin's issue page in your browser, filled in. You can still change it "
                               "there before sending."))
            send.clicked.connect(self._open_issue)
        copy = buttons.addButton(tr("Copy"), QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(self._copy)
        buttons.addButton(QDialogButtonBox.StandardButton.Close).clicked.connect(self.reject)
        for button in buttons.buttons():
            button.setAutoDefault(False)
        lay.addWidget(buttons)
        self.note = QLabel("" if self.url else tr("Copy the report and send it to the plugin's author (see "
                                                  "Plugins › Manage and Install Plugins › Sous Chef)."))
        self.note.setWordWrap(True)
        lay.addWidget(self.note)

    def _copy(self):
        QApplication.clipboard().setText((self.title.text().strip() + "\n\n" if self.title.text().strip() else "")
                                         + self.text.toPlainText())
        self.note.setText(tr("Copied."))

    def _open_issue(self):
        title = self.title.text().strip() or tr("Problem report")
        body = self.text.toPlainText()
        if len(body) > URL_LIMIT:   # too long for a web address: open the page and copy the text
            self._copy()
            body = tr("(The report was copied: paste it here.)")
            self.note.setText(tr("The report was too long to fill in, so it was copied: paste it into the issue."))
        url = f"{self.url.rstrip('/')}/new?title={quote(title)}&body={quote(body)}"
        QDesktopServices.openUrl(QUrl(url))


def show(parent=None, details: str = ""):
    ReportDialog(parent, details).exec()
