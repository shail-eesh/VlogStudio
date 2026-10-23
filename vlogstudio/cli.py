"""Command line interface:  ./vlog <command> [options]"""
import argparse
import json
import os
import shutil
import sys
import time

from . import config as C
from . import util
from .util import log

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _val(s):
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return s


def _project(args):
    sets = {}
    for kv in args.set or []:
        k, _, v = kv.partition("=")
        sets[k.strip()] = _val(v.strip())
    if args.no_llm:
        sets["llm.enabled"] = False
    if args.llm:
        sets["llm.enabled"] = True
    cfg_path = args.config or (os.path.join(os.getcwd(), "vlog.toml") if os.path.exists("vlog.toml")
                               else os.path.join(HERE, "vlog.toml"))
    if not os.path.exists(cfg_path):
        util.die(f"no config found ({cfg_path}). Run: ./vlog init /path/to/media")
    cfg = C.load(cfg_path, sets)
    if not os.path.isdir(cfg.media_folder):
        util.die(f"media folder not found: {cfg.media_folder} (edit [project] media_folder in {cfg_path})")
    from .project import Project
    return Project(cfg)


# ---------------- commands ----------------
def cmd_init(args):
    src = os.path.join(HERE, "vlog.example.toml")
    dst = os.path.abspath(args.config or "vlog.toml")
    if os.path.exists(dst) and not args.force:
        util.die(f"{dst} exists (use --force to overwrite)")
    with open(src, encoding="utf-8") as f:
        t = f.read()
    media = os.path.abspath(args.folder)
    try:
        rel = os.path.relpath(media, os.path.dirname(dst))
    except ValueError:  # different drive on Windows
        rel = media
    rel = rel.replace("\\", "/")  # forward slashes work everywhere and need no TOML escaping
    t = t.replace('media_folder    = ".."', f"media_folder    = {json.dumps(rel)}")
    t = t.replace('output_folder   = "../Vlog_Studio_Output"',
                  f"output_folder   = {json.dumps(rel.rstrip('/') + '/Vlog_Studio_Output')}")
    with open(dst, "w", encoding="utf-8") as f:
        f.write(t)
    log(f"wrote {dst} (media folder: {media}). Edit it, then run ./vlog doctor", level="ok")


def cmd_doctor(args):
    ok = True

    def check(name, good, hint=""):
        nonlocal ok
        print(f"  {'✓' if good else '✗'} {name}" + ("" if good else f"   → {hint}"))
        ok &= bool(good)

    print("Vlog Studio doctor")
    check(f"python {sys.version.split()[0]}", sys.version_info >= (3, 9), "Python 3.9+ required")
    for mod in ("numpy", "PIL", "scipy"):
        try:
            __import__(mod)
            check(f"python module {mod}", True)
        except ImportError:
            check(f"python module {mod}", False, "run ./setup.sh")
    hint = "winget install Gyan.FFmpeg  (then reopen the terminal)" if util.IS_WIN else "brew install ffmpeg"
    check("ffmpeg", util.have("ffmpeg"), hint)
    check("ffprobe", util.have("ffprobe"), hint)
    if util.have("ffmpeg"):
        v = util.run(["ffmpeg", "-version"], capture=True).stdout.split("\n")[0]
        import re as _re
        mv = _re.search(r"version (?:n)?(\d+)\.", v)
        check(f"ffmpeg version ({v.split(' Copyright')[0].replace('ffmpeg version ', '')})",
              (int(mv.group(1)) >= 7) if mv else True, "ffmpeg 7 or newer is needed (" + hint + ")")
        r = util.run(["ffmpeg", "-hide_banner", "-filters"], capture=True)
        for flt in ("lut3d", "zoompan", "loudnorm", "dynaudnorm", "drawbox", "vignette", "noise"):
            check(f"ffmpeg filter {flt}", f" {flt} " in r.stdout, "install a full ffmpeg build (brew install ffmpeg)")
        check("libx264 encoder", util.ffmpeg_has_encoder("libx264"), hint)
        g = util.gpu_kind()
        name = util.gpu_name()
        if g == "nvidia":
            print(f"  ✓ GPU encoding: NVIDIA NVENC{f' ({name})' if name else ''}")
        elif g == "apple":
            print("  ✓ GPU encoding: Apple VideoToolbox")
        else:
            print("  · GPU encoding not available — using the CPU (slower, same quality)"
                  + (f"; found {name} but NVENC failed: update the NVIDIA driver" if name else ""))
    from .media import HEIF_NATIVE
    print(f"  {'✓' if HEIF_NATIVE or util.have('sips') else '·'} HEIC photos ({'pillow-heif' if HEIF_NATIVE else 'sips' if util.have('sips') else 'pip install pillow-heif'})")
    try:
        proj = _project(args)
    except SystemExit:
        return
    from .titles import Fonts
    f = Fonts(proj.cfg["titles"], 1.0)
    for role in ("display", "light", "heavy", "medium", "script"):
        p, _ = f.resolve(role)
        check(f"font {role}: {os.path.basename(p) if p else 'none'}", bool(p),
              "set [titles] font_* in vlog.toml to any installed .ttf/.otf")
    print(f"  media folder: {proj.cfg.media_folder}")
    print(f"  output folder: {proj.cfg.output_folder}")
    lc = proj.cfg["llm"]
    if lc["enabled"]:
        try:
            models = proj.llm().list_models()
            check(f"local LLM server {lc['base_url']}", True)
            check(f"model {lc['model']} available", lc["model"] in models or not models,
                  f"pull it (e.g. `ollama pull {lc['model']}`); server has: {', '.join(models[:8])}")
        except Exception as e:
            check(f"local LLM server {lc['base_url']}", False, str(e))
    else:
        print("  · LLM disabled ([llm] enabled = false) — fully automatic mode")
    print("all good" if ok else "some checks failed")


def cmd_status(args):
    print(json.dumps(_project(args).status(), indent=1))


def cmd_scan(args):
    proj = _project(args)
    items = proj.scan(force=True)
    for m in items:
        print(f"{m['id']:<28} {m['kind']:<5} " + (f"{m['duration']:6.1f}s " if m["kind"] == "video" else "        ")
              + f"{m['width']}x{m['height']} flag={m['flag_cw']:>3}° {m['profile']:<9} {(m.get('created') or '')[:16]}")


def cmd_analyze(args):
    proj = _project(args)
    proj.scan(force=args.force)
    res = proj.analyze(force=True)
    for cid, r in res.items():
        print(f"{cid:<28} usable={str(r.get('usable')):<5} rot={r.get('rotate_cw', 0):>3} stops={r.get('stops', 0):+.2f} "
              f"wb={r.get('wb')} q={r.get('quality', 0):.2f} {r.get('reason', '')}")
    print("\nOverview sheets:", *proj.ensure_sheets(), sep="\n  ")


def cmd_sheets(args):
    proj = _project(args)
    paths = proj.ensure_sheets()
    if args.clip:
        paths = [proj.clip_sheet(c) for c in args.clip]
    print(*paths, sep="\n")
    if args.open and util.have("open"):
        util.run(["open", *paths], check=False)


def cmd_override(args):
    proj = _project(args)
    kw = {}
    for kv in args.values:
        k, _, v = kv.partition("=")
        kw[k] = _val(v)
    cur = proj.set_override(args.clip, **kw)
    log(f"{args.clip}: {cur}", level="ok")


def cmd_orient(args):
    proj = _project(args)
    proj.analyze()
    if args.llm:
        proj.llm_orientation()
    for cid, r in proj.analysis.items():
        if r.get("orientation_fixed") or r.get("rotate_cw"):
            print(f"{cid:<28} rotate_cw={r.get('rotate_cw')}  ({r.get('orientation_note')})")
    print("Check:", *proj.ensure_sheets(), sep="\n  ")


def cmd_caption(args):
    proj = _project(args)
    if not proj.cfg["llm"]["enabled"]:
        util.die("captions need a local model: set [llm] enabled = true (or pass --llm)")
    from .llm import LLMError
    try:
        caps = proj.caption(force=args.force)
    except LLMError as e:
        util.die(str(e))
    print(json.dumps(caps, indent=1)[:4000])


def cmd_music(args):
    proj = _project(args)
    mc = proj.cfg["music"]
    if args.preset:
        mc["preset"], mc["mode"] = args.preset, "generate"
    if args.file:
        mc["file"], mc["mode"] = args.file, "file"
    if args.bpm:
        mc["bpm"] = args.bpm
    if args.duration:
        proj.cfg["project"]["target_duration"] = args.duration
    st = proj.make_music(force=True)
    print(f"{st['wav']}\n{st['bpm']:.1f} BPM, {st['duration']:.1f}s")
    for s in st["sections"]:
        print(f"  {s['name']:<7} {s['energy']:<4} {s['start']:6.2f} – {s['end']:6.2f}s")


def cmd_plan(args):
    proj = _project(args)
    plan = proj.make_plan(use_llm=proj.llm_enabled("storyboard"))
    from .plan import describe
    print(describe(plan))
    for w in plan.get("warnings", []):
        print("  repaired:", w)


def cmd_preview(args):
    proj = _project(args)
    if not proj.plan or args.replan:
        proj.make_plan()
    out = proj.render_preview()
    print(out)
    if args.open and util.have("open"):
        util.run(["open", out], check=False)


def cmd_render(args):
    proj = _project(args)
    if not proj.plan or args.replan:
        proj.make_plan()
    outs = proj.render_final()
    print(*outs, sep="\n")


def cmd_grade(args):
    print(*_project(args).export_graded(), sep="\n")


def cmd_luts(args):
    print(*_project(args).export_luts(), sep="\n")


def cmd_make(args):
    """Full pipeline in one go."""
    proj = _project(args)
    if not args.preview_only:
        from .plan import describe
    outs = proj.make_all(preview=args.preview, preview_only=args.preview_only, graded=args.graded, rescan=args.rescan)
    print(*outs, sep="\n")


def cmd_ui(args):
    from .ui.server import serve
    serve(args.config, port=args.port, open_browser=not args.no_browser)


def cmd_agent(args):
    proj = _project(args)
    if not proj.cfg["llm"]["enabled"]:
        util.die("agent mode needs a local model: set [llm] enabled = true (or pass --llm)")
    from .agent import run
    goal = args.goal or ("Make a viral, cinematic, horizontal travel vlog from my footage with a bright, happy, "
                         "well-lit grade. Check orientation, build the storyboard, preview, fix issues, then render "
                         "the final video.")
    run(proj, goal, max_steps=args.max_steps)


def cmd_skills(args):
    from .tools import SKILLS, skill_index
    if args.name:
        with open(os.path.join(SKILLS, args.name + ".md")) as f:
            print(f.read())
    else:
        for n, d in skill_index():
            print(f"{n:<22} {d}")


def cmd_tools(args):
    from .tools import REGISTRY
    for name, t in REGISTRY.items():
        print(f"{name:<22} {t['schema']['function']['description']}")


def cmd_clean(args):
    proj = _project(args)
    targets = [proj.work] if args.all else [os.path.join(proj.work, d) for d in os.listdir(proj.work)
                                             if d.startswith(("segs_", "titles_", "review"))]
    for t in targets:
        if os.path.isdir(t):
            shutil.rmtree(t)
            log(f"removed {t}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vlog", description="Local cinematic travel-vlog maker (no cloud).")
    ap.add_argument("--config", "-c", help="path to vlog.toml")
    ap.add_argument("--set", "-s", action="append", metavar="SECTION.KEY=VALUE", help="override a config value")
    ap.add_argument("--no-llm", action="store_true", help="disable the local model for this run")
    ap.add_argument("--llm", action="store_true", help="enable the local model for this run")
    ap.add_argument("--verbose", "-v", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(fn=fn)
        return p

    p = add("init", cmd_init, "create vlog.toml for a media folder")
    p.add_argument("folder")
    p.add_argument("--force", action="store_true")
    add("doctor", cmd_doctor, "check ffmpeg, python deps, fonts and the local model server")
    add("status", cmd_status, "show cached progress")
    add("scan", cmd_scan, "list media and metadata")
    p = add("analyze", cmd_analyze, "sample frames, balance exposure/WB, detect orientation, score shots")
    p.add_argument("--force", action="store_true", help="rescan the folder too")
    p = add("sheets", cmd_sheets, "print/open contact sheets")
    p.add_argument("clip", nargs="*")
    p.add_argument("--open", action="store_true")
    p = add("override", cmd_override, "per-clip fix, e.g. override CLIP rotate=180 stops=-0.5 exclude=true")
    p.add_argument("clip")
    p.add_argument("values", nargs="+")
    p = add("orient", cmd_orient, "show orientation decisions (--llm: verify with the vision model)")
    p.add_argument("--llm", action="store_true", dest="llm_orient")
    p = add("caption", cmd_caption, "describe clips with the local vision model")
    p.add_argument("--force", action="store_true")
    p = add("music", cmd_music, "generate or analyse the soundtrack")
    p.add_argument("--preset")
    p.add_argument("--file")
    p.add_argument("--bpm", type=float)
    p.add_argument("--duration", type=float)
    add("plan", cmd_plan, "build the beat-synced storyboard")
    p = add("preview", cmd_preview, "fast 720p preview render")
    p.add_argument("--replan", action="store_true")
    p.add_argument("--open", action="store_true")
    p = add("render", cmd_render, "final 4K + 1080p render + thumbnail")
    p.add_argument("--replan", action="store_true")
    add("grade-clips", cmd_grade, "export graded, upright copies of every clip")
    add("export-luts", cmd_luts, "export master .cube LUTs")
    p = add("make", cmd_make, "run everything: analyze → music → storyboard → render")
    p.add_argument("--rescan", action="store_true", help="ignore caches")
    p.add_argument("--preview", action="store_true", help="also render a 720p preview first")
    p.add_argument("--preview-only", action="store_true")
    p.add_argument("--graded", action="store_true", help="also export graded clips")
    p = add("agent", cmd_agent, "let the local model drive the tools (needs [llm] enabled)")
    p.add_argument("goal", nargs="?")
    p.add_argument("--max-steps", type=int, default=40)
    p = add("ui", cmd_ui, "open the Vlog Studio app in your browser (local only)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p = add("skills", cmd_skills, "list or print skill guides")
    p.add_argument("name", nargs="?")
    add("tools", cmd_tools, "list agent tools")
    p = add("clean", cmd_clean, "delete render caches (--all: whole work folder)")
    p.add_argument("--all", action="store_true")
    args = ap.parse_args(argv)
    util.VERBOSE = args.verbose
    if args.cmd == "orient":
        args.llm = args.llm or args.llm_orient
        args.llm = getattr(args, "llm_orient", False) or args.llm
    try:
        args.fn(args)
    except KeyboardInterrupt:
        util.die("interrupted", 130)
    except RuntimeError as e:
        util.die(str(e))
