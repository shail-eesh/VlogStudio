"""Vlog Studio app: a local web UI (http://127.0.0.1:8765) over the same engine as the CLI.

Standard library only. Binds to 127.0.0.1, so nothing is reachable from other machines.
"""
import base64
import copy
import glob
import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
from PIL import Image

from .. import color, config as C, tomlw, util
from ..music import PRESETS as MUSIC_PRESETS
from ..plan import PACING_NAMES

UI_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(UI_DIR))
PROJECTS_DIR = os.path.join(ROOT, "projects")

# ---------------------------------------------------------------- settings schema (drives the forms)
F = []


def field(path, label, kind="text", help="", **kw):
    F.append({"path": path, "label": label, "type": kind, "help": help, **kw})


# project
field("project.media_folder", "Footage folder", "path", "Folder with your videos and photos (searched recursively).", pick="folder")
field("project.output_folder", "Output folder", "path", "Where videos, LUTs and the cache go.", pick="folder")
field("project.name", "Project name")
field("project.style", "Video type", "select", "Preset for look, music, pacing and text. Press “Apply type defaults” to fill the fields.",
      options=list(C.STYLES))
field("project.pacing", "Pacing", "select", "viral: 0.5–5 s cuts · cinematic: 1–6 s · documentary: shots up to ~10 s",
      options=PACING_NAMES)
field("project.target_duration", "Length (seconds)", "number", "Snapped to whole music bars.", min=15, max=600, step=1)
field("project.include_images", "Use photos too", "bool")
field("project.exclude", "Skip files matching", "list", "Comma-separated patterns, e.g. *_proxy.mov, screenshots/*")
field("project.title", "Main title", "text", "Big title word(s), e.g. NEW YORK")
field("project.title_script", "Script line above title", "text", "e.g. welcome to")
field("project.subtitle", "Title subline", "text", "e.g. CITY · OCTOBER 2024 (empty = month + year)")
field("project.hook", "Hook text (first 3 s)", "text", "e.g. 48 HOURS IN NYC (empty = hook template)")
field("project.hook_template", "Hook template", "text", "{span} = “48 HOURS” / “5 DAYS”, {title}, {month}")
field("project.hook_sub", "Hook subline", "text", "e.g. POV: YOUR FIRST TRIP TO THE BIG APPLE")
field("project.end_line", "End card line", "text", "e.g. you were a vibe.")
field("project.end_sub", "End card subline", "text", "e.g. SEE YOU AGAIN SOON")
field("project.chapter_cards", "Chapter cards (DAY 01…)", "bool")
field("project.location_tags", "Location tags", "bool")
field("project.location_sub", "Location tag subline", "text", "e.g. NEW YORK, NY")
# look
field("look.preset", "Look", "select", options=list(color.PRESETS))
field("look.strength", "Look strength", "range", min=0, max=1.5, step=0.05)
field("look.target_exposure", "Brightness", "range", "0.14 darker … 0.22 brighter", min=0.10, max=0.26, step=0.005)
field("look.auto_exposure", "Auto exposure per shot", "bool")
field("look.auto_white_balance", "Auto white balance per shot", "bool")
for k, lo, hi, st in (("contrast", 0, 0.6, 0.01), ("vibrance", 0, 0.8, 0.01), ("saturation", -0.4, 0.4, 0.01),
                      ("matte", 0, 0.08, 0.002), ("exposure", 0.5, 1.2, 0.01), ("skin_protect", 0, 1, 0.05)):
    field(f"look.overrides.{k}", k.replace("_", " ").title(), "range", "leave at preset value unless needed",
          min=lo, max=hi, step=st, optional=True)
# orientation
field("orientation.mode", "Orientation", "select", "auto fixes wrong rotation flags for a whole session",
      options=["auto", "honor_flag", "ignore_flag"])
field("orientation.portrait_fill", "Vertical shots", "select", "blur = blurred background, crop = fill the frame",
      options=["blur", "crop"])
# analysis
field("analysis.smart_moments", "Smart moments", "bool",
      "Long clips are shown to the AI in chunks; its moment lengths pick the slot and its ‘avoid’ ranges are never used.")
field("analysis.long_clip_chunk", "Seconds per contact sheet", "number", min=8, max=120, step=1)
field("analysis.max_moments_per_clip", "Max moments from one clip", "number", min=1, max=12, step=1)
field("analysis.sample_interval", "Analyse a frame every (s)", "number", min=0.5, max=5, step=0.1)
field("analysis.skip_start", "Skip first seconds of clips", "number", "phone auto-exposure settling", min=0, max=5, step=0.1)
field("analysis.workers", "Parallel decoders", "number", min=1, max=16, step=1)
# music
field("music.mode", "Music source", "select", options=["generate", "file"])
field("music.preset", "Music style", "select", options=list(MUSIC_PRESETS))
field("music.file", "My music file", "path", "Used when source = file (mp3, wav, m4a…)", pick="file")
field("music.bpm", "Tempo (BPM, 0 = style default)", "number", min=0, max=180, step=1)
field("music.key", "Key (empty = default)", "text", "e.g. A, F#, Bb")
field("music.seed", "Melody variation", "number", "change for a different melody", min=0, max=9999, step=1)
field("music.ambient_level", "Natural sound level", "range", "sound from your clips under the music", min=0, max=2, step=0.05)
# output
field("output.resolution", "Resolution", "select", options=["3840x2160", "2560x1440", "1920x1080", "1280x720"])
field("output.letterbox", "Cinematic bars", "select", "2.39 = widescreen film bars, 0 = none",
      options=[2.39, 2.0, 1.85, 0])
field("output.grain", "Film grain", "range", min=0, max=10, step=1)
field("output.vignette", "Vignette", "bool")
field("output.flashes", "Flash on music drops", "bool")
field("output.fps", "Frame rate", "select", options=[24, 25, 30])
field("output.crf", "Quality (lower = better)", "number", min=12, max=28, step=1)
field("output.x264_preset", "Encoder speed", "select", options=["veryfast", "fast", "medium", "slow"])
field("output.make_1080p", "Also make a 1080p copy", "bool")
field("output.export_graded_clips", "Also export graded clips", "bool")
field("output.render_workers", "Parallel renders", "number", min=1, max=8, step=1)
field("output.encoder", "Video encoder", "select",
      "auto = graphics card when available (NVIDIA NVENC on Windows, Apple VideoToolbox for drafts on Mac)",
      options=["auto", "gpu", "cpu"])
field("output.hw_decode", "GPU video decoding", "select", "auto = on for NVIDIA cards (phone H.264/HEVC footage)",
      options=["auto", "on", "off"])
field("output.basename", "File name", "text")
# llm
field("llm.enabled", "Use a local AI model", "bool", "Off = everything is decided automatically.")
field("llm.base_url", "Server address", "text", "Ollama http://localhost:11434/v1 · LM Studio http://localhost:1234/v1")
field("llm.model", "Model", "model")
field("llm.vision", "Model can see images", "bool", "needed for captions and orientation checks")
field("llm.use_for", "Use the model for", "multi", options=["orientation", "captions", "storyboard", "titles"])
field("llm.api_key", "API key (usually empty)", "password")
field("llm.temperature", "Creativity", "range", min=0, max=1.2, step=0.05)
field("llm.max_tokens", "Max reply tokens", "number", min=512, max=32768, step=256)
field("llm.timeout", "Timeout per request (s)", "number", min=30, max=3600, step=10)
field("llm.tool_mode", "Agent tool calling", "select", options=["auto", "native", "json"])
field("llm.json_mode", "Ask server for strict JSON", "bool")
field("llm.images_first", "Images before text in prompts", "bool", "recommended for Gemma 4 and Qwen-VL")
field("llm.unload_before_render", "Free the model's GPU memory before rendering", "bool",
      "Ollama only; the GPU encoder needs it on 8 GB cards. The model reloads by itself when needed")


# ---------------------------------------------------------------- state
class App:
    def __init__(self, cfg_path):
        self.cfg_path = os.path.abspath(cfg_path)
        self.lock = threading.Lock()
        self.job = {"running": False, "action": None, "log": [], "error": None, "result": None,
                    "started": None, "ended": None}
        self._proj = None
        util.SINKS.append(self._sink)

    def _sink(self, line, level):
        self.job["log"].append({"t": time.time(), "line": line, "level": level})
        if len(self.job["log"]) > 3000:
            del self.job["log"][:1000]

    @property
    def cfg(self):
        return self.project.cfg

    @property
    def project(self):
        if self._proj is None:
            from ..project import Project
            self._proj = Project(C.load(self.cfg_path))
        return self._proj

    def reload(self):
        self._proj = None
        return self.project

    def projects(self):
        out = [os.path.join(ROOT, "vlog.toml")] if os.path.exists(os.path.join(ROOT, "vlog.toml")) else []
        out += sorted(glob.glob(os.path.join(PROJECTS_DIR, "*.toml")))
        if self.cfg_path not in out:
            out.insert(0, self.cfg_path)
        res = []
        for p in out:
            try:
                c = C.load(p)
                res.append({"path": p, "name": c["project"]["name"], "media": c.media_folder})
            except Exception as e:
                res.append({"path": p, "name": os.path.basename(p), "media": f"(error: {e})"})
        return res

    def save_config(self, data):
        if self.job["running"]:
            raise RuntimeError("a job is running — settings are saved when it finishes")
        cur = copy.deepcopy(dict(self.cfg))
        for sec, body in data.items():
            if isinstance(body, dict) and sec not in ("places", "overrides"):
                cur.setdefault(sec, {})
                for k, v in body.items():
                    if k == "overrides" and isinstance(v, dict):
                        cur[sec]["overrides"] = {a: b for a, b in v.items() if b is not None and b != ""}
                    else:
                        cur[sec][k] = v
        if "places" in data:
            cur["places"] = data["places"]
        # keep paths relative to the config file when they were relative
        bak = self.cfg_path + ".bak"
        if os.path.exists(self.cfg_path) and not os.path.exists(bak):
            shutil.copy(self.cfg_path, bak)
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            f.write(tomlw.dumps(cur))
        self.reload()

    # ------------------------------------------------------------ jobs
    def start(self, action, args):
        with self.lock:
            if self.job["running"]:
                raise RuntimeError(f"already running: {self.job['action']}")
            util.CANCEL.clear()
            self.job.update(running=True, action=action, log=[], error=None, result=None,
                            started=time.time(), ended=None)
        threading.Thread(target=self._run, args=(action, args), daemon=True).start()

    def _run(self, action, a):
        proj = self.project
        try:
            util.log(f"{action} started", level="step")
            r = None
            if action == "analyze":
                proj.scan(force=True)
                r = proj.analyze(force=True) and "analysis done"
            elif action == "rethumb":
                from .. import analyze as an, sheets
                res = proj.analyze()
                an._write_thumbs(proj.cfg, proj.scan(), res, color.look_params(proj.cfg["look"]))
                util.save_json(proj.p("analysis.json"), res)
                sheets.overview_sheets(proj.cfg, proj.scan(), res)
                r = "thumbnails updated"
            elif action == "orient_ai":
                r = proj.llm_orientation()
            elif action == "captions":
                r = f"{len(proj.caption(force=bool(a.get('force'))))} captions"
            elif action == "music":
                r = proj.make_music(force=True)["wav"]
            elif action == "plan":
                r = f"{len(proj.make_plan(use_llm=bool(a.get('use_llm')))['shots'])} shots"
            elif action == "preview":
                if not proj.plan:
                    proj.make_plan()
                r = proj.render_preview()
            elif action == "render":
                if not proj.plan:
                    proj.make_plan()
                r = proj.render_final()
            elif action == "graded":
                r = proj.export_graded()
            elif action == "luts":
                r = proj.export_luts()
            elif action == "make":
                r = proj.make_all(preview=bool(a.get("preview")), graded=bool(a.get("graded")),
                                  rescan=bool(a.get("rescan")))
            elif action == "agent":
                from ..agent import run
                if not proj.cfg["llm"]["enabled"]:
                    raise RuntimeError("turn on “Use a local AI model” first")
                r = run(proj, a.get("goal") or "Make the video.", max_steps=int(a.get("max_steps", 40)))
            else:
                raise RuntimeError(f"unknown action {action}")
            self.job["result"] = r
            util.log(f"{action} finished", level="ok")
        except util.Cancelled:
            self.job["error"] = "cancelled"
            util.log("cancelled", level="warn")
        except Exception as e:
            self.job["error"] = f"{type(e).__name__}: {e}"
            util.log(f"{action} failed: {e}", level="error")
            util.debug(traceback.format_exc())
        finally:
            self.job.update(running=False, ended=time.time())
            self.reload() if action != "agent" else None

    # ------------------------------------------------------------ views
    def state(self):
        proj = self.project
        st = proj.status()
        out_dir = proj.cfg.output_folder
        outputs = []
        if os.path.isdir(out_dir):
            for f in sorted(os.listdir(out_dir)):
                p = os.path.join(out_dir, f)
                if os.path.isfile(p) and f.lower().endswith((".mp4", ".jpg")):
                    outputs.append({"name": f, "path": p, "size": os.path.getsize(p), "mtime": os.path.getmtime(p)})
        music = proj.music
        return {"status": st, "outputs": outputs, "config_path": self.cfg_path,
                "music": {k: music.get(k) for k in ("bpm", "duration", "sections", "wav", "source", "preset")} if music else None,
                "job": {k: v for k, v in self.job.items() if k != "log"},
                "projects": self.projects()}

    def media(self):
        proj = self.project
        items, res, caps = proj.media or [], proj.analysis or {}, proj.captions
        ov = proj.merged_overrides()
        from ..media import place_for
        out = []
        for m in items:
            r = res.get(m["id"], {})
            fr = r.get("frames") or []
            mid = fr[len(fr) // 2] if fr else {}
            c = caps.get(m["id"], {})
            out.append({"id": m["id"], "kind": m["kind"], "duration": m.get("duration"), "created": m.get("created"),
                        "profile": m.get("profile"), "size": f"{m.get('width')}x{m.get('height')}",
                        "usable": r.get("usable", True), "reason": r.get("reason", ""),
                        "rotate_cw": r.get("rotate_cw", 0), "stops": r.get("stops"), "quality": r.get("quality"),
                        "suspect": r.get("orientation_suspect", False), "thumb": mid.get("thumb"),
                        "place": place_for(m, proj.cfg.get("places", [])) or c.get("place", ""),
                        "caption": c.get("description", ""), "moments": c.get("best_moments", []),
                        "avoid": c.get("avoid", []), "override": ov.get(m["id"], {}),
                        "path": m.get("path")})
        return out

    def plan(self):
        proj = self.project
        p = proj.plan
        if not p:
            return None
        res = proj.analysis or {}
        for s in p["shots"]:
            fr = res.get(s["clip"], {}).get("frames") or []
            t = s["in"] + s["dur"] * s.get("speed", 1) / 2
            near = min(fr, key=lambda f: abs(f["t"] - t)) if fr else {}
            s["thumb"] = near.get("thumb")
        return p

    def look_preview(self, look, clips=None, n=8):
        proj = self.project
        media, res = proj.media or [], proj.analysis or {}
        lk = copy.deepcopy(proj.cfg["look"])
        for k, v in (look or {}).items():
            if k == "overrides":
                lk["overrides"] = {a: b for a, b in v.items() if b is not None and b != ""}
            else:
                lk[k] = v
        params = color.look_params(lk)
        use = [m for m in media if res.get(m["id"], {}).get("usable")]
        if clips:
            use = [m for m in use if m["id"] in clips]
        elif len(use) > n:
            use = [use[int(i * len(use) / n)] for i in range(n)]
        from ..analyze import rotate_cw
        out = []
        for m in use[:n]:
            r = res[m["id"]]
            fr = r["frames"][len(r["frames"]) // 2]
            a = np.asarray(Image.open(fr["file"]).convert("RGB"), dtype=np.float64) / 255.0
            # brightness slider: rescale this shot's auto exposure to the new target
            stops = r.get("stops", 0)
            if lk.get("auto_exposure", True) and "target_exposure" in (look or {}):
                stops += float(np.log2(float(look["target_exposure"]) / proj.cfg["look"].get("target_exposure", 0.17)))
            g = rotate_cw(color.grade(a, m["profile"], params, stops, r.get("wb", [1, 1, 1])), r.get("rotate_cw", 0))
            im = Image.fromarray((np.clip(g, 0, 1) * 255 + 0.5).astype(np.uint8))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
            out.append({"id": m["id"], "img": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()})
        return out

    def allowed(self, path):
        norm = lambda p: os.path.normcase(os.path.realpath(p))  # Windows paths are case-insensitive
        path = norm(path)
        roots = [norm(self.cfg.output_folder), norm(self.cfg.work_folder)]
        mf = self.cfg["music"].get("file")
        ok = any(path.startswith(r + os.sep) or path == r for r in roots)
        return ok or bool(mf and path == norm(self.cfg.resolve(mf)))


# ---------------------------------------------------------------- OS helpers
_TK_PICK = r"""
import sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
kind, title = sys.argv[1], sys.argv[2]
p = filedialog.askdirectory(title=title) if kind == "folder" else filedialog.askopenfilename(title=title)
print(p or "")
"""


def pick(kind, prompt_text):
    """Native folder/file chooser. macOS: Finder dialog; Windows/Linux: the system dialog via tkinter."""
    if util.IS_MAC and util.have("osascript"):
        what = "choose folder" if kind == "folder" else "choose file"
        safe = prompt_text.replace('"', "'")
        r = subprocess.run(["osascript", "-e", f'POSIX path of ({what} with prompt "{safe}")'],
                           capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    try:
        r = subprocess.run([sys.executable, "-c", _TK_PICK, kind, prompt_text], capture_output=True, text=True,
                           encoding="utf-8", timeout=600, creationflags=util._NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        r = None
    if r is None or r.returncode != 0:
        if util.have("zenity"):
            z = subprocess.run(["zenity", "--file-selection"] + (["--directory"] if kind == "folder" else [])
                               + ["--title", prompt_text], capture_output=True, text=True)
            return z.stdout.strip() or None
        raise RuntimeError("no file dialog available here — type the path into the box instead")
    p = r.stdout.strip()
    return os.path.normpath(p) if p else None


def new_project(folder, name):
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name or os.path.basename(folder.rstrip("/"))).strip("_") or "project"
    path = os.path.join(PROJECTS_DIR, slug + ".toml")
    k = 2
    while os.path.exists(path):
        path = os.path.join(PROJECTS_DIR, f"{slug}_{k}.toml")
        k += 1
    with open(os.path.join(ROOT, "vlog.example.toml"), encoding="utf-8") as f:
        t = f.read()
    t = t.replace('name            = "My Trip"', f"name            = {json.dumps(name or slug)}")
    t = t.replace('media_folder    = ".."', f"media_folder    = {json.dumps(os.path.abspath(folder))}")
    t = t.replace('output_folder   = "../Vlog_Studio_Output"',
                  f"output_folder   = {json.dumps(os.path.join(os.path.abspath(folder), 'Vlog_Studio_Output'))}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(t)
    return path


# ---------------------------------------------------------------- HTTP
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".mp4": "video/mp4", ".mov": "video/quicktime", ".wav": "audio/wav",
          ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
          ".cube": "text/plain; charset=utf-8", ".json": "application/json"}


def _mime(p):
    # fixed table: Windows' registry can map .js to text/plain, which browsers may refuse
    return _TYPES.get(os.path.splitext(p)[1].lower()) or mimetypes.guess_type(p)[0] or "application/octet-stream"


class Handler(BaseHTTPRequestHandler):
    app: App = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _static(self, name):
        p = os.path.join(UI_DIR, name)
        if not os.path.isfile(p):
            return self._json({"error": "not found"}, 404)
        with open(p, "rb") as f:
            b = f.read()
        self.send_response(200)
        self.send_header("Content-Type", _mime(p))
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _file(self, path):
        if not path or not os.path.isfile(path) or not self.app.allowed(path):
            return self._json({"error": "not allowed"}, 403)
        size = os.path.getsize(path)
        ctype = _mime(path)
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else max(0, size - int(b))
            end = int(b) if (a and b) else size - 1
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        app = self.app
        try:
            if u.path in ("/", "/index.html"):
                return self._static("index.html")
            if u.path in ("/app.js", "/app.css"):
                return self._static(u.path[1:])
            if u.path == "/file":
                return self._file(q.get("path", [""])[0])
            if u.path == "/api/meta":
                return self._json({"fields": F, "styles": C.STYLES, "defaults": C.DEFAULTS,
                                   "look_presets": color.PRESETS, "platform": sys.platform,
                                   "gpu": util.gpu_kind()})
            if u.path == "/api/config":
                c = copy.deepcopy(dict(app.cfg))  # show absolute paths in the app
                c["project"]["media_folder"] = app.cfg.media_folder
                c["project"]["output_folder"] = app.cfg.output_folder
                if c["music"].get("file"):
                    c["music"]["file"] = app.cfg.resolve(c["music"]["file"])
                return self._json(c)
            if u.path == "/api/state":
                return self._json(app.state())
            if u.path == "/api/job":
                since = float(q.get("since", ["0"])[0])
                return self._json({**{k: v for k, v in app.job.items() if k != "log"},
                                   "log": [l for l in app.job["log"] if l["t"] > since]})
            if u.path == "/api/media":
                return self._json(app.media())
            if u.path == "/api/plan":
                return self._json(app.plan())
            if u.path == "/api/agent_log":
                p = app.project.p("agent_log.jsonl")
                lines = []
                if os.path.exists(p):
                    with open(p, encoding="utf-8") as f:
                        lines = f.read().splitlines()[-200:]
                return self._json([json.loads(l) for l in lines])
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        app = self.app
        try:
            b = self._body()
            if u.path == "/api/config":
                app.save_config(b)
                return self._json({"ok": True})
            if u.path == "/api/run":
                app.start(b["action"], b.get("args") or {})
                return self._json({"ok": True})
            if u.path == "/api/cancel":
                if app.job["running"]:  # a stray click with nothing running must not affect the next job
                    util.CANCEL.set()
                return self._json({"ok": True})
            if u.path == "/api/override":
                if app.job["running"]:
                    raise RuntimeError("wait for the running job to finish")
                kw = {k: b.get(k) for k in ("fix_rotation_cw", "stops", "exclude") if k in b}
                if b.get("reset_stops"):
                    ov = app.project.overrides
                    ov.get(b["clip"], {}).pop("stops", None)
                    util.save_json(app.project.p("overrides.json"), ov)
                cur = app.project.set_override(b["clip"], **kw)
                return self._json({"ok": True, "override": cur})
            if u.path == "/api/sheet":
                return self._json({"path": app.project.clip_sheet(b["clip"])})
            if u.path == "/api/look_preview":
                return self._json(app.look_preview(b.get("look"), b.get("clips")))
            if u.path == "/api/plan/apply":
                if app.job["running"]:
                    raise RuntimeError("wait for the running job to finish")
                assign = {int(s["slot"]): {k: s[k] for k in ("clip", "in", "speed") if k in s}
                          for s in b.get("shots", []) if s.get("clip")}
                p = app.project.make_plan(use_llm=False, assignments=assign or None, text=b.get("text"))
                return self._json({"ok": True, "warnings": p.get("warnings", [])})
            if u.path == "/api/pick":
                return self._json({"path": pick(b.get("kind", "folder"), b.get("prompt", "Choose"))})
            if u.path == "/api/reveal":
                path = b.get("path", "")
                if os.path.exists(path):
                    util.reveal(path)
                return self._json({"ok": True})
            if u.path == "/api/llm/test":
                from ..llm import LLM
                c = copy.deepcopy(dict(app.cfg))
                c["llm"].update({k: v for k, v in b.items() if k in ("base_url", "api_key", "model")})
                return self._json({"models": LLM(c).list_models()})
            if u.path == "/api/project/open":
                app.cfg_path = os.path.abspath(b["path"])
                app.reload()
                return self._json({"ok": True})
            if u.path == "/api/project/new":
                folder = b.get("folder") or pick("folder", "Choose the folder with your footage")
                if not folder:
                    return self._json({"ok": False})
                app.cfg_path = new_project(folder, b.get("name") or os.path.basename(folder.rstrip("/")))
                app.reload()
                return self._json({"ok": True, "path": app.cfg_path})
            if u.path == "/api/quit":
                self._json({"ok": True})
                threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
                return
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            return self._json({"error": f"{e}"}, 400)


def serve(cfg_path=None, port=8765, open_browser=True):
    if sys.stdout is None:  # started without a console (Windows pythonw / launcher): log to a file
        sys.stdout = sys.stderr = open(os.path.join(ROOT, "vlogstudio_app.log"), "a", encoding="utf-8", buffering=1)
    cfg_path = cfg_path or (os.path.join(os.getcwd(), "vlog.toml") if os.path.exists("vlog.toml")
                            else os.path.join(ROOT, "vlog.toml"))
    if not os.path.exists(cfg_path):
        shutil.copy(os.path.join(ROOT, "vlog.example.toml"), cfg_path)
    url = f"http://127.0.0.1:{port}/"
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        # already running? then just bring it up in the browser
        try:
            import urllib.request
            urllib.request.urlopen(url + "api/meta", timeout=3).read()
            util.log(f"Vlog Studio is already running at {url}", level="ok")
            if open_browser:
                webbrowser.open(url)
            return
        except Exception:
            raise SystemExit(f"port {port} is busy; start with --port {port + 1}")
    Handler.app = App(cfg_path)
    util.log(f"Vlog Studio app running at {url}  (Ctrl+C to stop)", level="ok")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
