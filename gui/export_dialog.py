"""The Export window: still images, animations and written recipes in one place.

A switch at the top chooses the kind of output; a gallery of thumbnails, drawn from the
recipe itself, chooses the design (favourites first); the side panel shows only the
settings that design uses, grouped into sections that open and close."""

from __future__ import annotations

import os
import types
import tempfile

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import QDateTime, QRectF, Qt, QTimer, QUrl
from qgis.PyQt.QtGui import QColor, QDesktopServices, QImage, QMovie, QPainter, QPixmap, QTextDocument
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..core import builder
from ..i18n import tr
from . import animation, images, written
from .image_dialog import _on_checkerboard, _size, recipe_pdf, save_pdf, to_image, to_slide, with_background
from .widgets import CSS, Gallery, Section, Segmented, colour_picker

KEY = "souschef/export/"
DETAILS = [("general", "General"), ("named", "Named"), ("specific", "Specific")]
DETAIL_TIPS = ("General: tools and kinds of data, no layer names. Named: the layers and steps of your run. "
               "Specific: also the settings you changed from the defaults.")
STILL_ABOUT = {
    "boxes": "the flowchart as it looks in the review window.",
    "cards": "each step a card, with the settings you changed listed on it.",
    "minimal": "small round icons on thin lines: clean, for slides and reports.",
    "metro": "each starting layer a coloured line, the tools its stations.",
    "swimlanes": "steps sorted into lanes: inputs, preparation, analysis, results.",
    "data": "the data in the middle: a picture of each layer's shapes, tools as small pills between them.",
    "storyboard": "one card per step with a small map of its result, in reading order.",
    "summary": "the workflow in numbers: totals, the tools used most, what it starts from and makes.",
    "mindmap": "the recipe in the middle, its results around it, then the tools that made them, out to the starting "
               "layers.",
    "river": "each layer a band as wide as its number of features (log scale), flowing into the tools and out again.",
    "board": "the steps as sticky notes on a corkboard: ingredients, preparation, cooking and served.",
    "comic": "storyboard panels as a comic strip, with speech bubbles in the cookbook's kitchen language.",
    "pilgrimage": "a knight's journey on old paper: a medieval building for each tool, each with a small map of its "
                  "result, inside an illuminated border.",
}
STILL_NAMES = {"boxes": "Boxes", "cards": "Cards", "minimal": "Minimal", "metro": "Metro map", "swimlanes": "Swimlanes",
               "data": "Data-centred", "storyboard": "Storyboard", "summary": "Summary sheet",
               "pilgrimage": "Pilgrimage", "mindmap": "Radial flowchart", "river": "Feature river", "board": "Sticky notes",
               "comic": "Comic strip"}
RECIPES = [
    ("cookbook", "Cookbook recipe", "a fun recipe card: word-art title, kitchen language, a serving size and a "
                                    "prep time. Saved as PDF."),
    ("written", "Written recipe", "plain instructions: what it makes, the ingredients and the method as numbered "
                                  "steps. PDF, web page, Markdown or text."),
    ("methods", "Methods paragraph", "the workflow as a paragraph for the methods section of a report or paper, with "
                                     "the QGIS version, tools and settings. Text or Markdown, or copy it."),
    ("checklist", "Checklist", "tick boxes for doing or checking the workflow by hand: before you start, each step, "
                               "and the results to check. PDF, Markdown or text."),
]
RECIPE_FORMATS = {"cookbook": [("pdf", "PDF")],
                  "written": [("pdf", "PDF"), ("html", "Web"), ("md", "Markdown"), ("txt", "Text")],
                  "methods": [("txt", "Text"), ("md", "Markdown")],
                  "checklist": [("pdf", "PDF"), ("md", "Markdown"), ("txt", "Text")]}
MAP_ANIMS = ("map_story", "map_metro", "map_pilgrimage")
# designs built from real maps: not available with General detail (it shows only the kinds of data)
MAP_STILLS = ("storyboard", "comic", "data")
MAP_ONLY = MAP_ANIMS + ("before_after", "storyboard", "comic", "data_flow")
OWN_COLOURS = ("pilgrimage", "map_pilgrimage", "typewriter", "kitchen", "assembly", "board_game",
               "comic")   # animations with their own look


def _check(text, key, default, tip=""):
    box = QCheckBox(tr(text))
    box.setChecked(QgsSettings().value(KEY + key, default, type=bool))
    if tip:
        box.setToolTip(tr(tip))
    return box


def _html_image(html: str, pictures: dict | None = None, width=520, height=680) -> QImage:
    doc = QTextDocument()
    for name, img in (pictures or {}).items():
        doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), img)
    doc.setHtml(html)
    doc.setTextWidth(width)
    image = QImage(width, min(int(doc.size().height()) + 4, height), QImage.Format.Format_RGB32)
    image.fill(QColor("#FFFFFF"))
    p = QPainter(image)
    doc.drawContents(p, QRectF(0, 0, width, image.height()))
    p.end()
    return image


class PartView:
    """The controller seen through one separate part: its own plan of just that part's steps.
    The controller's methods run against this plan; everything else is the controller's."""

    def __init__(self, controller, included, name):
        self._c = controller
        self.included = included
        others = {step_plan.step.entry_id for step_plan in controller.plan.steps} - included
        self.steps = [st for st in controller.steps if st.entry_id not in others]   # not the other parts' steps
        self.model_name = name
        self.plan = builder.analyse(self.selected_steps(), controller.choices, controller.context())

    def __getattr__(self, name):
        attr = getattr(type(self._c), name, None)
        if isinstance(attr, types.FunctionType):
            return attr.__get__(self)
        if isinstance(attr, property):
            return attr.fget(self)
        return getattr(self._c, name)


class ExportDialog(QDialog):
    def __init__(self, controller, parent=None, mode=None):
        super().__init__(parent)
        self.controller = controller
        s = QgsSettings()
        self.setWindowTitle(tr("Images & Animations — {name}").format(name=controller.model_name))
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setStyleSheet(CSS)
        self.resize(1240, 860)
        self._shuffle = 0
        self._movie = None
        self._svg = ""
        self._preview_dir = tempfile.mkdtemp(prefix="sous_chef_previews_")   # removed when the window closes
        self._previews = {}   # the animation's settings -> its preview GIF
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.mode = Segmented([("still", "🖼  Still image"), ("animation", "🎞  Animation"), ("recipe", "📖  Recipe")],
                              mode or s.value(KEY + "mode", "still"), mode=True)
        top = QHBoxLayout()
        top.addWidget(self.mode)
        top.addStretch(1)
        lay.addLayout(top)

        # the galleries, one per mode
        self.galleries = QStackedWidget()
        self.still_gallery = Gallery("still", [(k, STILL_NAMES[k], STILL_ABOUT[k]) for k, _l in images.STYLES],
                                     self._still_thumb, s.value(KEY + "style", "cards"))
        self.anim_gallery = Gallery("animation", [(k, n, d) for k, n, d in animation.KINDS], self._anim_thumb,
                                    s.value(KEY + "kind", "map_story"))
        self.recipe_gallery = Gallery("recipe", RECIPES, self._recipe_thumb, s.value(KEY + "recipe", "cookbook"))
        for g in (self.still_gallery, self.anim_gallery, self.recipe_gallery):
            self.galleries.addWidget(g)
            g.chosen.connect(self._changed)
        self.galleries.setFixedHeight(self.still_gallery.sizeHint().height() + 6)
        lay.addWidget(self.galleries)

        body = QHBoxLayout()
        lay.addLayout(body, 1)
        # the previews
        self.previews = QStackedWidget()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setStyleSheet("QScrollArea { background: #E9E6DF; border: none; border-radius: 8px; }")
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.picture)
        self.previews.addWidget(self.scroll)
        player = QFrame()
        player.setStyleSheet("QFrame#player { background: #E9E6DF; border-radius: 8px; }")
        player.setObjectName("player")
        pv = QVBoxLayout(player)
        self.movie_label = QLabel(tr("Press ▶ Preview to see the animation play here.\n"
                                     "Saving makes it at full size."))
        self.movie_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.movie_label.setStyleSheet("color: #6B7178;")
        pv.addWidget(self.movie_label, 1)
        bar = QHBoxLayout()
        self.play = QPushButton("▶  " + tr("Preview"))
        self.play.setToolTip(tr("Make the animation small and play it here"))
        self.play.clicked.connect(self.make_preview)
        self.pause_button = QPushButton("❚❚")
        self.pause_button.setCheckable(True)
        self.pause_button.setEnabled(False)
        self.pause_button.toggled.connect(lambda on: self._movie is not None and self._movie.setPaused(on))
        self.estimate = QLabel()
        self.estimate.setObjectName("estimate")
        self.estimate.setToolTip(tr("Roughly how long the animation runs, how big the GIF file is and how many "
                                    "frames it has, at the size and timing chosen"))
        bar.addWidget(self.play)
        bar.addWidget(self.pause_button)
        bar.addStretch(1)
        bar.addWidget(self.estimate)
        pv.addLayout(bar)
        self.previews.addWidget(player)
        self.text_preview = QTextBrowser()
        self.text_preview.setStyleSheet("QTextBrowser { background: #FFFFFF; border-radius: 8px; padding: 10px; }")
        self.previews.addWidget(self.text_preview)
        body.addWidget(self.previews, 1)

        # the settings
        side = QWidget()
        self.side = QVBoxLayout(side)
        self.side.setContentsMargins(4, 0, 8, 0)
        self._build_settings()
        self.side.addStretch(1)
        panel = QScrollArea()
        panel.setWidgetResizable(True)
        panel.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panel.setWidget(side)
        panel.setFixedWidth(450)
        right = QVBoxLayout()
        right.addWidget(panel, 1)
        buttons = QHBoxLayout()
        self.save_button = QPushButton(tr("Save…"))
        self.save_button.setDefault(True)
        self.save_button.setStyleSheet("QPushButton { background: #2F6DB5; color: white; border-radius: 5px; "
                                       "padding: 6px 16px; font-weight: 600; } QPushButton:disabled { background: #9BB5D6; }")
        self.save_button.clicked.connect(self.save)
        self.second_button = QPushButton(tr("Copy"))
        self.second_button.clicked.connect(self.second_action)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.close)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.second_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        right.addLayout(buttons)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.linkActivated.connect(self._status_link)
        right.addWidget(self.status)
        body.addLayout(right)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.refresh)
        self.mode.changed.connect(self._changed)
        self._changed()

    # ------------------------------------------------------------ settings
    def _build_settings(self):
        s = QgsSettings()

        def seg(items, key, default):
            w = Segmented(items, s.value(KEY + key, default))
            w.changed.connect(self._changed)
            return w

        def hook(box):
            box.toggled.connect(self._changed)
            return box

        # what's shown (all modes)
        self.shown = Section("What's shown", "shown")
        self.detail = self.shown.add(seg(DETAILS, "detail", "named"), "Detail")
        self.detail.setToolTip(tr(DETAIL_TIPS))
        self.title_edit = QLineEdit(s.value(KEY + "title", ""))
        self.title_edit.textChanged.connect(self._changed)
        self.shown.add(self.title_edit, "Title")
        self.numbers = self.shown.add(hook(_check("Step numbers", "numbers", True)))
        self.notes = self.shown.add(hook(_check("Notes on steps", "notes", True)))
        self.left_out = self.shown.add(hook(_check("Left-out steps", "left_out", self.controller.show_left_out,
                                                   "Steps that aren't part of the recipe, drawn grey and dashed")))
        self.legend = self.shown.add(hook(_check("Legend", "legend", True)))
        self.inputs = self.shown.add(hook(_check("Starting layers", "inputs", True,
                                                 "The layers the workflow starts from, as their own cards")))
        self.inputs_side = self.shown.add(seg([("top", "Above"), ("left", "On the left")], "inputs_side", "top"),
                                          "Starting layers")
        parts = getattr(self.controller.plan, "parts", None) or []
        self.part = Segmented([(-1, "All")] + [(k, f"Part {k + 1}") for k in range(len(parts))], -1)
        self.part.setToolTip(tr("The steps form separate parts that don't use each other's results. All: one after "
                                "another (map animations zoom to each part in turn); or just one part."))
        self.part.changed.connect(self._changed)
        self.shown.add(self.part, "Parts")
        from . import snapshots
        self.final_output = QComboBox()
        self.final_output.addItem(tr("All the final outputs"), 0)
        for number, name in snapshots.output_choices(self.controller):
            self.final_output.addItem(name, number)
        self.final_output.setToolTip(tr("What the animation's ending shows. Outputs far apart are shown side by side."))
        self.final_output.currentIndexChanged.connect(self._changed)
        self.shown.add(self.final_output, "Shown at the end")
        self.fold = self.shown.add(hook(_check("Fold small steps", "fold", True,
                                               "Housekeeping steps (reproject, merge, fix geometries…) become a note on "
                                               "the next card instead of a card of their own")))
        self.pages = self.shown.add(seg([("auto", "Split when crowded"), ("one", "One page")], "pages", "auto"), "Pages")
        self.pages.setToolTip(tr("A long storyboard goes over several pages (a PDF with pages, or numbered "
                                 "PNG/SVG files) rather than shrinking every card"))
        self.captions = self.shown.add(hook(_check("Captions on the map", "captions", True)))
        self.stop_maps = self.shown.add(hook(_check("A map at each stop", "stop_maps", True,
                                                    "A small map of each step's result beside its building")))
        self.subtitle_on = self.shown.add(hook(_check("Subtitle", "subtitle_on", True,
                                                      "The line under the pilgrimage's title")))
        self.subtitle_edit = QLineEdit(QgsSettings().value(KEY + "subtitle", ""))
        self.subtitle_edit.setPlaceholderText(tr("Automatic: “A pilgrimage in … stops, from the village to the cathedral”"))
        self.subtitle_edit.textChanged.connect(self._changed)
        self.shown.add(self.subtitle_edit, "Subtitle text")
        self.maps = self.shown.add(hook(_check("Map of each step", "maps", False,
                                               "A small map of each step's result. Layers that are gone are made "
                                               "again by running the recipe on its starting layers.")))
        self.side.addWidget(self.shown)

        # wording (cookbook)
        self.wording = Section("Wording", "wording")
        self.shuffle = QPushButton("🎲  " + tr("Reshuffle wording and title"))
        self.shuffle.clicked.connect(self._reshuffle)
        self.wording.add(self.shuffle)
        note = QLabel(tr("New phrasings, a new adjective and a new word-art look each time."))
        note.setWordWrap(True)
        note.setStyleSheet("color: #5F6670;")
        self.wording.add(note)
        self.side.addWidget(self.wording)

        # maps
        self.map_section = Section("Maps", "maps")
        self.order = QComboBox()
        for key, label in animation.ORDERS:
            self.order.addItem(tr(label), key)
        self.order.setCurrentIndex(max(self.order.findData(s.value(KEY + "order", "auto")), 0))
        self.order.currentIndexChanged.connect(self._changed)
        self.map_section.add(self.order, "Drawing order")
        self.earlier = self.map_section.add(hook(_check("Keep earlier results, faint", "earlier", False,
                                                        "Off: each step's map shows only the layers it used and "
                                                        "its result")))
        self.basemaps = self.map_section.add(hook(_check("Background layers (basemaps)", "basemaps", False,
                                                         "The project's visible raster and web layers underneath. "
                                                         "Online basemaps need an internet connection.")))
        self.strip = self.map_section.add(seg([("scroll", "Scroll"), ("wrap", "Wrap")], "strip", "scroll"),
                                          "Flowchart strip")
        self.strip.setToolTip(tr("Long workflows: scroll to keep the current step in the middle, or wrap onto "
                                 "more rows"))
        self.wipe = self.map_section.add(seg([("across", "Across"), ("down", "Down"), ("fade", "Fade")], "wipe",
                                             "across"), "Before → after")
        self.overview = self.map_section.add(hook(_check("Overview map", "overview", False,
                                                         "A small plain world map in a corner, with a box where the "
                                                         "layers are")))
        self.overview_zoom = self.map_section.add(seg([("world", "World"), ("region", "Region")], "overview_zoom",
                                                      "world"), "Overview")
        self.corner = self.map_section.add(seg([("top-right", "Top right"), ("bottom-right", "Bottom right"),
                                                ("top-left", "Top left")], "corner", "top-right"), "Corner")
        self.side.addWidget(self.map_section)

        # drawing (build-up and spotlight)
        self.drawing = Section("Drawing", "drawing")
        self.style = QComboBox()
        for key, label in images.STYLES:
            if key not in ("storyboard", "data", "pilgrimage", "summary", "comic", "river"):
                self.style.addItem(tr(STILL_NAMES[key]), key)
        self.style.setCurrentIndex(max(self.style.findData(s.value(KEY + "anim_style", "cards")), 0))
        self.style.currentIndexChanged.connect(self._changed)
        self.drawing.add(self.style, "Image style")
        self.side.addWidget(self.drawing)

        # timing (animations)
        self.timing = Section("Timing", "timing")
        speed = QWidget()
        sh = QHBoxLayout(speed)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.addWidget(QLabel(tr("Relaxed")))
        self.speed = QSlider(Qt.Orientation.Horizontal)
        self.speed.setRange(0, 100)
        self.speed.setValue(int(s.value(KEY + "speed", 50)))
        self.speed.setToolTip(tr("Sets the drawing time, the pauses and the fades together"))
        self.speed.valueChanged.connect(self._speed_changed)
        sh.addWidget(self.speed, 1)
        sh.addWidget(QLabel(tr("Quick")))
        self.timing.add(speed, "Speed")
        self.customise = QPushButton(tr("Customise timing ▸"))
        self.customise.setFlat(True)
        self.customise.setStyleSheet("QPushButton { color: #2F6DB5; text-align: left; border: none; }")
        self.customise.clicked.connect(self._toggle_custom)
        self.timing.add(self.customise)
        self.spins = {}
        timings = (("draw", "Drawing each step", 2.5, 0.5, 10), ("pause", "Pause on each step", 1.6, 0, 10),
                   ("end", "Pause at the end", 4.0, 0, 20), ("fade", "Fade between steps", 0.6, 0.1, 3))
        for key, label, default, low, high in timings:
            box = QDoubleSpinBox()
            box.setRange(low, high)
            box.setSingleStep(0.25)
            box.setSuffix(" s")
            box.setValue(float(s.value(KEY + key, default)))
            box.valueChanged.connect(self._changed)
            self.spins[key] = self.timing.add(box, label)
        self.fps = Segmented([(8, "8"), (10, "10"), (12, "12"), (15, "15")], s.value(KEY + "fps", 12))
        self.fps.changed.connect(self._changed)
        self.timing.add(self.fps, "Frames a second")
        self.loop = self.timing.add(seg([("loop", "Loop"), ("once", "Play once")], "loop", "loop"), "Playback")
        self._custom_open = s.value(KEY + "custom_timing", False, type=bool)
        self.side.addWidget(self.timing)

        # look
        self.look = Section("Look", "look", open_=False, note="colours, spacing, fonts")
        self.theme = colour_picker(images.THEME_NAMES, s.value(KEY + "theme", "standard"))
        self.theme.currentIndexChanged.connect(self._changed)
        self.look.add(self.theme, "Colours")
        self.spacing = self.look.add(seg([(0.55, "Compact"), (1.0, "Normal"), (1.4, "Spacious")], "spacing", 1.0),
                                     "Spacing")
        self.themed_fonts = self.look.add(hook(_check("Fonts to match the design", "fonts", True,
                                                      "Each design uses fonts that suit it, from those on this "
                                                      "computer. Off: your system's standard font.")))
        self.title_font = self.look.add(self._font_box("title_font"), "Title font")
        self.text_font = self.look.add(self._font_box("text_font"), "Text font")
        self.side.addWidget(self.look)

        # file
        self.file = Section("File", "file")
        self.canvas = self.file.add(seg([("slide", "Slide (16:9)"), ("fit", "Fit the drawing")], "canvas", "slide"),
                                    "Page")
        self.canvas.setToolTip(tr("Slide: the drawing centred on a 16:9 page, ready for a presentation. Fit: the "
                                  "drawing's own shape."))
        self.fmt = self.file.add(seg([("svg", "SVG"), ("png", "PNG"), ("pdf", "PDF")], "format", "png"), "Save as")
        self.slide_size = self.file.add(seg([(1280, "1280×720"), (1920, "1920×1080"), (3840, "3840×2160")],
                                            "slide_size", 1920), "PNG size")
        self.scale = self.file.add(seg([(1, "1×"), (2, "2×"), (3, "3×")], "scale", 2), "PNG size")
        self.background = self.file.add(seg([("opaque", "Opaque"), ("clear", "Clear")], "background", "opaque"),
                                        "Background")
        from . import video
        self.video_fmt = self.file.add(seg([("gif", "GIF"), ("mp4", "MP4 video")], "video_format", "gif"), "Save as")
        self.video_fmt.setToolTip(tr("MP4: every colour (basemaps stay smooth) and a much smaller file, for slides and "
                                     "the web; it needs ffmpeg on this computer{found}. GIF: plays anywhere, even "
                                     "in e-mail, but has only 256 colours.").format(
            found=tr(" (found)") if video.find_ffmpeg() else tr(", which wasn't found: install it, or set where it is "
                                                                "in Settings › Options › Sous Chef")))
        self.video_fmt.changed.connect(self._changed)
        self.size = self.file.add(seg([(854, "854×480"), (1280, "1280×720"), (1920, "1920×1080")], "anim_size", 1280),
                                  "Size")
        self.size.setToolTip(tr("Standard 16:9 screen sizes. 854×480 is small enough for chat and e-mail; 1280×720 "
                                "suits most slides; 1920×1080 is full HD (a bigger file, slower to make)."))
        self.recipe_fmt = QStackedWidget()
        self.recipe_fmts = {}
        for key, items in RECIPE_FORMATS.items():
            w = seg(items, f"recipe_format_{key}", items[0][0])
            self.recipe_fmts[key] = w
            self.recipe_fmt.addWidget(w)
        self.file.add(self.recipe_fmt, "Save as")
        self.side.addWidget(self.file)
        self.sections = [self.shown, self.wording, self.map_section, self.drawing, self.timing, self.look, self.file]

    def _font_box(self, key):
        """Automatic (the design's own font), or any font on this computer: for other alphabets,
        or for taste."""
        from qgis.PyQt.QtGui import QFontDatabase
        box = QComboBox()
        box.addItem(tr("Automatic"), "")
        try:
            families = QFontDatabase.families()
        except TypeError:
            families = QFontDatabase().families()
        for family in sorted(set(families), key=str.lower):
            box.addItem(family, family)
        box.setCurrentIndex(max(box.findData(QgsSettings().value(KEY + key, "")), 0))
        box.setToolTip(tr("Choose a font for other alphabets, or for taste. Automatic: the design's own."))
        box.currentIndexChanged.connect(self._changed)
        return box

    def view(self):
        """The controller, or for one separate part, a copy that has only that part's steps."""
        index = self.part.value() if not self.part.isHidden() else -1
        if index is None or int(index) < 0 or len(getattr(self.controller.plan, "parts", []) or []) < 2:
            return self.controller
        index = int(index)
        cache = self.__dict__.setdefault("_part_views", {})
        key = (index, id(self.controller.plan))
        if key not in cache:
            numbers = self.controller.plan.parts[index]
            keep = {step_plan.step.entry_id for step_plan in self.controller.plan.steps if step_plan.number in numbers}
            view = PartView(self.controller, set(self.controller.included) & keep,
                            f"{self.controller.model_name} (part {index + 1})")
            cache.clear()
            cache[key] = view
        return cache[key]

    def _speed_changed(self, value):
        factor = 2 ** ((50 - value) / 50)   # 0: twice as slow, 100: twice as quick
        for key, base in (("draw", 2.5), ("pause", 1.6), ("end", 4.0), ("fade", 0.6)):
            self.spins[key].blockSignals(True)
            self.spins[key].setValue(round(base * factor * 4) / 4 if key != "fade" else round(base * factor, 2))
            self.spins[key].blockSignals(False)
        self._changed()

    def _toggle_custom(self):
        self._custom_open = not self._custom_open
        QgsSettings().setValue(KEY + "custom_timing", self._custom_open)
        self._changed()

    def _reshuffle(self):
        self._shuffle += 1
        self._changed()

    # ------------------------------------------------------------ state
    def current_mode(self):
        return self.mode.value()

    def still_options(self, style=None, detail=None, theme=None) -> images.ImageOptions:
        return images.ImageOptions(
            style or self.still_gallery.current(), detail or self.detail.value(), theme or self.theme.currentData(),
            self.left_out.isChecked(), self.notes.isChecked(), self.numbers.isChecked(),
            background=self.background.value() != "clear", legend=self.legend.isChecked(),
            title=self.title_edit.text(), map_background=self.basemaps.isChecked(),
            spacing=float(self.spacing.value()), inputs=self.inputs.isChecked(), fonts=self.themed_fonts.isChecked(),
            font=self.text_font.currentData(), title_font=self.title_font.currentData(),
            inputs_side=self.inputs_side.value(), overview=self.overview.isChecked(),
            overview_zoom=self.overview_zoom.value(), step_maps=self.stop_maps.isChecked(),
            canvas=self.canvas.value(), subtitle=self.subtitle_value(),
            fold=self.fold.isChecked(), pages=self.pages.value())

    def anim_options(self, width=None, kind=None) -> animation.AnimationOptions:
        return animation.AnimationOptions(
            kind=kind or self.anim_gallery.current(), width=int(width or self.size.value()), fps=int(self.fps.value()),
            draw=self.spins["draw"].value(), pause=self.spins["pause"].value(), end=self.spins["end"].value(),
            fade=self.spins["fade"].value(), loop=self.loop.value() == "loop", earlier=self.earlier.isChecked(),
            basemaps=self.basemaps.isChecked(), order=self.order.currentData(), strip=self.strip.value(),
            captions=self.captions.isChecked(), legend=self.legend.isChecked(), numbers=self.numbers.isChecked(),
            inputs=self.inputs.isChecked(), style=self.style.currentData(), detail=self.detail.value(),
            theme=self.theme.currentData(), title=self.title_edit.text(), wipe=self.wipe.value(),
            fonts=self.themed_fonts.isChecked(), font=self.text_font.currentData(),
            title_font=self.title_font.currentData(), inputs_side=self.inputs_side.value(),
            overview=self.overview.isChecked(), overview_zoom=self.overview_zoom.value(), corner=self.corner.value(),
            part=int(self.part.value()) if not self.part.isHidden() else -1,
            subtitle=self.subtitle_value(),
            final_output=int(self.final_output.currentData() or 0) if not self.final_output.isHidden() else 0)

    def subtitle_value(self):
        """"auto" for the automatic subtitle, the user's own, or "" for none."""
        if not self.subtitle_on.isChecked():
            return ""
        return self.subtitle_edit.text().strip() or "auto"

    def _changed(self, *_args):
        mode = self.current_mode()
        gallery = {"still": self.still_gallery, "animation": self.anim_gallery, "recipe": self.recipe_gallery}[mode]
        self.galleries.setCurrentWidget(gallery)
        gallery.start()
        self._show_options(mode, gallery.current())
        self._remember()
        if mode == "animation":
            self.previews.setCurrentIndex(1)
            self._update_estimate()
            self._show_preview()
            self.save_button.setText(tr("Save MP4…") if self.video_fmt.value() == "mp4" else tr("Save GIF…"))
            self.second_button.setText(tr("Save Frames…"))
            self.second_button.setToolTip(tr("Every frame as a numbered PNG, with their timings, for a video editor"))
            self.second_button.setEnabled(True)
            return
        self.save_button.setText(tr("Save…"))
        if mode == "recipe":
            kind = self.recipe_gallery.current()
            self.second_button.setText(tr("Copy Text"))
            self.second_button.setToolTip(tr("Copy the text, to paste into a document or e-mail"))
            self.second_button.setEnabled(kind != "cookbook")
        else:
            self.second_button.setText(tr("Copy"))
            self.second_button.setToolTip(tr("Copy the image to the clipboard (2×)"))
            self.second_button.setEnabled(True)
        self._timer.start()

    def _show_options(self, mode, key):
        """Only the settings the chosen design uses are shown."""
        detail = self.detail.value()
        why = tr("Built from real maps, so it needs Named or Specific detail (General shows only the kinds of data)")
        self.still_gallery.set_unavailable({k: why for k in MAP_STILLS} if detail == "general" else {})
        self.anim_gallery.set_unavailable({k: why for k in MAP_ONLY} if detail == "general" else {})
        key = {"still": self.still_gallery, "animation": self.anim_gallery}.get(mode, self.recipe_gallery).current()
        sec = self.shown
        still, anim, recipe = mode == "still", mode == "animation", mode == "recipe"
        general = detail == "general" and key not in ("data", "data_flow")
        sec.show_row(self.numbers, (still and key != "summary" and not general)
                     or (anim and key not in ("before_after", "typewriter"))
                     or False)
        sec.show_row(self.notes, (still and key not in ("data", "summary", "pilgrimage", "mindmap", "river", "board", "comic"))
                     or (recipe and key in ("cookbook", "written", "checklist")))
        sec.show_row(self.left_out, still and key in ("boxes", "cards", "minimal", "metro", "swimlanes"))
        legend_still = key in ("cards", "minimal", "metro", "data") and not (
            key in ("cards", "minimal", "metro") and detail == "general")
        sec.show_row(self.legend, (still and legend_still)
                     or (anim and key in MAP_ANIMS + ("before_after", "build_up", "spotlight", "data_flow", "camera_tour")))
        sec.show_row(self.inputs, (still or anim) and key == "storyboard")
        sec.show_row(self.inputs_side, (still or anim) and key == "storyboard" and self.inputs.isChecked())
        sec.show_row(self.part, len(getattr(self.controller.plan, "parts", None) or []) > 1)
        sec.show_row(self.final_output, anim and self.final_output.count() > 2
                     and key in MAP_ANIMS + ("before_after", "pilgrimage", "board_game", "assembly"))
        sec.show_row(self.fold, still and key == "storyboard")
        sec.show_row(self.pages, still and key == "storyboard")
        sec.show_row(self.captions, anim and key in MAP_ANIMS + ("before_after",))
        sec.show_row(self.stop_maps, still and key == "pilgrimage" and detail != "general")
        pilgrim = key in ("pilgrimage", "map_pilgrimage") and not recipe
        sec.show_row(self.subtitle_on, pilgrim)
        sec.show_row(self.subtitle_edit, pilgrim and self.subtitle_on.isChecked())
        sec.show_row(self.maps, recipe and key in ("cookbook", "written") and detail != "general")
        sec.show_row(self.detail, True)
        sec.show_row(self.title_edit, True)
        self.wording.show_row(self.shuffle, recipe and key == "cookbook")
        for w, _l in self.wording.rows[1:]:
            self.wording.show_row(w, recipe and key == "cookbook")
        ms = self.map_section
        ms.show_row(self.order, anim and key in MAP_ANIMS)
        ms.show_row(self.earlier, anim and key in MAP_ANIMS)
        overview_on = detail != "general" and (
            (still and key in ("storyboard", "pilgrimage"))
            or (anim and key in MAP_ANIMS + ("before_after", "storyboard", "pilgrimage"))
            or (recipe and key in ("cookbook", "written") and self.maps.isChecked()))
        ms.show_row(self.overview, overview_on)
        ms.show_row(self.overview_zoom, overview_on and self.overview.isChecked())
        ms.show_row(self.corner, overview_on and anim and key in MAP_ANIMS + ("before_after",) and self.overview.isChecked())
        ms.show_row(self.basemaps, detail != "general" and (
            (still and key in ("storyboard", "pilgrimage", "comic"))
            or (anim and key in MAP_ANIMS + ("before_after", "metro_ride", "storyboard", "pilgrimage",
                                             "typewriter", "kitchen", "assembly", "board_game", "comic"))
            or (recipe and key in ("cookbook", "written") and self.maps.isChecked())))
        ms.show_row(self.strip, anim and key == "map_story")
        ms.show_row(self.wipe, anim and key == "before_after")
        self.drawing.show_row(self.style, anim and key in ("build_up", "spotlight", "camera_tour"))
        tm = self.timing
        tm.show_row(self.speed.parentWidget(), anim)
        tm.show_row(self.customise, anim)
        self.customise.setText(tr("Hide timing details ▾") if self._custom_open else tr("Customise timing ▸"))
        for k, box in self.spins.items():
            tm.show_row(box, anim and self._custom_open)
        tm.show_row(self.fps, anim and self._custom_open)
        tm.show_row(self.loop, anim)
        lk = self.look
        lk.show_row(self.theme, (still and key not in ("boxes", "pilgrimage", "board", "comic"))
                    or (anim and key not in OWN_COLOURS))
        lk.show_row(self.spacing, still and key not in ("summary", "pilgrimage"))
        lk.show_row(self.themed_fonts, (still and key != "boxes") or (anim and key != "typewriter"))
        no_title_font = ("methods", "checklist", "written", "typewriter")
        lk.show_row(self.title_font, not (still and key == "boxes") and key not in no_title_font)
        lk.show_row(self.text_font, not (still and key == "boxes"))
        f = self.file
        f.show_row(self.fmt, still)
        f.show_row(self.canvas, still)
        f.show_row(self.scale, still and self.fmt.value() == "png" and self.canvas.value() == "fit")
        f.show_row(self.slide_size, still and self.fmt.value() == "png" and self.canvas.value() == "slide")
        f.show_row(self.background, still and self.fmt.value() == "png")
        f.show_row(self.size, anim)
        f.show_row(self.video_fmt, anim)
        f.show_row(self.recipe_fmt, recipe)
        if recipe:
            self.recipe_fmt.setCurrentWidget(self.recipe_fmts[key])
        for section in self.sections:
            section.refresh_visible()

    def _remember(self):
        s = QgsSettings()
        s.setValue(KEY + "mode", self.current_mode())
        for key, g in (("style", self.still_gallery), ("kind", self.anim_gallery), ("recipe", self.recipe_gallery)):
            if g.current():
                s.setValue(KEY + key, g.current())
        for key, w in (("inputs_side", self.inputs_side), ("overview_zoom", self.overview_zoom), ("corner", self.corner),
                       ("detail", self.detail), ("strip", self.strip), ("wipe", self.wipe), ("loop", self.loop),
                       ("video_format", self.video_fmt), ("spacing", self.spacing), ("format", self.fmt), ("scale", self.scale),
                       ("background", self.background), ("anim_size", self.size), ("fps", self.fps),
                       ("canvas", self.canvas), ("slide_size", self.slide_size)):
            s.setValue(KEY + key, w.value())
        for key, box in (("numbers", self.numbers), ("notes", self.notes), ("left_out", self.left_out),
                         ("legend", self.legend), ("inputs", self.inputs), ("captions", self.captions),
                         ("maps", self.maps), ("earlier", self.earlier), ("basemaps", self.basemaps),
                         ("fonts", self.themed_fonts), ("overview", self.overview), ("stop_maps", self.stop_maps),
                         ("subtitle_on", self.subtitle_on)):
            s.setValue(KEY + key, box.isChecked())
        s.setValue(KEY + "subtitle", self.subtitle_edit.text())
        s.setValue(KEY + "title_font", self.title_font.currentData())
        s.setValue(KEY + "text_font", self.text_font.currentData())
        for key, box in self.spins.items():
            s.setValue(KEY + key, box.value())
        for key, w in self.recipe_fmts.items():
            s.setValue(KEY + f"recipe_format_{key}", w.value())
        s.setValue(KEY + "speed", self.speed.value())
        s.setValue(KEY + "order", self.order.currentData())
        s.setValue(KEY + "anim_style", self.style.currentData())
        s.setValue(KEY + "theme", self.theme.currentData())
        s.setValue(KEY + "title", self.title_edit.text())

    def _update_estimate(self):
        opts = self.anim_options()
        seconds, frames, kb = animation.rough_estimate(opts, len(self.controller.plan.steps))
        if self.video_fmt.value() == "mp4":
            from . import video
            kb = video.estimate_kb(seconds, opts.width, int(opts.width * 9 / 16))
        size = f"{kb / 1024:.1f} MB" if kb >= 1024 else f"{kb:.0f} KB"
        self.estimate.setText(tr("≈ {s:.0f} s  ·  ≈ {size}  ·  ≈ {f} frames").format(s=seconds, size=size, f=frames))

    # ------------------------------------------------------------ thumbnails
    def _still_thumb(self, key):
        opts = images.ImageOptions(key, "named", "standard", False, True, True, legend=False, spacing=0.55)
        return to_image(images.render_svg(self.controller, opts), 0.6)

    def _anim_thumb(self, key):
        frames = animation.make(self.controller, animation.AnimationOptions(kind=key, width=420, fps=8)).keyframes()
        whole_first = ("typewriter", "build_up", "spotlight", "data_flow", "storyboard")   # their last key frame is faded
        return frames[0] if key in whole_first else frames[-1]

    def _recipe_thumb(self, key):
        html, pictures = self.recipe_page(key, for_thumbnail=True)
        return _html_image(html, pictures)

    # ------------------------------------------------------------ the recipes
    def recipe_page(self, key, for_thumbnail=False):
        """(html for Qt, pictures)."""
        detail = self.detail.value()
        notes, title = self.notes.isChecked(), self.title_edit.text()
        maps = {} if for_thumbnail or not self.maps.isChecked() or key not in ("cookbook", "written") \
            or detail == "general" else \
            self._step_maps()
        if key == "cookbook":
            from . import cookbook
            recipe = cookbook.build(self.view(), detail, notes, self._shuffle, title)
            return cookbook.page(recipe, maps or None, for_qt=True, logo=cookbook.logo_image(),
                                 title_font=self.title_font.currentData(), font=self.text_font.currentData(),
                                 overview=self._overview_image() if maps else None)
        if key == "written":
            r = written.build(self.view(), detail, notes, title)
            pictures = {f"map:{n}": img for n, img in maps.items()}
            inset = self._overview_image() if maps else None
            if inset is not None:
                pictures["overview:map"] = inset
            return written.as_qt_html(r, maps=bool(maps), font=self.text_font.currentData(),
                                      overview=inset is not None), pictures
        if key == "methods":
            return written.methods_qt_html(written.methods(self.view(), detail), self.text_font.currentData()), {}
        return written.checklist_qt_html(written.checklist(written.build(self.view(), detail, notes, title)),
                                         self.text_font.currentData()), {}

    def recipe_text(self, key, fmt):
        detail, notes, title = self.detail.value(), self.notes.isChecked(), self.title_edit.text()
        if key == "methods":
            m = written.methods(self.view(), detail)
            return written.methods_markdown(m) if fmt == "md" else written.methods_text(m)
        r = written.build(self.view(), detail, notes, title)
        if key == "checklist":
            c = written.checklist(r)
            return written.checklist_markdown(c) if fmt == "md" else written.checklist_text(c)
        return {"md": written.as_markdown, "html": written.as_html}.get(fmt, written.as_text)(r)

    def _overview_image(self):
        """One small world map for the whole recipe, when it's asked for."""
        if not self.overview.isChecked():
            return None
        from qgis.PyQt.QtCore import QSize
        from . import overview, snapshots
        found, _used, self._overview_keep = snapshots.result_layers(self.view(), rerun=False)
        maps = animation.MapMaker(list(snapshots.starting_layers(self.view()).values()) + list(found.values()), 10, 10)
        return overview.inset([(maps.settings.extent(), maps.settings.destinationCrs(), True)], QSize(480, 240),
                              dict(images.THEMES["standard"], zoom=self.overview_zoom.value()))

    def _step_maps(self):
        from . import snapshots
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            found = snapshots.collect(self.view(), self.basemaps.isChecked())
        finally:
            QApplication.restoreOverrideCursor()
        return {n: (img if img is not None else snapshots.placeholder()) for n, img in found.items()}

    # ------------------------------------------------------------ previews
    def still_svg(self, style=None) -> str:
        """The image (the first page of a long storyboard)."""
        return self._finish_svg(images.render_svg(self.view(), self.still_options(style)), style)

    def still_svgs(self, style=None) -> list:
        """Every page of the image: one, unless a long storyboard is split over pages."""
        return [self._finish_svg(svg, style) for svg in images.render_pages(self.view(), self.still_options(style))]

    def _finish_svg(self, svg, style=None) -> str:
        style = style or self.still_gallery.current()
        if style == "boxes" and self.background.value() != "clear":
            svg = with_background(svg, "#FFFFFF")
        if self.canvas.value() == "slide":
            svg = to_slide(svg)
        if self.background.value() == "clear" and self.fmt.value() == "png":
            svg = with_background(svg, None)
        return svg

    def png_scale(self, svg) -> float:
        """The PNG's scale: a screen size for a slide, otherwise 1×–3×."""
        if self.canvas.value() == "slide":
            return float(self.slide_size.value()) / max(_size(svg).width(), 1)
        return float(self.scale.value())

    def refresh(self):
        mode = self.current_mode()
        try:
            if mode == "recipe":
                self.previews.setCurrentWidget(self.text_preview)
                html, pictures = self.recipe_page(self.recipe_gallery.current())
                document = self.text_preview.document()
                for name, image in pictures.items():
                    document.addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), image)
                self.text_preview.setHtml(html)
                return
            if mode != "still":
                return
            self.previews.setCurrentWidget(self.scroll)
            pages = self.still_svgs()
            self._svg = pages[0]
            room = self.scroll.viewport().size()
            size = _size(self._svg)
            fit = min((room.width() - 24) / max(size.width(), 1), 1.5)
            ratio = self.devicePixelRatioF()
            image = to_image(self._svg, fit * ratio)
            if len(pages) > 1:   # the pages one under another
                shots = [image] + [to_image(svg, fit * ratio) for svg in pages[1:]]
                space = int(16 * ratio)
                image = QImage(max(i.width() for i in shots), sum(i.height() for i in shots) + space * (len(shots) - 1),
                               QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                painter = QPainter(image)
                top = 0
                for shot in shots:
                    painter.drawImage(0, top, shot)
                    top += shot.height() + space
                painter.end()
            if self.background.value() == "clear" and self.fmt.value() == "png":
                image = _on_checkerboard(image)
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(ratio)
            self.picture.setPixmap(pixmap)
            self.picture.resize(pixmap.size() / ratio)
        except Exception as e:  # show the problem instead of crashing QGIS
            self.previews.setCurrentWidget(self.scroll)
            self.picture.setPixmap(QPixmap())
            self.picture.setText(tr("The preview could not be drawn:\n{error}").format(error=e))
            self.picture.adjustSize()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.current_mode() == "still":
            self._timer.start()

    # ------------------------------------------------------------ animations
    def _run(self, job, label):
        dialog = QProgressDialog(label, tr("Cancel"), 0, 100, self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(300)

        def progress(done, total):
            dialog.setMaximum(total)
            dialog.setValue(min(done, total - 1))
            QApplication.processEvents()
            return not dialog.wasCanceled()

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return job(progress)
        except animation.Cancelled:
            return None
        except Exception as e:  # shown rather than lost
            QMessageBox.warning(self, tr("Export"), tr("This couldn't be made: {error}").format(error=e))
            return None
        finally:
            QApplication.restoreOverrideCursor()
            dialog.close()

    def _preview_width(self):
        """Previews are made to fill the preview area (but no bigger than the real thing)."""
        room = max(self.movie_label.width() - 24, 480)
        return int(min(int(self.size.value()), room, (self.movie_label.height() - 24) * 16 / 9 if
                       self.movie_label.height() > 200 else room))

    def _preview_key(self):
        return repr(self.anim_options(width=self._preview_width()))

    def _show_preview(self):
        """The animation's preview if it was made before (kept while the window is open),
        otherwise a prompt: never the preview of a different animation."""
        if self._movie is not None:
            self._movie.stop()
            self.movie_label.setMovie(None)
            self._movie = None
        path = self._previews.get(self._preview_key())
        if path and os.path.exists(path):
            self._movie = QMovie(path)
            self.movie_label.setMovie(self._movie)
            self._movie.start()
            self.pause_button.setEnabled(True)
            self.pause_button.setChecked(False)
        else:
            self.movie_label.setText(tr("Press ▶ Preview to see this animation play here.\n"
                                        "Saving makes it at full size."))
            self.pause_button.setEnabled(False)

    def make_preview(self):
        key = self._preview_key()
        path = os.path.join(self._preview_dir, f"preview_{len(self._previews)}.gif")
        opts = self.anim_options(width=self._preview_width())
        frames = self._run(lambda progress: animation.write_gif(self.view(), opts, path, progress),
                           tr("Making the preview…"))
        if not frames:
            return
        self._previews[key] = path
        self._show_preview()

    # ------------------------------------------------------------ saving
    def default_name(self, ext) -> str:
        stamp = QDateTime.currentDateTime().toString("yyyy-MM-dd_HHmm")
        mode = self.current_mode()
        key = {"still": self.still_gallery, "animation": self.anim_gallery, "recipe": self.recipe_gallery}[mode].current()
        name = "_".join([builder.slug(self.controller.model_name, "workflow"), key, self.detail.value(), stamp])
        folder = QgsSettings().value("souschef/image_folder", "") or os.path.expanduser("~")
        if not os.path.isdir(folder):
            folder = os.path.expanduser("~")
        return os.path.join(folder, name + ext)

    def _ask_path(self, ext, label):
        path, _f = QFileDialog.getSaveFileName(self, tr("Save {what}").format(what=label), self.default_name(ext),
                                               f"{label} (*{ext})")
        if not path:
            return None
        if not path.lower().endswith(ext):
            path += ext
        QgsSettings().setValue("souschef/image_folder", os.path.dirname(path))
        return path

    def _status_link(self, link):
        if link == "report:":
            from . import report
            report.show(self)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(link))

    def _saved(self, path, extra=""):
        self.status.setText(tr("Saved {extra}<a href='{folder}'>{name}</a>").format(
            extra=extra, folder=os.path.dirname(path), name=os.path.basename(path)))

    def save(self):
        mode = self.current_mode()
        try:
            if mode == "still":
                fmt = self.fmt.value()
                path = self._ask_path("." + fmt, {"svg": "SVG image", "png": "PNG image", "pdf": "PDF"}[fmt])
                if not path:
                    return
                pages = self.still_svgs()
                if fmt == "pdf":
                    save_pdf(pages, path)   # one page each
                else:
                    stem, ext = os.path.splitext(path)
                    for k, svg in enumerate(pages):   # a long storyboard: name_1, name_2…
                        target = path if len(pages) == 1 else f"{stem}_{k + 1}{ext}"
                        if fmt == "svg":
                            from .fonts import embed
                            with open(target, "w", encoding="utf-8") as fh:
                                fh.write(embed(svg))   # carries its fonts
                        elif not to_image(svg, self.png_scale(svg)).save(target, "PNG"):
                            raise OSError(tr("the PNG file could not be written"))
                if len(pages) > 1:
                    self._saved(path, tr("{n} pages: ").format(n=len(pages)))
                    return
                self._saved(path)
            elif mode == "animation":
                from . import video
                mp4 = self.video_fmt.value() == "mp4"
                ffmpeg = video.find_ffmpeg() if mp4 else ""
                if mp4 and not ffmpeg:
                    self.status.setText(tr("<span style='color:#B3261E'>MP4 needs ffmpeg, which wasn't found on this "
                                           "computer.</span> Install it, or set where it is in Settings › Options › "
                                           "Sous Chef; or save a GIF."))
                    return
                path = self._ask_path(".mp4", "MP4 video") if mp4 else self._ask_path(".gif", "GIF")
                if not path:
                    return
                opts = self.anim_options()
                if mp4:
                    frames = self._run(lambda progress: video.write_mp4(self.view(), opts, path, progress, ffmpeg),
                                       tr("Making the video…"))
                else:
                    frames = self._run(lambda progress: animation.write_gif(self.view(), opts, path, progress),
                                       tr("Making the animation…"))
                if frames:
                    self._saved(path, tr("{n} frames, {mb:.1f} MB: ").format(n=frames,
                                                                             mb=os.path.getsize(path) / 1048576))
            else:
                key = self.recipe_gallery.current()
                fmt = self.recipe_fmts[key].value()
                label = {"pdf": "PDF", "html": "Web page", "md": "Markdown", "txt": "Text"}[fmt]
                path = self._ask_path("." + fmt, label)
                if not path:
                    return
                if fmt == "pdf":
                    html, pictures = self.recipe_page(key)
                    recipe_pdf(html, path, pictures)
                else:
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(self.recipe_text(key, fmt))
                self._saved(path)
        except Exception as e:  # report instead of crashing QGIS
            from . import report
            report.remember("Images & Animations: saving", e)
            self.status.setText(f"<span style='color:#B3261E'>{tr('Not saved')}: {e}</span> "
                                f"<a href='report:'>{tr('Report…')}</a>")

    def second_action(self):
        mode = self.current_mode()
        if mode == "animation":
            folder = QFileDialog.getExistingDirectory(self, tr("Folder for the Frames"),
                                                      os.path.dirname(self.default_name(".gif")))
            if not folder:
                return
            target = os.path.join(folder, os.path.splitext(os.path.basename(self.default_name(".gif")))[0] + "_frames")
            opts = self.anim_options()
            frames = self._run(lambda progress: animation.write_frames(self.view(), opts, target, progress),
                               tr("Saving the frames…"))
            if frames:
                self._saved(target, tr("{n} frames and their timings in ").format(n=frames))
        elif mode == "recipe":
            key = self.recipe_gallery.current()
            QApplication.clipboard().setText(self.recipe_text(key, "txt"))
            self.status.setText(tr("Copied the text to the clipboard."))
        else:
            QApplication.clipboard().setImage(to_image(self.still_svg(), 2.0))
            self.status.setText(tr("Copied the image (2×) to the clipboard."))

    def closeEvent(self, event):
        if self._movie is not None:
            self._movie.stop()
            self.movie_label.setMovie(None)
            self._movie = None
        import shutil
        shutil.rmtree(self._preview_dir, ignore_errors=True)
        super().closeEvent(event)
