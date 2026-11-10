"""The cookbook recipe: the workflow written out like a recipe card, for fun.

Comic Sans, a word-art title with a cooking adjective, and a method in kitchen language.
Each kind of tool has several phrasings, filled in with the real layer names and values;
which phrasing is used depends on the recipe's name and a shuffle number, so the wording
stays put until you ask for new wording (Reshuffle).
"""

from __future__ import annotations

import base64
import html
import math
import os
import random
import zlib

from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from qgis.PyQt.QtGui import QFont, QFontMetricsF, QImage, QPainter
from qgis.PyQt.QtSvg import QSvgRenderer

from ..core import builder
from . import icons

FONT = "'Comic Sans MS', 'Comic Neue', 'Chalkboard SE', 'Comic Relief', cursive"

ADJECTIVES = ["Hearty", "Rustic", "Grandma's Famous", "Slow-Cooked", "Zesty", "Classic", "Homestyle",
              "Award-Winning", "Golden", "Crispy", "Lovingly Prepared", "Twice-Baked", "Farmhouse",
              "Chef's Special", "Sunday", "Old-Fashioned", "Hand-Rolled", "Signature"]
INTROS = ["A dependable weeknight favourite that never fails to impress the project manager.",
          "Passed down through generations of GIS analysts, now written down at last.",
          "Simple, honest ingredients, and a result you'll want to make again and again.",
          "Ready faster than you can say \"reproject on the fly\".",
          "Comforting, reliable, and surprisingly easy to scale up for a crowd.",
          "The kind of recipe you'll want to keep in the front of the book."]
CLOSERS = ["Serve immediately on a fresh map.", "Garnish with a legend and serve.",
           "Best enjoyed with a north arrow on the side.", "Leftovers keep well in a GeoPackage.",
           "Plate up, add a scale bar, and enjoy.", "Let it rest for a moment, then share with friends."]
FIRST = ["First,", "To begin,", "Before anything else,", ""]
MIDDLE = ["Next,", "Then", "Now", "Meanwhile,", "Once that's settled,", "While it's still warm,", "", "", ""]
LAST = ["Finally,", "To finish,", "Last of all,"]
NUMBERS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]

# {what}: everything the step uses · {first}, {second}: its main and second layer · {value}: its main setting
TEMPLATES = {
    "buffer": ["Fold a {value} border around {what}.", "Pad {what} with a generous {value} cushion.",
               "Wrap {what} in a {value} blanket of space.", "Give {what} {value} of breathing room on every side."],
    "clip": ["Trim {first} to fit {second}, like pastry to a tin.", "Cut {first} neatly along the edges of {second}.",
             "Press {second} over {first} like a cookie cutter, and keep what's inside."],
    "intersection": ["Layer {first} over {second} and keep only the bits they share.",
                     "Keep just the overlap of {first} and {second}, the best part of the sandwich."],
    "difference": ["Scoop out of {first} everything covered by {second}.", "Carve {second} away from {first}."],
    "union": ["Blend {first} and {second} together, keeping every last piece.",
              "Fold {first} and {second} into one another until fully combined."],
    "merge": ["Combine {what} in one big bowl.", "Tip {what} into the same pot and stir.",
              "Mix {what} together until evenly combined."],
    "dissolve": ["Melt {what} down until the seams disappear.", "Let {what} dissolve into one smooth layer."],
    "simplify": ["Simmer {what} down, skimming off any detail smaller than {value}.",
                 "Trim the fussy corners off {what}, to within {value}.",
                 "Reduce {what} gently, to a tolerance of {value}."],
    "centroids": ["Find the very heart of every piece of {what}.", "Pit {what}, keeping only the centre of each piece."],
    "grid": ["Roll out a fresh grid of {value} squares over {what}.", "Lay a {value} lattice over {what}, like a pie crust.",
             "Score {what} into neat {value} squares."],
    "fieldcalc": ["Season each feature of {what} with {field} = {formula}.",
                  "Stir a new ingredient into {what}: {field}, made from {formula}.",
                  "Sprinkle {field} over {what}, mixed from {formula}."],
    "fieldcalc_plain": ["Season {what} with a freshly calculated column.",
                        "Stir a newly calculated column into {what}."],
    "reproject": ["Move {what} to a new kitchen: {value}.", "Transfer {what} carefully into {value}."],
    "count": ["Count how many pieces of {second} fit in each part of {first}.",
              "Sprinkle {second} over {first} and count what lands in each piece."],
    "join": ["Marry {first} with {second}.", "Fold the flavours of {second} into {first}."],
    "extract": ["Sift {what}, keeping only what you need.", "Pick out the best bits of {what}."],
    "fix": ["Pick any bones out of {what}.", "Tidy up any broken pieces in {what}."],
    "save": ["Spoon {what} into a jar for later.", "Set aside a portion of {what} for later."],
    "generic": ["Put {what} through {tool}.", "Run {tool} over {what} until done.", "Treat {what} to a round of {tool}."],
    "generic_none": ["Whip up a fresh batch of {tool}.", "Prepare {tool} from scratch."],
}
KINDS = [("buffer", ("buffer",)), ("clip", ("native:clip", "qgis:clip", "gdal:clipvector")),
         ("intersection", ("intersection",)), ("difference", ("difference",)), ("union", ("native:union", "qgis:union")),
         ("merge", ("mergevectorlayers",)), ("dissolve", ("dissolve",)), ("simplify", ("simplify",)),
         ("centroids", ("centroids",)), ("grid", ("creategrid",)), ("fieldcalc", ("fieldcalculator",)),
         ("reproject", ("reprojectlayer",)), ("count", ("countpointsinpolygon",)),
         ("join", ("joinattributes", "joinbylocation")), ("extract", ("extractby", "selectbyexpression")),
         ("fix", ("fixgeometries",)), ("save", ("savefeatures", "package"))]
MAIN_SETTING = {"buffer": "DISTANCE", "simplify": "TOLERANCE", "grid": "HSPACING", "reproject": "TARGET_CRS"}
SECOND = ("OVERLAY", "POINTS", "JOIN", "INPUT_2", "PREDICATE_LAYER")


def _kind(alg_id: str) -> str:
    for kind, keys in KINDS:
        if any(k in alg_id for k in keys):
            return kind
    return "generic"


def _list(items):
    items = [i for i in items if i]
    if len(items) > 1:
        return ", ".join(items[:-1]) + " and " + items[-1]
    return items[0] if items else ""


def _count(n, word):
    number = NUMBERS[n] if n < len(NUMBERS) else str(n)
    if n == 1:
        return f"{number} {word}"
    return f"{number} {word}{'es' if word.endswith(('ch', 'sh', 's', 'x')) else 's'}"


def _cap(text):
    return text[:1].upper() + text[1:]


class _Writer:
    def __init__(self, controller, detail, shuffle):
        self.controller, self.detail, self.plan = controller, detail, controller.plan
        seed = zlib.crc32(f"{controller.model_name}|{shuffle}".encode("utf-8"))
        self.rng = random.Random(seed)  # nosec B311: drawing variety, not security
        self.by_number = {step_plan.number: step_plan for step_plan in self.plan.steps}
        # the method's own numbering: a batch is one step of the method
        self.index_of, done, index = {}, {}, 0
        for step_plan in self.plan.steps:
            g = controller.group_of(step_plan.number)
            if g is None or g not in done:
                index += 1
                if g is not None:
                    done[g] = index
            self.index_of[step_plan.number] = done.get(g, index) if g is not None else index
        words = {uid: icons.layer_icon(input_plan.layer_kind)[1] for uid, input_plan in self.plan.inputs.items()}
        self.input_names, seen = {}, {}
        for uid, input_plan in self.plan.inputs.items():
            if detail == "general":
                same = [u for u in words if words[u] == words[uid]]
                seen[words[uid]] = seen.get(words[uid], 0) + 1
                self.input_names[uid] = (f"the {words[uid]}" if len(same) == 1
                                         else f"{words[uid]} {seen[words[uid]]}")
            else:
                self.input_names[uid] = f"“{input_plan.recorded or input_plan.label}”"

    def pick(self, options):
        return self.rng.choice(options)

    def source(self, src):
        """(key, single name, name for several of them): several results of one batch share a key."""
        if src[0] == "link":
            prev = self.by_number.get(src[1])
            label = prev.output_labels.get(src[2], "") if prev else ""
            index = self.index_of.get(src[1], src[1])
            if self.detail == "general" or not label:
                return (f"step{index}", f"the mixture from step {index}", "the {n} batches from step " + str(index))
            return (f"step{index}:{label}", f"“{label}”", "all {n} “" + label + "” batches")
        name = self.input_names.get(src[1], "a layer")
        return (f"input:{src[1]}", name, name)

    def names(self, sources):
        keys, counts, text = [], {}, {}
        for src in sources:
            key, one, many = self.source(src)
            if key not in counts:
                keys.append(key)
            counts[key] = counts.get(key, 0) + 1
            text[key] = (one, many)
        out = []
        for key in keys:
            one, many = text[key]
            n = counts[key]
            if n == 1:
                out.append(one)
            elif n == 2 and many.startswith("all {n} "):
                out.append("both " + many[len("all {n} "):])
            else:
                out.append(many.replace("{n}", NUMBERS[n] if n < len(NUMBERS) else str(n)))
        return out

    def layers(self, step_plan):
        """(all, first, second) names of the layers a step uses."""
        first, second, every = [], [], []
        for param in step_plan.params:
            if param.kind not in ("input", "link"):
                continue
            names = self.names(param.sources)
            for n in names:
                if n not in every:
                    every.append(n)
            (second if param.name in SECOND else first).extend(n for n in names if n not in first + second)
        if not second and len(first) > 1:
            first, second = first[:1], first[1:]
        return every, first, second

    def value(self, step_plan, kind):
        pp = step_plan.param(MAIN_SETTING.get(kind, ""))
        return pp.shown_value if pp is not None and pp.shown_value else ""

    def sentence(self, step_plan, kind):
        every, first, second = self.layers(step_plan)
        what, value = _list(every), self.value(step_plan, kind)
        tool = f"<i>{html.escape(step_plan.step.alg.displayName())}</i>"
        if kind == "grid" and not what:
            what = "the work surface"
        if kind == "fieldcalc":
            field = step_plan.step.inputs.get("FIELD_NAME", "")
            formula = step_plan.step.inputs.get("FORMULA", "")
            if self.detail == "general" or not field:
                kind = "fieldcalc_plain"
            return self.fill(kind, what=what, field=f"<b>{html.escape(str(field))}</b>",
                             formula=f"<code>{html.escape(str(formula))}</code>")
        needs = TEMPLATES.get(kind, [])
        ok = (kind in TEMPLATES and what and ("{value}" not in "".join(needs) or value)
              and ("{second}" not in "".join(needs) or second))
        if not ok:
            kind = "generic" if what else "generic_none"
        return self.fill(kind, what=what, first=_list(first) or what, second=_list(second), value=html.escape(value),
                         tool=tool)

    def fill(self, kind, **slots):
        text = self.pick(TEMPLATES[kind])
        for key, val in slots.items():
            safe = val if key in ("tool", "field", "formula", "value") else html.escape(val)
            text = text.replace("{" + key + "}", safe)
        return text


def build(controller, detail="named", notes=True, shuffle=0, title="") -> dict:
    """The cookbook recipe as data: title, adjective, intro, facts, ingredients, method…"""
    w = _Writer(controller, detail, shuffle)
    plan = controller.plan
    name = title.strip() or (builder.general_title(plan) if detail == "general"
                             else controller.model_name or "Mystery Recipe")
    recipe = {"adjective": w.pick(ADJECTIVES), "wordart": w.pick(WORDART_NAMES), "name": name, "intro": w.pick(INTROS),
              "description": controller.choices.description.strip(), "closer": w.pick(CLOSERS)}
    kinds = {}
    for _sp, pp in plan.outputs:
        word = icons.layer_icon(pp.layer_kind)[1]
        kinds[word] = kinds.get(word, 0) + 1
    recipe["serves"] = _list([_count(n, word) for word, n in kinds.items()]) or "the whole team"
    when = [s.step.timestamp for s in plan.steps if s.step.timestamp is not None]
    if len(when) > 1:
        minutes = round(min(when).secsTo(max(when)) / 60)
        recipe["prep"] = "under a minute" if minutes < 1 else f"about {_count(minutes, 'minute')}"
    else:
        recipe["prep"] = ""
    n = len(plan.steps)
    recipe["difficulty"] = "Easy" if n <= 3 else "Medium" if n <= 7 else "Ambitious"
    recipe["ingredients"] = []
    for uid, input_plan in plan.inputs.items():
        word = icons.layer_icon(input_plan.layer_kind)[1]
        if detail == "general":
            label = w.input_names[uid]
            if label.startswith("the "):
                label = ("an " if label[4:5] in "aeiou" else "a ") + label[4:]
            recipe["ingredients"].append(label)
        else:
            recipe["ingredients"].append(f"{input_plan.recorded or input_plan.label} (a {word})")
    recipe["to_taste"] = []
    for _sp, pp in plan.asked:
        line = pp.display
        if detail != "general" and pp.shown_value:
            line += f" (we used {pp.shown_value})"
        recipe["to_taste"].append(line)
    recipe["equipment"] = ["a working copy of QGIS"] + [
        f"the {name_} plugin" for name_ in builder.plugins_needed([step_plan.step.alg for step_plan in plan.steps])]

    method, done = [], set()
    group_of = {step_plan.number: controller.group_of(step_plan.number) for step_plan in plan.steps}
    for step_plan in plan.steps:
        g = group_of.get(step_plan.number)
        if g is not None and g in done:
            continue
        kind = _kind(step_plan.step.alg_id)
        text = w.sentence(step_plan, kind)
        if g is not None:
            done.add(g)
            members = [s for s in plan.steps if group_of.get(s.number) == g]
            key = MAIN_SETTING.get(kind, "")
            rest = [m.param(key).shown_value for m in members[1:] if key and m.param(key) is not None]
            if rest and all(rest):
                text += f" Repeat with {html.escape(_list(rest))}, for {_count(len(members), 'batch')} in all."
            else:
                text += f" Repeat {_count(len(members) - 1, 'more time')} with different settings."
        method.append({"text": text, "note": step_plan.note if notes else "", "number": step_plan.number})
    for i, step in enumerate(method):   # openers once the order is known, so the last step can say "Finally"
        opener = w.pick(FIRST if i == 0 else LAST if i == len(method) - 1 and len(method) > 2 else MIDDLE)
        if opener:
            step["text"] = f"{opener} {step['text'][:1].lower()}{step['text'][1:]}"
    recipe["method"] = method
    return recipe


# ------------------------------------------------------------ word-art title

# Word-art looks for the title. Each names fonts to try in order (the first one installed
# is used, so every computer gets something that fits), the colours and the effects.
WORDART = {
    "sunset": dict(fonts=["Comic Sans MS", "Comic Neue", "Chalkboard SE", "Comic Relief"], bold=True, italic=False,
                   fill=["#FFD54A", "#FF8A3D", "#E53935"], direction="down", outline="#5D2E0C", outer=None,
                   shadow=("#5D2E0C", 0.35, 4, 5), extrude=0, adjective=("#2E7D32", "#00897B"), arc=True, board=None),
    "diner": dict(fonts=["Bungee", "Impact", "Haettenschweiler", "Arial Black", "Arial Narrow", "DejaVu Sans Condensed"],
                  bold=True, italic=False, fill=["#FF5A4E", "#C62828"], direction="down", outline="#FFFFFF",
                  outer="#3E2723", shadow=None, extrude=0, adjective=("#00838F", "#00838F"), arc=False, board=None,
                  upper=True),
    "bakery": dict(fonts=["Georgia", "Palatino Linotype", "Book Antiqua", "Palatino", "DejaVu Serif"], bold=True,
                   italic=True, fill=["#F8BBD0", "#EC407A", "#8E24AA"], direction="down", outline="#FFFFFF",
                   outer="#4A148C", shadow=("#4A148C", 0.25, 3, 4), extrude=0, adjective=("#8E24AA", "#D81B60"),
                   arc=True, board=None),
    "retro": dict(fonts=["Arial Black", "Arial Rounded MT Bold", "Verdana", "DejaVu Sans"], bold=True, italic=False,
                  fill=["#E53935", "#FB8C00", "#FDD835", "#43A047", "#1E88E5"], direction="across", outline="#1A1A2E",
                  outer=None, shadow=None, extrude=7, adjective=("#1A1A2E", "#3949AB"), arc=True, board=None),
    "script": dict(fonts=["Brush Script MT", "Segoe Script", "Lucida Handwriting", "Snell Roundhand", "URW Chancery L",
                          "Z003"], bold=False, italic=True, fill=["#9CCC65", "#558B2F", "#33691E"], direction="down",
                   outline="#F9A825", outer=None, shadow=("#33691E", 0.3, 3, 4), extrude=0,
                   adjective=("#BF360C", "#E65100"), arc=True, board=None, bigger=1.15),
    "chalkboard": dict(fonts=["Chalkboard SE", "Chalkduster", "Segoe Print", "Patrick Hand", "Comic Neue"], bold=True,
                       italic=False, fill=["#FFFFFF", "#ECEFF1"], direction="down", outline=None, outer=None,
                       shadow=None, extrude=0, adjective=("#FFF59D", "#FFE082"), arc=False, board=("#2F4F4F", "#8D6E63")),
}
WORDART.update({
    "saloon": dict(fonts=["Rye", "Rockwell Extra Bold", "Rockwell", "Georgia"], bold=False, italic=False,
                   fill=["#6B3A17", "#3B1D0B"], direction="down", outline="#F6E7C1", outer=None,
                   shadow=("#3B1D0B", 0.25, 3, 3), extrude=0, adjective=("#8B2E16", "#8B2E16"), arc=False,
                   board=("#E8CF9E", "#6B3E1C"), upper=True),
    "medieval": dict(fonts=["UnifrakturMaguntia", "Old English Text MT", "Blackadder ITC", "Georgia"], bold=False,
                     italic=False, fill=["#B3261E", "#6E0F0F"], direction="down", outline="#D4AF37", outer=None,
                     shadow=("#4A2A10", 0.2, 2, 3), extrude=0, adjective=("#1F3A93", "#2C5DB8"), arc=True,
                     board=("#F3E5C0", "#8B1E1E"), bigger=1.1),
})
WORDART_NAMES = list(WORDART)
_INSTALLED = []


def _font_family(candidates) -> str:
    """The first of ``candidates`` that's installed (or bundled with Sous Chef), otherwise the
    first one (Qt then picks the nearest it has)."""
    from . import fonts
    fonts.load()
    if not _INSTALLED:
        try:
            from qgis.PyQt.QtGui import QFontDatabase
            try:
                _INSTALLED.extend(QFontDatabase.families())          # Qt 6
            except TypeError:
                _INSTALLED.extend(QFontDatabase().families())        # Qt 5
        except Exception:
            _INSTALLED.append("")
    for family in candidates:
        if family in _INSTALLED:
            return family
    return candidates[0]


def title_image(adjective: str, name: str, width=760, scale=2.0, look="sunset", family="") -> QImage:
    """The title as word art (see WORDART): the adjective above the recipe's name."""
    svg, h = title_svg(adjective, name, width, look, family)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    image = QImage(int(width * scale), int(h * scale), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    renderer.render(painter, QRectF(0, 0, image.width(), image.height()))
    painter.end()
    image.setDotsPerMeterX(int(3780 * scale))
    image.setDotsPerMeterY(int(3780 * scale))
    return image


def _metrics(family, size, bold=True, italic=False):
    font = QFont(family)
    font.setPixelSize(int(size))
    font.setBold(bold)
    font.setItalic(italic)
    return QFontMetricsF(font)


def _lines(text, m, room):
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if m.horizontalAdvance(trial) <= room or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def title_svg(adjective: str, name: str, width=760, look="sunset", family=""):
    e = html.escape
    st = WORDART.get(look, WORDART["sunset"])
    family = family or _font_family(st["fonts"])
    bold, italic = st["bold"], st["italic"]
    if st.get("upper"):
        adjective, name = adjective.upper(), name.upper()
    room = width - 60 - st["extrude"]
    size = int(52 * st.get("bigger", 1.0))

    def metrics(n):
        return _metrics(family, n, bold, italic)

    lines = _lines(name, metrics(size), room)
    while (len(lines) > 2 or any(metrics(size).horizontalAdvance(x) > room for x in lines)) and size > 26:
        size -= 2
        lines = _lines(name, metrics(size), room)
    weight = ' font-weight="bold"' if bold else ""
    slant = ' font-style="italic"' if italic else ""
    font = f'font-family="{e(family)}"{weight}{slant}'
    # the adjective: letter by letter on a gentle arc, or straight and spaced out
    adj_size = 34 if st["arc"] else 26
    m = _metrics(family, adj_size, bold, italic)
    if st["arc"]:
        total = m.horizontalAdvance(adjective) * 1.06
        radius = max(total * 1.15, 320)
        cx, cy = width / 2, 66 + radius
        letters, x = [], -total / 2
        for ch in adjective:
            adv = m.horizontalAdvance(ch) * 1.06
            angle = (x + adv / 2) / radius
            px, py = cx + radius * math.sin(angle), cy - radius * math.cos(angle)
            letters.append(f'<text x="0" y="0" transform="translate({px:.1f},{py:.1f}) rotate({math.degrees(angle):.1f})" '
                           f'text-anchor="middle">{e(ch)}</text>')
            x += adv
        adjective_svg = "".join(letters)
    else:
        adjective_svg = (f'<text x="{width / 2:.1f}" y="68" text-anchor="middle" letter-spacing="4">'
                         f'{e(adjective)}</text>')
    top = 92
    placed = [(line, top + size + i * size * 1.08) for i, line in enumerate(lines)]
    h = int(top + size * 1.08 * len(lines) + 30 + st["extrude"] + (18 if st["board"] else 0))

    def copies(dx=0.0, dy=0.0):
        return "".join(f'<text x="{width / 2 + dx:.1f}" y="{y + dy:.1f}" text-anchor="middle">{e(t)}</text>'
                       for t, y in placed)

    stops = st["fill"]
    x2, y2 = ("1", "0") if st["direction"] == "across" else ("0", "1")
    gradient = (f'<linearGradient id="fill" x1="0" y1="0" x2="{x2}" y2="{y2}">'
                + "".join(f'<stop offset="{i / max(len(stops) - 1, 1):.2f}" stop-color="{c}"/>' for i, c in enumerate(stops))
                + "</linearGradient>"
                f'<linearGradient id="adj" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{st["adjective"][0]}"/>'
                f'<stop offset="1" stop-color="{st["adjective"][1]}"/></linearGradient>')
    body = []
    if st["board"]:   # a chalkboard in a wooden frame behind the title
        slate, wood = st["board"]
        body.append(f'<rect x="8" y="8" width="{width - 16}" height="{h - 16}" rx="14" fill="{wood}"/>'
                    f'<rect x="20" y="20" width="{width - 40}" height="{h - 40}" rx="8" fill="{slate}"/>')
    body.append(f'<g {font} font-size="{adj_size}" fill="url(#adj)"'
                + (' stroke="#FFFFFF" stroke-width="1"' if not st["board"] else "") + f'>{adjective_svg}</g>')
    text = f'<g {font} font-size="{size}"'
    if st["shadow"]:
        colour, opacity, dx, dy = st["shadow"]
        body.append(f'{text} fill="{colour}" fill-opacity="{opacity}">{copies(dx, dy)}</g>')
    for i in range(st["extrude"], 0, -1):   # a 3D block of the outline colour behind the letters
        body.append(f'{text} fill="{st["outline"]}">{copies(i, i)}</g>')
    if st["outer"]:
        body.append(f'{text} fill="none" stroke="{st["outer"]}" stroke-width="{max(size / 4.5, 6):.1f}" '
                    f'stroke-linejoin="round">{copies()}</g>')
    if st["outline"]:
        body.append(f'{text} fill="none" stroke="{st["outline"]}" stroke-width="{max(size / 7, 4):.1f}" '
                    f'stroke-linejoin="round">{copies()}</g>')
    body.append(f'{text} fill="url(#fill)"' + (' fill-opacity="0.92"' if st["board"] else "") + f'>{copies()}</g>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{h}" viewBox="0 0 {width} {h}">'
           f'<defs>{gradient}</defs>{"".join(body)}</svg>')
    return svg, h


# ------------------------------------------------------------------ pages

def png_uri(image: QImage) -> str:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return "data:image/png;base64," + base64.b64encode(bytes(data)).decode()


def page(recipe: dict, maps: dict | None = None, for_qt=False, logo: QImage | None = None, title_font="", font="",
         overview: QImage | None = None):
    """(html, images): ``images`` maps the names used in the page to QImages. For a browser
    the pictures are inlined; for Qt (PDF, preview) they're resources named cookbook:…"""
    from . import fonts
    fonts.load()   # the bundled blackletter, for the medieval title look
    e = html.escape
    images = {"cookbook:title": title_image(recipe["adjective"], recipe["name"], look=recipe.get("wordart", "sunset"),
                                            family=title_font)}
    body_font = f"'{font}', {FONT}" if font else FONT
    if logo is not None:
        images["cookbook:logo"] = logo
    for number, image in (maps or {}).items():
        if image is not None:
            images[f"cookbook:map{number}"] = image

    def src(name):
        return name if for_qt else png_uri(images[name])

    cream, brown, green, red = "#FFF8E7", "#5D2E0C", "#2E7D32", "#C62828"

    def fs(px):   # Qt's PDF output counts px as device pixels (tiny at 1200 dpi): points there
        return f"{round(px * 0.75)}pt" if for_qt else f"{px}px"

    facts = [("Serves", recipe["serves"]), ("Prep time", recipe["prep"]), ("Difficulty", recipe["difficulty"])]
    facts_html = " &nbsp;·&nbsp; ".join(f"<b>{k}:</b> {e(v)}" for k, v in facts if v)

    def section(title, items):
        if not items:
            return ""
        return (f"<h2 style='color:{red}; font-size:{fs(20)}; margin:18px 0 6px'>{title}</h2><ul style='margin-top:0'>"
                + "".join(f"<li style='margin:3px 0'>{e(x)}</li>" for x in items) + "</ul>")

    steps = []
    for i, step in enumerate(recipe["method"], start=1):
        tip = (f"<div style='color:{green}; margin-top:4px'><b>Chef's tip:</b> <i>{e(step['note'])}</i></div>"
               if step["note"] else "")
        picture = ""
        name = f"cookbook:map{step['number']}"
        if maps is not None:
            picture = (f"<img src='{src(name)}' width='240' style='border:3px solid #FFFFFF'>"
                       if name in images else "")
        number = (f"<span style='display:inline-block; background:{red}; color:#FFFFFF; border-radius:14px; "
                  f"padding:2px 10px; font-weight:bold'>{i}</span>")
        steps.append(f"<tr><td valign='top' width='44' style='padding:8px 6px 8px 0'>{number}</td>"
                     f"<td valign='top' style='padding:8px 10px 8px 0; font-size:{fs(16)}'>{step['text']}{tip}</td>"
                     + (f"<td valign='top' width='250' style='padding:8px 0'>{picture}</td>" if maps is not None else "")
                     + "</tr>")
    description = (f"<p style='font-style:italic'><b>From the chef:</b> {e(recipe['description'])}</p>"
                   if recipe["description"] else "")
    logo_html = (f"<img src='{src('cookbook:logo')}' width='20' height='20' style='vertical-align:middle'> "
                 if logo is not None else "")
    if overview is not None:   # where in the world: once, under the title
        images["cookbook:overview"] = overview
    where = (f"<div style='text-align:center'><img src='{src('cookbook:overview')}' width='{overview.width() // 2}'>"
             "</div>") if overview is not None else ""
    body = (f"<div style='text-align:center'><img src='{src('cookbook:title')}' width='680'></div>{where}"
            f"<p style='text-align:center; font-size:{fs(15)}; color:{brown}; margin-top:0'><i>{e(recipe['intro'])}</i></p>"
            f"<p style='text-align:center; font-size:{fs(14)}'>{facts_html}</p>{description}"
            + section("You will need", recipe["ingredients"])
            + section("Season to taste", recipe["to_taste"])
            + section("Kitchen equipment", recipe["equipment"])
            + f"<h2 style='color:{red}; font-size:{fs(20)}; margin:18px 0 6px'>Method</h2>"
            + f"<table cellspacing='0' cellpadding='0' width='100%'>{''.join(steps)}</table>"
            + f"<p style='text-align:center; font-size:{fs(18)}; color:{brown}; margin-top:18px'><b>{e(recipe['closer'])}</b></p>"
            + f"<p style='text-align:center; font-size:{fs(11)}; color:#8A8478'>{logo_html}Made with Sous Chef</p>")
    if for_qt:
        return (f"<html><body style=\"font-family:{body_font}; color:{brown}; background:{cream}\">{body}</body></html>",
                images)
    style = (f"body {{ background:#F1E4C8; margin:0; font-family:{body_font}; color:{brown}; }}"
             f".card {{ max-width:860px; margin:28px auto; background:{cream}; border-radius:18px; padding:26px 36px; "
             "box-shadow:0 3px 14px rgba(0,0,0,.18); border:2px dashed #E0C38A; }"
             "code { font-family: 'Courier New', monospace; background:#F6E9C9; padding:1px 4px; border-radius:4px; }"
             "@media print { body { background:#FFFFFF; } .card { box-shadow:none; margin:0; } }")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{e(recipe['adjective'])} {e(recipe['name'])}</title>"
            f"<style>{style}</style></head><body><div class='card'>{body}</div></body></html>\n", images)


def logo_image(size=40) -> QImage | None:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icons", "sous_chef_64.png")
    image = QImage(path)
    if image.isNull():
        return None
    return image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
