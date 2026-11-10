"""The workflow in words: a written recipe (what it makes, the ingredients, the method as
numbered steps), a methods paragraph for a report or paper, and a checklist for doing or
checking it by hand.

Detail: general (tools and kinds of data), named (the run's layers and steps), specific
(also the settings that differ from the tools' defaults).
"""

from __future__ import annotations

import html

from qgis.PyQt.QtCore import QDateTime

from ..core import builder
from . import icons


def _layer_words(kind):
    return icons.layer_icon(kind)[1]


def _input_name(input_plan, detail):
    words = _layer_words(input_plan.layer_kind)
    if detail == "general":
        return f"a {words}"
    return f"{input_plan.recorded}" if input_plan.recorded else input_plan.label


def build(controller, detail="named", notes=True, title="") -> dict:
    """The recipe as plain data: title, intro, makes, ingredients, to_taste, equipment, method."""
    plan = controller.plan
    by_number = {step_plan.number: step_plan for step_plan in plan.steps}
    group_of = {step_plan.number: controller.group_of(step_plan.number) for step_plan in plan.steps}
    if not title.strip():   # a general recipe names its tools, not the project or layers
        title = builder.general_title(plan) if detail == "general" else (controller.model_name or "Recipe")
    recipe = {"title": title.strip(), "intro": controller.choices.description.strip(),
              "makes": [], "ingredients": [], "to_taste": [], "equipment": [], "method": []}

    for step_plan, param in plan.outputs:
        words = _layer_words(param.layer_kind)
        recipe["makes"].append(f"{words}" if detail == "general" else f"{param.display} ({words})")

    for input_plan in plan.inputs.values():
        words = _layer_words(input_plan.layer_kind)
        if detail == "general":
            line = f"{words.capitalize()}: {input_plan.label}"
        else:
            fmt = input_plan.format if input_plan.format and "temporary" not in input_plan.format.lower() else ""
            extra = ", ".join(x for x in (f"recorded with {input_plan.recorded}" if input_plan.recorded else "", fmt) if x)
            line = f"{input_plan.label}: a {words}" + (f" ({extra})" if extra else "")
        if input_plan.help and not input_plan.help.startswith("Recorded with"):
            line += f". {input_plan.help}"
        recipe["ingredients"].append(line)

    for step_plan, param in plan.asked:
        line = param.display
        if detail != "general" and param.shown_value:
            line += f" (recorded: {param.shown_value})"
        if param.help and not param.help.startswith("Recorded with"):
            line += f". {param.help}"
        recipe["to_taste"].append(line)

    for name, tools in builder.plugins_needed([step_plan.step.alg for step_plan in plan.steps]).items():
        recipe["equipment"].append(f"the {name} plugin (for {', '.join(tools)})")

    def source_text(src):
        if src[0] == "link":
            prev = by_number.get(src[1])
            label = prev.output_labels.get(src[2], "") if prev else ""
            if detail == "general" or not label:
                return f"the result of step {src[1]}"
            return f"the result of step {src[1]} ({label})"
        input_plan = plan.inputs.get(src[1])
        return _input_name(input_plan, detail) if input_plan else "a layer"

    parts = getattr(plan, "parts", None) or []
    part_of = {n: k for k, part in enumerate(parts) for n in part}
    recipe["parts"] = len(parts)
    done_groups = set()
    for step_plan in sorted(plan.steps, key=lambda step_plan: (part_of.get(step_plan.number, 0), step_plan.number)):
        name = step_plan.step.alg.displayName()
        g = group_of.get(step_plan.number)
        if detail == "general" and g is not None:
            if g in done_groups:
                continue
            done_groups.add(g)
            members = [s for s in plan.steps if group_of.get(s.number) == g]
            recipe["method"].append({"text": f"{name}, {len(members)} times with different settings, "
                                     f"on {_uses(members[0], source_text)}.", "note": "",
                                     "number": members[0].number, "part": part_of.get(members[0].number, 0)})
            continue
        uses = _uses(step_plan, source_text)
        if step_plan.step.alg_id in builder.FIELD_CALCULATORS:
            field = step_plan.step.inputs.get("FIELD_NAME", "")
            text = (f"Calculate the field “{field}” of {uses} as {step_plan.step.inputs.get('FORMULA', '')}"
                    if detail != "general" else f"Calculate a new field of {uses} with an expression")
        else:
            text = f"{name}: use {uses}" if uses else name
        settings = []
        for param in step_plan.params:
            if param.kind != "static" or ("#" in param.name and not param.expose):
                continue   # an expression's columns are only mentioned when they're asked
            if param.expose:
                recorded = f" (recorded: {param.shown_value})" if detail != "general" else ""
                settings.append(f"{param.display} to taste{recorded}")
            elif detail == "specific" and not param.is_default and step_plan.step.alg_id not in builder.FIELD_CALCULATORS:
                settings.append(f"{param.label}: {param.shown_value}")
        if settings:
            text += " — " + "; ".join(settings)
        results = [param for param in step_plan.params if param.kind in ("output", "temp")]
        kept = [param for param in results if param.role in ("final", "save")]
        if kept and detail != "general":
            text += ". Makes " + ", ".join(f"“{param.display}”" for param in kept)
        elif kept:
            text += ". Makes a " + ", a ".join(_layer_words(param.layer_kind) for param in kept)
        recipe["method"].append({"text": text.rstrip(".") + ".", "note": step_plan.note if notes else "",
                                 "number": step_plan.number,
                                 "part": part_of.get(step_plan.number, 0)})
    when = [s.step.timestamp for s in plan.steps if s.step.timestamp is not None]
    recipe["footer"] = ("Written by Sous Chef" + (f" from a recording of {min(when).toString('d MMM yyyy')}" if when else "")
                        + f" · {QDateTime.currentDateTime().toString('d MMM yyyy')}")
    return recipe


def part_heading(r, index, always=False) -> str:
    """"Part 2" where the method reaches a new separate part, when there are several."""
    if r.get("parts", 0) < 2:
        return ""
    method = r["method"]
    part = method[index].get("part", 0)
    if always or index == 0 or method[index - 1].get("part", 0) != part:
        return f"Part {part + 1}"
    return ""


def _uses(step_plan, source_text):
    parts = []
    for param in step_plan.params:
        if param.kind in ("input", "link"):
            for src in param.sources:
                text = source_text(src)
                if text not in parts:
                    parts.append(text)
    if len(parts) > 1:
        return ", ".join(parts[:-1]) + " and " + parts[-1]
    return parts[0] if parts else ""


# ---------------------------------------------------------------- formats

def as_text(r) -> str:
    out = [r["title"], "=" * len(r["title"]), ""]
    if r["intro"]:
        out += [r["intro"], ""]
    if r["makes"]:
        out += ["Makes: " + "; ".join(r["makes"]), ""]
    if r["ingredients"]:
        out += ["INGREDIENTS"] + [f"  • {x}" for x in r["ingredients"]] + [""]
    if r["to_taste"]:
        out += ["TO TASTE (chosen when the recipe runs)"] + [f"  • {x}" for x in r["to_taste"]] + [""]
    if r["equipment"]:
        out += ["EQUIPMENT"] + [f"  • {x}" for x in r["equipment"]] + [""]
    out += ["METHOD"]
    for i, step in enumerate(r["method"], start=1):
        if part_heading(r, i - 1):
            out.append(f"  — {part_heading(r, i - 1)} —")
        out.append(f"  {i}. {step['text']}")
        if step["note"]:
            out.append(f"     Note: {step['note']}")
    out += ["", r["footer"]]
    return "\n".join(out) + "\n"


def as_markdown(r) -> str:
    out = [f"# {r['title']}", ""]
    if r["intro"]:
        out += [r["intro"], ""]
    if r["makes"]:
        out += ["**Makes:** " + "; ".join(r["makes"]), ""]
    if r["ingredients"]:
        out += ["## Ingredients", ""] + [f"- {x}" for x in r["ingredients"]] + [""]
    if r["to_taste"]:
        out += ["## To taste", "", "*Chosen when the recipe runs.*", ""] + [f"- {x}" for x in r["to_taste"]] + [""]
    if r["equipment"]:
        out += ["## Equipment", ""] + [f"- {x}" for x in r["equipment"]] + [""]
    out += ["## Method", ""]
    for i, step in enumerate(r["method"], start=1):
        if part_heading(r, i - 1):
            out.append(f"{i}. **{part_heading(r, i - 1)}:** {step['text']}")
            continue
        out.append(f"{i}. {step['text']}")
        if step["note"]:
            out.append(f"   *Note: {step['note']}*")
    out += ["", f"*{r['footer']}*"]
    return "\n".join(out) + "\n"


def as_html(r, maps: dict | None = None) -> str:
    """``maps``: {step number: image URI} to show a small map of each step's result."""
    e = html.escape
    maps = maps or {}

    def items(xs):
        return "".join(f"<li>{e(x)}</li>" for x in xs)

    method = "".join("<li>" + (f"<div class='part'>{part_heading(r, k)}</div>" if part_heading(r, k) else "")
                     + f"{e(s['text'])}" + (f"<div class='note'>✎ {e(s['note'])}</div>" if s["note"] else "")
                     + (f"<div><img class='map' src='{maps[s['number']]}' width='300'></div>"
                        if maps.get(s.get("number")) else "") + "</li>"
                     for k, s in enumerate(r["method"]))
    parts = [f"<h1>{e(r['title'])}</h1>"]
    if r["intro"]:
        parts.append(f"<p class='intro'>{e(r['intro']).replace(chr(10), '<br>')}</p>")
    if r["makes"]:
        parts.append(f"<p class='makes'><b>Makes:</b> {e('; '.join(r['makes']))}</p>")
    side = ""
    if r["ingredients"]:
        side += f"<h2>Ingredients</h2><ul>{items(r['ingredients'])}</ul>"
    if r["to_taste"]:
        side += f"<h2>To taste</h2><p class='hint'>Chosen when the recipe runs.</p><ul>{items(r['to_taste'])}</ul>"
    if r["equipment"]:
        side += f"<h2>Equipment</h2><ul>{items(r['equipment'])}</ul>"
    parts.append(f"<div class='cols'><aside>{side}</aside><main><h2>Method</h2><ol>{method}</ol></main></div>")
    parts.append(f"<p class='footer'>{e(r['footer'])}</p>")
    style = """
    body { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; color: #22272B; background: #F4F1EA; margin: 0; }
    .card { max-width: 900px; margin: 32px auto; background: #FFFDF7; border-radius: 14px; padding: 32px 40px;
            box-shadow: 0 2px 10px rgba(0,0,0,.12); border-top: 8px solid #4E8A3C; }
    h1 { margin: 0 0 8px; font-size: 28px; } h2 { font-size: 15px; letter-spacing: .08em; text-transform: uppercase;
    color: #4E8A3C; margin: 20px 0 8px; } .intro { color: #444; }
    .makes { background: #E3F2D9; padding: 8px 12px; border-radius: 8px; }
    .cols { display: flex; gap: 32px; flex-wrap: wrap; } aside { flex: 1 1 240px; } main { flex: 2 1 380px; }
    aside ul { padding-left: 18px; } aside li { margin: 4px 0; } ol li { margin: 8px 0; line-height: 1.45; }
    .note { color: #6B7178; font-style: italic; margin-top: 2px; } .hint { color: #6B7178; margin: 0 0 4px; font-size: 13px; }
    .map { border: 1px solid #DDD6C8; border-radius: 6px; margin-top: 6px; }
    .part { font-weight: 600; color: #4E8A3C; margin: 10px 0 2px; }
    .footer { color: #8A8A8A; font-size: 12px; margin-top: 24px; border-top: 1px solid #E5E0D5; padding-top: 8px; }
    @media print { body { background: #FFF; } .card { box-shadow: none; margin: 0; } }
    """
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{e(r['title'])}</title><style>{style}</style>"
            f"</head><body><div class='card'>{''.join(parts)}</div></body></html>\n")


def _body(font, base="'Segoe UI', 'Helvetica Neue', Arial, sans-serif", extra=""):
    face = (f"'{font}', " if font else "") + base
    return f"<html><body style=\"font-family: {face}; color:#22272B{extra}\">"


def as_qt_html(r, maps=False, font="", overview=False) -> str:
    """Plain HTML a QTextDocument lays out well (preview and PDF). ``maps``: show the step maps,
    added to the document as resources named map:<step number>."""
    e = html.escape
    green = "#4E8A3C"
    out = [f"<h1>{e(r['title'])}</h1>"]
    if overview:   # where in the world: once, under the title
        out.append("<p><img src='overview:map' width='240'></p>")
    if r["intro"]:
        out.append(f"<p>{e(r['intro']).replace(chr(10), '<br>')}</p>")
    if r["makes"]:
        out.append(f"<p style='background:#E3F2D9'>&nbsp;<b>Makes:</b> {e('; '.join(r['makes']))}</p>")
    for key, title, hint in (("ingredients", "Ingredients", ""), ("to_taste", "To taste", "Chosen when the recipe runs."),
                             ("equipment", "Equipment", "")):
        if r[key]:
            out.append(f"<h3 style='color:{green}'>{title.upper()}</h3>")
            if hint:
                out.append(f"<p style='color:#6B7178'><i>{hint}</i></p>")
            out.append("<ul>" + "".join(f"<li>{e(x)}</li>" for x in r[key]) + "</ul>")
    out.append(f"<h3 style='color:{green}'>METHOD</h3><ol>")
    for k, step in enumerate(r["method"]):
        if part_heading(r, k):
            out.append(f"</ol><h4 style='color:{green}'>{part_heading(r, k)}</h4><ol start='{k + 1}'>")
        note = f"<br><i style='color:#6B7178'>✎ {e(step['note'])}</i>" if step["note"] else ""
        picture = (f"<br><img src='map:{step.get('number')}' width='300'>"
                   if maps and step.get("number") is not None else "")
        out.append(f"<li style='margin-bottom:6px'>{e(step['text'])}{note}{picture}</li>")
    out.append(f"</ol><p style='color:#8A8A8A; font-size:small'>{e(r['footer'])}</p>")
    return (_body(font)
            + "".join(out) + "</body></html>")


# ---------------------------------------------------------------- methods paragraph

_LINKS = ["Next,", "Then,", "After that,"]


def methods(controller, detail="named") -> dict:
    """A methods section for a report or paper: {"title", "paragraphs": [str]}."""
    from qgis.core import Qgis, QgsProject
    plan = controller.plan
    by_number = {step_plan.number: step_plan for step_plan in plan.steps}

    def name_of(src):
        if src[0] == "link":
            prev = by_number.get(src[1])
            made = next((param for param in prev.params if param.name == src[2]), None) if prev else None
            if made is not None and detail != "general":
                return f"“{made.display}”"
            return f"the result of step {src[1]}"
        input_plan = plan.inputs.get(src[1])
        if input_plan is None:
            return "a layer"
        words = _layer_words(input_plan.layer_kind)
        return f"a {words}" if detail == "general" else f"the {input_plan.recorded or input_plan.label} {words}"

    intro = [f"All processing was carried out in QGIS {Qgis.version().split('-')[0]}"]
    plugins = builder.plugins_needed([step_plan.step.alg for step_plan in plan.steps])
    if plugins:
        intro[0] += " with the " + ", ".join(plugins) + (" plugin" if len(plugins) == 1 else " plugins")
    crs = QgsProject.instance().crs()
    if crs.isValid() and detail != "general":
        intro[0] += f", in {crs.description()} ({crs.authid()})"
    intro[0] += "."
    starts = []
    for input_plan in plan.inputs.values():
        words = _layer_words(input_plan.layer_kind)
        if detail == "general":
            starts.append(f"a {words}")
        else:
            fmt = input_plan.format if input_plan.format and "temporary" not in input_plan.format.lower() else ""
            starts.append(f"{input_plan.recorded or input_plan.label} (a {words}" + (f", {fmt}" if fmt else "") + ")")
    if starts:
        intro.append("The analysis started from " + _join(starts) + ".")
    parts = getattr(plan, "parts", None) or []
    part_of = {n: k for k, part in enumerate(parts) for n in part}
    ordered = sorted(plan.steps, key=lambda step_plan: (part_of.get(step_plan.number, 0), step_plan.number))
    steps, paragraphs = [], []
    for i, step_plan in enumerate(ordered):
        if len(parts) > 1 and i and part_of.get(step_plan.number) != part_of.get(ordered[i - 1].number):
            paragraphs.append(" ".join(steps))   # a new, separate part of the work
            steps = []
        alg = step_plan.step.alg
        tool = alg.displayName() if alg else step_plan.step.label
        uses = []
        for param in step_plan.params:
            if param.kind in ("input", "link"):
                for src in param.sources:
                    text = name_of(src)
                    if text not in uses:
                        uses.append(text)
        changed = []
        if detail != "general":
            for param in step_plan.params:
                if param.kind == "static" and not param.is_default and "#" not in param.name and param.shown_value:
                    changed.append(f"{param.label.lower()} {param.shown_value}")
        made = [param for param in step_plan.params if param.kind in ("output", "temp")]
        last = len(ordered) - 1
        first_of_part = i == 0 or (len(parts) > 1 and part_of.get(step_plan.number) != part_of.get(ordered[i - 1].number))
        link = ("First," if i == 0 else "Separately,") if first_of_part else "Finally," if i == last \
            else _LINKS[(i - 1) % len(_LINKS)]
        if step_plan.step.alg_id in builder.FIELD_CALCULATORS and detail != "general":
            text = (f"{link} the field “{step_plan.step.inputs.get('FIELD_NAME', '')}” of {_join(uses)} was calculated "
                    f"as {step_plan.step.inputs.get('FORMULA', '')}")
        else:
            text = f"{link} the {tool} tool" + (f" (`{step_plan.step.alg_id}`)" if detail == "specific" else "")
            text += f" was applied to {_join(uses)}" if uses else " was run"
            if changed:
                text += " (" + "; ".join(changed[:4]) + ")"
        if made and detail != "general":
            text += ", giving " + _join([f"“{param.display}”" for param in made])
        steps.append(text + ".")
    outro = ("The workflow was recorded with the Sous Chef plugin and saved as a QGIS Processing model "
             f"(“{controller.model_name}”), so it can be run again on other data.")
    paragraphs.append(" ".join(steps))
    if len(parts) > 1:
        intro.append(f"The work falls into {len(parts)} separate parts, which don't use each other's results.")
    return {"title": "Methods", "paragraphs": [" ".join(intro)] + paragraphs + [outro]}


def _join(parts):
    parts = [p for p in parts if p]
    if len(parts) > 1:
        return ", ".join(parts[:-1]) + " and " + parts[-1]
    return parts[0] if parts else ""


def methods_text(m) -> str:
    return "\n\n".join(p.replace("`", "") for p in m["paragraphs"]) + "\n"


def methods_markdown(m) -> str:
    return f"## {m['title']}\n\n" + "\n\n".join(m["paragraphs"]) + "\n"


def methods_qt_html(m, font="") -> str:
    e = html.escape
    body = "".join(f"<p style='line-height:150%'>{e(p).replace('`', '')}</p>" for p in m["paragraphs"])
    return (_body(font, "Georgia, 'Times New Roman', serif", "; font-size: 12pt")
            + f"<h2>{e(m['title'])}</h2>{body}</body></html>")


# ---------------------------------------------------------------- checklist

def checklist(r) -> dict:
    """Sections of tick-box items from a written recipe: before, steps, after."""
    before = ["QGIS 3.44 or newer is open"]
    before += [f"{x[0].upper()}{x[1:]} is installed" for x in r["equipment"]]
    before += [f"Layer ready: {x}" for x in r["ingredients"]]
    before += [f"Decide: {x}" for x in r["to_taste"]]
    sections = [("Before you start", None)]
    for i, s in enumerate(r["method"], 1):
        title = "Steps" + (f" — {part_heading(r, i - 1, True)}" if r.get("parts", 0) > 1 else "")
        if sections[-1][0] != title:
            sections.append((title, []))
        sections[-1][1].append(f"{i}. {s['text']}" + (f" (Note: {s['note']})" if s["note"] else ""))
    after = [f"Check the result: {x}" for x in r["makes"]] + ["Results saved where they need to be",
                                                              "Styles and layer names look right"]
    sections[0] = ("Before you start", before)
    return {"title": r["title"], "sections": sections + [("When it's done", after)],
            "footer": r["footer"]}


def checklist_text(c) -> str:
    out = [c["title"] + " — checklist", ""]
    for title, items in c["sections"]:
        out += [title.upper()] + [f"  [ ] {x}" for x in items] + [""]
    return "\n".join(out + [c["footer"]]) + "\n"


def checklist_markdown(c) -> str:
    out = [f"# {c['title']} — checklist", ""]
    for title, items in c["sections"]:
        out += [f"## {title}", ""] + [f"- [ ] {x}" for x in items] + [""]
    return "\n".join(out + [f"*{c['footer']}*"]) + "\n"


def checklist_qt_html(c, font="") -> str:
    e = html.escape
    out = [f"<h1>{e(c['title'])}</h1><p style='color:#6B7178'>Checklist</p>"]
    for title, items in c["sections"]:
        out.append(f"<h3 style='color:#2F6DB5'>{e(title).upper()}</h3><table cellspacing='4'>")
        out += [f"<tr><td style='font-size:14pt; padding-right:6px'>☐</td><td>{e(x)}</td></tr>" for x in items]
        out.append("</table>")
    out.append(f"<p style='color:#8A8A8A; font-size:small'>{e(c['footer'])}</p>")
    return (_body(font)
            + "".join(out) + "</body></html>")
