"""Run dialog view: how the model's window will look in the Processing Toolbox.

With "Edit text" on, names, help texts, the description and the model name can be
edited right here."""

from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import style
from ..i18n import tr


class RunDialogView(QScrollArea):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

    def rebuild(self):
        c = self.controller
        editing = c.editing
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(12, 10, 12, 10)

        top = QHBoxLayout()
        hint = QLabel(tr("The recipe's window in the Processing Toolbox, as the person running it will see it."))
        hint.setStyleSheet(f"color: {style.MUTED};")
        hint.setWordWrap(True)
        top.addWidget(hint, 1)
        toggle = QPushButton("Done editing" if editing else "Edit text")
        toggle.setCheckable(True)
        toggle.setChecked(editing)
        toggle.toggled.connect(c.set_editing)
        top.addWidget(toggle)
        outer.addLayout(top)

        frame = QFrame()
        frame.setObjectName("runframe")
        frame.setStyleSheet(f"#runframe {{ border: 1px solid {style.FRAME['border']}; border-radius: 4px; "
                            f"background: {style.FRAME['bg']}; }} #runframe QLabel {{ color: {style.INK}; }}")
        box = QVBoxLayout(frame)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        title_bar = QWidget()
        title_bar.setStyleSheet(f"background: {style.FRAME['title']};")
        tl = QHBoxLayout(title_bar)
        tl.setContentsMargins(10, 6, 10, 6)
        if editing:
            title = QLineEdit(c.model_name)
            title.setAccessibleName("Recipe name")
            title.editingFinished.connect(lambda w=title: c.set_model_name(w.text()))
            tl.addWidget(title)
        else:
            label = QLabel(f"<b>{_esc(c.model_name)}</b>")
            tl.addWidget(label)
        box.addWidget(title_bar)

        body = QHBoxLayout()
        body.setSpacing(0)
        params = QWidget()
        params.setStyleSheet(f"background: {style.PAGE};")
        pl = QVBoxLayout(params)
        pl.setContentsMargins(12, 10, 12, 10)
        pl.setSpacing(10)
        head = QLabel(tr("PARAMETERS"))
        head.setStyleSheet(f"color: {style.MUTED}; font-weight: bold; font-size: 10px;")
        pl.addWidget(head)
        for kind, uid, name, _detail in c.enter_items():
            pl.addWidget(self._parameter(kind, uid, name, editing))
        if c.plan.outputs:
            head = QLabel(tr("<b>Outputs</b>"))
            head.setStyleSheet(f"font-size: 13px; padding-top: 8px; border-top: 1px solid {style.FRAME['rule']};")
            pl.addWidget(head)
        for step_plan, param in c.plan.outputs:
            pl.addWidget(self._output(param, editing))
        pl.addStretch(1)
        body.addWidget(params, 3)

        desc = QWidget()
        desc.setStyleSheet(f"background: {style.FRAME['side']};")
        dl = QVBoxLayout(desc)
        dl.setContentsMargins(12, 10, 12, 10)
        head = QLabel(tr("DESCRIPTION"))
        head.setStyleSheet(f"color: {style.MUTED}; font-weight: bold; font-size: 10px;")
        dl.addWidget(head)
        dl.addWidget(QLabel(f"<b>{_esc(c.model_name)}</b>"))
        text = c.plan.description
        if editing:
            editor = QPlainTextEdit(c.choices.description)
            editor.setPlaceholderText(tr("Explain what the recipe does and what to prepare before running it"))
            editor.setAccessibleName("Recipe description")
            editor.textChanged.connect(lambda e=editor: c.set_description(e.toPlainText()))
            dl.addWidget(editor, 1)
            notes = text[len(c.choices.description):].strip()
            if notes:
                extra = QLabel(_esc(notes).replace("\n", "<br>"))
                extra.setWordWrap(True)
                extra.setStyleSheet(f"color: {style.MUTED};")
                dl.addWidget(extra)
        else:
            label = QLabel(_esc(text).replace("\n", "<br>") if text else
                           "<i>No description yet. Switch on “Edit text” to add one.</i>")
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignTop)
            dl.addWidget(label, 1)
        body.addWidget(desc, 2)
        box.addLayout(body)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(10, 8, 10, 8)
        buttons.addStretch(1)
        for text in ("Close", "Run"):
            fake = QLabel(text)
            fake.setStyleSheet(f"border: 1px solid {style.FRAME['border']}; border-radius: 4px; padding: 4px 14px; "
                               f"background: {style.FRAME['side']};")
            buttons.addWidget(fake)
        box.addLayout(buttons)
        outer.addWidget(frame)
        outer.addStretch(1)
        old = self.takeWidget()
        self.setWidget(page)
        if old is not None:
            old.hide()
            old.deleteLater()   # not at once: one of its widgets may still be handling a click

    def _parameter(self, kind, uid, name, editing):
        colours = style.LAYER if kind == "layer" else style.SETTING
        w = QFrame()
        w.setStyleSheet(f"QFrame#p {{ border-left: 3px solid {colours['border']}; }}")
        w.setObjectName("p")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 0, 0, 0)
        lay.setSpacing(3)
        help_text = self._help(kind, uid)
        if editing:
            edit = QLineEdit(name)
            edit.setAccessibleName(f"Name of {name}")
            edit.editingFinished.connect(lambda e=edit, u=uid: self.controller.set_name(u, e.text(), quiet=True))
            lay.addWidget(edit)
        else:
            button = QPushButton(name)
            button.setFlat(True)
            button.setStyleSheet("text-align: left; font-weight: bold; padding: 0;")
            button.clicked.connect(lambda _c=False, u=uid, k=kind: self.controller.select(f"input:{u}" if k == "layer" else u))
            lay.addWidget(button)
        if kind == "layer":
            input_plan = self.controller.plan.inputs[uid]
            field = f"Choose one of the {input_plan.accepts}…  ▾"
        else:
            _sp, param = self.controller.param_by_uid(uid)
            field = f"{param.shown_value}  ⇅" if param else ""
        mock = QLabel(field)
        mock.setStyleSheet(f"background: {style.FRAME['field']}; border: 1px solid {style.FRAME['border']}; "
                           "border-radius: 3px; padding: 3px 6px;")
        lay.addWidget(mock)
        if editing:
            editor = QPlainTextEdit(help_text)
            editor.setPlaceholderText(tr("Add help text for this input"))
            editor.setFixedHeight(48)
            editor.setAccessibleName(f"Help text for {name}")
            editor.textChanged.connect(lambda e=editor, u=uid: self.controller.set_help(u, e.toPlainText(), quiet=True))
            lay.addWidget(editor)
        elif help_text:
            hl = QLabel(_esc(help_text))
            hl.setWordWrap(True)
            hl.setStyleSheet(f"color: {style.MUTED}; font-size: 11px;")
            lay.addWidget(hl)
        return w

    def _help(self, kind, uid):
        if kind == "layer":
            return self.controller.plan.inputs[uid].help
        _sp, param = self.controller.param_by_uid(uid)
        return param.help if param else ""

    def _output(self, param, editing):
        w = QFrame()
        w.setObjectName("o")
        w.setStyleSheet(f"QFrame#o {{ border-left: 3px solid {style.OUTPUT['border']}; }}")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 0, 0, 0)
        lay.setSpacing(3)
        if editing:
            edit = QLineEdit(param.display)
            edit.setAccessibleName("Output name")
            edit.editingFinished.connect(lambda e=edit, u=param.uid: self.controller.set_name(u, e.text(), quiet=True))
            lay.addWidget(edit)
        else:   # click to select it in the flowchart and side panel, where its name can be changed
            button = QPushButton(param.display)
            button.setFlat(True)
            button.setStyleSheet("text-align: left; font-weight: bold; padding: 0;")
            button.setToolTip(tr("Click to see this result in the side panel, or use “Edit text” to rename it here"))
            button.clicked.connect(lambda _c=False, u=param.uid: self.controller.select(u))
            lay.addWidget(button)
        mock = QLabel(tr("[Create temporary layer]"))
        mock.setStyleSheet(f"background: {style.FRAME['field']}; border: 1px solid {style.FRAME['border']}; "
                           "border-radius: 3px; padding: 3px 6px; "
                           f"color: {style.MUTED};")
        lay.addWidget(mock)
        return w


def _esc(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
