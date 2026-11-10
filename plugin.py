"""QGIS GUI integration for Sous Chef."""

from __future__ import annotations

import os
import shutil

from qgis.core import Qgis, QgsApplication, QgsProcessingModelAlgorithm, QgsProject, QgsSettings
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QDateTime, Qt, QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QToolBar,
    QToolButton,
)

try:
    from qgis.PyQt.QtGui import QAction  # Qt 6 (QGIS 4)
except ImportError:
    from qgis.PyQt.QtWidgets import QAction  # Qt 5

from .core import builder, cleanup, lineage, opener
from .core.history import BAR_EXPRESSIONS, BarWatcher, Recorder
from .core.projects import project_label
from .gui.controller import ReviewController
from .gui import icons, recipe_actions
from .gui.window import ReviewWindow
from . import i18n
from .provider import RecipeBookProvider, icon_file, toolbox_folder
from .i18n import tr

MENU = "&Sous Chef"
TITLE = "Sous Chef"
MAX_SESSIONS = 10   # recordings kept to reopen, this QGIS session only
HERE = os.path.dirname(__file__)


class SousChefPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.recorder = Recorder()
        # always on while QGIS is open: catches Save As, Field Calculator, batch and iterate runs,
        # which QGIS's Processing history leaves out, so layers can be traced back later
        self.tracker = Recorder(passive=True)
        self.actions = []
        self.sessions = []   # {controller, when, paths}, oldest first
        self._designers = []  # keep Model Designer windows alive
        self.timer = QTimer()
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self._update_counter)

    # ----------------------------------------------------------------- setup

    def initGui(self):
        i18n.install()   # the translation for QGIS's language, when there is one
        win = self.iface.mainWindow()
        self.icon = icons.plugin_icon()
        self.icon_recording = icons.plugin_icon(recording=True)

        self.record_action = QAction(self.icon, tr("Record"), win)
        self.record_action.setCheckable(True)
        self.record_action.setToolTip(tr("Record what you do, then turn it into a recipe (a Processing model)"))
        self.record_action.toggled.connect(self._toggle_recording)
        self.icon_pause = QIcon(os.path.join(HERE, "pause.png"))
        self.icon_resume = QIcon(os.path.join(HERE, "resume.png"))
        self.pause_action = QAction(self.icon_pause, tr("Pause"), win)
        self.pause_action.setCheckable(True)
        self.pause_action.setToolTip(tr("Pause recording, e.g. while trying something you don't want in the recipe"))
        self.pause_action.toggled.connect(self._toggle_pause)
        self.review_action = QAction(QIcon(os.path.join(HERE, "review.png")), tr("Review…"), win)
        self.review_action.setToolTip(tr("Look at what has been recorded so far (recording continues)"))
        self.review_action.triggered.connect(self.review_so_far)
        self.note_action = QAction(QgsApplication.getThemeIcon("/mActionTextAnnotation.svg"), tr("Add a Note…"), win)
        self.note_action.setToolTip(tr("Add a note to the step you just ran: it shows in the review and is saved "
                                    "in the recipe"))
        self.note_action.triggered.connect(self.add_note)
        self.discard_action = QAction(QIcon(os.path.join(HERE, "discard.png")), tr("Discard"), win)
        self.discard_action.setToolTip(tr("Stop recording without making a recipe"))
        self.discard_action.triggered.connect(self.discard)

        self.toolbar = QToolBar("Sous Chef", win)
        self.toolbar.setObjectName("SousChefToolbar")
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.toolbar.addAction(self.record_action)
        self.counter = QLabel("")
        self.counter.setStyleSheet("color: #8E1515; font-weight: bold; padding: 0 8px;")
        self.counter_action = self.toolbar.addWidget(self.counter)
        for action in (self.pause_action, self.note_action, self.review_action, self.discard_action):
            self.toolbar.addAction(action)
        win.addToolBar(self.toolbar)

        # recordings made since QGIS started, to reopen their review
        self.sessions_menu = QMenu(tr("Recent Recordings and Recipes"), win)
        self.sessions_action = self.sessions_menu.menuAction()
        self.sessions_action.setIcon(QIcon(os.path.join(HERE, "review.png")))
        record_button = self.toolbar.widgetForAction(self.record_action)
        if isinstance(record_button, QToolButton):
            record_button.setMenu(self.sessions_menu)
            record_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.record_button = record_button

        self.open_action = QAction(QIcon(os.path.join(HERE, "review.png")), tr("Open a Recipe to Review or Tweak…"), win)
        self.open_action.triggered.connect(lambda: self.open_model())
        self.provider = RecipeBookProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)
        # right-click menu of recipes in the Recipe Book: open, edit, show in folder, delete
        self.recipe_actions = recipe_actions.make_actions(self)
        try:
            from processing.gui.ProviderActions import ProviderContextMenuActions
            ProviderContextMenuActions.registerProviderContextMenuActions(self.recipe_actions)
        except ImportError:
            self.recipe_actions = []

        # right-click a layer: a model of how it was made
        self.trace_action = QAction(self.icon, tr("Create Recipe from This Layer…"), win)
        self.trace_action.setToolTip(tr("Trace how the selected layer(s) were made, and turn those steps into a recipe"))
        self.trace_action.triggered.connect(self.model_from_layers)
        self._tree_view = self.iface.layerTreeView()
        if self._tree_view is not None and hasattr(self._tree_view, "contextMenuAboutToShow"):
            # its own section of the menu, between Layer CRS and Export / Make Permanent
            self._tree_view.contextMenuAboutToShow.connect(self._layer_menu)
            self._custom_layer_action = False
        else:
            for layer_type in (Qgis.LayerType.Vector, Qgis.LayerType.Raster):
                self.iface.addCustomActionForLayerType(self.trace_action, "", layer_type, True)
            self._custom_layer_action = True
        cleanup.at_startup()   # temporary files from earlier sessions
        self.tracking_action = QAction(tr("Background Tracking (for Create Recipe from This Layer)"), win)
        self.tracking_action.setCheckable(True)
        self.tracking_action.setChecked(QgsSettings().value("souschef/tracking", True, type=bool))
        self.tracking_action.setToolTip(tr("Note Save As, Field Calculator, batch and iterate runs while QGIS is open, "
                                        "so layers made with them can be traced back. Nothing is written to disk."))
        self.tracking_action.toggled.connect(self._set_tracking)
        self.clear_action = QAction(tr("Clear Sous Chef's Temporary Data…"), win)
        self.clear_action.triggered.connect(self.clear_temporary_data)
        self.help_action = QAction(QgsApplication.getThemeIcon("/mActionHelpContents.svg"), tr("How Sous Chef Works…"), win)
        self.help_action.triggered.connect(self.show_help)
        self.report_action = QAction(tr("Report a Problem…"), win)
        self.report_action.triggered.connect(lambda: self._report())
        from .gui.settings_page import SettingsFactory
        self.settings_factory = SettingsFactory(self)
        self.iface.registerOptionsWidgetFactory(self.settings_factory)
        if self.tracking_action.isChecked():
            self.tracker.start()
        self.bar_watcher = BarWatcher()   # notes the attribute table's quick calculation bar
        self.bar_watcher.start()

        self.menu_actions = [self.record_action, self.open_action, self.sessions_action, self.tracking_action,
                             self.clear_action, self.help_action, self.report_action]
        self.menu, self.menu_parent = None, None
        if not self._add_to_processing_menu():
            # QGIS's Processing plugin makes that menu; it may start after Sous Chef
            QTimer.singleShot(2500, lambda: self._add_to_processing_menu() or self._add_to_plugins_menu())
        self._update_sessions_menu()

        self.status = QLabel("")
        self.status.setStyleSheet("color: #8E1515; background: #FBE4E4; border-radius: 8px; padding: 1px 8px; font-weight: bold;")
        self.status.setVisible(False)
        self.iface.statusBarIface().addPermanentWidget(self.status)

        self.recorder.changed.connect(self._update_counter)
        if not QgsSettings().value("souschef/welcome_shown", False, type=bool):
            QTimer.singleShot(4000, self._welcome)
        self.recorder.projectChanged.connect(self._project_changed)
        self._set_recording_ui(False)

    def unload(self):
        if self.recorder.recording:
            self.recorder.stop()
        self.bar_watcher.stop()
        if self.tracker.recording:
            self.tracker.stop()   # after the recorder: their Processing hooks are removed in reverse order
        if self._custom_layer_action:
            # QGIS removes one layer type's entry per call; it was added for vector and raster
            for _layer_type in range(2):
                try:
                    if not self.iface.removeCustomActionForLayerType(self.trace_action):
                        break
                except RuntimeError:
                    break
        else:
            try:
                self._tree_view.contextMenuAboutToShow.disconnect(self._layer_menu)
            except (TypeError, RuntimeError):
                pass
        QgsApplication.processingRegistry().removeProvider(self.provider)
        try:
            from processing.gui.ProviderActions import ProviderContextMenuActions
            ProviderContextMenuActions.deregisterProviderContextMenuActions(self.recipe_actions)
        except (ImportError, ValueError):
            pass
        self.timer.stop()
        for action in self.actions:
            self.iface.removePluginMenu(MENU, action)
        self.actions = []
        if self.menu is not None and self.menu_parent is not None:
            try:
                self.menu_parent.removeAction(self.menu.menuAction())
                self.menu_parent.removeAction(self.menu_separator)
            except RuntimeError:
                pass
            self.menu = None
        self.iface.statusBarIface().removeWidget(self.status)
        self.iface.unregisterOptionsWidgetFactory(self.settings_factory)
        i18n.uninstall()
        self.iface.mainWindow().removeToolBar(self.toolbar)
        self.toolbar.deleteLater()

    # ------------------------------------------------------------- recording

    def _set_recording_ui(self, on: bool):
        self.record_action.blockSignals(True)
        self.record_action.setChecked(on)
        self.record_action.blockSignals(False)
        self.record_action.setText("Stop" if on else "Record")
        self.record_action.setIcon(self.icon_recording if on else self.icon)
        self.record_action.setToolTip("Stop recording and review what was recorded" if on
                                      else "Record what you do, then turn it into a recipe (a Processing model)")
        for action in (self.pause_action, self.note_action, self.review_action, self.discard_action):
            action.setVisible(on)
        self.pause_action.blockSignals(True)
        self.pause_action.setChecked(False)
        self._pause_look(False)
        self.pause_action.blockSignals(False)
        self.counter_action.setVisible(on)
        self.status.setVisible(on)
        if on:
            self.timer.start()
        else:
            self.timer.stop()
            self._set_tracking(self.tracking_action.isChecked())   # a change made while recording
        self._update_counter()

    def _toggle_recording(self, on: bool):
        if on:
            self.recorder.start()
            self._set_recording_ui(True)
            self.iface.messageBar().pushMessage(
                TITLE, "Recording: work as usual. Press Stop on the Sous Chef toolbar when you're done.",
                Qgis.MessageLevel.Info, 6)
            return
        steps = self.recorder.stop()
        self._set_recording_ui(False)
        if not steps and not self.recorder.edits and not self.recorder.copies:
            self.iface.messageBar().pushMessage(TITLE, tr("Nothing was recorded."), Qgis.MessageLevel.Info, 5)
            return
        self.review(steps, recording=True, keep=True)

    def _toggle_pause(self, on: bool):
        if on:
            self.recorder.pause()
        else:
            self.recorder.resume()
        self._pause_look(on)
        self._update_counter()

    def _pause_look(self, paused: bool):
        self.pause_action.setText("Resume" if paused else "Pause")
        self.pause_action.setIcon(self.icon_resume if paused else self.icon_pause)
        self.pause_action.setToolTip("Resume recording" if paused else
                                     "Pause recording, e.g. while trying something you don't want in the recipe")

    def _project_changed(self, path):
        self.pause_action.blockSignals(True)
        self.pause_action.setChecked(True)
        self._pause_look(True)
        self.pause_action.blockSignals(False)
        self.iface.messageBar().pushMessage(
            TITLE, f"Recording paused: another project was opened ({project_label(path)}). Press Resume to record "
                   "in it too. Steps from different projects are marked in the review.", Qgis.MessageLevel.Warning, 10)
        self._update_counter()

    def _update_counter(self):
        if not self.recorder.recording:
            self.counter.setText("")
            return
        steps, edits = self.recorder.count()
        text = f"{'❚❚' if self.recorder.paused else '●'} {steps}"
        if edits:
            text += f" + {edits}✎"
        self.counter.setText(text)
        self.counter.setToolTip(f"{steps} step{'s' if steps != 1 else ''} and "
                                f"{edits} hand edit{'s' if edits != 1 else ''} recorded")
        self.status.setText(f"● Sous Chef {'paused' if self.recorder.paused else 'recording'} · "
                            f"{project_label(QgsProject.instance().fileName())}")

    def _report(self, details=""):
        from .gui import report
        report.show(self.iface.mainWindow(), details)

    def show_help(self):
        from .gui.help import show_help
        show_help(self.iface.mainWindow())

    def _welcome(self):
        """The first time Sous Chef starts: a short tutorial."""
        from .gui import tutorial
        tutorial.show(self.iface.mainWindow())

    def add_note(self):
        """A note on the most recent step, typed while it's fresh in mind."""
        if not self.recorder.recording:
            return
        steps = self.recorder.steps()
        if not steps:
            self.iface.messageBar().pushMessage(TITLE, "Run a step first, then add a note to it.",
                                                Qgis.MessageLevel.Info, 5)
            return
        last = steps[-1]
        text, ok = QInputDialog.getMultiLineText(self.iface.mainWindow(), TITLE, f"Note on “{last.label}”:",
                                                 self.recorder.notes.get(last.entry_id, ""))
        if ok:
            self.recorder.notes[last.entry_id] = text.strip()

    def review_so_far(self):
        if self.recorder.recording:
            self.review(self.recorder.steps(), recording=True)

    def discard(self):
        if not self.recorder.recording:
            return
        if QMessageBox.question(self.iface.mainWindow(), TITLE, "Stop recording without making a recipe?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.recorder.stop()
        self._set_recording_ui(False)

    # ---------------------------------------------------------------- review

    def review(self, steps, recording=False, keep=False):
        """Review ``steps``. With ``keep``, the review (and every choice made in it) can be
        reopened later in this QGIS session, even after it was cancelled."""
        r = self.recorder
        controller = ReviewController(
            steps, snapshot=r.layer_snapshot if recording else {}, extents=r.extents if recording else [],
            edits=r.edits if recording else [], copies=r.copies if recording else [],
            unexplained=r.unexplained_layers(steps) if recording else [],
            recording_project=r.project_path if recording else None)
        if recording and r.notes:
            for entry_id, note in r.notes.items():
                if note:
                    controller.choices.notes.setdefault(entry_id, note)
            controller.refresh()
        session = {"controller": controller, "when": QDateTime.currentDateTime(), "paths": []}
        if keep:
            self.sessions.append(session)
            del self.sessions[:-MAX_SESSIONS]
            self._update_sessions_menu()
        return self._open_review(session)

    def reopen(self, session):
        self._open_review(session)

    def _layer_menu(self, menu):
        """Add "Create Recipe from This Layer…" to a layer's right-click menu, in a section of
        its own after the Layer CRS entry (before Export / Make Permanent)."""
        layers = [layer for layer in self._tree_view.selectedLayers()
                  if layer.type() in (Qgis.LayerType.Vector, Qgis.LayerType.Raster)]
        if not layers:
            return
        # say how many layers the recipe will cover: all the selected ones go into it
        self.trace_action.setText(tr("Create Recipe from This Layer…") if len(layers) == 1
                                  else tr("Create Recipe from {n} Layers…").format(n=len(layers)))
        actions = [a for a in menu.actions() if a.isVisible()]
        index = None
        for i, action in enumerate(actions):
            text = action.text().replace("&", "").lower()
            if action.menu() is not None and "crs" in text:
                index = i + 1
                break
        if index is None:   # no Layer CRS entry: before Export, else at the end
            index = next((i for i, a in enumerate(actions)
                          if a.menu() is not None and a.text().replace("&", "").lower().startswith("export")),
                         len(actions))
        # a line of our own directly above and below, whatever QGIS put around this spot; Qt merges
        # lines that end up next to each other, and drops one left at the very end of the menu
        menu.setSeparatorsCollapsible(True)
        before = actions[index] if index < len(actions) else None
        if before is None:
            menu.addSeparator()
            menu.addAction(self.trace_action)
            menu.addSeparator()
        else:
            menu.insertSeparator(before)
            menu.insertAction(before, self.trace_action)
            menu.insertSeparator(before)

    def _processing_menu(self):
        for action in self.iface.mainWindow().menuBar().actions():
            menu = action.menu()
            if menu is not None and menu.objectName() == "processing":
                return menu
        return None

    def _add_to_processing_menu(self) -> bool:
        """A Sous Chef submenu in QGIS's Processing menu."""
        if self.menu is not None or self.actions:
            return True
        parent = self._processing_menu()
        if parent is None:
            return False
        self.menu = QMenu(tr("Sous Chef"), parent)
        self.menu.setIcon(self.icon)
        for action in self.menu_actions:
            self.menu.addAction(action)
        self.menu_separator = parent.addSeparator()
        parent.addMenu(self.menu)
        self.menu_parent = parent
        return True

    def _add_to_plugins_menu(self):
        """Fallback when QGIS has no Processing menu."""
        if self.menu is not None or self.actions:
            return
        for action in self.menu_actions:
            self.iface.addPluginToMenu(MENU, action)
            self.actions.append(action)

    def _set_tracking(self, on: bool):
        QgsSettings().setValue("souschef/tracking", bool(on))
        if self.recorder.recording:
            return   # applied when recording stops (both hook into the same Processing windows)
        if on and not self.tracker.recording:
            self.tracker.start()
        elif not on and self.tracker.recording:
            self.tracker.stop()
            self.tracker.clear_notes()

    def clear_temporary_data(self):
        answer = QMessageBox.question(
            self.iface.mainWindow(), TITLE,
            "Clear Sous Chef's temporary data?\n\n"
            "• what the background tracker noted (Save As, Field Calculator, batch and iterate runs)\n"
            "• expressions noted from the attribute table's calculation bar\n"
            "• the recent recordings and recipes in the Record button's menu\n"
            "• leftover Taste Test files and unused symbol copies\n\n"
            "Your recipes, styles, symbols, backups and images are kept. Create Recipe from This Layer… will "
            "then only see Processing tools QGIS logs.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self.tracker.clear_notes()
        BAR_EXPRESSIONS.clear()
        self.sessions = []
        self._update_sessions_menu()
        cleanup.remove_test_runs()
        cleanup.remove_symbol_copies()
        self.iface.messageBar().pushMessage(TITLE, "Temporary data cleared.", Qgis.MessageLevel.Info, 4)
        return True

    def model_from_layers(self):
        """Trace the selected layers back to the layers they started from, and review those steps."""
        layers = self.iface.layerTreeView().selectedLayers() or (
            [self.iface.activeLayer()] if self.iface.activeLayer() else [])
        if not layers:
            return None
        try:
            traced = lineage.trace(layers, self.tracker if self.tracker.recording else None)
        except Exception as e:  # report instead of crashing QGIS
            from .gui import report
            report.remember("Create Recipe from This Layer", e)
            box = QMessageBox(QMessageBox.Icon.Warning, TITLE, f"The layer could not be traced:\n\n{e}",
                              QMessageBox.StandardButton.Ok, self.iface.mainWindow())
            report_button = box.addButton(tr("Report…"), QMessageBox.ButtonRole.HelpRole)
            box.exec()
            if box.clickedButton() is report_button:
                self._report(lineage.diagnose(layers))
            return None
        if not traced.steps:
            box = QMessageBox(QMessageBox.Icon.Information, TITLE,
                              "No Processing steps lead to " + ", ".join(f"'{layer.name()}'" for layer in layers) + ".\n\n"
                              "Layers can be traced back through Processing tools QGIS has logged (see Processing › "
                              "History), and through Save As, Field Calculator and batch runs made since the plugin was "
                              "loaded. Temporary results can only be traced while they're still in the project.\n\n"
                              "If a Processing tool did make this layer, “Show Details…” lists what was compared: "
                              "please include it when reporting the problem.", QMessageBox.StandardButton.Ok,
                              self.iface.mainWindow())
            box.setDetailedText(lineage.diagnose(layers))
            report_button = box.addButton(tr("Report…"), QMessageBox.ButtonRole.HelpRole)
            box.exec()
            if box.clickedButton() is report_button:
                self._report(lineage.diagnose(layers))
            return None
        controller = ReviewController(traced.steps, snapshot=traced.snapshot, extents=traced.extents,
                                      edits=traced.edits, traced=traced)
        session = {"controller": controller, "when": QDateTime.currentDateTime(), "paths": []}
        self.sessions.append(session)
        del self.sessions[:-MAX_SESSIONS]
        self._update_sessions_menu()
        return self._open_review(session)

    def open_model(self, path=None):
        """Open a saved recipe (Processing model) in the review window, to tweak it."""
        if not path:
            path, _ = QFileDialog.getOpenFileName(self.iface.mainWindow(), "Open a recipe to review or tweak",
                                                  toolbox_folder(), "Processing models (*.model3)")
        if not path:
            return None
        try:
            opened = opener.open_model(path)
        except Exception as e:  # report instead of crashing QGIS
            QMessageBox.warning(self.iface.mainWindow(), TITLE, f"The recipe could not be opened:\n\n{e}")
            return None
        controller = ReviewController(opened.steps, snapshot=opened.snapshot, opened=opened)
        session = {"controller": controller, "when": QDateTime.currentDateTime(), "paths": [path]}
        self.sessions.append(session)
        del self.sessions[:-MAX_SESSIONS]
        self._update_sessions_menu()
        return self._open_review(session)

    def _open_review(self, session):
        session["answered"] = False   # asked once per window whether to replace the earlier model
        window = ReviewWindow(session["controller"], self.iface.mainWindow())
        window.on_add = lambda: self._save(session, open_designer=False, parent=window)
        accepted = window.exec()
        open_designer = window.open_check.isChecked()
        window.deleteLater()   # stop it following the controller once closed
        if not accepted:
            return None
        return self._save(session, open_designer)

    def _save(self, session, open_designer, parent=None):
        controller = session["controller"]
        parent = parent or self.iface.mainWindow()
        if controller.read_only:
            QMessageBox.information(parent, TITLE, controller.read_only)
            return None
        replace = None
        if session["paths"] and all(os.path.exists(p) for p in session["paths"]):
            if session.get("answered"):
                replace = session.get("replace")
            else:
                names = "\n".join(os.path.basename(p) for p in session["paths"])
                what = ("Save your changes to this recipe?" if controller.opened is not None
                        else "A recipe was already made from this recording:")
                answer = QMessageBox.question(
                    parent, TITLE, f"{what}\n\n{names}\n\nYes replaces it. No saves a new recipe next to it.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
                if answer == QMessageBox.StandardButton.Cancel:
                    return None
                replace = session["paths"] if answer == QMessageBox.StandardButton.Yes else None
        paths = self.create(controller, open_designer=open_designer, replace=replace)
        if paths:
            session["paths"], session["answered"], session["replace"] = paths, True, paths
            self._update_sessions_menu()
        return paths

    def _update_sessions_menu(self):
        menu = self.sessions_menu
        menu.clear()
        menu.addAction(tr("Open a Recipe to Review or Tweak…")).triggered.connect(lambda: self.open_model())
        menu.addSection(tr("Recordings and recipes from this QGIS session"))
        for session in reversed(self.sessions):
            c = session["controller"]
            steps = len(c.included)
            kind = ("opened" if c.opened is not None else
                    f"traced, {steps} step{'s' if steps != 1 else ''}" if c.traced is not None else
                    f"{steps} step{'s' if steps != 1 else ''}")
            text = f"{session['when'].toString('HH:mm')}  ·  {c.model_name}  ·  {kind}"
            if session["paths"] and c.opened is None:
                text += "  ·  recipe saved"
            action = menu.addAction(text)
            action.triggered.connect(lambda _c=False, s=session: self.reopen(s))
        if not self.sessions:
            empty = menu.addAction(tr("None yet"))
            empty.setEnabled(False)

    def create(self, controller, open_designer=True, replace=None):
        plan = controller.plan
        name = controller.model_name.strip() or "Recorded workflow"
        group = controller.group or ""
        parts = plan.parts if controller.one_model_per_part and len(plan.parts) > 1 else [None]
        paths, problems = [], []
        try:
            for i, part in enumerate(parts):
                part_name = name if part is None else f"{name} (part {i + 1})"
                if replace and len(replace) == len(parts):
                    path = replace[i]
                else:
                    path = builder.model_path(toolbox_folder(), part_name)
                style_dir = os.path.splitext(path)[0] + "_styles"
                model = builder.build_model(plan, part_name, group, style_dir=style_dir, only_steps=part)
                ok, errors = model.validate()
                if not ok:
                    problems += errors
                if replace and os.path.exists(path):
                    shutil.copyfile(path, path + ".bak")   # the version before: rename to .model3 to go back
                builder.save_model(model, path)
                self._save_symbol(controller.icon_path, path)
                paths.append(path)
        except Exception as e:  # report instead of crashing QGIS
            QMessageBox.warning(self.iface.mainWindow(), TITLE, f"The recipe could not be saved:\n\n{e}")
            return None
        for pid in ("souschef", "model"):
            provider = QgsApplication.processingRegistry().providerById(pid)
            if provider:
                provider.refreshAlgorithms()
        what = f"Recipe '{name}'" if len(paths) == 1 else f"{len(paths)} recipes '{name} (part …)'"
        in_book = all(os.path.dirname(p) == toolbox_folder() for p in paths)
        where = f"in the Recipe Book{' → ' + group if group else ''}" if in_book else f"in {os.path.dirname(paths[0])}"
        message = f"{what} saved. Find it in the Processing Toolbox {where}."
        if problems:
            message += " The Model Designer reports problems: " + "; ".join(problems)
        self.iface.messageBar().pushMessage(
            TITLE, message, Qgis.MessageLevel.Warning if problems else Qgis.MessageLevel.Success, 10)
        if open_designer:
            for path in paths:
                self.open_in_designer(path)
        return paths

    @staticmethod
    def _save_symbol(svg, model_path):
        """The model's symbol in the toolbox: an .svg with the model's name next to it."""
        target = icon_file(model_path)
        if svg and os.path.isfile(svg):
            if os.path.abspath(svg) != os.path.abspath(target):
                shutil.copyfile(svg, target)
        elif os.path.exists(target):
            os.remove(target)

    def open_in_designer(self, path: str):
        try:
            from processing.modeler.ModelerDialog import ModelerDialog
        except ImportError:
            return
        model = QgsProcessingModelAlgorithm()
        model.fromFile(path)
        model.setSourceFilePath(path)
        designer = ModelerDialog.create(model)
        designer.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)   # released when closed
        designer.destroyed.connect(lambda _o=None, d=designer: self._designers.remove(d) if d in self._designers else None)
        designer.show()
        # keep open designer windows alive; forget the closed ones
        self._designers = [d for d in self._designers if not sip.isdeleted(d) and d.isVisible()] + [designer]
