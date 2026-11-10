"""More still designs: a mind map, a feature river, a sticky-note board and a comic strip.
They use the drawing helpers of images.py and are drawn into the same kind of SVG."""

from __future__ import annotations

import math
import random

from qgis.core import QgsRasterLayer, QgsVectorLayer
from qgis.PyQt.QtCore import QRectF, QSize

from . import icons
from . import images as im


def _opt():
    return im._OPT


# ---------------------------------------------------------------- the workflow as a graph

def graph(controller, detail):
    """Data (starting layers and results) and tools, with the links between them."""
    plan = controller.plan
    data, tools, edges, out_key = {}, {}, [], {}
    general = detail == "general"
    for uid, input_plan in plan.inputs.items():
        words = icons.layer_icon(input_plan.layer_kind)[1]
        data[f"input:{uid}"] = dict(title=words.capitalize() if general else (input_plan.recorded or input_plan.label),
                                    kind=input_plan.layer_kind,
                                    role="input", uid=uid, number=None)
    finals = {param.uid for _sp, param in plan.outputs if param.role in ("final", "save")}
    for step_plan in plan.steps:
        tkey = f"tool:{step_plan.number}"
        name = step_plan.step.alg.displayName() if step_plan.step.alg else step_plan.step.label
        tools[tkey] = dict(title=f"{step_plan.number}. {name}" if _opt().numbers and not general else name,
                           alg=step_plan.step.alg,
                           number=step_plan.number, sp=step_plan)
        for param in step_plan.params:
            if param.kind in ("link", "input"):
                for src in param.sources:
                    a = out_key.get((src[1], src[2])) if src[0] == "link" else f"input:{src[1]}"
                    if a:
                        edges.append((a, tkey))
            elif param.kind in ("output", "temp"):
                out_key[(step_plan.number, param.name)] = param.uid
                words = icons.layer_icon(param.layer_kind)[1]
                data[param.uid] = dict(title=words.capitalize() if general else param.default_display, kind=param.layer_kind,
                                       role="output" if param.uid in finals else "intermediate", number=step_plan.number)
                edges.append((tkey, param.uid))
    edges = list(dict.fromkeys(edges))
    return data, tools, edges


def _depths(data, tools, edges):
    depth = {k: 0 for k in data if k.startswith("input:")}
    for _ in range(len(data) + len(tools) + 2):
        for a, b in edges:
            if a in depth:
                depth[b] = max(depth.get(b, 0), depth[a] + 1)
    for k in list(data) + list(tools):
        depth.setdefault(k, 0 if k in data else 1)
    return depth


# ---------------------------------------------------------------- mind map

def mind_map(controller, detail, theme, left_out):
    """The recipe in the middle, its results around it, then the tools that made them, then
    what those used, out to the starting layers at the edge."""
    data, tools, edges = graph(controller, detail)
    doc = im._Doc(theme)
    children = {}
    for a, b in edges:   # outward: from a result to its tool, from a tool to what it used
        children.setdefault(b, []).append(a)
    used_by = {a for a, _b in edges}
    roots = [k for k, d in data.items() if d["role"] == "output"] or [k for k in data if k not in used_by and k in data
                                                                      and not k.startswith("input:")]
    seen, tree = set(), {}

    def grow(k):
        seen.add(k)
        tree[k] = [c for c in children.get(k, []) if c not in seen]
        for c in tree[k]:
            seen.add(c)
        for c in tree[k]:
            grow(c)

    for r in roots:
        if r not in seen:
            grow(r)
    leaves = {}

    def count(k):
        leaves[k] = sum(count(c) for c in tree.get(k, [])) or 1
        return leaves[k]

    total = sum(count(r) for r in roots) or 1
    ring = 118 * max(_opt().spacing, 0.7)
    place, level = {}, {}

    def lay(k, a0, a1, lv):
        place[k], level[k] = (a0 + a1) / 2, lv
        a = a0
        for c in tree.get(k, []):
            span = (a1 - a0) * leaves[c] / leaves[k]
            lay(c, a, a + span, lv + 1)
            a += span

    a = -math.pi / 2
    for r in roots:
        span = 2 * math.pi * leaves[r] / total
        lay(r, a, a + span, 1)
        a += span

    def xy(k, extra=0.0):
        r = ring * level[k] + extra
        return r * math.cos(place[k]), r * math.sin(place[k])

    # links first, curving outward
    for k, kids in tree.items():
        for c in kids:
            x1, y1 = xy(k)
            x2, y2 = xy(c)
            mx, my = xy(c, -ring * 0.5)
            doc.add(f'<path d="M{x1:.1f},{y1:.1f} Q{mx:.1f},{my:.1f} {x2:.1f},{y2:.1f}" fill="none" '
                    f'stroke="{theme["line"]}" stroke-width="1.6" stroke-opacity="0.7"/>')
    for r in roots:
        x2, y2 = xy(r)
        doc.add(f'<path d="M0,0 L{x2:.1f},{y2:.1f}" stroke="{theme["line"]}" stroke-width="2.4" stroke-opacity="0.6"/>')
    # the hub
    title = im.image_title(controller, detail)
    lines = im._wrap(title, 120, 14, True, lines=3)
    hub = [f'<circle cx="0" cy="0" r="72" fill="{theme["tint"]}" stroke="{theme["step"][1]}" stroke-width="2"/>']
    for i, line in enumerate(lines):
        hub.append(im._text(0, -8 * (len(lines) - 1) + 16 * i + 5, line, 14, theme["ink"], True, "middle"))
    doc.add("".join(hub), QRectF(-72, -72, 144, 144))
    for k in place:
        x, y = xy(k)
        if k in tools:
            d = tools[k]
            fill = theme["tints"].get(d["alg"].group(), theme["tint"]) if d["alg"] else theme["tint"]
            label = im._elide(d["title"], 170, 13, True)
            w = im._width(label, 13, True)
            doc.add(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="21" fill="{fill}" stroke="{theme["step"][1]}" stroke-width="1.4"/>'
                    + im._image(im._alg_icon(d["alg"]), x - 10, y - 10, 20)
                    + im._text(x, y + 38, label, 13, theme["ink"], True, "middle", halo=theme["bg"]),
                    QRectF(x - max(w / 2, 20), y - 20, max(w, 40), 60))
        else:
            d = data[k]
            bg, edge = {"input": theme["input"], "output": theme["output"]}.get(d["role"], (theme["step"][0], theme["line"]))
            label = im._elide(d["title"], 190, 13, True)
            w = im._width(label, 13, True) + 40
            dash = ' stroke-dasharray="4 3"' if d["role"] == "intermediate" else ""
            doc.add(f'<rect x="{x - w / 2:.1f}" y="{y - 14:.1f}" width="{w:.1f}" height="28" rx="14" fill="{bg}" '
                    f'stroke="{edge}" stroke-width="1.4"{dash}/>'
                    + im._image(im._layer_icon(d["kind"]), x - w / 2 + 8, y - 8, 16)
                    + im._text(x - w / 2 + 28, y + 4.5, label, 13, theme["ink"], True), QRectF(x - w / 2, y - 14, w, 28))
    return doc.svg(title)


# ---------------------------------------------------------------- feature river

def _feature_counts(controller):
    """{data key: feature count (None: not known)} from the layers in the project."""
    from . import snapshots
    found, _used, keep = snapshots.result_layers(controller)
    starts = snapshots.starting_layers(controller)
    counts = {}
    for uid, layer in starts.items():
        counts[f"input:{uid}"] = layer.featureCount() if isinstance(layer, QgsVectorLayer) else None
    plan = controller.plan
    for step_plan in plan.steps:
        layer = found.get(step_plan.number)
        for param in step_plan.params:
            if param.kind in ("output", "temp"):
                counts[param.uid] = layer.featureCount() if isinstance(layer, QgsVectorLayer) and layer is not None else (
                    None if not isinstance(layer, QgsRasterLayer) else None)
    return counts, keep


def river(controller, detail, theme, left_out):
    """Each layer a band as wide as its number of features (on a log scale), flowing into the
    tools and out again."""
    data, tools, edges = graph(controller, detail)
    counts, keep = _feature_counts(controller)
    doc = im._Doc(theme)
    doc.keep = keep
    depth = _depths(data, tools, edges)
    top = max([c for c in counts.values() if c] or [1])

    def band(k):
        n = counts.get(k)
        if not n:
            return 10.0
        return 6 + 44 * math.log10(1 + n) / math.log10(1 + top)

    colours = {}
    palette = theme["metro"]
    for i, k in enumerate(k for k in data if k.startswith("input:")):
        colours[k] = palette[i % len(palette)]
    for _ in range(len(edges) + 1):
        for a, b in edges:
            if a in colours and b not in colours:
                colours[b] = colours[a]
    columns = {}
    for k, d in depth.items():
        columns.setdefault(d, []).append(k)
    gap_x = 150 * _opt().spacing
    xs, x = {}, 0.0
    for col in sorted(columns):
        xs[col] = x
        x += (16 if col % 2 else 12) + gap_x
    height = {k: (band(k) if k in data else 0) for k in depth}
    for k in tools:   # a tool is as tall as everything flowing into it, or out of it
        ins = sum(band(a) for a, b in edges if b == k)
        outs = sum(band(b) for a, b in edges if a == k)
        height[k] = max(ins, outs, 24)
    ys = {}
    tallest = max(sum(height[k] + 46 for k in v) for v in columns.values())
    for col in sorted(columns):
        keys = columns[col]
        if col:
            keys.sort(key=lambda k: sum(ys.get(a, 0) for a, b in edges if b == k)
                      / max(1, sum(1 for a, b in edges if b == k and a in ys)))
        y = (tallest - sum(height[k] + 46 for k in keys)) / 2
        for k in keys:
            ys[k] = y
            y += height[k] + 46
    # ribbons
    into, outof = {}, {}
    for a, b in edges:
        w = band(a) if a in data else band(b)
        ya = ys[a] + outof.get(a, 0.0)
        yb = ys[b] + into.get(b, 0.0)
        outof[a] = outof.get(a, 0.0) + (w if a in tools else 0)
        into[b] = into.get(b, 0.0) + (w if b in tools else 0)
        x1 = xs[depth[a]] + (12 if a in data else 16)
        x2 = xs[depth[b]]
        mid = (x2 - x1) / 2
        c = colours.get(a, theme["data"])
        d = (f"M{x1:.1f},{ya:.1f} C{x1 + mid:.1f},{ya:.1f} {x2 - mid:.1f},{yb:.1f} {x2:.1f},{yb:.1f} "
             f"L{x2:.1f},{yb + w:.1f} C{x2 - mid:.1f},{yb + w:.1f} {x1 + mid:.1f},{ya + w:.1f} {x1:.1f},{ya + w:.1f} Z")
        doc.add(f'<path d="{d}" fill="{c}" fill-opacity="0.38" stroke="{c}" stroke-opacity="0.5" stroke-width="0.6"/>')
    for k, d in data.items():
        x, y, h = xs[depth[k]], ys[k], height[k]
        c = colours.get(k, theme["data"])
        n = counts.get(k)
        label = d["title"] + (f" · {n:,} feature{'s' if n != 1 else ''}" if n is not None else "")
        doc.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="12" height="{h:.1f}" rx="3" fill="{c}"/>'
                + im._text(x + 18, y + h / 2 + 4, im._elide(label, gap_x * 1.6, 11), 11, theme["ink"], True, halo=theme["bg"]),
                QRectF(x, y, 12 + min(im._width(label, 11), gap_x * 1.6) + 20, h))
    for k, d in tools.items():
        x, y, h = xs[depth[k]], ys[k], height[k]
        label = im._elide(d["title"], gap_x * 1.4, 11)
        doc.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="16" height="{h:.1f}" rx="4" fill="{theme["step"][1]}" '
                f'fill-opacity="0.85"/>'
                + im._text(x + 8, y - 8, label, 11, theme["ink"], True, "middle", halo=theme["bg"]),
                QRectF(x - 40, y - 22, 96, h + 22))
    note = "Band widths: number of features, on a log scale" + (
        "" if all(v is not None for v in counts.values()) else " (layers that couldn't be counted are drawn thin)")
    doc.add(im._text(0, tallest + 26, note, 10.5, theme["muted"]), QRectF(0, tallest + 10, 300, 20))
    return doc.svg(im.image_title(controller, detail))


# ---------------------------------------------------------------- sticky-note board

def board(controller, detail, theme, left_out):
    """The steps as sticky notes pinned on a corkboard, in four columns: ingredients,
    preparation, cooking and served."""
    plan = controller.plan
    data, tools, _edges = graph(controller, detail)
    doc = im._Doc(dict(theme, bg="#C9A26B"))
    cols = [("Ingredients", "#FFF3A1", [k for k in data if k.startswith("input:")]),
            ("Preparation", "#FFD6E0", [k for k, d in tools.items() if d["alg"] and d["alg"].group() in im.PREPARATION]),
            ("Cooking", "#CFE8FF", [k for k, d in tools.items() if not (d["alg"] and d["alg"].group() in im.PREPARATION)]),
            ("Served", "#D4F5C9", [k for k, d in data.items() if d["role"] == "output"])]
    note_w, note_h = 176, 124
    gap = 26 * _opt().spacing + 10
    rnd = random.Random(5)  # nosec B311: drawing variety, not security
    x = 0.0
    tallest = 0.0
    for title, colour, keys in cols:
        per = 5
        sub = max(1, math.ceil(len(keys) / per))
        width = sub * note_w + (sub - 1) * 14
        doc.add(f'<g transform="rotate({rnd.uniform(-2, 2):.1f} {x + width / 2:.1f} 0)">'
                f'<rect x="{x + width / 2 - 70:.1f}" y="-18" width="140" height="32" fill="#FBF8F0" '
                f'stroke="#B8A27A" stroke-width="0.8"/>'
                + im._text(x + width / 2, 4, title, 15, "#3B2F22", True, "middle") + "</g>",
                QRectF(x, -20, width, 36))
        for i, k in enumerate(keys):
            nx = x + (i // per) * (note_w + 14)
            ny = 40 + (i % per) * (note_h + gap)
            angle = rnd.uniform(-3.5, 3.5)
            if k in tools:
                d = tools[k]
                step_plan = d["sp"]
                head = d["title"]
                results = [param.default_display for param in step_plan.params if param.kind in ("output", "temp")]
                body = ("→ " + ", ".join(results)) if results and detail != "general" else ""
                if detail == "specific":
                    changed = [f"{param.label}: {param.shown_value}" for param in step_plan.params
                               if param.kind == "static" and not param.is_default and "#" not in param.name][:2]
                    body = "; ".join(changed) or body
            else:
                d = data[k]
                head = d["title"]
                body = icons.layer_icon(d["kind"])[1]
            lines = im._wrap(head, note_w - 24, 14, True, lines=2)
            more = im._wrap(body, note_w - 24, 12, lines=3) if body else []
            parts = [f'<g transform="rotate({angle:.1f} {nx + note_w / 2:.1f} {ny + note_h / 2:.1f})">',
                     f'<rect x="{nx + 3:.1f}" y="{ny + 5:.1f}" width="{note_w}" height="{note_h}" fill="#000000" '
                     f'fill-opacity="0.18"/>',
                     f'<rect x="{nx:.1f}" y="{ny:.1f}" width="{note_w}" height="{note_h}" fill="{colour}"/>']
            ty = ny + 34
            for line in lines:
                parts.append(im._text(nx + 12, ty, line, 14, "#2B2B2B", True))
                ty += 18
            for line in more:
                parts.append(im._text(nx + 12, ty + 4, line, 12, "#4A4A4A"))
                ty += 15
            parts.append(f'<circle cx="{nx + note_w / 2:.1f}" cy="{ny + 9:.1f}" r="6" fill="#D33F3F" stroke="#8E1F1F"/>'
                         f'<circle cx="{nx + note_w / 2 - 2:.1f}" cy="{ny + 7:.1f}" r="1.8" fill="#FFFFFF" '
                         f'fill-opacity="0.8"/></g>')
            doc.add("".join(parts), QRectF(nx - 6, ny - 6, note_w + 12, note_h + 12))
            tallest = max(tallest, ny + note_h)
        x += width + 40
    if not plan.steps:
        doc.add(im._text(0, 60, "No steps to show.", 13, "#3B2F22"), QRectF(0, 40, 200, 30))
    # the cork: speckles across the whole board
    specks = []
    rs = random.Random(9)  # nosec B311: drawing variety, not security
    for _ in range(int(x * tallest / 900)):
        specks.append(f'<circle cx="{rs.uniform(-20, x):.0f}" cy="{rs.uniform(-30, tallest + 20):.0f}" '
                      f'r="{rs.uniform(0.8, 2.2):.1f}" fill="#7A5A2E" fill-opacity="{rs.uniform(0.15, 0.4):.2f}"/>')
    doc.parts.insert(0, "".join(specks))
    return doc.svg(im.image_title(controller, detail))


# ---------------------------------------------------------------- comic strip

def comic(controller, detail, theme, left_out):
    """Storyboard panels, comic style: a caption box, the step's map, and a speech bubble in
    the cookbook's kitchen language."""
    from . import cookbook, snapshots
    from .cookbook import png_uri
    import html as h
    plan = controller.plan
    maps = snapshots.collect(controller, _opt().map_background)
    recipe = cookbook.build(controller, detail, False, 0)
    said = {}
    for item in recipe["method"]:
        said[item["number"]] = h.unescape(item["text"])
    for step_plan in plan.steps:   # a repeated step says what its first run said
        g = controller.group_of(step_plan.number)
        if step_plan.number not in said and g is not None:
            first = next((s for s in plan.steps if controller.group_of(s.number) == g), None)
            if first is not None and first.number in said:
                said[step_plan.number] = said[first.number]
    doc = im._Doc(dict(theme, bg="#FFFDF3"))
    from . import fonts
    fonts.load()
    lettering = fonts.first_installed(["Bangers", "Impact", "Arial Black"]) or ""
    panel_w, map_w, map_h = 320, 296, 168
    per_row = 2 if _opt().spacing < 0.8 else 4 if _opt().spacing > 1.2 else 3
    if len(plan.steps) <= 4:   # a short workflow reads best as one strip
        per_row = max(len(plan.steps), 1)
    gap = 22
    y = 0.0
    steps = list(plan.steps)
    for row in range(0, len(steps), per_row):
        chunk = steps[row:row + per_row]
        bubbles = [im._wrap(said.get(step_plan.number, ""), panel_w - 56, 12, lines=4) for step_plan in chunk]
        panel_h = 44 + map_h + 26 + 16 * max((len(b) for b in bubbles), default=1)
        for i, step_plan in enumerate(chunk):
            x = i * (panel_w + gap)
            name = step_plan.step.alg.displayName() if step_plan.step.alg else step_plan.step.label
            caption = f"{step_plan.number}. {name}" if _opt().numbers and detail != "general" else name
            image = maps.get(step_plan.number) or snapshots.placeholder(QSize(300, 190))
            uri = png_uri(image)
            parts = [f'<rect x="{x:.1f}" y="{y:.1f}" width="{panel_w}" height="{panel_h:.1f}" fill="#FFFFFF" '
                     f'stroke="#111111" stroke-width="3"/>',
                     f'<image href="{uri}" xlink:href="{uri}" x="{x + 12:.1f}" y="{y + 40:.1f}" width="{map_w}" '
                     f'height="{map_h}" preserveAspectRatio="xMidYMid slice"/>',
                     f'<rect x="{x + 12:.1f}" y="{y + 40:.1f}" width="{map_w}" height="{map_h}" fill="none" '
                     f'stroke="#111111" stroke-width="1.5"/>']
            text = im._elide(caption, panel_w - 50, 13, True)
            cw = min(im._width(text, 13, True) * 1.08 + 24, panel_w - 24)
            parts.append(f'<rect x="{x + 10:.1f}" y="{y + 10:.1f}" width="{cw:.1f}" height="24" fill="#FFE66D" '
                         f'stroke="#111111" stroke-width="1.5"/>'
                         + im._text(x + 20, y + 28, text, 15 if lettering == "Bangers" else 13, "#111111",
                                    lettering != "Bangers", family=lettering))
            lines = bubbles[i]
            if lines and lines[0]:
                by = y + 40 + map_h + 8
                bh = 16 * len(lines) + 14
                tail_x = x + panel_w * 0.3
                parts.append(f'<path d="M{tail_x:.1f},{by + 2:.1f} L{tail_x + 6:.1f},{by - 18:.1f} '
                             f'L{tail_x + 20:.1f},{by + 2:.1f} Z" fill="#FFFFFF" stroke="#111111" stroke-width="1.5"/>'
                             f'<rect x="{x + 16:.1f}" y="{by:.1f}" width="{panel_w - 32}" height="{bh:.1f}" rx="16" '
                             f'fill="#FFFFFF" stroke="#111111" stroke-width="1.5"/>'
                             f'<path d="M{tail_x + 1:.1f},{by + 1.6:.1f} L{tail_x + 19:.1f},{by + 1.6:.1f}" '
                             f'stroke="#FFFFFF" stroke-width="2.4"/>')
                for k, line in enumerate(lines):
                    parts.append(im._text(x + panel_w / 2, by + 19 + 16 * k, line, 12, "#111111", anchor="middle"))
            doc.marks.setdefault("cards", []).append((step_plan.number, QRectF(x - 3, y - 3, panel_w + 6, panel_h + 6)))
            doc.add("".join(parts), QRectF(x, y, panel_w, panel_h))
        y += panel_h + gap
    if not steps:
        doc.add(im._text(0, 20, "No steps to show.", 13, "#111111"), QRectF(0, 0, 300, 30))
    return doc.svg(im.image_title(controller, detail))
