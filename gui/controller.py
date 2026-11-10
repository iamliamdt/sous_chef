"""State of the review window: the recorded steps, the user's choices, the current plan
and selection. Views read from it and call its methods; it emits ``changed``."""

from __future__ import annotations

import copy

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QDateTime, QObject, pyqtSignal

from ..core import builder
from ..core.builder import Choices, Context
from ..core.prepare import prepare_steps
from ..core.projects import project_label


class ReviewController(QObject):
    changed = pyqtSignal(str)   # what changed: "plan", "selection", "view", "text"

    def __init__(self, steps, snapshot=None, extents=None, edits=None, copies=None, unexplained=None,
                 recording_project=None, opened=None, traced=None, parent=None):
        super().__init__(parent)
        self.snapshot = snapshot or {}
        self.extents = extents or []
        self.edits = edits or []
        self.copies = copies or []
        self.unexplained = unexplained or []
        self.recording_project = recording_project
        self.opened = opened            # core.opener.OpenedModel when reviewing a saved model
        self.traced = traced            # core.lineage.Lineage when made from a layer's history
        self.choices = opened.choices if opened is not None else Choices()
        self.read_only = opened.read_only if opened is not None else ""
        self.icon_path = opened.icon if opened is not None else ""   # optional SVG symbol for the toolbox
        self.view = "flow"
        self.detail = "simple"
        self.show_left_out = True
        self.editing = False
        self.selected = "overview"
        self.multi: list = []          # several output uids / step keys selected
        self.expanded_groups: set = set()
        self.positions: dict = {}      # flowchart boxes moved by hand: key -> QPointF
        self.model_name = f"Recorded workflow {QDateTime.currentDateTime().toString('yyyy-MM-dd HH:mm')}"
        self.group = ""   # a sub-group in the Recipe Book in the toolbox (none by default)
        if opened is not None:
            self.model_name = opened.name or "Model"
            self.group = "" if opened.group in ("", "Recipe Book", "Sous Chef") else opened.group
        if traced is not None and traced.targets:
            self.model_name = f"{', '.join(traced.targets)} workflow"
        self.one_model_per_part = False
        self.plan = None
        self._undo, self._redo, self._state, self._restoring, self._last_text = [], [], None, False, (0, "")
        self.set_steps(steps)

    # ------------------------------------------------------------------ data

    def set_steps(self, raw_steps):
        self.raw_steps = raw_steps
        self.steps, reasons, self.converted_edits = prepare_steps(raw_steps, self.edits, self.copies)
        self._tag_made_steps()
        project = QgsProject.instance()
        # a saved model's steps are all meant: no repeats or earlier attempts to find
        trusted = self.opened is not None or self.traced is not None   # every step leads to the result
        self.reasons = {} if trusted else builder.find_redundant(self.steps, project, self.snapshot)
        self.reasons.update(reasons)
        self._label_projects()
        self.included = {s.entry_id for s in self.steps if s.ok and s.entry_id not in self.reasons}
        self.refresh("plan")

    def _tag_made_steps(self):
        """Steps made from Save As, the Field Calculator or a selection have no project of their
        own: they belong to the project of the recorded step nearest in time."""
        if self.recording_project is None:
            return
        real = [s for s in self.steps if not s.synthetic and s.timestamp is not None]
        for step in self.steps:
            if not step.synthetic or step.project:
                continue
            if real and step.timestamp is not None:
                nearest = min(real, key=lambda s: abs(s.timestamp.msecsTo(step.timestamp)))
                step.project = nearest.project or self.recording_project
            else:
                step.project = self.recording_project

    def _label_projects(self):
        """Mark steps run in another project than the one recording started in."""
        if self.recording_project is None:
            return
        for step in self.steps:
            if step.ok and step.entry_id not in self.reasons and step.project != self.recording_project:
                self.reasons[step.entry_id] = f"run in another project ({project_label(step.project)})"

    def context(self) -> Context:
        return Context(QgsProject.instance(), self.snapshot, self.extents, self.edits, self.converted_edits,
                       [s for s in self.steps if s.ok and s.entry_id not in self.included])

    def selected_steps(self):
        return [s for s in self.steps if s.ok and s.entry_id in self.included]

    # ------------------------------------------------------------------ undo

    def _snapshot(self):
        return copy.deepcopy(self.choices), frozenset(self.included), self.one_model_per_part

    def _remember(self, what):
        """Keep the state before each change, for Undo. Typing in a name or help text is
        one change, not one per letter."""
        new = self._snapshot()
        if self._state is None:
            self._state = new
            return
        if new == self._state:
            return
        now = QDateTime.currentMSecsSinceEpoch()
        typing = what == "text" and self._last_text[1] == "text" and now - self._last_text[0] < 1500
        if not typing:
            self._undo.append(self._state)
            del self._undo[:-100]
        self._redo = []
        self._last_text = (now, what)
        self._state = new

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self):
        if self._undo:
            self._redo.append(self._state)
            self._restore(self._undo.pop())

    def redo(self):
        if self._redo:
            self._undo.append(self._state)
            self._restore(self._redo.pop())

    def _restore(self, state):
        choices, included, per_part = state
        self.choices = copy.deepcopy(choices)
        self.included = set(included)
        self.one_model_per_part = per_part
        self._state = state
        self._last_text = (0, "")
        self._restoring = True
        try:
            self.refresh("plan")
        finally:
            self._restoring = False

    def refresh(self, what="plan"):
        if not self._restoring:
            self._remember(what)
        self.plan = builder.analyse(self.selected_steps(), self.choices, self.context())
        if self.unexplained:
            self.plan.warnings.append(
                f"{len(self.unexplained)} layer(s) appeared while recording that no recorded step made "
                f"({', '.join(self.unexplained[:3])}{'…' if len(self.unexplained) > 3 else ''}). They may come "
                "from a tool QGIS doesn't log.")
        if self.traced is not None:
            for note in reversed(self.traced.notes):
                self.plan.warnings.insert(0, note)
        if self.opened is not None:
            self.plan.warnings = [w for w in self.plan.warnings if "edited by hand" not in w]
            for note in ([self.read_only] if self.read_only else []) + self.opened.warnings:
                self.plan.warnings.insert(0, note)
        self.changed.emit(what)

    # -------------------------------------------------------------- lookups

    def step_plan(self, entry_id):
        return next((sp for sp in self.plan.steps if sp.step.entry_id == entry_id), None)

    def step(self, entry_id):
        return next((s for s in self.steps if s.entry_id == entry_id), None)

    def param_by_uid(self, uid):
        for step_plan in self.plan.steps:
            for param in step_plan.params:
                if param.uid == uid:
                    return step_plan, param
        return None, None

    def enter_items(self):
        """(kind, uid, name, detail) for everything the person running the model enters."""
        items = [("layer", input_plan.uid, input_plan.label, f"{input_plan.accepts} · recorded with {input_plan.recorded}")
                 for input_plan in self.plan.inputs.values()]
        items += [("setting", param.uid, param.display,
                   f"step {step_plan.number}, {step_plan.step.alg.displayName()} · default {param.shown_value}")
                  for step_plan, param in self.plan.asked]
        return items

    def group_of(self, number):
        for i, members in enumerate(self.plan.groups):
            if number in members:
                return i
        return None

    # -------------------------------------------------------------- actions

    def select(self, key, additive=False):
        if additive:
            keys = list(self.multi) or ([self.selected] if self.selected and self.selected != "overview" else [])
            if key in keys:
                keys.remove(key)
            else:
                keys.append(key)
            self.multi = keys if len(keys) > 1 else []
            self.selected = keys[-1] if keys else "overview"
        else:
            self.multi = []
            self.selected = key
        self.changed.emit("selection")

    def select_many(self, keys):
        keys = [k for k in keys if k]
        if len(keys) > 1:
            self.multi, self.selected = keys, keys[-1]
            self.changed.emit("selection")
        elif keys:
            self.select(keys[0])

    def set_view(self, view):
        self.view = view
        self.changed.emit("view")

    def set_detail(self, detail):
        self.detail = detail
        self.changed.emit("view")

    def set_show_left_out(self, on):
        self.show_left_out = on
        self.changed.emit("view")

    def set_editing(self, on):
        self.editing = on
        self.changed.emit("view")

    def set_included(self, entry_id, on):
        (self.included.add if on else self.included.discard)(entry_id)
        if on:
            self.choices.starts.discard(entry_id)
        self.refresh()

    def steps_leading_to(self, entry_id) -> list:
        """The entry ids of a step and every step whose result leads into it."""
        by_number = {step_plan.number: step_plan for step_plan in self.plan.steps}
        step_plan = next((x for x in self.plan.steps if x.step.entry_id == entry_id), None)
        if step_plan is None:
            return []
        out, todo = [], [step_plan]
        while todo:
            current = todo.pop()
            if current.step.entry_id in out:
                continue
            out.append(current.step.entry_id)
            for param in current.params:
                for src in param.sources:
                    if src[0] == "link" and src[1] in by_number:
                        todo.append(by_number[src[1]])
        return out

    def start_from(self, entry_id):
        """Start the recipe from this step's result: the step and every step that leads to it
        are left out, and its result becomes a starting layer (say, a merged layer you'll reuse)."""
        leading = self.steps_leading_to(entry_id)
        if not leading:
            return
        for eid in leading:
            self.included.discard(eid)
            self.choices.starts.add(eid)
        self.refresh()

    def start_menu(self, key, menu) -> bool:
        """Add "Start the Recipe from This Step's Result" to a right-click menu for a step's key;
        False when it doesn't apply."""
        if not isinstance(key, str) or not key.startswith("step:"):
            return False
        try:
            entry_id = int(key[5:])
        except ValueError:
            return False
        if not self.used_later(entry_id):
            return False
        from ..i18n import tr
        action = menu.addAction(tr("Start the Recipe from This Step's Result"))
        action.setToolTip(tr("Leave out this step and the steps that lead to it; its result becomes a starting layer"))
        action.triggered.connect(lambda: self.start_from(entry_id))
        return True

    def used_later(self, entry_id) -> bool:
        """Whether a later step in the recipe uses this step's result."""
        step_plan = next((x for x in self.plan.steps if x.step.entry_id == entry_id), None)
        return step_plan is not None and any(src[0] == "link" and src[1] == step_plan.number
                                             for other in self.plan.steps for param in other.params for src in param.sources)

    def set_ask(self, entry_id, param, on):
        key = (entry_id, param)
        self.choices.ask.discard(key)
        self.choices.fixed.discard(key)
        (self.choices.ask if on else self.choices.fixed).add(key)
        if not on and self.selected == f"set:{entry_id}:{param}":
            self.selected = f"step:{entry_id}"
        self.refresh()

    def set_name(self, uid, text, quiet=False):
        self.choices.names[uid] = text.strip()
        self.refresh("text" if quiet else "plan")

    def set_note(self, entry_id, text):
        if text.strip():
            self.choices.notes[entry_id] = text.strip()
        else:
            self.choices.notes.pop(entry_id, None)
        self.refresh("text")

    def set_help(self, uid, text, quiet=False):
        self.choices.helps[uid] = text
        self.refresh("text" if quiet else "plan")

    def set_role(self, uids, role):
        for uid in uids:
            self.choices.roles[uid] = role
        self.refresh()

    def set_style(self, uids, on):
        for uid in uids:
            (self.choices.unstyled.discard if on else self.choices.unstyled.add)(uid)
        self.refresh()

    def set_batch_mode(self, batch_id, mode):
        self.choices.batch_modes[batch_id] = mode
        self.refresh()

    def set_edit_as_input(self, index, on):
        (self.choices.edits_as_input.add if on else self.choices.edits_as_input.discard)(index)
        self.refresh()

    def toggle_group(self, index):
        (self.expanded_groups.discard if index in self.expanded_groups else self.expanded_groups.add)(index)
        self.changed.emit("view")

    def set_description(self, text, quiet=True):
        self.choices.description = text
        self.refresh("text" if quiet else "plan")

    def set_model_name(self, text):
        self.model_name = text
        self.changed.emit("text")

    def output_uids_for(self, keys):
        """Output uids behind selected keys (outputs, steps or groups)."""
        uids = []
        for key in keys:
            if key.startswith("out:"):
                uids.append(key)
            elif key.startswith("step:"):
                sp = self.step_plan(int(key.split(":", 1)[1]))
                if sp:
                    uids += [param.uid for param in sp.params if param.kind in ("output", "temp")]
            elif key.startswith("group:"):
                members = self.plan.groups[int(key.split(":", 1)[1])]
                for sp in self.plan.steps:
                    if sp.number in members:
                        uids += [param.uid for param in sp.params if param.kind in ("output", "temp")]
        return list(dict.fromkeys(uids))
