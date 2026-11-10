"""Flowchart view of the review window: inputs → steps → outputs, in columns.

Layout
- Layer inputs (amber) in the first column; each step one column right of the
  deepest step whose result it uses; final outputs (green) in a last column.
- Settings asked when the model runs: one violet box attached above their step.
- Inputs, outputs and asked settings show QGIS's icon for their type (point,
  line, polygon, raster, number, text…).
- With "Left-out steps" ticked, left-out steps sit among the others as dashed grey
  boxes, with dashed lines to what they used and to steps that used their result.
- Repeated steps (same tool on the same layers, or batch rows) collapse into one
  group box in the simplified view.

Lines are routed along the gaps between columns, with rounded corners, and detour
above or below boxes instead of crossing them.

Mouse: drag the background to pan, drag a box to move it (lines follow), click to
select, Ctrl-click or Shift-drag a rectangle to select several, wheel to zoom.
"""

from __future__ import annotations

import re

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QPointF, QRectF, Qt, QUrl
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QTextDocument,
)
from qgis.PyQt.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsTextItem,
    QGraphicsView,
)

from ..core import builder
from . import icons, style

GAP_X, GAP_Y, PAD, RADIUS, CLEAR = 110, 34, 8, 8, 12


class Box(QGraphicsRectItem):
    """A clickable box. Top-level boxes (steps, groups, inputs, outputs) can be dragged."""

    def __init__(self, view, key, rect, fill, border, dashed=False, radius=6, is_row=False, movable=False):
        super().__init__(rect)
        self.view, self.key, self.radius, self.is_row = view, key, radius, is_row
        self._press = None
        pen = QPen(QColor(border), 1.2)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setBrush(QBrush(QColor(fill)))
        if key:
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        if movable:
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self.pen())
        if self.key and self.key in self.view.highlight_keys and not (self.is_row and self.key.startswith("step:")):
            pen = QPen(QColor(style.SELECT), 2.4)
        painter.setPen(pen)
        painter.setBrush(self.brush())
        painter.drawRoundedRect(self.rect(), self.radius, self.radius)

    def mousePressEvent(self, event):
        self._press = event.scenePos()
        if self.is_row or not (self.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        moved = self._press is not None and (event.scenePos() - self._press).manhattanLength() > 4
        if not self.is_row and (self.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable):
            super().mouseReleaseEvent(event)
        if not moved and self.key and event.button() == Qt.MouseButton.LeftButton:
            additive = bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
            self.view.clicked(self.key, additive)
        elif moved and not self.is_row:
            self.view.node_moved(self)
        self._press = None

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and not self.view._building:
            self.view.reroute()
        return super().itemChange(change, value)


class FlowView(QGraphicsView):
    def __init__(self, controller, parent=None, mode="review"):
        super().__init__(parent)
        self.controller = controller
        # review: the interactive flowchart; general: tools and kinds of data only (for an image);
        # specific: the recorded run, with layer names and values (for an image)
        self.mode = mode
        self.left_out_override = None
        self.show_notes = True
        self.show_numbers = True
        self.gap_x = GAP_X   # room between columns (images can make it narrower)
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setBackgroundBrush(QBrush(QColor(style.CANVAS)))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.highlight_keys: set = set()
        self.node_of_key: dict = {}      # any selectable key -> top-level node key
        self.nodes: dict = {}            # node key -> Box
        self.edge_specs: list = []       # (from node, to node, label)
        self.dashed: set = set()         # edge specs to or from left-out steps
        self.edges: list = []            # (from node, to node, item)
        self._building = False
        self._fitted = False
        self.scene_.selectionChanged.connect(self._rubber_band)

    # -------------------------------------------------------------- events

    def clicked(self, key, additive):
        self.controller.select(key, additive)

    def node_moved(self, box):
        self.controller.positions[box.key] = QPointF(box.pos())

    def mousePressEvent(self, event):
        item = self.itemAt(event.pos())
        self._background_press = None
        if item is None:
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag if shift else QGraphicsView.DragMode.ScrollHandDrag)
            if not shift and event.button() == Qt.MouseButton.LeftButton:
                self._background_press = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        start = getattr(self, "_background_press", None)
        self._background_press = None
        if start is not None and self.mode == "review" and (event.pos() - start).manhattanLength() < 4:
            # a click on the empty background (not a drag to pan): nothing selected any more
            self.controller.select("overview")

    def contextMenuEvent(self, event):
        """Right-click a step: start the recipe from its result."""
        if self.mode != "review":
            return super().contextMenuEvent(event)
        item = self.itemAt(event.pos())
        while item is not None and not (isinstance(item, Box) and item.key and item.key.startswith("step:")):
            item = item.parentItem()
        if item is None:
            return super().contextMenuEvent(event)
        from qgis.PyQt.QtWidgets import QMenu
        menu = QMenu(self)
        if self.controller.start_menu(item.key, menu):
            menu.exec(event.globalPos())

    def _rubber_band(self):
        if self._building or self.dragMode() != QGraphicsView.DragMode.RubberBandDrag:
            return
        keys = [i.key for i in self.scene_.selectedItems() if isinstance(i, Box) and i.key]
        if len(keys) > 1:
            self.controller.select_many(keys)

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def fit(self):
        rect = self.scene_.itemsBoundingRect().adjusted(-20, -20, 20, 20)
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        if self.transform().m11() > 1.0:
            self.resetTransform()

    def reset_layout(self):
        self.controller.positions.clear()
        self.rebuild()

    # ------------------------------------------------------------- drawing

    def _html_height(self, html, width, size):
        probe = QGraphicsTextItem()
        font = QFont()
        font.setPointSize(size)
        probe.setFont(font)
        _set_html(probe, html)
        probe.setTextWidth(width)
        return probe.document().size().height()

    def _box(self, key, w, html, fill, border, dashed=False, min_h=0, size=9, radius=6, movable=True, parent=None):
        h = max(min_h, self._html_height(html, w - 2 * PAD, size) + 8)
        box = Box(self, key, QRectF(0, 0, w, h), fill, border, dashed, radius, movable=movable)
        text = QGraphicsTextItem(box)
        font = QFont()
        font.setPointSize(size)
        text.setFont(font)
        text.setDefaultTextColor(QColor(style.INK))
        _set_html(text, html)
        text.setTextWidth(w - 2 * PAD + 8)
        text.setPos(PAD - 4, 2)
        text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        if parent is not None:
            box.setParentItem(parent)
        else:
            self.scene_.addItem(box)
        return box

    def _row(self, parent, key, y, width, label, value, colour, fill, bold_value=True, icon=None) -> float:
        h = 21
        row = Box(self, key, QRectF(0, 0, width - 2, h), fill or "#00000000", fill or "#00000000", radius=0, is_row=True)
        if not fill:
            row.setPen(QPen(Qt.PenStyle.NoPen))
            row.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        row.setParentItem(parent)
        row.setPos(1, y)
        font = QFont()
        font.setPointSize(8)
        vfont = QFont(font)
        vfont.setBold(bold_value)
        val = QGraphicsSimpleTextItem(_elide(value, vfont, (width - 2 * PAD) * 0.55), row)
        val.setFont(vfont)
        val.setBrush(QBrush(QColor(colour if not bold_value else style.INK)))
        val_w = val.boundingRect().width()
        val.setPos(width - PAD - val_w - 4, 3)
        indent = 0
        if icon:
            pix = QGraphicsPixmapItem(QPixmap.fromImage(icons.image(icon[0], 36)), row)
            pix.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            pix.setScale(1 / 3)
            pix.setPos(PAD - 2, 4)
            indent = 15
        text = QGraphicsSimpleTextItem(_elide(label, font, width - 2 * PAD - val_w - 12 - indent), row)
        text.setFont(font)
        text.setBrush(QBrush(QColor(colour)))
        text.setPos(PAD + indent, 3)
        row.setToolTip(f"{label}: {value}" + (f" ({icon[1]})" if icon else ""))
        return h

    def rebuild(self):
        self._building = True
        self.scene_.clear()
        self.nodes, self.node_of_key, self.edge_specs, self.edges = {}, {}, [], []
        plan = self.controller.plan
        simple = self.controller.detail == "simple" if self.mode == "review" else self.mode in ("general", "named")
        width = 210 if simple else 250

        collapsed = {i for i in range(len(plan.groups)) if i not in self.controller.expanded_groups} if simple else set()
        if self.mode in ("general", "named"):
            collapsed = set(range(len(plan.groups)))
        node_for_step = {}
        for step_plan in plan.steps:
            g = self.controller.group_of(step_plan.number)
            node_for_step[step_plan.number] = f"group:{g}" if g in collapsed else f"step:{step_plan.step.entry_id}"
        depth = builder.layer_columns(plan)
        col, members = {}, {}
        for step_plan in plan.steps:
            key = node_for_step[step_plan.number]
            col[key] = min(col.get(key, 99), depth.get(step_plan.number, 1))
            members.setdefault(key, []).append(step_plan)
        left, left_specs = self._left_out_links(plan, node_for_step, col)

        # edges
        specs = []
        for step_plan in plan.steps:
            target = node_for_step[step_plan.number]
            for param in step_plan.params:
                d = step_plan.step.alg.parameterDefinition(param.name)
                label = "extent" if d is not None and d.type() == "extent" else ""
                for src in param.sources:
                    source = node_for_step.get(src[1]) if src[0] == "link" else f"input:{src[1]}"
                    if source and source != target:
                        specs.append((source, target, label))
            for param in step_plan.params:
                if param.kind in ("output", "temp") and param.role in ("final", "save"):
                    specs.append((node_for_step[step_plan.number], param.uid, ""))
        self.dashed = set(left_specs)
        self.edge_specs = list(dict.fromkeys(specs + left_specs))
        # a step always sits right of every step it uses (left-out steps can feed later ones)
        for _round in range(len(col) + 1):
            moved = False
            for a, b, _l in self.edge_specs:
                if b in col and col[b] <= (col.get(a, 0)):
                    col[b] = col.get(a, 0) + 1
                    moved = True
            if not moved:
                break
        last_col = max(col.values(), default=0) + 1

        # nodes per column
        columns = {0: [f"input:{uid}" for uid in plan.inputs]}
        for key in list(members) + list(left):
            columns.setdefault(col[key], []).append(key)
        outputs = [(step_plan, param) for step_plan in plan.steps for param in step_plan.params
                   if param.kind in ("output", "temp") and param.role in ("final", "save")]
        columns[last_col] = [param.uid for _sp, param in outputs]

        y_center = {}
        for c in range(0, last_col + 1):
            keys = columns.get(c, [])
            if c > 0:
                def bary(k):
                    ys = [y_center[a] for a, b, _l in self.edge_specs if b == k and a in y_center]
                    return sum(ys) / len(ys) if ys else 1e9
                keys.sort(key=bary)
            x = 30 + c * (width + self.gap_x)
            y = 30
            for key in keys:
                if key in left:
                    box, top_extra = self._left_out_node(key, left[key], width), 0
                else:
                    box, top_extra = self._make_node(key, members, plan, width, simple, outputs)
                y += top_extra
                pos = self.controller.positions.get(key)
                box.setPos(pos if pos is not None else QPointF(x, y))
                self.nodes[key] = box
                y_center[key] = box.pos().y() + min(box.rect().height() / 2, 26)
                y += box.rect().height() + GAP_Y

        if self.show_left_out and self.mode != "general":
            bottom = max((b.sceneBoundingRect().bottom() for b in self.nodes.values()), default=30)
            self._draw_left_out(30, bottom + 50, width)

        self._building = False
        self.reroute()
        self.update_highlight()
        if not self._fitted:
            self._fitted = True
            self.fit()

    def _make_node(self, key, members, plan, width, simple, outputs):
        """Create a top-level node box; returns (box, space needed above it)."""
        if key.startswith("input:"):
            input_plan = plan.inputs[key[6:]]
            icon, words = icons.layer_icon(input_plan.layer_kind)
            if self.mode == "general":
                # only the kind of layer; a number tells apart inputs of the same kind
                same = [k for k, i in plan.inputs.items() if icons.layer_icon(i.layer_kind)[1] == words]
                label = words.capitalize() + (f" {same.index(key[6:]) + 1}" if len(same) > 1 else "")
                html = f'{style.tag_html("LAYER")} {_img(icon)} <b>{_esc(label)}</b>'
            elif self.mode in ("specific", "named"):
                html = (f'{style.tag_html("LAYER")} {_img(icon)} <b>{_esc(input_plan.recorded)}</b><br>'
                        f'<span style="color:{style.MUTED}">{_esc(words)} · ingredient “{_esc(input_plan.label)}”</span>')
            else:
                html = (f'{style.tag_html("LAYER")} {_img(icon)} <b>{_esc(input_plan.label)}</b><br>'
                        f'<span style="color:{style.MUTED}">was {_esc(input_plan.recorded)}</span>')
            if input_plan.format and self.mode in ("review", "specific") and not simple:
                html += f'<br><span style="color:{style.MUTED}">format: {_esc(input_plan.format)}</span>'
            box = self._box(key, width, html, style.LAYER["bg"], style.LAYER["border"], min_h=48)
            box.setToolTip(f"{input_plan.label}: {words}" + (f" · {input_plan.format}" if input_plan.format else ""))
            self.node_of_key[key] = key
            return box, 0
        if key.startswith("out:"):
            step_plan, param = next((s, p) for s, p in outputs if p.uid == key)
            role = "final output" if param.role == "final" else "intermediate, kept"
            styled = " · keeps my style" if param.keep_style else ""
            icon, words = icons.layer_icon(param.layer_kind)
            if self.mode == "general":
                html = (f"{_img(icon)} <b>{_esc(words.capitalize())}</b><br><span style='color:{style.MUTED}'>"
                        f"{role} of {_esc(step_plan.step.alg.displayName())}</span>")
            elif self.mode in ("specific", "named"):
                of = f"step {step_plan.number}" if self.show_numbers else _esc(step_plan.step.alg.displayName())
                html = (f"{_img(icon)} <b>{_esc(param.default_display)}</b><br><span style='color:{style.MUTED}'>"
                        f"{_esc(words)} · {role} of {of}{styled}</span>")
            else:
                html = (f"{_img(icon)} <b>{_esc(param.display)}</b><br><span style='color:{style.MUTED}'>"
                        f"{role} of step {step_plan.number}{styled}</span>")
            if param.format and self.mode in ("review", "specific") and not simple:
                html += f"<br><span style='color:{style.MUTED}'>recorded as: {_esc(param.format)}</span>"
            box = self._box(key, width, html, style.OUTPUT["bg"], style.OUTPUT["border"], min_h=44, radius=12)
            box.setToolTip(f"{param.display}: {words}" + (f" · recorded as {param.format}" if param.format else ""))
            self.node_of_key[key] = key
            return box, 0

        sps = members[key]
        if key.startswith("group:"):
            index = int(key[6:])
            title = builder.group_title(plan, plan.groups[index])
            html = (f"<b>{_esc(title)}</b><br><span style='color:{style.MUTED}'>steps "
                    f"{', '.join(str(step_plan.number) for step_plan in sps)} · click to see each</span>")
            if self.mode == "general":
                html = (f"<b>{len(sps)} × {_esc(sps[0].step.alg.displayName())}</b><br>"
                        f"<span style='color:{style.MUTED}'>repeated with different settings</span>")
            elif self.mode in ("named", "specific") and not self.show_numbers:
                html = (f"<b>{_esc(title)}</b><br><span style='color:{style.MUTED}'>{len(sps)} runs</span>")
            elif self.mode == "named":
                html = (f"<b>{len(sps)} × {_esc(sps[0].step.alg.displayName())}</b><br>"
                        f"<span style='color:{style.MUTED}'>steps {', '.join(str(step_plan.number) for step_plan in sps)}</span>")
            box = self._box(key, width, html, style.STEP["bg"], style.STEP["border"], min_h=50)
            shadow = QGraphicsRectItem(QRectF(4, 4, width, box.rect().height()), box)
            shadow.setPen(QPen(QColor(style.STEP["border"]), 1))
            shadow.setBrush(QBrush(QColor(style.SHADOW)))
            shadow.setFlag(QGraphicsItem.GraphicsItemFlag.ItemStacksBehindParent, True)
        else:
            step_plan = sps[0]
            name = step_plan.step.alg.displayName()
            badge = ""
            if step_plan.step.synthetic:
                badge = (f"<br><span style='background-color:{style.INFO['bg']}; color:{style.INFO['text']};'>"
                         f"&nbsp;{_esc(step_plan.step.synthetic)}&nbsp;</span>")
            if step_plan.step.alg_id in builder.FIELD_CALCULATORS and self.mode != "general":
                formula = str(step_plan.step.inputs.get("FORMULA", ""))
                formula = formula if len(formula) <= 48 else formula[:47] + "…"
                badge += (f"<br><span style='color:{style.INFO['text']};'>{_esc(step_plan.step.inputs.get('FIELD_NAME', ''))}"
                          f" = {_esc(formula)}</span>")
            if step_plan.note and self.show_notes:
                note = step_plan.note if len(step_plan.note) <= 90 else step_plan.note[:89] + "…"
                badge += f"<br><i style='color:{style.MUTED};'>✎ {_esc(note)}</i>"
            statics = [p for p in step_plan.params if p.kind == "static"]
            numbered = f"{step_plan.number}. {_esc(name)}" if self.show_numbers else _esc(name)
            if self.mode in ("general", "named"):
                html = f"<b>{_esc(name) if self.mode == 'general' else numbered}</b>{badge}"
                box = self._box(key, width, html, style.STEP["bg"], style.STEP["border"], min_h=34)
            elif simple:
                changed = sum(1 for p in statics if not p.expose and not p.is_default)
                fixed = sum(1 for p in statics if not p.expose)
                html = (f"<b>{numbered}</b><br><span style='color:{style.MUTED}'>"
                        f"{_esc(step_plan.step.time_text[-8:])} · {fixed} fixed setting{'s' if fixed != 1 else ''}"
                        f"{f' ({changed} changed)' if changed else ''}</span>{badge}")
                box = self._box(key, width, html, style.STEP["bg"], style.STEP["border"], min_h=50)
            else:
                html = (f"<b>{numbered}</b><br><span style='color:{style.MUTED}'>"
                        f"{_esc(step_plan.step.time_text[-8:])}</span>{badge}")
                box = self._box(key, width, html, style.STEP["bg"], style.STEP["border"], min_h=40)
                ry = box.rect().height()
                # asked first, then values changed from the tool's default, then defaults
                ordered = sorted(statics, key=lambda p: (0 if p.expose else 1 if not p.is_default else 2))
                for p in ordered:
                    if p.expose:
                        definition = step_plan.step.alg.parameterDefinition(p.name)
                        ry += self._row(box, p.uid, ry, width, p.display, p.shown_value, style.SETTING["text"],
                                        style.SETTING["bg"], icon=icons.setting_icon(definition))
                        self.node_of_key[p.uid] = key
                    elif not p.is_default:
                        ry += self._row(box, key, ry, width, p.label, p.shown_value, style.INK, None)
                    elif self.mode != "specific":   # images: only settings that differ from the defaults
                        ry += self._row(box, key, ry, width, p.label, p.shown_value, style.QUIET, None, bold_value=False)
                for p in step_plan.params:
                    if p.kind in ("output", "temp") and p.role == "intermediate":
                        ry += self._row(box, p.uid, ry, width, f"→ {p.display}", "not saved", style.QUIET, None, bold_value=False)
                        self.node_of_key[p.uid] = key
                box.setRect(QRectF(0, 0, width, ry + 4))
        for step_plan in sps:
            self.node_of_key[f"step:{step_plan.step.entry_id}"] = key
        self.node_of_key[key] = key

        # settings asked when the model runs: one violet box above the step
        top_extra = 0
        asked = [(step_plan, param) for step_plan in sps for param in step_plan.params if param.expose]
        if simple and asked and self.mode == "review":   # images leave asked settings out
            html = f'{style.tag_html("SETTING")} <b style="color:{style.SETTING["text"]}">{len(asked)} to enter</b>'
            ask = self._box(f"ask:{key}", width, html, style.SETTING["bg"], style.SETTING["border"], min_h=24,
                            movable=False, parent=box)
            ry = ask.rect().height()
            for asp, param in asked[:3]:
                definition = asp.step.alg.parameterDefinition(param.name)
                ry += self._row(ask, param.uid, ry, width, param.display, "" if self.mode == "general" else param.shown_value,
                                style.SETTING["text"], None, icon=icons.setting_icon(definition))
                self.node_of_key[param.uid] = key
            if len(asked) > 3:
                ry += self._row(ask, key, ry, width, f"+ {len(asked) - 3} more", "", style.SETTING["text"], None)
            ask.setRect(QRectF(0, 0, width, ry + 4))
            ask.setPos(0, -(ry + 4) - 10)
            self.node_of_key[f"ask:{key}"] = key
            top_extra = ry + 14
        return box, top_extra

    def _left_out_links(self, plan, node_for_step, col):
        """Left-out steps placed among the others: {node key: step}, and dashed links
        from what they used and to the steps that used their results."""
        project, snapshot = QgsProject.instance(), self.controller.snapshot
        by_entry = {step_plan.step.entry_id: step_plan for step_plan in plan.steps}
        input_keys = []
        for uid, input_plan in plan.inputs.items():
            for _n, _p, value in input_plan.used_by:
                if isinstance(value, str) and value:
                    input_keys.append((builder.norm_source(value), f"input:{uid}"))

        def layer_keys(step):
            keys = []
            for name, value in step.inputs.items():
                d = step.alg.parameterDefinition(name)
                if d is None or d.isDestination() or d.type() not in builder.LAYER_TYPES:
                    continue
                for item in value if isinstance(value, list) else [value]:
                    if isinstance(item, str) and item:
                        keys.append(builder.norm_source(item))
            return keys

        produced, left, specs = [], {}, []
        self.left_uses = {}
        for step in self.controller.steps:
            if not step.ok or step.alg is None:
                continue
            step_plan = by_entry.get(step.entry_id)
            if step_plan is not None:
                node = node_for_step[step_plan.number]
                for key in layer_keys(step):
                    source = next((n for k, n in reversed(produced) if builder.same_source(k, key)), None)
                    if source in left:
                        specs.append((source, node, ""))
            elif self.show_left_out:
                node = f"step:{step.entry_id}"
                left[node] = step
                sources, names = set(), []
                for key in layer_keys(step):
                    source = next((n for k, n in reversed(produced) if builder.same_source(k, key)), None) or \
                        next((n for k, n in input_keys if builder.same_source(k, key)), None)
                    if source:
                        sources.add(source)
                    else:
                        layer = builder._layer_for(key, project)
                        names.append(layer.name() if layer is not None else str(key[1] or key[0]).split("/")[-1])
                self.left_uses[node] = names
                col[node] = max((col.get(src, 0) for src in sources), default=0) + 1
                specs += [(src, node, "") for src in sorted(sources)]
            else:
                continue
            for value in (step.results or {}).values():
                for key in builder._result_keys(value, project, snapshot)[0]:
                    if key:
                        produced.append((key, node))
        return left, list(dict.fromkeys(specs))

    def _left_out_node(self, key, step, width):
        reason = self.controller.reasons.get(step.entry_id) or "left out"
        uses = self.left_uses.get(key)
        uses = f"<br><span style='color:{style.MUTED}'>used {_esc(', '.join(uses))}</span>" if uses else ""
        html = (f"<b>{_esc(step.label)}</b><br><span style='color:{style.MUTED}'>{_esc(step.time_text[-8:])} · "
                f"not in the recipe</span>{uses}<br><span style='background-color:{style.WARN['bg']}; "
                f"color:{style.WARN['text']}'>&nbsp;{_esc(reason)}&nbsp;</span>")
        box = self._box(key, width, html, style.LEFT_OUT["bg"], style.LEFT_OUT["border"], dashed=True, min_h=50)
        box.setToolTip("Left out of the recipe. Select it and tick “Include this step” to add it.")
        self.node_of_key[key] = key
        return box

    def _draw_left_out(self, x, y, width):
        left = [s for s in self.controller.steps if s.entry_id not in self.controller.included and (not s.ok or s.alg is None)]
        edits = [(i, e) for i, e in enumerate(self.controller.edits) if i not in self.controller.converted_edits]
        if not left and not edits:
            return
        title = QGraphicsSimpleTextItem("Not repeatable by a recipe")
        font = QFont()
        font.setBold(True)
        title.setFont(font)
        title.setBrush(QBrush(QColor(style.MUTED)))
        title.setPos(x, y)
        self.scene_.addItem(title)
        y += 26
        col, row_h = 0, 0
        entries = [("step", s) for s in left] + [("edit", e) for e in edits]
        for kind, item in entries:
            if kind == "step":
                step = item
                reason = self.controller.reasons.get(step.entry_id) or (
                    "failed, or the tool isn't available" if not step.ok else "left out")
                html = (f"<b>{_esc(step.label)}</b><br><span style='color:{style.MUTED}'>{_esc(step.time_text[-8:])}</span><br>"
                        f"<span style='background-color:{style.WARN['bg']}; color:{style.WARN['text']}'>"
                        f"&nbsp;{_esc(reason)}&nbsp;</span>")
                box = self._box(f"step:{step.entry_id}" if step.ok else "", width, html, style.LEFT_OUT["bg"],
                                style.LEFT_OUT["border"], dashed=True, min_h=50, movable=False)
            else:
                index, edit = item
                html = (f"<b>✎ Edited by hand: {_esc(edit.layer_name)}</b><br>"
                        f"<span style='color:{style.MUTED}'>{_esc(edit.timestamp.toString('HH:mm:ss'))}: "
                        f"{_esc(edit.describe())}</span>")
                box = self._box(f"edit:{index}", width, html, style.STEP["bg"], style.LEFT_OUT["border"], dashed=True,
                                min_h=50, movable=False)
            box.setPos(x + col * (width + 30), y)
            row_h = max(row_h, box.rect().height())
            col += 1
            if col == 4:
                col, y, row_h = 0, y + row_h + 20, 0

    # -------------------------------------------------------------- routing

    def _obstacles(self, exclude):
        rects = []
        for key, box in self.nodes.items():
            if key in exclude:
                continue
            rects.append(box.sceneBoundingRect().adjusted(-CLEAR, -CLEAR, CLEAR, CLEAR))
            for child in box.childItems():
                if isinstance(child, Box) and not child.is_row and child.isVisible():
                    rects.append(child.sceneBoundingRect().adjusted(-CLEAR, -CLEAR, CLEAR, CLEAR))
        return rects

    @staticmethod
    def _h_free(y, x1, x2, rects):
        lo, hi = min(x1, x2), max(x1, x2)
        return not any(r.top() < y < r.bottom() and r.left() < hi and r.right() > lo for r in rects)

    @staticmethod
    def _v_free(x, y1, y2, rects):
        lo, hi = min(y1, y2), max(y1, y2)
        return not any(r.left() < x < r.right() and r.top() < hi and r.bottom() > lo for r in rects)

    def _route(self, a, b, out_lane, in_lane):
        """Orthogonal route from box a to box b avoiding other boxes: list of points."""
        ra, rb = a.sceneBoundingRect(), b.sceneBoundingRect()
        start = QPointF(ra.right(), ra.top() + min(ra.height() / 2, 26))
        end = QPointF(rb.left(), rb.top() + min(rb.height() / 2, 26))
        if rb.left() <= ra.right() + 2 * CLEAR:
            return None  # boxes overlap horizontally (moved by hand): use a curve
        rects = self._obstacles({a.key, b.key})
        x1 = ra.right() + CLEAR + 4 + out_lane * 6
        x2 = rb.left() - CLEAR - 4 - in_lane * 6
        x2 = max(x2, x1)
        # 1) straight across at the start height, turn down/up just before the target
        if self._h_free(start.y(), x1, x2, rects) and self._v_free(x2, start.y(), end.y(), rects):
            return [start, QPointF(x2, start.y()), QPointF(x2, end.y()), end]
        # 2) turn right after the source, then straight across at the target height
        if self._v_free(x1, start.y(), end.y(), rects) and self._h_free(end.y(), x1, x2, rects):
            return [start, QPointF(x1, start.y()), QPointF(x1, end.y()), end]
        # 3) go around: find a free height between or below/above the boxes in the way
        candidates = {start.y(), end.y()}
        for r in rects:
            if r.right() > x1 and r.left() < x2:
                candidates.update({r.top() - 2, r.bottom() + 2})
        for y in sorted(candidates, key=lambda v: abs(v - start.y()) + abs(v - end.y())):
            if (self._h_free(y, x1, x2, rects) and self._v_free(x1, start.y(), y, rects)
                    and self._v_free(x2, y, end.y(), rects)):
                return [start, QPointF(x1, start.y()), QPointF(x1, y), QPointF(x2, y), QPointF(x2, end.y()), end]
        return [start, QPointF(x1, start.y()), QPointF(x1, end.y()), end]

    @staticmethod
    def _rounded(points):
        path = QPainterPath(points[0])
        for i in range(1, len(points) - 1):
            p0, p1, p2 = points[i - 1], points[i], points[i + 1]
            d1 = max(abs(p1.x() - p0.x()), abs(p1.y() - p0.y()))
            d2 = max(abs(p2.x() - p1.x()), abs(p2.y() - p1.y()))
            r = min(RADIUS, d1 / 2, d2 / 2)
            if r <= 0.5:
                path.lineTo(p1)
                continue
            a = p1 + (p0 - p1) * (r / d1)
            b = p1 + (p2 - p1) * (r / d2)
            path.lineTo(a)
            path.quadTo(p1, b)
        path.lineTo(points[-1])
        return path

    def reroute(self):
        for _a, _b, item in self.edges:
            self.scene_.removeItem(item)
        self.edges = []
        out_count, in_count = {}, {}
        specs = [s for s in self.edge_specs if s[0] in self.nodes and s[1] in self.nodes]
        specs.sort(key=lambda s: self.nodes[s[1]].pos().y())
        marked = set()   # boxes that already show the extent marker: one per box, never stacked
        for source, target, label in specs:
            a, b = self.nodes[source], self.nodes[target]
            out_lane = out_count.get(source, 0)
            in_lane = in_count.get(target, 0)
            out_count[source] = out_lane + 1
            in_count[target] = in_lane + 1
            points = self._route(a, b, out_lane, in_lane)
            if points is None:
                ra, rb = a.sceneBoundingRect(), b.sceneBoundingRect()
                p1 = QPointF(ra.right(), ra.center().y())
                p2 = QPointF(rb.left(), rb.center().y())
                path = QPainterPath(p1)
                path.cubicTo(QPointF(p1.x() + 60, p1.y()), QPointF(p2.x() - 60, p2.y()), p2)
                end, prev = p2, QPointF(p2.x() - 10, p2.y())
            else:
                path = self._rounded(points)
                end, prev = points[-1], points[-2]
            dashed = (source, target, label) in self.dashed
            extent = label == "extent"   # only the layer's extent is used, not its features: a dotted line
            colour = QColor(style.QUIET if dashed else style.LINE)
            item = QGraphicsPathItem(path)
            pen = QPen(colour, 1.8 if extent else 1.6)
            if dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            elif extent:
                pen.setStyle(Qt.PenStyle.DotLine)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            item.setPen(pen)
            item.setZValue(-1)
            self.scene_.addItem(item)
            self.edges.append((source, target, item))
            arrow = QGraphicsPolygonItem(QPolygonF([end, QPointF(end.x() - 8, end.y() - 4), QPointF(end.x() - 8, end.y() + 4)]))
            arrow.setBrush(QBrush(colour))
            arrow.setPen(QPen(Qt.PenStyle.NoPen))
            self.scene_.addItem(arrow)
            self.edges.append((source, target, arrow))
            item.setToolTip("Only the extent of this layer is used" if extent else "")
            if extent and target not in marked:
                marked.add(target)
                mark = QGraphicsPixmapItem(QPixmap.fromImage(icons.image("mActionZoomToLayer.svg", 42)))
                mark.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
                mark.setScale(1 / 3)
                mark.setPos(end.x() - 34, end.y() - 18)
                mark.setToolTip("The extent comes from this layer")
                mark.setData(0, "extent")
                self.scene_.addItem(mark)
                self.edges.append((source, target, mark))
                if self.mode == "specific":   # the recorded-run images say it in words too
                    t = QGraphicsSimpleTextItem("extent")
                    t.setBrush(QBrush(QColor(style.INFO["text"])))
                    font = QFont()
                    font.setPointSize(8)
                    t.setFont(font)
                    t.setPos(end.x() - 38 - t.boundingRect().width(), end.y() - 19)
                    self.scene_.addItem(t)
                    self.edges.append((source, target, t))
            elif label and not extent:
                t = QGraphicsSimpleTextItem(label)
                t.setBrush(QBrush(QColor(style.INFO["text"])))
                font = QFont()
                font.setPointSize(8)
                t.setFont(font)
                t.setPos(prev.x() - t.boundingRect().width() - 4, end.y() - 16)
                self.scene_.addItem(t)
                self.edges.append((source, target, t))
        if not self._building:
            self.update_highlight()

    # ----------------------------------------------------------- highlight

    @property
    def show_left_out(self) -> bool:
        return self.controller.show_left_out if self.left_out_override is None else self.left_out_override

    def update_highlight(self):
        if self.mode != "review":
            self.highlight_keys = set()
            return
        keys = list(self.controller.multi) or [self.controller.selected]
        self.highlight_keys = set(keys)
        nodes = {self.node_of_key.get(k) for k in keys} - {None}
        related = set(nodes)
        if nodes and not self.controller.multi:
            forward, backward = {}, {}
            for a, b, _l in self.edge_specs:
                forward.setdefault(a, set()).add(b)
                backward.setdefault(b, set()).add(a)
            for graph in (forward, backward):
                todo = list(nodes)
                while todo:
                    for nxt in graph.get(todo.pop(), ()):
                        if nxt not in related:
                            related.add(nxt)
                            todo.append(nxt)
        dim = bool(nodes) and not self.controller.multi
        for key, box in self.nodes.items():
            box.setOpacity(0.3 if dim and key not in related else 1.0)
        for a, b, item in self.edges:
            item.setOpacity(0.25 if dim and not (a in related and b in related) else 1.0)
        self.scene_.update()


def _elide(text, font, width) -> str:
    return QFontMetrics(font).elidedText(str(text), Qt.TextElideMode.ElideRight, int(max(width, 20)))


def svg_text(controller, mode: str, left_out: bool, notes: bool = True, numbers: bool = True, title: str = "",
             spacing: float = 1.0) -> str:
    """The workflow as SVG text. mode: general (tools and kinds of data), named or
    specific (the recorded run: layer names and values)."""
    with style.light():   # saved images always use the light colours
        return _svg_text(controller, mode, left_out, notes, numbers, title or controller.model_name, spacing)


def _svg_text(controller, mode, left_out, notes, numbers, title_text, spacing=1.0) -> str:
    from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice
    from qgis.PyQt.QtSvg import QSvgGenerator

    view = FlowView(controller, mode=mode)
    view.left_out_override = left_out
    view.show_notes = notes
    view.show_numbers = numbers
    view.gap_x = max(48, int(GAP_X * spacing))
    view.rebuild()
    scene = view.scene_
    content = scene.itemsBoundingRect()
    title = QGraphicsSimpleTextItem(title_text)
    font = QFont()
    font.setPointSize(13)
    font.setBold(True)
    title.setFont(font)
    title.setPos(content.left(), content.top() - 40)
    scene.addItem(title)
    rect = scene.itemsBoundingRect().adjusted(-24, -24, 24, 24)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    generator = QSvgGenerator()
    generator.setOutputDevice(buffer)
    generator.setSize(rect.size().toSize())
    generator.setViewBox(QRectF(0, 0, rect.width(), rect.height()))
    generator.setTitle(controller.model_name)
    painter = QPainter(generator)
    scene.render(painter, QRectF(0, 0, rect.width(), rect.height()), rect)
    painter.end()
    buffer.close()
    view.deleteLater()
    return bytes(data).decode("utf-8")


def _img(name: str) -> str:
    return f'<img src="souschef-icon:{name}" width="14" height="14" style="vertical-align: middle">'


def _set_html(item, html: str):
    """setHtml, with QGIS theme icons referenced as souschef-icon:<file> made available to the text."""
    doc = item.document()
    for name in set(re.findall(r'souschef-icon:([\w.]+)', html)):
        doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(f"souschef-icon:{name}"), icons.image(name, 42))
    item.setHtml(html)


def _esc(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
