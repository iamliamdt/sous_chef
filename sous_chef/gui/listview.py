"""The review window's list: a plain table of the steps, one row each, for long workflows.

Columns: in the recipe (a tick box), number, tool, what it takes, its settings, what it makes,
and when it ran. Filters across the top show everything, only what's in the recipe, only
what's left out, or only the steps that need a look (a warning is shown on the step's own row).
Rows are shaded alternately so a long table is easy to follow across."""

from __future__ import annotations

import re

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QBrush, QColor, QPalette
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import builder
from ..i18n import tr
from . import style

KEY_ROLE = Qt.ItemDataRole.UserRole
KIND_ROLE = Qt.ItemDataRole.UserRole + 1   # "step", "group", "left_out", "edit", "warning"
COLUMNS = ["In recipe", "#", "Tool", "Takes", "Settings", "Makes", "Time"]
IN_RECIPE, NUMBER, TOOL, TAKES, SETTINGS, MAKES, TIME = range(len(COLUMNS))
FILTERS = [("all", "All"), ("in", "In recipe"), ("out", "Left out"), ("look", "Needs a look")]


def step_warnings(controller) -> dict:
    """{step number: [warning]}: the plan's warnings that are about one step, found by the step
    they name ("Step 3 (Clip) …", "… before step 4 used it"), the starting layer they name, or
    the result they name."""
    plan = controller.plan
    found = {}
    numbers = {step_plan.number for step_plan in plan.steps}
    for text in plan.warnings:
        hits = {int(n) for n in re.findall(r"\bstep (\d+)\b", text, flags=re.IGNORECASE)} & numbers
        if not hits:
            for input_plan in plan.inputs.values():
                if input_plan.recorded and f"'{input_plan.recorded}'" in text:
                    hits |= {used[0] for used in input_plan.used_by}
        if not hits:
            hits = {step_plan.number for step_plan, param in plan.outputs if f"'{param.display}'" in text}
        for number in hits:
            found.setdefault(number, []).append(text)
    return found


class StepList(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.filter = "all"
        self._building = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        bar = QHBoxLayout()
        bar.setContentsMargins(4, 4, 4, 0)
        self.filter_buttons = {}
        for key, label in FILTERS:
            button = QPushButton()
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setAutoDefault(False)
            button.setChecked(key == "all")
            button.setProperty("label", label)
            button.setStyleSheet("QPushButton { padding: 3px 12px; } "
                                 f"QPushButton:checked {{ background: {style.ACCENT}; color: white; }}")
            button.clicked.connect(lambda _checked=False, k=key: self._set_filter(k))
            bar.addWidget(button)
            self.filter_buttons[key] = button
        bar.addStretch(1)
        layout.addLayout(bar)
        self.table = _Table(self)
        layout.addWidget(self.table)

    # ------------------------------------------------------------ building
    def _set_filter(self, key):
        self.filter = key
        self.rebuild()

    def rebuild(self):
        self._building = True
        table = self.table
        table.clear()
        plan = self.controller.plan
        warnings = step_warnings(self.controller)
        left_out = [s for s in self.controller.steps if s.entry_id not in self.controller.included]
        edits = [(i, e) for i, e in enumerate(self.controller.edits) if i not in self.controller.converted_edits]
        counts = {"all": len(plan.steps) + len(left_out) + len(edits), "in": len(plan.steps),
                  "out": len(left_out) + len(edits), "look": len(warnings)}
        for key, button in self.filter_buttons.items():
            text = f"{button.property('label')}  {counts[key]}"
            button.setText(text)
        show_in = self.filter in ("all", "in", "look")
        show_out = self.filter in ("all", "out")
        if show_in:
            parts = plan.parts if len(plan.parts) > 1 else []
            part_of = {n: k for k, part in enumerate(parts) for n in part}
            grouped = {n: i for i, g in enumerate(plan.groups) for n in g}
            group_items = {}
            for step_plan in plan.steps:
                if self.filter == "look" and step_plan.number not in warnings:
                    continue
                parent = table.invisibleRootItem()
                g = grouped.get(step_plan.number)
                if g is not None and self.filter != "look":
                    if g not in group_items:
                        group_items[g] = self._group_row(plan, g)
                    parent = group_items[g]
                self._step_row(parent, step_plan, part_of.get(step_plan.number), warnings.get(step_plan.number, []))
        if show_out:
            for step in left_out:
                self._left_out_row(step)
            for index, edit in edits:
                self._edit_row(index, edit)
        if table.topLevelItemCount() == 0:
            empty = QTreeWidgetItem(table, ["", "", tr("Nothing to show here.")])
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
        table.expandAll()
        self.show_selection()
        self._building = False

    def _group_row(self, plan, index):
        """A repeated step or a batch: a bold row, its runs underneath."""
        numbers = plan.groups[index]
        item = QTreeWidgetItem(self.table.invisibleRootItem())
        item.setData(0, KEY_ROLE, f"group:{index}")
        item.setData(0, KIND_ROLE, "group")
        item.setText(TOOL, builder.group_title(plan, numbers))
        font = item.font(TOOL)
        font.setBold(True)
        item.setFont(TOOL, font)
        first = next((step_plan for step_plan in plan.steps if step_plan.number == numbers[0]), None)
        if first is not None:
            item.setText(TIME, first.step.time_text[-8:])
            self.table.setItemWidget(item, TAKES, _label(", ".join(self._takes(first))))
        return item

    def _step_row(self, parent, step_plan, part, warnings):
        item = QTreeWidgetItem(parent)
        key = f"step:{step_plan.step.entry_id}"
        item.setData(0, KEY_ROLE, key)
        item.setData(0, KIND_ROLE, "step")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(IN_RECIPE, Qt.CheckState.Checked)
        item.setText(NUMBER, str(step_plan.number))
        item.setText(TIME, step_plan.step.time_text[-8:])
        tool = _esc(step_plan.step.alg.displayName())
        if part:   # the second part on: a small tag (the first part needs none)
            tool += " " + _chip(f"part {part + 1}", {"bg": style.LEFT_OUT["bg"], "text": style.MUTED})
        self.table.setItemWidget(item, TOOL, _label(tool))
        self.table.setItemWidget(item, TAKES, _label(", ".join(self._takes(step_plan))))
        self.table.setItemWidget(item, SETTINGS, _label(self._settings(step_plan)))
        self.table.setItemWidget(item, MAKES, _label(" ".join(self._makes(step_plan))))
        if step_plan.step.synthetic:
            item.setToolTip(TOOL, step_plan.step.synthetic)
        for text in warnings:   # what to check, on the step's own row
            note = QTreeWidgetItem(item)
            note.setData(0, KEY_ROLE, key)
            note.setData(0, KIND_ROLE, "warning")
            note.setFirstColumnSpanned(True)
            note.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            note.setText(0, "⚠ " + text)   # one line; the whole text in the tooltip and the side panel
            note.setToolTip(0, text)
            note.setForeground(0, QBrush(QColor(style.WARN["text"])))
            for column in range(len(COLUMNS)):
                note.setBackground(column, QBrush(QColor(style.WARN["bg"])))
        return item

    def _left_out_row(self, step):
        item = QTreeWidgetItem(self.table.invisibleRootItem())
        item.setData(0, KEY_ROLE, f"step:{step.entry_id}" if step.ok else "")
        item.setData(0, KIND_ROLE, "left_out")
        if step.ok:
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(IN_RECIPE, Qt.CheckState.Unchecked)
        reason = self.controller.reasons.get(step.entry_id) or (tr("failed, or the tool isn't available") if not step.ok
                                                                else tr("left out"))
        item.setText(TOOL, step.label)
        item.setText(SETTINGS, tr("left out: {reason}").format(reason=reason))
        item.setText(TIME, step.time_text[-8:])
        muted = QBrush(QColor(style.MUTED))
        for column in range(len(COLUMNS)):
            item.setForeground(column, muted)
        font = item.font(SETTINGS)
        font.setItalic(True)
        item.setFont(SETTINGS, font)

    def _edit_row(self, index, edit):
        item = QTreeWidgetItem(self.table.invisibleRootItem())
        item.setData(0, KEY_ROLE, f"edit:{index}")
        item.setData(0, KIND_ROLE, "edit")
        item.setText(TOOL, "✎ " + tr("Edited by hand"))
        item.setText(TAKES, edit.layer_name)
        item.setText(SETTINGS, edit.describe())
        item.setText(MAKES, tr("can't be repeated"))
        item.setText(TIME, edit.timestamp.toString("HH:mm:ss") if edit.timestamp is not None else "")
        muted = QBrush(QColor(style.MUTED))
        for column in range(len(COLUMNS)):
            item.setForeground(column, muted)

    # ------------------------------------------------------------ cell contents
    def _takes(self, step_plan):
        """The layers a step uses: starting layers as amber chips, earlier steps by number."""
        takes = []
        for param in step_plan.params:
            if param.kind not in ("input", "link"):
                continue
            for source in param.sources:
                if source[0] == "input":
                    input_plan = self.controller.plan.inputs[source[1]]
                    takes.append(_chip(input_plan.label, style.LAYER))
                else:
                    takes.append(tr("step {n}").format(n=source[1]))
        return takes

    def _settings(self, step_plan):
        """The settings changed from the tool's defaults, with their values; asked ones as violet chips."""
        shown, asked = [], []
        for param in step_plan.params:
            if param.kind != "static":
                continue
            if param.expose:
                asked.append(_chip(param.display, style.SETTING))
            elif not param.is_default and param.shown_value not in ("", None):
                value = str(param.shown_value)
                if value in ("Yes", "No", "True", "False"):   # a tick box: say which
                    value = f"{param.label.lower()}: {'yes' if value in ('Yes', 'True') else 'no'}"
                shown.append(_esc(_short(value)))
        text = " · ".join(shown) or f'<span style="color:{style.QUIET}">{tr("defaults")}</span>'
        return text + (" " + " ".join(asked) if asked else "")

    def _makes(self, step_plan):
        makes = []
        for param in step_plan.params:
            if param.kind not in ("output", "temp"):
                continue
            if param.role == "intermediate":
                not_kept = _chip(tr("not kept"), {"bg": style.LEFT_OUT["bg"], "text": style.MUTED})
                makes.append(f"{_esc(param.display)} {not_kept}")
            else:
                makes.append(_chip(param.display, style.OUTPUT, bold=True)
                             + (f' <span style="color:{style.MUTED}">{tr("kept")}</span>' if param.role == "save" else ""))
        return makes

    # ------------------------------------------------------------ selection
    def show_selection(self):
        """Select the rows of what's selected in the window (the flowchart or the side panel)."""
        was = self._building
        self._building = True
        self.table.clearSelection()
        keys = set(self.controller.multi) or {self.controller.selected}
        stack = [self.table.topLevelItem(i) for i in range(self.table.topLevelItemCount())]
        while stack:
            item = stack.pop()
            stack += [item.child(i) for i in range(item.childCount())]
            if item.data(0, KEY_ROLE) in keys and item.data(0, KIND_ROLE) != "warning":
                item.setSelected(True)
        self._building = was

    def _selection_changed(self):
        if self._building:
            return
        keys = []
        for item in self.table.selectedItems():
            key = item.data(0, KEY_ROLE)
            if key and key not in keys:
                keys.append(key)
        if len(keys) > 1:
            self.controller.select_many(keys)
        elif keys:
            self.controller.select(keys[0])

    def _ticked(self, item, column):
        """The tick box puts a step in or out of the recipe."""
        if self._building or column != IN_RECIPE:
            return
        key = item.data(0, KEY_ROLE) or ""
        if item.data(0, KIND_ROLE) in ("step", "left_out") and key.startswith("step:"):
            self.controller.set_included(int(key[5:]), item.checkState(IN_RECIPE) == Qt.CheckState.Checked)


class _Table(QTreeWidget):
    """The table itself: alternate row shading, a click on empty space clears the selection,
    and a right-click on a step offers to start the recipe from its result."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setColumnCount(len(COLUMNS))
        self.setHeaderLabels([tr(c) for c in COLUMNS])
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setRootIsDecorated(False)
        self.setIndentation(14)
        self.setUniformRowHeights(False)
        self.setAlternatingRowColors(True)
        palette = self.palette()
        base = palette.color(QPalette.ColorRole.Base)
        palette.setColor(QPalette.ColorRole.AlternateBase, base.darker(104) if base.lightness() > 128 else base.lighter(118))
        self.setPalette(palette)
        for column, width in ((IN_RECIPE, 84), (NUMBER, 34), (TOOL, 200), (TAKES, 220), (SETTINGS, 230), (MAKES, 210)):
            self.setColumnWidth(column, width)
        self.itemSelectionChanged.connect(owner._selection_changed)
        self.itemChanged.connect(owner._ticked)

    def mousePressEvent(self, event):
        if self.itemAt(event.pos()) is None:   # empty space: nothing selected any more
            self.clearSelection()
            self.owner.controller.select("overview")
            return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        key = item.data(0, KEY_ROLE) if item is not None else None
        menu = QMenu(self)
        if self.owner.controller.start_menu(key, menu):
            menu.exec(event.globalPos())


def _label(html):
    label = QLabel(html)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setWordWrap(True)
    label.setContentsMargins(4, 3, 4, 3)
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)   # clicks select the row
    return label


def _chip(text, colours, bold=False) -> str:
    weight = "font-weight:bold;" if bold else ""
    return (f'<span style="color:{colours["text"]}; background-color:{colours["bg"]};{weight}">'
            f'&nbsp;{_esc(text)}&nbsp;</span>')


def _short(text, most=40) -> str:
    text = str(text)
    return text if len(text) <= most else text[:most - 1] + "…"


def _esc(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
