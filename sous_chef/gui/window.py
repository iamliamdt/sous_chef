"""The review window: Flowchart / List / Run dialog views, a side panel, and model settings."""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt, QTime, QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
)

try:
    from qgis.PyQt.QtGui import QShortcut  # Qt 6 (QGIS 4)
except ImportError:
    from qgis.PyQt.QtWidgets import QShortcut  # Qt 5

from . import style
from .flow import FlowView
from .listview import StepList
from .panel import SidePanel
from .runview import RunDialogView
from ..i18n import tr


class ReviewWindow(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        style.apply_theme()   # light or dark, following QGIS's theme
        self.controller = controller
        self.setWindowTitle(f"Recipe: {os.path.basename(controller.opened.path)}" if controller.opened is not None
                            else "Create Recipe from Workflow")
        self.resize(1280, 820)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ---- header
        header = QHBoxLayout()
        self.view_group = QButtonGroup(self)
        for value, label in (("flow", "Flowchart"), ("list", "List"), ("dialog", "Run dialog")):
            b = self._segment(label, value, self.view_group)
            b.clicked.connect(lambda _c=False, v=value: self.controller.set_view(v))
            header.addWidget(b)
        header.addSpacing(10)
        self.detail_group = QButtonGroup(self)
        self.detail_buttons = []
        for value, label in (("simple", "Simplified"), ("detailed", "Detailed")):
            b = self._segment(label, value, self.detail_group)
            b.clicked.connect(lambda _c=False, v=value: self.controller.set_detail(v))
            header.addWidget(b)
            self.detail_buttons.append(b)
        self.fit_button = QToolButton()
        self.fit_button.setText(tr("Fit"))
        self.fit_button.setToolTip(tr("Zoom to show the whole flowchart and clear the selection (scroll to zoom)"))
        self.fit_button.clicked.connect(self._fit)
        header.addWidget(self.fit_button)
        self.tidy_button = QToolButton()
        self.tidy_button.setText(tr("Tidy"))
        self.tidy_button.setToolTip(tr("Put boxes you moved back in their automatic place"))
        self.tidy_button.clicked.connect(lambda: self.flow.reset_layout())
        header.addWidget(self.tidy_button)
        from qgis.core import QgsApplication
        from qgis.PyQt.QtGui import QKeySequence
        self.undo_button, self.redo_button = QToolButton(), QToolButton()
        for button, icon, tip, slot, keys in (
                (self.undo_button, "/mActionUndo.svg", "Undo the last change (Ctrl+Z)", self.controller.undo,
                 QKeySequence.StandardKey.Undo),
                (self.redo_button, "/mActionRedo.svg", "Redo (Ctrl+Shift+Z)", self.controller.redo,
                 QKeySequence.StandardKey.Redo)):
            button.setIcon(QgsApplication.getThemeIcon(icon))
            button.setToolTip(tip)
            button.clicked.connect(slot)
            button.setEnabled(False)
            header.addWidget(button)
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(slot)
        help_button = QToolButton()
        help_button.setIcon(QgsApplication.getThemeIcon("/mActionHelpContents.svg"))
        help_button.setToolTip(tr("How Sous Chef works, or report a problem"))
        help_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        help_menu = QMenu(help_button)
        help_menu.addAction(tr("How Sous Chef Works…")).triggered.connect(self._help)
        help_menu.addAction(tr("Report a Problem…")).triggered.connect(self._report)
        help_button.setMenu(help_menu)
        header.addWidget(help_button)
        self.image_button = QToolButton()
        self.image_button.setText(tr("Images && Animations…"))
        self.image_button.setIcon(QgsApplication.getThemeIcon("/mActionSaveMapAsImage.svg"))
        self.image_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.image_button.setToolTip(tr("Pictures, animations (GIF or MP4) and written recipes of this workflow. "
                                        "To save or share the recipe itself, use Add to Recipe Book or Share."))
        self.image_button.clicked.connect(self._open_export)
        header.addWidget(self.image_button)
        self.left_out = QCheckBox(tr("Left-out steps"))
        self.left_out.setToolTip(tr("Show the steps that aren't part of the recipe, as dashed grey boxes"))
        self.left_out.setChecked(self.controller.show_left_out)
        self.left_out.toggled.connect(self.controller.set_show_left_out)
        header.addWidget(self.left_out)
        header.addStretch(1)
        header.addWidget(QLabel(tr("Ingredients:")))
        self.layer_count = QPushButton()
        self.layer_count.setStyleSheet(style.chip_css("layer"))
        self.layer_count.clicked.connect(lambda: self.controller.select("overview"))
        header.addWidget(self.layer_count)
        self.setting_count = QPushButton()
        self.setting_count.setStyleSheet(style.chip_css("setting"))
        self.setting_count.clicked.connect(lambda: self.controller.select("overview"))
        header.addWidget(self.setting_count)
        layout.addLayout(header)

        # ---- views + panel
        self.flow = FlowView(self.controller)
        self.list = StepList(self.controller)
        self.run = RunDialogView(self.controller)
        self.stack = QStackedWidget()
        for w in (self.flow, self.list, self.run):
            self.stack.addWidget(w)
        self.panel = SidePanel(self.controller)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.stack)
        splitter.addWidget(self.panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([900, 360])
        layout.addWidget(splitter, 1)

        # ---- footer
        info = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        info.addWidget(self.summary, 1)
        self.saved_label = QLabel("")
        self.saved_label.setStyleSheet(f"color: {style.OUTPUT['text']};")
        info.addWidget(self.saved_label)
        self.parts_check = QCheckBox(tr("One recipe per part"))
        self.parts_check.setToolTip(tr("The steps form separate parts that don't use each other's results"))
        self.parts_check.toggled.connect(lambda on: setattr(self.controller, "one_model_per_part", on))
        info.addWidget(self.parts_check)
        layout.addLayout(info)
        footer = QHBoxLayout()
        footer.addWidget(QLabel(tr("Recipe name")))
        self.name_edit = QLineEdit(self.controller.model_name)
        self.name_edit.setMinimumWidth(200)
        # keep the controller's name in step with every keystroke, so a later refresh never puts back an older name
        self.name_edit.textEdited.connect(lambda text: setattr(self.controller, "model_name", text))
        self.name_edit.editingFinished.connect(lambda: self.controller.set_model_name(self.name_edit.text()))
        footer.addWidget(self.name_edit)
        footer.addWidget(QLabel(tr("Group")))
        self.group_edit = QLineEdit(self.controller.group)
        self.group_edit.setMinimumWidth(100)
        self.group_edit.setPlaceholderText(tr("none"))
        self.group_edit.setToolTip(tr("Optional: a sub-group in the Recipe Book (Processing Toolbox)"))
        self.group_edit.editingFinished.connect(lambda: setattr(self.controller, "group", self.group_edit.text().strip()))
        footer.addWidget(self.group_edit)
        self.symbol_button = QToolButton()
        self.symbol_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.symbol_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        symbol_menu = QMenu(self.symbol_button)
        symbol_menu.addAction(tr("Choose a symbol…")).triggered.connect(self._browse_symbols)
        self.colours_action = symbol_menu.addAction(tr("Change symbol colours…"))
        self.colours_action.triggered.connect(self._browse_symbols)
        self.colours_action.setEnabled(False)
        symbol_menu.addAction(tr("No symbol (standard icon)")).triggered.connect(lambda: self._set_symbol(""))
        self.symbol_button.setMenu(symbol_menu)
        self.symbol_button.setToolTip(tr("Optional symbol for the recipe in the Recipe Book (Processing Toolbox)"))
        footer.addWidget(self.symbol_button)
        self._set_symbol(self.controller.icon_path)
        self.open_check = QCheckBox(tr("Open in Model Designer"))
        self.open_check.setChecked(False)
        footer.addWidget(self.open_check)
        footer.addStretch(1)
        self.test_button = QPushButton(tr("Taste Test"))
        self.test_button.setToolTip(tr("Run the recipe on the layers it was recorded with, and compare the results "
                                    "with the recorded ones"))
        self.test_button.clicked.connect(self._test_run)
        info.insertWidget(info.indexOf(self.parts_check), self.test_button)
        self.share_button = QToolButton()
        self.share_button.setText(tr("Share"))
        self.share_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        share_menu = QMenu(self.share_button)
        share_menu.addAction(tr("Share as One Zip File…")).triggered.connect(self._share_zip)
        share_menu.addAction(tr("Save as Python Script…")).triggered.connect(self._share_script)
        self.share_button.setMenu(share_menu)
        self.share_button.setToolTip(tr("Save the recipe with its styles and symbol as one zip, or as a Processing "
                                     "script (Python)"))
        info.insertWidget(info.indexOf(self.parts_check), self.share_button)
        self.on_add = None   # set by the plugin: saves the model to the toolbox, window stays open
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.add_button = self.buttons.addButton("Add to Recipe Book", QDialogButtonBox.ButtonRole.ActionRole)
        self.add_button.setToolTip(tr("Save the recipe to the Recipe Book (Processing Toolbox) and keep "
                                   "working here"))
        self.add_button.clicked.connect(self._add)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Add and Close"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Close"))
        for button in self.buttons.buttons():   # Enter in a text box saves the text; it never closes the window
            button.setAutoDefault(False)
            button.setDefault(False)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        footer.addWidget(self.buttons)
        layout.addLayout(footer)

        self.dirty = {"flow", "list", "dialog"}
        self._pending = []
        self.controller.changed.connect(self._changed)
        self._apply("plan")

    def keyPressEvent(self, event):
        """Enter or Return finishes typing in a box (the box saves its text), but doesn't press
        Add and Close: people naturally press Enter when they've finished writing."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)

    def _segment(self, label, value, group):
        b = QToolButton()
        b.setText(label)
        b.setCheckable(True)
        b.setProperty("value", value)
        b.setStyleSheet("QToolButton { padding: 4px 9px; } "
                        f"QToolButton:checked {{ background: {style.ACCENT}; color: white; }}")
        group.addButton(b)
        group.setExclusive(True)
        return b

    def _open_export(self, mode=None):
        """Its own window with a live preview: changing a setting doesn't close anything."""
        from .export_dialog import ExportDialog
        if getattr(self, "_export_dialog", None) is None:
            self._export_dialog = ExportDialog(self.controller, self, mode)
            self._export_dialog.finished.connect(lambda _r: setattr(self, "_export_dialog", None))
        elif mode:
            self._export_dialog.mode.set(mode)
        self._export_dialog.show()
        self._export_dialog.raise_()
        self._export_dialog.activateWindow()

    def showEvent(self, event):
        """Fit the flowchart to the window once it has its real size (before that, the
        window is smaller and the flowchart would be fitted too small)."""
        super().showEvent(event)
        if not getattr(self, "_shown_once", False):
            self._shown_once = True
            if self.stack.currentWidget() is self.flow:
                self._flow_fitted = True
                QTimer.singleShot(0, self.flow.fit)
            else:
                self._flow_fitted = False

    def _fit(self):
        self.controller.select("overview")
        self.flow.fit()

    def _sync(self):
        self.controller.set_model_name(self.name_edit.text())
        self.controller.group = self.group_edit.text().strip()

    def _accept(self):
        self._sync()
        self.accept()

    def _add(self):
        self._sync()
        if self.on_add is not None and self.on_add():
            self.saved_label.setText(f"✓ Added at {QTime.currentTime().toString('HH:mm')}")
            self.saved_label.setToolTip(tr("Saved to the Recipe Book in the Processing Toolbox"))

    def _report(self):
        from . import report
        report.show(self)

    def _help(self):
        from .help import show_help
        show_help(self)

    def _share_path(self, title, suffix, filter_text):
        from qgis.core import QgsSettings
        from ..core import builder
        self._sync()
        folder = QgsSettings().value("souschef/image_folder", "") or os.path.expanduser("~")
        if not os.path.isdir(folder):
            folder = os.path.expanduser("~")
        start = os.path.join(folder, builder.slug(self.controller.model_name, "recipe") + suffix)
        path, _ = QFileDialog.getSaveFileName(self, title, start, filter_text)
        if path and not path.lower().endswith(suffix):
            path += suffix
        if path:
            QgsSettings().setValue("souschef/image_folder", os.path.dirname(path))
        return path

    def _share_zip(self):
        from ..core import share
        path = self._share_path("Share recipe as one zip file", ".zip", "Zip files (*.zip)")
        if not path:
            return
        try:
            added = share.zip_plan(self.controller.plan, self.controller.model_name, self.controller.group,
                                   self.controller.icon_path, path)
        except Exception as e:  # report instead of crashing QGIS
            QMessageBox.warning(self, "Sous Chef", f"The zip file could not be made:\n\n{e}")
            return
        QMessageBox.information(self, "Sous Chef", f"Saved {os.path.basename(path)} with: {', '.join(added)}.\n\n"
                                "To use it, unzip it and add the .model3 file with Processing Toolbox › "
                                "Models › Add Model to Toolbox, keeping the files together.")

    def _share_script(self):
        from ..core import share
        path = self._share_path("Save recipe as Python script", ".py", "Python scripts (*.py)")
        if not path:
            return
        try:
            share.script_from_plan(self.controller.plan, self.controller.model_name, self.controller.group, path)
        except Exception as e:  # report instead of crashing QGIS
            QMessageBox.warning(self, "Sous Chef", f"The script could not be saved:\n\n{e}")
            return
        QMessageBox.information(self, "Sous Chef", f"Saved {os.path.basename(path)}.\n\nAdd it with Processing "
                                "Toolbox › Scripts › Add Script to Toolbox, or open it in the Processing script editor.")

    def _test_run(self):
        from .testrun import TestRunDialog, run_test
        self._sync()
        try:
            result = run_test(self.controller)
        except Exception as e:  # report instead of crashing QGIS
            result = {"rows": [], "error": str(e), "missing": [], "context": None}
        TestRunDialog(result, self).exec()
        # the model and results written for the test: not needed once the window is closed
        result.pop("context", None)
        if result.get("folder"):
            import shutil
            shutil.rmtree(result["folder"], ignore_errors=True)

    def _browse_symbols(self):
        """QGIS's SVG browser with fill and outline colours and a live preview, in one window."""
        from .symbols import SymbolDialog, save_coloured_copy
        source = getattr(self, "_symbol_source", "") or self.controller.icon_path
        colours = getattr(self, "_symbol_colours", (None, None)) if getattr(self, "_symbol_source", "") else (None, None)
        dialog = SymbolDialog(source, colours[0], colours[1], self)
        if not dialog.exec() or not dialog.path:
            return
        self._symbol_source = dialog.path
        self._symbol_colours = (dialog.fill(), dialog.outline())
        self._set_symbol(save_coloured_copy(dialog.path, dialog.fill(), dialog.outline(), dialog.gear()))
        self.colours_action.setEnabled(True)

    def _set_symbol(self, path):
        self.controller.icon_path = path or ""
        if not path and hasattr(self, "colours_action"):
            self._symbol_source = ""
            self.colours_action.setEnabled(False)
        elif hasattr(self, "colours_action"):
            self.colours_action.setEnabled(True)
        if path and os.path.isfile(path):
            self.symbol_button.setIcon(QIcon(path))
            self.symbol_button.setText(tr("Symbol"))
        else:
            self.symbol_button.setIcon(QIcon())
            self.symbol_button.setText(tr("Symbol: none"))

    # ------------------------------------------------------------ updates

    def _changed(self, what):
        """Update the views after the current event (a click, a typed key) has finished.
        Rebuilding straight away would delete the very widget that sent the change while
        Qt is still using it, which crashes QGIS."""
        self.undo_button.setEnabled(self.controller.can_undo())
        self.redo_button.setEnabled(self.controller.can_redo())
        if what not in self._pending:
            self._pending.append(what)
        if len(self._pending) == 1:
            QTimer.singleShot(0, self._flush)

    def _flush(self):
        pending, self._pending = self._pending, []
        for what in pending:
            self._apply(what)

    def _apply(self, what):
        c = self.controller
        if what in ("plan", "text"):
            self.dirty |= {"flow", "list"} | ({"dialog"} if what == "plan" else set())
        if what == "view":
            self.dirty |= {"flow", "list", "dialog"}
        for b in self.view_group.buttons():
            b.setChecked(b.property("value") == c.view)
        for b in self.detail_group.buttons():
            b.setChecked(b.property("value") == c.detail)
        is_flow = c.view == "flow"
        for b in self.detail_buttons:
            b.setVisible(is_flow)
        self.fit_button.setVisible(is_flow)
        self.tidy_button.setVisible(is_flow)
        self.left_out.setVisible(c.view != "dialog")
        self.stack.setCurrentIndex({"flow": 0, "list": 1, "dialog": 2}[c.view])
        if is_flow and not getattr(self, "_flow_fitted", True):   # the first time the flowchart shows
            self._flow_fitted = True
            QTimer.singleShot(0, self.flow.fit)
        current = {"flow": "flow", "list": "list", "dialog": "dialog"}[c.view]
        if current in self.dirty:
            {"flow": self.flow, "list": self.list, "dialog": self.run}[current].rebuild()
            self.dirty.discard(current)
        elif what == "selection":
            if current == "flow":
                self.flow.update_highlight()
            elif current == "list":
                self.list.show_selection()
        if what in ("plan", "selection", "view"):
            self.panel.rebuild()
        items = c.enter_items()
        layers = sum(1 for i in items if i[0] == "layer")
        self.layer_count.setText(f"■ {layers} layer{'s' if layers != 1 else ''}")
        self.setting_count.setText(f"■ {len(items) - layers} setting{'s' if len(items) - layers != 1 else ''}")
        warn = f"  ·  ⚠ {len(c.plan.warnings)} to check (see the side panel overview)" if c.plan.warnings else ""
        self.summary.setText(f"<b>Recipe:</b> {c.plan.summary()}{warn}")
        self.parts_check.setVisible(len(c.plan.parts) > 1)
        can_save = bool(c.plan.steps) and not c.read_only
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(can_save)
        self.add_button.setEnabled(can_save)
        if c.read_only:
            self.add_button.setToolTip(c.read_only)
        if what == "text" and self.name_edit.text() != c.model_name and not self.name_edit.hasFocus():
            self.name_edit.setText(c.model_name)
