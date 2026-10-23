"""Small shared helpers: logging, subprocess, ffprobe, json, parallel map."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

_T0 = time.time()
VERBOSE = False
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
# Windows: don't flash a console window for every ffmpeg call when the app runs without one
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WIN else 0
SINKS = []                  # callables receiving every log line (the app UI subscribes here)
CANCEL = threading.Event()  # set by the UI's Cancel button; running ffmpeg processes are stopped


class Cancelled(BaseException):
    """Raised when the user presses Cancel.

    A BaseException (like KeyboardInterrupt), so the `except Exception` handlers that skip an
    unreadable file or keep the agent loop alive let it through and the whole job stops.
    """


def check_cancel():
    """Stop here if the user pressed Cancel. Call it in long loops that don't run ffmpeg."""
    if CANCEL.is_set():
        raise Cancelled("cancelled")


def log(msg, *, level="info"):
    tag = {"info": "•", "warn": "!", "error": "✗", "ok": "✓", "step": "▶"}.get(level, "•")
    line = f"[{time.time() - _T0:7.1f}s] {tag} {msg}"
    print(line, flush=True)
    for s in list(SINKS):
        try:
            s(line, level)
        except Exception:
            pass


def debug(msg):
    if VERBOSE:
        log(msg, level="info")


def die(msg, code=1):
    log(msg, level="error")
    sys.exit(code)


def run(cmd, *, check=True, capture=False, quiet=False, cwd=None):
    """Run a command list (cancellable). Returns CompletedProcess."""
    debug("$ " + " ".join(str(c) for c in cmd))
    check_cancel()
    pipe = subprocess.PIPE if (capture or quiet or sys.stdout is None) else None
    p = subprocess.Popen([str(c) for c in cmd], stdout=pipe, stderr=pipe, text=True, encoding="utf-8",
                         errors="replace", cwd=cwd, creationflags=_NO_WINDOW)
    while True:
        try:
            out, err = p.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if CANCEL.is_set():
                p.kill()
                p.wait()
                raise Cancelled("cancelled")
    r = subprocess.CompletedProcess(p.args, p.returncode, out, err)
    if check and r.returncode != 0:
        err = (r.stderr or "").strip()[-2000:] if (capture or quiet) else ""
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(str(c) for c in cmd)[:400]}\n{err}")
    return r


def ffprobe_json(path):
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
            capture=True)
    return json.loads(r.stdout)


def have(binary):
    return shutil.which(binary) is not None


_ENCODERS = None


def ffmpeg_has_encoder(name):
    global _ENCODERS
    if _ENCODERS is None:
        r = run(["ffmpeg", "-hide_banner", "-encoders"], capture=True, check=False)
        _ENCODERS = r.stdout
    return f" {name} " in _ENCODERS


_GPU = {}


def encoder_works(name):
    """True if ffmpeg can actually encode with `name` here (NVENC is listed even without an NVIDIA GPU)."""
    if name not in _GPU:
        ok = ffmpeg_has_encoder(name)
        if ok:
            r = run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=256x256:d=0.2", "-frames:v", "3",
                     "-c:v", name, "-f", "null", "-"], capture=True, check=False)
            ok = r.returncode == 0
        _GPU[name] = ok
    return _GPU[name]


def gpu_kind():
    """'apple' (VideoToolbox), 'nvidia' (NVENC) or None."""
    if IS_MAC and ffmpeg_has_encoder("hevc_videotoolbox"):
        return "apple"
    if encoder_works("h264_nvenc"):
        return "nvidia"
    return None


def gpu_name():
    if have("nvidia-smi"):
        r = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], capture=True, check=False)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0]
    return None


def reveal(path):
    """Show a file in Finder / Explorer (or open a folder)."""
    path = os.path.normpath(path)
    if IS_MAC:
        subprocess.run(["open", "-R", path] if os.path.isfile(path) else ["open", path])
    elif IS_WIN:
        if os.path.isfile(path):
            subprocess.run(["explorer", f"/select,{path}"], creationflags=_NO_WINDOW)
        else:
            os.startfile(path)  # noqa: S606 (Windows only)
    elif have("xdg-open"):
        subprocess.run(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])


def concat_line(path):
    """A line for ffmpeg's concat list (safe for spaces, quotes and Windows paths)."""
    return "file '" + path.replace("\\", "/").replace("'", "'\\''") + "'\n"


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)


def pmap(fn, items, workers=4):
    """Parallel map. Stops early on Cancel: items not started yet are dropped."""
    items = list(items)

    def one(x):
        check_cancel()
        return fn(x)
    if workers <= 1 or len(items) <= 1:
        return [one(x) for x in items]
    ex = ThreadPoolExecutor(workers)
    try:
        return list(ex.map(one, items))
    finally:
        ex.shutdown(wait=True, cancel_futures=True)


def fmt_time(t):
    return f"{int(t // 60)}:{t % 60:05.2f}"
