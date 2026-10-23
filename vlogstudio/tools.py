"""Tools the local agent can call. Each returns ToolResult(text, images)."""
import glob
import json
import os

from . import plan as pl
from . import render as rd
from .color import PRESETS
from .music import PRESETS as MUSIC_PRESETS

SKILLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")


class ToolResult:
    def __init__(self, text, images=()):
        self.text, self.images = text, list(images)


REGISTRY = {}


def tool(name, description, params=None, required=()):
    def deco(fn):
        REGISTRY[name] = {"fn": fn, "schema": {"type": "function", "function": {
            "name": name, "description": description,
            "parameters": {"type": "object", "properties": params or {}, "required": list(required)}}}}
        return fn
    return deco


def schemas():
    return [t["schema"] for t in REGISTRY.values()]


def call(proj, name, args):
    if name not in REGISTRY:
        return ToolResult(f"ERROR: unknown tool {name!r}. Available: {', '.join(REGISTRY)}")
    try:
        r = REGISTRY[name]["fn"](proj, **(args or {}))
        return r if isinstance(r, ToolResult) else ToolResult(str(r))
    except TypeError as e:
        return ToolResult(f"ERROR: bad arguments for {name}: {e}")
    except Exception as e:  # tools must never crash the agent loop
        return ToolResult(f"ERROR in {name}: {type(e).__name__}: {e}")


def skill_index():
    out = []
    for p in sorted(glob.glob(os.path.join(SKILLS, "*.md"))):
        name, desc = os.path.basename(p)[:-3], ""
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.startswith("description:"):
                    desc = line.split(":", 1)[1].strip()
                    break
        out.append((name, desc))
    return out


# ---------------- knowledge ----------------
@tool("list_skills", "List available craft skills (guides) you can read.")
def _list_skills(proj):
    return "\n".join(f"- {n}: {d}" for n, d in skill_index())


@tool("read_skill", "Read one skill guide in full.", {"name": {"type": "string"}}, ["name"])
def _read_skill(proj, name):
    p = os.path.join(SKILLS, os.path.basename(name).replace(".md", "") + ".md")
    if not os.path.exists(p):
        return f"ERROR: no skill {name!r}. Use list_skills."
    with open(p, encoding="utf-8") as f:
        return f.read()


# ---------------- state ----------------
@tool("project_status", "Show what has been done so far and where files are.")
def _status(proj):
    return json.dumps(proj.status(), indent=1)


@tool("scan_media", "Find all videos and photos in the media folder and read their metadata.",
      {"force": {"type": "boolean", "description": "rescan even if cached"}})
def _scan(proj, force=False):
    items = proj.scan(force=force)
    lines = [f"{len(items)} items:"]
    for m in items:
        lines.append(f"{m['id']} | {m['kind']} | " + (f"{m['duration']:.1f}s | " if m["kind"] == "video" else "")
                     + f"{m['width']}x{m['height']} flag={m['flag_cw']}° | {m['profile']} | {(m.get('created') or '')[:16]}")
    return "\n".join(lines)


@tool("analyze_media", "Sample frames, auto exposure/white balance, detect orientation, score shots. Cached.",
      {"force": {"type": "boolean"}})
def _analyze(proj, force=False):
    res = proj.analyze(force=force)
    media = proj.scan()
    lines = []
    for m in media:
        r = res.get(m["id"], {})
        lines.append(f"{m['id']} | usable={r.get('usable')} {r.get('reason', '')} | rotate_cw={r.get('rotate_cw', 0)} | "
                     f"stops={r.get('stops')} | quality={r.get('quality')}")
    return "\n".join(lines) + "\nNext: view_overview to check orientation visually."


@tool("view_overview", "See overview sheet(s): one graded, rotated thumbnail per clip with its id.",
      {"page": {"type": "integer", "description": "sheet number starting at 1; omit for all"}})
def _overview(proj, page=None):
    paths = proj.ensure_sheets()
    if page:
        paths = paths[int(page) - 1:int(page)]
    return ToolResult(f"{len(paths)} overview sheet(s) attached. Check every frame is upright.", paths)


@tool("view_clip_sheet", "See one clip's timeline: frames every ~1.5 s with timestamps.",
      {"clip": {"type": "string"}}, ["clip"])
def _clip_sheet(proj, clip):
    p = proj.clip_sheet(clip)
    m = proj.get_media(clip)
    r = proj.analysis[clip]
    return ToolResult(f"{clip}: {m['kind']}, {m['duration']:.1f}s, rotate_cw={r.get('rotate_cw')}, "
                      f"stops={r.get('stops')}, wb={r.get('wb')}", [p] if p else [])


@tool("get_clip_info", "Technical details and frame scores for one clip.", {"clip": {"type": "string"}}, ["clip"])
def _clip_info(proj, clip):
    m = proj.get_media(clip)
    r = dict(proj.analysis[clip])
    r["frames"] = [{"t": f["t"], "score": f.get("score")} for f in r.get("frames", [])]
    cap = proj.captions.get(clip)
    return json.dumps({"media": {k: m[k] for k in m if k not in ("override",)}, "analysis": r, "caption": cap}, indent=1, default=str)


@tool("set_clip_override", "Fix one clip: rotation, exposure, white balance, or exclude it.",
      {"clip": {"type": "string"},
       "fix_rotation_cw": {"type": "integer", "description": "extra clockwise degrees (90/180/270) to make the frame as currently shown upright"},
       "stops": {"type": "number", "description": "exposure in stops (e.g. -0.5 darker)"},
       "wb": {"type": "array", "items": {"type": "number"}, "description": "RGB gains, e.g. [0.9,1,1.1]"},
       "exclude": {"type": "boolean"}}, ["clip"])
def _override(proj, clip, fix_rotation_cw=None, stops=None, wb=None, exclude=None):
    cur = proj.set_override(clip, fix_rotation_cw=fix_rotation_cw, stops=stops, wb=wb, exclude=exclude)
    r = proj.analysis[clip]
    return ToolResult(f"{clip} now: {json.dumps(cur)} (rotate_cw={r.get('rotate_cw')})", [proj.clip_sheet(clip)])


@tool("set_look", "Change the colour look preset and/or parameters. Presets: " + ", ".join(PRESETS),
      {"preset": {"type": "string"}, "strength": {"type": "number"},
       "target_exposure": {"type": "number", "description": "0.14 darker … 0.22 brighter (default 0.17)"},
       "overrides": {"type": "object", "description": "look params e.g. {\"contrast\":0.3,\"vibrance\":0.4}"}})
def _set_look(proj, preset=None, strength=None, target_exposure=None, overrides=None):
    lk = proj.cfg["look"]
    if preset:
        if preset not in PRESETS:
            return f"ERROR: unknown preset. Choose: {', '.join(PRESETS)}"
        lk["preset"] = preset
    if strength is not None:
        lk["strength"] = float(strength)
    if overrides:
        lk["overrides"] = {**lk.get("overrides", {}), **overrides}
    reanalyze = target_exposure is not None
    if reanalyze:
        lk["target_exposure"] = float(target_exposure)
    from .util import save_json
    save_json(proj.p("look_session.json"), lk)
    if reanalyze:
        proj.analyze(force=True)
    else:
        from . import analyze as an, color, sheets
        res = proj.analysis
        an._write_thumbs(proj.cfg, proj.scan(), res, color.look_params(lk))
        save_json(proj.p("analysis.json"), res)
        sheets.overview_sheets(proj.cfg, proj.scan(), res)
    return ToolResult(f"look now {lk['preset']} strength={lk['strength']} overrides={lk.get('overrides')}",
                      proj.ensure_sheets()[:1])


# ---------------- music & storyboard ----------------
@tool("make_music", "Create the soundtrack (fixes the cut grid). Presets: " + ", ".join(MUSIC_PRESETS),
      {"preset": {"type": "string"}, "bpm": {"type": "number"}, "duration": {"type": "number"},
       "file": {"type": "string", "description": "use your own track instead (path)"}})
def _music(proj, preset=None, bpm=None, duration=None, file=None):
    mc = proj.cfg["music"]
    if preset:
        mc["preset"], mc["mode"] = preset, "generate"
    if bpm:
        mc["bpm"] = bpm
    if file:
        mc["file"], mc["mode"] = file, "file"
    if duration:
        proj.cfg["project"]["target_duration"] = float(duration)
    st = proj.make_music(force=bool(preset or bpm or file or duration))
    return (f"music: {st['source']} {st['bpm']:.0f} BPM, {st['duration']:.1f}s\nsections: "
            + ", ".join(f"{s['name']}({s['energy']}) {s['start']:.1f}-{s['end']:.1f}s" for s in st["sections"]))


@tool("get_slots", "The fixed beat-synced shot slots you must fill (index, start, duration, section, role).")
def _slots(proj):
    st = proj.make_music()
    slots = pl.make_slots(st, proj.cfg["project"].get("pacing", "viral"))
    return "\n".join(f"{s['i']} | {s['start']:.2f}s | {s['dur']:.2f}s | {s['section']} | {s['role']}" for s in slots)


@tool("get_catalog", "Compact list of usable clips with captions/best moments for choosing shots.")
def _catalog(proj):
    from .creative import _catalog_text
    proj.analyze()
    return "id | kind | duration | created | place | category | quality | description | best(t:dur)\n" + _catalog_text(proj)


@tool("auto_storyboard", "Build an automatic storyboard (good starting point). Returns the shot list.")
def _auto(proj):
    plan = proj.make_plan(use_llm=False)
    return pl.describe(plan)


@tool("set_storyboard", "Set the storyboard: JSON with 'shots' (slot, clip, in, speed, zoom, pos) and optional 'text'. "
      "See skill storyboard-json.", {"storyboard": {"type": "object"}}, ["storyboard"])
def _set_sb(proj, storyboard):
    if isinstance(storyboard, str):
        storyboard = json.loads(storyboard)
    assign = {}
    for sh in storyboard.get("shots", []):
        assign[int(sh["slot"])] = {k: sh[k] for k in ("clip", "in", "speed", "zoom", "pos") if k in sh}
    plan = proj.make_plan(use_llm=False, assignments=assign or None, text=storyboard.get("text"))
    w = plan.get("warnings") or []
    return pl.describe(plan) + ("\nREPAIRED:\n" + "\n".join(w) if w else "")


@tool("get_storyboard", "Show the current storyboard.")
def _get_sb(proj):
    return pl.describe(proj.plan) if proj.plan else "no storyboard yet"


# ---------------- render ----------------
@tool("render_preview", "Fast 720p render of the current storyboard. Returns frames to review.")
def _preview(proj):
    out = proj.render_preview()
    plan = proj.plan
    times = [s["start"] + s["dur"] / 2 for s in plan["shots"]]
    if len(times) > 12:
        step = len(times) / 12
        times = [times[int(i * step)] for i in range(12)]
    frames = rd.preview_frames(proj.cfg, out, times, proj.p("review"))
    idx = {round(s["start"] + s["dur"] / 2, 3): s["i"] for s in plan["shots"]}
    txt = "\n".join(f"frame at {t:.2f}s = shot {idx.get(round(t, 3), '?')}" for t in times)
    return ToolResult(f"preview: {out}\n{txt}\nReview the attached frames; fix problems or call render_final.", frames)


@tool("render_final", "Render the final 4K master, 1080p version and thumbnail.")
def _final(proj):
    outs = proj.render_final()
    return "final files:\n" + "\n".join(outs)


@tool("export_graded_clips", "Write graded, upright copies of every clip (HEVC 10-bit).")
def _graded(proj):
    return f"{len(proj.export_graded())} graded clips written to {os.path.join(proj.cfg.output_folder, 'Graded_Clips')}"


@tool("export_luts", "Write master .cube LUTs (65³) for each camera profile in the project.")
def _luts(proj):
    return "\n".join(proj.export_luts())
