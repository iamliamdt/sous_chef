"""Side panel of the review window: details of whatever is selected."""

from __future__ import annotations

import os

from qgis.core import QgsProject
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core import builder
from ..core.projects import project_label
from . import style
from ..i18n import tr

ROLES = [("final", "Final output: added to the project"),
         ("intermediate", "Intermediate: not kept"),
         ("save", "Intermediate: also kept and added to the project")]


class SidePanel(QScrollArea):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumWidth(330)

    # ------------------------------------------------------------ helpers

    def _page(self, kind, title, sub="", kind_colour=None):
        kind_colour = kind_colour or style.MUTED
        page = QWidget()
        page.setObjectName("panelpage")
        # only the page itself is white: an unscoped rule would also reach drop-down lists,
        # where (on Windows) the highlighted entry then becomes white text on white
        page.setStyleSheet(f"QWidget#panelpage {{ background: {style.PAGE}; }} QWidget#panelpage QLabel {{ color: {style.INK}; }}"
                           "QComboBox QAbstractItemView { background: palette(base); color: palette(text);"
                           " selection-background-color: palette(highlight); selection-color: palette(highlighted-text); }")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        k = QLabel(kind)
        k.setStyleSheet(f"color: {kind_colour}; font-weight: bold; font-size: 10px; letter-spacing: 1px;")
        lay.addWidget(k)
        t = QLabel(f"<b>{_esc(title)}</b>")
        t.setStyleSheet("font-size: 15px;")
        t.setWordWrap(True)
        lay.addWidget(t)
        if sub:
            s = QLabel(_esc(sub))
            s.setWordWrap(True)
            s.setStyleSheet(f"color: {style.MUTED};")
            lay.addWidget(s)
        return page, lay

    def _note(self, lay, text, colours=None):
        colours = colours or style.WARN
        label = QLabel(_esc(text))
        label.setWordWrap(True)
        label.setStyleSheet(f"background: {colours['bg']}; color: {colours['text']}; padding: 6px 8px; border-radius: 4px;")
        lay.addWidget(label)

    def _name_help(self, lay, uid, name, help_text, name_label="Name shown when the recipe runs"):
        lay.addWidget(QLabel(f"<b>{name_label}</b>"))
        edit = QLineEdit(name)
        edit.setAccessibleName(name_label)
        edit.editingFinished.connect(lambda e=edit: self.controller.set_name(uid, e.text()) if e.text().strip() != name else None)
        lay.addWidget(edit)
        lay.addWidget(QLabel(tr("<b>Help text</b>")))
        helper = QPlainTextEdit(help_text)
        helper.setPlaceholderText(tr("Explain what to choose, e.g. units or which layer"))
        helper.setFixedHeight(70)
        helper.setAccessibleName("Help text")
        helper.textChanged.connect(lambda h=helper: self.controller.set_help(uid, h.toPlainText(), quiet=True))
        lay.addWidget(helper)

    @staticmethod
    def _facts(lay, rows):
        """Short "name: value" lines that wrap to the panel's width."""
        html = "<br>".join(f"<b>{_esc(name)}:</b> {_esc(value)}" for name, value in rows if value)
        label = QLabel(html)
        label.setWordWrap(True)
        label.setStyleSheet("padding-top: 4px; line-height: 140%;")
        lay.addWidget(label)

    @staticmethod
    def _fit_width(page):
        """Nothing on the page may be wider than the panel: text wraps, drop-downs shrink."""
        for label in page.findChildren(QLabel):
            if label.objectName() == "value":   # a setting's value: its own width, up to half the panel
                label.setWordWrap(True)
                label.setMaximumWidth(170)
                continue
            if not isinstance(label, _Card):
                label.setWordWrap(True)
                label.setSizePolicy(QSizePolicy.Policy.Ignored, label.sizePolicy().verticalPolicy())
                label.setMinimumWidth(40)
        for combo in page.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)

    def _check(self, lay, text, checked, on_toggle):
        """A checkbox whose text wraps (a QCheckBox's own text is cut off in a narrow panel)."""
        row = QHBoxLayout()
        box = QCheckBox()
        box.setChecked(checked)
        box.setAccessibleName(text)
        box.toggled.connect(on_toggle)
        label = QLabel(_esc(text))
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.setMinimumWidth(80)
        label.mousePressEvent = lambda _e, b=box: b.click()
        row.addWidget(box, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(label, 1)
        lay.addLayout(row)
        return box

    def _link(self, lay, text, key, colours=None):
        """A clickable card whose text wraps to the panel's width."""
        first, _, rest = text.partition("\n")
        html = f"<b>{_esc(first)}</b>" + (f"<br>{_esc(rest)}" if rest else "")
        card = _Card(html, lambda k=key: self.controller.select(k))
        css = "padding: 6px 8px; border-radius: 4px;"
        if colours:
            css += (f" background: {colours['bg']}; border: 1px solid {colours['border']};"
                    f" border-left: 4px solid {colours['border']};")
        card.setStyleSheet(f"QLabel {{ {css} }} QLabel:hover {{ border-color: {style.SELECT}; }}")
        lay.addWidget(card)

    def _role_and_style(self, lay, pps):
        uids = [param.uid for param in pps]
        combo = QComboBox()
        for value, label in ROLES:
            combo.addItem(label, value)
        roles = {param.role for param in pps}
        if len(roles) == 1:
            combo.setCurrentIndex([r for r, _l in ROLES].index(roles.pop()))
        else:
            combo.insertItem(0, "(mixed)", "")
            combo.setCurrentIndex(0)
        combo.setAccessibleName("What to do with the result")
        combo.activated.connect(lambda _i, cb=combo: cb.currentData() and self.controller.set_role(uids, cb.currentData()))
        lay.addWidget(QLabel(tr("<b>What to do with the result</b>")))
        lay.addWidget(combo)
        styleable = [param for param in pps if param.style_layer_id]
        if styleable:
            on = all(param.keep_style for param in styleable)
            button = QPushButton("🖌  Keeps my style" if on else "🖌  Default style")
            button.setCheckable(True)
            button.setChecked(on)
            button.setToolTip(tr("Give the recipe's result the style of the layer you made"))
            button.toggled.connect(lambda checked, u=[p.uid for p in styleable]: self.controller.set_style(u, checked))
            lay.addWidget(button)
        elif len(pps) == 1:
            muted = QLabel(tr("No style to keep: the result layer is no longer in the project."))
            muted.setStyleSheet(f"color: {style.MUTED};")
            muted.setWordWrap(True)
            lay.addWidget(muted)

    # -------------------------------------------------------------- build

    def rebuild(self):
        c = self.controller
        key = c.selected or "overview"
        if c.multi:
            page = self._multi()
        elif key.startswith("input:"):
            page = self._input(key[6:])
        elif key.startswith("set:"):
            page = self._setting(key)
        elif key.startswith("out:"):
            page = self._output(key)
        elif key.startswith("step:"):
            page = self._step(int(key[5:]))
        elif key.startswith("group:"):
            page = self._group(int(key[6:]))
        elif key.startswith("edit:"):
            page = self._edit(int(key[5:]))
        elif key.startswith("ask:"):
            page = self._overview(only_settings_of=key[4:])
        else:
            page = self._overview()
        if page is None:
            page = self._overview()
        page.layout().addStretch(1)
        self._fit_width(page)
        old = self.takeWidget()
        self.setWidget(page)
        if old is not None:
            old.hide()
            old.deleteLater()   # not at once: one of its widgets may still be handling a click

    def _overview(self, only_settings_of=None):
        items = self.controller.enter_items()
        layers = [i for i in items if i[0] == "layer"]
        settings = [i for i in items if i[0] == "setting"]
        page, lay = self._page("INGREDIENTS: WHAT THE RECIPE ASKS FOR",
                               f"{len(layers)} layer{'s' if len(layers) != 1 else ''} and "
                               f"{len(settings)} setting{'s' if len(settings) != 1 else ''}",
                               "Click one to rename it or add help text.")
        if only_settings_of is None:
            head = QLabel(tr("LAYERS TO CHOOSE"))
            head.setStyleSheet(f"color: {style.LAYER['text']}; font-weight: bold; font-size: 10px;")
            lay.addWidget(head)
            for _k, uid, name, detail in layers:
                self._link(lay, f"{name}\n{detail}", f"input:{uid}", style.LAYER)
        head = QLabel(tr("SETTINGS TO ENTER"))
        head.setStyleSheet(f"color: {style.SETTING['text']}; font-weight: bold; font-size: 10px;")
        lay.addWidget(head)
        if not settings:
            hint = QLabel(tr("None yet. Select a step and choose “Ask when the recipe runs” for a setting."))
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {style.MUTED};")
            lay.addWidget(hint)
        for _k, uid, name, detail in settings:
            self._link(lay, f"{name}\n{detail}", uid, style.SETTING)
        if self.controller.plan.warnings:
            head = QLabel(tr("CHECK THESE"))
            head.setStyleSheet(f"color: {style.WARN['text']}; font-weight: bold; font-size: 10px;")
            lay.addWidget(head)
            for w in self.controller.plan.warnings:
                self._note(lay, w)
        return page

    def _input(self, uid):
        input_plan = self.controller.plan.inputs.get(uid)
        if input_plan is None:
            return None
        page, lay = self._page("LAYER TO CHOOSE WHEN RUN", input_plan.label, "The person running the recipe picks this layer.",
                               style.LAYER["text"])
        self._name_help(lay, f"{uid}", input_plan.label, input_plan.help)
        used = ", ".join(f"step {n}" for n, _p, _v in input_plan.used_by)
        self._facts(lay, [("Accepts", input_plan.accepts), ("Recorded with", input_plan.recorded),
                          ("Format", input_plan.format or "unknown"), ("Used by", used)])
        return page

    def _setting(self, uid):
        step_plan, param = self.controller.param_by_uid(uid)
        if param is None:
            return None
        page, lay = self._page("SETTING TO ENTER WHEN RUN", param.display,
                               "The person running the recipe can change this value.", style.SETTING["text"])
        self._name_help(lay, uid, param.display, param.help)
        facts = [("Default value", param.shown_value),
                 ("Setting", f"{param.label} of step {step_plan.number} ({step_plan.step.alg.displayName()})")]
        if param.column_note:
            facts.append(("Column", param.column_note))
        self._facts(lay, facts)
        fixed = QPushButton(tr("Use the fixed value instead"))
        fixed.clicked.connect(lambda: self.controller.set_ask(step_plan.step.entry_id, param.name, False))
        lay.addWidget(fixed)
        return page

    def _output(self, uid):
        step_plan, param = self.controller.param_by_uid(uid)
        if param is None:
            return None
        page, lay = self._page(f"RESULT OF STEP {step_plan.number}", param.display,
                               "Used by a later step" if param.kind == "temp" else "Nothing later uses it", style.OUTPUT["text"])
        lay.addWidget(QLabel(tr("<b>Output name</b>")))
        edit = QLineEdit(param.display)
        edit.setAccessibleName("Output name")
        edit.editingFinished.connect(
            lambda e=edit: self.controller.set_name(uid, e.text()) if e.text().strip() != param.display else None)
        lay.addWidget(edit)
        self._role_and_style(lay, [param])
        default_file = builder._numbered_file(param.value, param.display,
                                              QgsProject.instance()) if param.role != "intermediate" else ""
        when_run = ("not kept" if param.role == "intermediate" else
                    f"a new file, {os.path.basename(default_file)} by default (changeable when run)" if default_file else
                    "a temporary layer, unless a file is chosen when the recipe runs")
        self._facts(lay, [("Recorded as", param.format or "unknown"), ("When the recipe runs", when_run)])
        return page

    def _step(self, entry_id):
        step = self.controller.step(entry_id)
        if step is None:
            return None
        sp = self.controller.step_plan(entry_id)
        included = sp is not None
        title = f"{sp.number}. {step.alg.displayName()}" if included else step.label
        page, lay = self._page("STEP", title, f"run at {step.time_text}"
                               + (f" · {project_label(step.project)}" if step.project else ""))
        reason = self.controller.reasons.get(entry_id)
        if reason and not included:
            self._note(lay, reason)
        if step.synthetic:
            self._note(lay, f"Made from what you did: {step.synthetic}.", style.INFO)
        if step.note and step.alg_id not in builder.FIELD_CALCULATORS:   # those get their own summary
            self._note(lay, step.note, style.INFO)
        box = QCheckBox(tr("Include this step in the recipe"))
        box.setChecked(included)
        box.toggled.connect(lambda on: self.controller.set_included(entry_id, on))
        lay.addWidget(box)
        if included and self.controller.used_later(entry_id):
            before = len(self.controller.steps_leading_to(entry_id)) - 1
            start = QPushButton(tr("Start the Recipe from This Step's Result"))
            start.setToolTip(tr("Leave out this step and the {n} step(s) before it that lead to it, and use its "
                                "result as a starting layer instead: for when you'll start from it next time (a "
                                "merged layer, say). Undo brings them back.").format(n=before))
            start.clicked.connect(lambda: self.controller.start_from(entry_id))
            lay.addWidget(start)
        elif not included and entry_id in self.controller.choices.starts:
            self._note(lay, tr("The recipe starts from this step's result: it's a starting layer instead."), style.INFO)
        if step.batch_id:
            self._batch_choice(lay, step)
        if not included:
            return page
        head = QLabel(tr("NOTE"))
        head.setStyleSheet(f"color: {style.MUTED}; font-weight: bold; font-size: 10px;")
        lay.addWidget(head)
        note = QPlainTextEdit(self.controller.choices.notes.get(entry_id, ""))
        note.setPlaceholderText(tr("A note on this step: why it's there, what to watch for… "
                                "Saved as a comment in the recipe, and optionally shown in images."))
        note.setFixedHeight(58)
        note.setAccessibleName("Note on this step")
        note.textChanged.connect(lambda e=note: self.controller.set_note(entry_id, e.toPlainText()))
        lay.addWidget(note)
        if step.alg_id in builder.FIELD_CALCULATORS:
            self._calculation(lay, sp)
        # layers used
        uses = [param for param in sp.params if param.kind in ("input", "link")]
        if uses:
            head = QLabel(tr("LAYERS IT USES"))
            head.setStyleSheet(f"color: {style.MUTED}; font-weight: bold; font-size: 10px;")
            lay.addWidget(head)
            for param in uses:
                label = QLabel(f"{_esc(param.label)}: <b>{_esc(param.detail)}</b>")
                label.setWordWrap(True)
                lay.addWidget(label)
        # settings
        settings = [param for param in sp.params if param.kind == "static"]
        if settings:
            head = QLabel(tr("SETTINGS"))
            head.setStyleSheet(f"color: {style.MUTED}; font-weight: bold; font-size: 10px;")
            lay.addWidget(head)
            for param in settings:
                row = QFrame()
                row.setObjectName("srow")
                accent = style.SETTING["border"] if param.expose else "transparent"
                row.setStyleSheet(f"QFrame#srow {{ border-left: 3px solid {accent}; }}")
                rl = QVBoxLayout(row)
                rl.setContentsMargins(8, 2, 0, 2)
                top = QHBoxLayout()
                top.addWidget(QLabel(_esc(param.label)), 1)
                value = QLabel(f"<b>{_esc(param.shown_value)}</b>")
                value.setObjectName("value")
                value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
                top.addWidget(value)
                rl.addLayout(top)
                combo = QComboBox()
                combo.addItem("Fixed value", False)
                combo.addItem("Ask when the recipe runs", True)
                combo.setCurrentIndex(1 if param.expose else 0)
                combo.setEnabled(param.exposable)
                combo.setAccessibleName(f"{param.label}: fixed or asked")
                if not param.exposable:
                    combo.setToolTip(param.column_note if param.column else
                                     "This setting can't be asked for: it depends on a layer made by an earlier step.")
                combo.activated.connect(lambda _i, cb=combo, n=param.name: self.controller.set_ask(entry_id, n, cb.currentData()))
                rl.addWidget(combo)
                if param.column and param.column_note:
                    note = QLabel(_esc(f"Column: {param.column_note}."))
                    note.setWordWrap(True)
                    note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                    note.setStyleSheet(f"color: {style.MUTED}; font-size: 11px;")
                    rl.addWidget(note)
                lay.addWidget(row)
        # results
        results = [param for param in sp.params if param.kind in ("output", "temp")]
        for param in results:
            head = QLabel(f"RESULT: {_esc(param.display).upper()}")
            head.setStyleSheet(f"color: {style.OUTPUT['text']}; font-weight: bold; font-size: 10px;")
            lay.addWidget(head)
            self._role_and_style(lay, [param])
        return page

    def _calculation(self, lay, step_plan):
        """What a Field Calculator step does, in words."""
        inputs = step_plan.step.inputs
        name = inputs.get("FIELD_NAME", "")
        kind = step_plan.param("FIELD_TYPE").shown_value if step_plan.param("FIELD_TYPE") else ""
        length, precision = inputs.get("FIELD_LENGTH") or 0, inputs.get("FIELD_PRECISION") or 0
        size = f", {length} long" if length else ""
        size += f", {precision} decimals" if precision and "ecimal" in str(kind) else ""
        new = "existing" not in (step_plan.step.note or "")
        columns = builder._expression_columns(inputs.get("FORMULA"))
        html = (f"<b>Calculates the {'new' if new else 'existing'} field “{_esc(name)}”</b> "
                f"({_esc(kind)}{_esc(size)})<br>as <code>{_esc(inputs.get('FORMULA', ''))}</code>")
        if columns:
            html += "<br>reading the column" + ("s " if len(columns) > 1 else " ") + ", ".join(
                f"“{_esc(c)}”" for c in columns)
        label = QLabel(html)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        label.setStyleSheet(f"background: {style.INFO['bg']}; color: {style.INFO['text']}; padding: 6px 8px; "
                            "border-radius: 4px;")
        lay.addWidget(label)

    def _batch_choice(self, lay, step):
        rows = [s for s in self.controller.steps if s.batch_id == step.batch_id]
        mode = self.controller.choices.batch_modes.get(step.batch_id, "rows")
        head = QLabel(f"BATCH OF {len(rows)} RUNS")
        head.setStyleSheet(f"color: {style.INFO['text']}; font-weight: bold; font-size: 10px;")
        lay.addWidget(head)
        group = QButtonGroup(lay.parentWidget())
        a = QRadioButton(tr("Each row as its own step (exact replay)"))
        b = QRadioButton(tr("One step; run the recipe as a batch to repeat it"))
        b.setToolTip(tr("Settings that differed between the rows are asked when the recipe runs."))
        group.addButton(a)
        group.addButton(b)
        (b if mode == "template" else a).setChecked(True)
        a.toggled.connect(lambda on: on and self.controller.set_batch_mode(step.batch_id, "rows"))
        b.toggled.connect(lambda on: on and self.controller.set_batch_mode(step.batch_id, "template"))
        lay.addWidget(a)
        lay.addWidget(b)

    def _group(self, index):
        plan = self.controller.plan
        if index >= len(plan.groups):
            return None
        members = [step_plan for step_plan in plan.steps if step_plan.number in plan.groups[index]]
        page, lay = self._page("GROUP OF REPEATED STEPS", builder.group_title(plan, plan.groups[index]),
                               "The same tool on the same layers, with different settings.")
        expanded = index in self.controller.expanded_groups
        toggle = QPushButton("Show as one box" if expanded else "Show each step separately")
        toggle.clicked.connect(lambda: self.controller.toggle_group(index))
        lay.addWidget(toggle)
        for step_plan in members:
            self._link(lay, f"{step_plan.number}. {step_plan.step.alg.displayName()}  ·  {step_plan.step.time_text[-8:]}",
                       f"step:{step_plan.step.entry_id}")
        if members and members[0].step.batch_id:
            self._batch_choice(lay, members[0].step)
        outputs = [param for step_plan in members for param in step_plan.params if param.kind in ("output", "temp")]
        if outputs:
            head = QLabel(f"ALL {len(outputs)} RESULTS")
            head.setStyleSheet(f"color: {style.OUTPUT['text']}; font-weight: bold; font-size: 10px;")
            lay.addWidget(head)
            self._role_and_style(lay, outputs)
        return page

    def _multi(self):
        uids = self.controller.output_uids_for(self.controller.multi)
        pps = [self.controller.param_by_uid(u)[1] for u in uids]
        pps = [param for param in pps if param is not None]
        page, lay = self._page("SEVERAL SELECTED", f"{len(self.controller.multi)} items, {len(pps)} results",
                               "Change what happens to all their results at once.")
        if pps:
            self._role_and_style(lay, pps)
        else:
            hint = QLabel(tr("None of the selected items has a result."))
            hint.setStyleSheet(f"color: {style.MUTED};")
            lay.addWidget(hint)
        return page

    def _edit(self, index):
        if index >= len(self.controller.edits):
            return None
        edit = self.controller.edits[index]
        page, lay = self._page("EDITED BY HAND", edit.layer_name,
                               f"{edit.timestamp.toString('HH:mm:ss')}: {edit.describe()}")
        self._note(lay, "A recipe can't repeat typed values, digitising or deleted features. Either redo it with a "
                        "Processing tool and record again, or have the recipe ask for the edited layer.")
        self._check(lay, "Ask for the edited layer when the recipe runs (for the steps after this edit)",
                    index in self.controller.choices.edits_as_input, lambda on: self.controller.set_edit_as_input(index, on))
        return page


class _Card(QLabel):
    def __init__(self, html, on_click):
        super().__init__(html)
        self._on_click = on_click
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(80)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._on_click()
            return
        super().keyPressEvent(event)


def _esc(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
