"""Scan a folder for videos and photos and read their technical metadata."""
import fnmatch
import math
import os
import re
from datetime import datetime

from PIL import Image, ImageOps

from .util import ffprobe_json, have, log, pmap, run

try:  # HEIC/HEIF photos on Windows/Linux (and macOS) without Apple's sips
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_NATIVE = True
except ImportError:
    HEIF_NATIVE = False

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mxf", ".mkv", ".avi", ".mts"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp"}
SKIP_DIRS = {"work", "Graded_Clips", "LocalVlogStudio", "__pycache__", ".venv"}


def _parse_iso6709(s):
    m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", s or "")
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def _parse_date(s):
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            d = datetime.strptime(s, fmt)
            return d.replace(tzinfo=None).isoformat()  # keep local wall-clock time
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None).isoformat()
    except ValueError:
        return None


def _rate(r):
    try:
        a, b = r.split("/")
        return float(a) / float(b) if float(b) else 0.0
    except (ValueError, AttributeError):
        return 0.0


def detect_profile(stream, tags):
    """Return the transfer profile the color math should decode."""
    gamma = (tags.get("com.apple.proapps.customgamma") or "").lower()
    trc = (stream.get("color_transfer") or "").lower()
    if "apple-log" in gamma or "applelog" in gamma:
        return "apple_log"
    if trc == "arib-std-b67":
        return "hlg"
    if trc == "smpte2084":
        return "pq"
    return "bt709"


def probe_video(path):
    j = ffprobe_json(path)
    vs = [s for s in j["streams"] if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic", 0) == 0]
    if not vs:
        return None
    v = vs[0]
    tags = dict(j.get("format", {}).get("tags", {}))
    tags.update({k: val for k, val in v.get("tags", {}).items() if k not in tags})
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(round(float(sd["rotation"])))
    if "rotate" in v.get("tags", {}):
        rot = -int(v["tags"]["rotate"])
    cs = (v.get("color_space") or "").lower()
    matrix = "bt2020" if cs.startswith("bt2020") else ("bt601" if cs in ("smpte170m", "bt470bg") else "bt709")
    lat, lon = _parse_iso6709(tags.get("com.apple.quicktime.location.ISO6709") or tags.get("location"))
    created = (_parse_date(tags.get("com.apple.quicktime.creationdate"))
               or _parse_date(tags.get("com.blackmagic-design.camera.dateRecorded"))
               or _parse_date(tags.get("creation_time")))
    dur = float(j.get("format", {}).get("duration") or v.get("duration") or 0)
    return {
        "kind": "video",
        "duration": dur,
        "fps": _rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate")) or 24.0,
        "width": int(v["width"]), "height": int(v["height"]),
        "codec": v.get("codec_name"),
        "pix_fmt": v.get("pix_fmt"),
        "rotation_flag": rot,
        "flag_cw": (-rot) % 360,
        "profile": detect_profile(v, tags),
        "matrix": matrix,
        "range": "pc" if v.get("color_range") == "pc" else "tv",
        "created": created,
        "lat": lat, "lon": lon,
        "has_audio": any(s.get("codec_type") == "audio" for s in j["streams"]),
        "camera": tags.get("com.apple.quicktime.model") or tags.get("com.apple.proapps.cameraName"),
    }


def _exif_gps(exif):
    try:
        gps = exif.get_ifd(0x8825)
        if not gps:
            return None, None

        def conv(v, ref):
            d = float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
            return -d if ref in ("S", "W") else d
        return conv(gps[2], gps.get(1, "N")), conv(gps[4], gps.get(3, "E"))
    except Exception:
        return None, None


def prepare_image(path, work):
    """Convert any photo (incl. HEIC) to an upright sRGB JPEG in the work folder."""
    os.makedirs(os.path.join(work, "images"), exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(work, "images", stem + ".jpg")
    src = path
    if path.lower().endswith((".heic", ".heif")) and not HEIF_NATIVE:
        tmp = os.path.join(work, "images", stem + ".heic.jpg")
        if not os.path.exists(tmp):
            if not have("sips"):
                raise RuntimeError("HEIC photos need the pillow-heif package (pip install pillow-heif)")
            run(["sips", "-s", "format", "jpeg", path, "--out", tmp], quiet=True)
        src = tmp
    im = Image.open(src)
    exif = im.getexif()
    created = None
    try:
        created = _parse_date(exif.get_ifd(0x8769).get(0x9003) or exif.get(0x0132))
    except Exception:
        pass
    lat, lon = _exif_gps(exif)
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(path):
        im = ImageOps.exif_transpose(im).convert("RGB")
        if max(im.size) > 6000:
            im.thumbnail((6000, 6000), Image.LANCZOS)
        im.save(out, quality=95)
    w, h = Image.open(out).size
    if not created:
        created = datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
    return {
        "kind": "image", "duration": 0.0, "fps": 0.0, "width": w, "height": h,
        "rotation_flag": 0, "flag_cw": 0, "profile": "srgb", "matrix": "bt601", "range": "pc",
        "created": created, "lat": lat, "lon": lon, "has_audio": False, "source_image": out,
        "camera": None,
    }


def scan(cfg):
    folder = cfg.media_folder
    excl = cfg["project"].get("exclude", [])
    out_root = os.path.abspath(cfg.output_folder)
    files = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")
                   and not os.path.abspath(os.path.join(root, d)).startswith(out_root)
                   and "Output" not in d]
        for n in sorted(names):
            ext = os.path.splitext(n)[1].lower()
            if n.startswith("."):
                continue
            rel = os.path.relpath(os.path.join(root, n), folder)
            if any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(n, p) for p in excl):
                continue
            if ext in VIDEO_EXT or (ext in IMAGE_EXT and cfg["project"].get("include_images", True)):
                files.append(os.path.join(root, n))

    def one(path):
        try:
            ext = os.path.splitext(path)[1].lower()
            info = probe_video(path) if ext in VIDEO_EXT else prepare_image(path, cfg.work_folder)
            if info is None:
                return None
            info["path"] = path
            return info
        except Exception as e:  # unreadable file: skip, but say so
            log(f"skipping {os.path.basename(path)}: {e}", level="warn")
            return None

    items = [m for m in pmap(one, files, cfg["analysis"].get("workers", 6)) if m]
    # unique, short ids: file stem, de-duplicated
    seen = {}
    for m in items:
        stem = os.path.splitext(os.path.basename(m["path"]))[0]
        k = stem
        n = 2
        while k in seen:
            k = f"{stem}_{n}"
            n += 1
        seen[k] = True
        m["id"] = k
    items.sort(key=lambda m: (m["created"] or "", m["id"]))
    for i, m in enumerate(items):
        m["index"] = i
        # per-clip manual overrides from config
        ov = cfg.get("overrides", {}).get(m["id"])
        if ov:
            m["override"] = ov
            if "profile" in ov:
                m["profile"] = ov["profile"]
    nv = sum(m["kind"] == "video" for m in items)
    log(f"found {nv} videos and {len(items) - nv} photos in {folder}", level="ok")
    return items


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def place_for(m, places):
    """Name of the nearest configured place within its radius, or None."""
    if m.get("lat") is None:
        return None
    best = None
    for p in places:
        d = haversine_m(m["lat"], m["lon"], p["lat"], p["lon"])
        if d <= p.get("radius_m", 500) and (best is None or d < best[0]):
            best = (d, p["name"])
    return best[1] if best else None
