"""Frame sampling, per-shot balancing, orientation detection and shot scoring."""
import glob
import os

import numpy as np
from PIL import Image

from . import color
from .util import log, pmap, run, save_json

THUMB_W = 320


def sample_times(m, interval, skip_start):
    if m["kind"] == "image":
        return [0.0]
    d = m["duration"]
    start = skip_start if d > skip_start + 2.5 else min(0.3, d / 4)
    ts = list(np.arange(start, max(d - 0.3, start + 0.01), interval))
    if len(ts) < 3 and d > 1:
        ts = list(np.linspace(min(0.3, d / 4), d - 0.3, 3))
    return [round(float(t), 3) for t in ts]


def _extract(job):
    m, t, out = job
    if os.path.exists(out):
        return out
    if m["kind"] == "image":
        im = Image.open(m["source_image"]).convert("RGB")
        im.thumbnail((THUMB_W * 2, THUMB_W * 2))
        im.save(out)
        return out
    vf = f"scale={THUMB_W}:-2:in_color_matrix={m['matrix']}:in_range={m['range']},format=rgb24"
    run(["ffmpeg", "-v", "error", "-y", "-noautorotate", "-threads", "1", "-ss", f"{t:.3f}", "-i", m["path"],
         "-frames:v", "1", "-vf", vf, out], check=False, quiet=True)
    return out


def rotate_cw(a, cw):
    return np.rot90(a, k=-(cw // 90) % 4)


def _sky_score(disp):
    """How much this frame looks 'upright': bright/blue top, darker/greener/warmer bottom."""
    h = disp.shape[0]
    top, bot = disp[: h // 3], disp[-(h // 3):]
    lt, lb = top.mean(), bot.mean()
    bt = (top[..., 2] - top[..., :2].mean(-1)).mean()
    bb = (bot[..., 2] - bot[..., :2].mean(-1)).mean()
    return float((lt - lb) + 0.8 * (bt - bb))


def _sky_frac(disp):
    """Fraction of the top third that looks like open sky (blue or bright, low texture)."""
    top = disp[: disp.shape[0] // 3]
    y = top @ color.LUMA709
    blue = (top[..., 2] - top[..., 0] > 0.03) & (y > 0.35)
    return float(blue.mean())


def _frame_metrics(disp):
    y = disp @ color.LUMA709
    lap = y[1:-1, 1:-1] * 4 - y[:-2, 1:-1] - y[2:, 1:-1] - y[1:-1, :-2] - y[1:-1, 2:]
    # sharpness of the most detailed region (a crisp subject on clean sky still scores high)
    th, tw = lap.shape[0] // 4, lap.shape[1] // 4
    tiles = [lap[i * th:(i + 1) * th, j * tw:(j + 1) * tw].var() for i in range(4) for j in range(4)]
    sharp = float(np.percentile(tiles, 95))
    clip_hi = float((y > 0.97).mean())
    clip_lo = float((y < 0.03).mean())
    rg = disp[..., 0] - disp[..., 1]
    yb = 0.5 * (disp[..., 0] + disp[..., 1]) - disp[..., 2]
    colorful = float(np.sqrt(rg.var() + yb.var()) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))
    return {"sharp": sharp, "clip_hi": clip_hi, "clip_lo": clip_lo, "colorful": colorful,
            "mean": float(y.mean()), "y": y}


def analyze(cfg, media):
    work = cfg.work_folder
    acfg = cfg["analysis"]
    look = color.look_params(cfg["look"])
    jobs, per = [], {}
    for m in media:
        d = os.path.join(work, "frames", m["id"])
        os.makedirs(d, exist_ok=True)
        ts = sample_times(m, acfg["sample_interval"], acfg["skip_start"])
        per[m["id"]] = [(t, os.path.join(d, f"{i:03d}.png")) for i, t in enumerate(ts)]
        jobs += [(m, t, p) for t, p in per[m["id"]]]
    log(f"sampling {len(jobs)} frames from {len(media)} items…", level="step")
    pmap(_extract, jobs, acfg.get("workers", 6))

    results = {}
    for m in media:
        frames = [(t, p) for t, p in per[m["id"]] if os.path.exists(p)]
        if not frames:
            results[m["id"]] = {"usable": False, "reason": "no frames decoded"}
            continue
        arrs = [np.asarray(Image.open(p).convert("RGB"), dtype=np.float64) / 255.0 for _, p in frames]
        ov = m.get("override", {})
        stops, wb, info = color.auto_balance(arrs, m["profile"], cfg["look"])
        stops = float(ov.get("stops", stops))
        wb = ov.get("wb", wb)
        disp = [color.grade(a, m["profile"], look, stops, wb) for a in arrs]
        rot_scores = np.zeros(4)
        frame_sky = []
        for dsp in disp:
            rots = [rotate_cw(dsp, k * 90) for k in range(4)]
            fs = [_sky_score(x) for x in rots]
            frame_sky.append([_sky_frac(x) for x in rots])
            rot_scores += np.array(fs)
        rot_scores /= len(disp)
        mets = [_frame_metrics(dsp) for dsp in disp]
        if float(np.mean([mt["y"].std() for mt in mets])) < 0.025:
            info["black"] = True  # featureless: black, white or lens covered
        for i, mt in enumerate(mets):
            mt["motion"] = float(np.abs(mt["y"] - mets[i - 1]["y"]).mean()) if i > 0 else 0.0
        rec = {
            "usable": not info.get("black") and m.get("override", {}).get("exclude") is not True,
            "reason": "blank (black/white/covered)" if info.get("black") else ("excluded" if ov.get("exclude") else ""),
            "stops": round(stops, 3), "wb": wb, "strong_wb": info.get("strong_wb", False),
            "logavg": info.get("logavg"),
            "rot_scores": [round(float(s), 4) for s in rot_scores],
            "frames": [{"t": t, "file": p, "sharp": mt["sharp"], "clip_hi": mt["clip_hi"],
                        "clip_lo": mt["clip_lo"], "colorful": mt["colorful"], "mean": mt["mean"],
                        "motion": mt["motion"], "sky": [round(v, 4) for v in fs]}
                       for (t, p), mt, fs in zip(frames, mets, frame_sky)],
        }
        results[m["id"]] = rec

    _resolve_orientation(cfg, media, results)
    _score_frames(results)
    _write_thumbs(cfg, media, results, look)
    save_json(os.path.join(work, "analysis.json"), results)
    bad = [k for k, r in results.items() if not r.get("usable")]
    log(f"analysis done ({len(results) - len(bad)} usable, {len(bad)} skipped: {', '.join(bad) or '-'})", level="ok")
    return results


def _resolve_orientation(cfg, media, results):
    """Pick the clockwise rotation that makes each clip upright.

    honor_flag : trust the file's rotation metadata (standard players)
    ignore_flag: use stored pixels as-is
    auto       : score all four rotations ("bright/blue sky on top") and use the consensus of
                 all clips that share the same rotation flag. This reliably fixes a wrong flag
                 written by an app for a whole session. Single-clip guesses are NOT applied
                 (simple image statistics confuse dark canopies, interiors, pavement) — clips that
                 look flipped are listed as 'suspect' for the vision model or you to confirm.
    """
    mode = cfg["orientation"]["mode"]
    groups = {}
    for m in media:
        r = results.get(m["id"])
        if not r or "rot_scores" not in r:
            continue
        groups.setdefault((m["kind"], m["flag_cw"]), []).append((m, r))
    for (kind, flag), items in groups.items():
        total = np.sum([r["rot_scores"] for _, r in items], axis=0)
        consensus = int(np.argmax(total)) * 90
        for m, r in items:
            s = np.array(r["rot_scores"])
            if kind == "image" or mode == "honor_flag":
                cw = flag if kind != "image" else 0
            elif mode == "ignore_flag":
                cw = 0
            else:
                cw = consensus
            flipped = (cw + 180) % 360
            margin = float(s[flipped // 90] - s[cw // 90])
            r["orientation_suspect"] = bool(kind == "video" and margin > 0.15)
            ov = m.get("override", {})
            if "rotate" in ov:
                cw = int(ov["rotate"]) % 360
                r["orientation_suspect"] = False
            r["rotate_cw"] = cw
            r["orientation_note"] = (f"flag={flag} consensus={consensus} flip_margin={margin:.3f}"
                                     + (" (override)" if "rotate" in ov else ""))
            r["orientation_fixed"] = bool(mode == "auto" and kind == "video" and cw != flag)
    fixed = [m["id"] for m in media if results.get(m["id"], {}).get("orientation_fixed")]
    sus = [m["id"] for m in media if results.get(m["id"], {}).get("orientation_suspect")]
    if fixed:
        log(f"orientation: {len(fixed)} clips have a wrong rotation flag (session consensus) — corrected", level="warn")
    if sus:
        log(f"orientation: please confirm these look upright on the overview sheet (or run the vision check): "
            f"{', '.join(sus)}", level="warn")


def _score_frames(results):
    allf = [f for r in results.values() for f in r.get("frames", [])]
    if not allf:
        return
    sharp = np.array([f["sharp"] for f in allf])
    colf = np.array([f["colorful"] for f in allf])
    mot = np.array([f["motion"] for f in allf])

    def rank(x, v):
        return float((x < v).mean())
    for r in results.values():
        k = r.get("rotate_cw", 0) // 90
        for f in r.get("frames", []):
            expo_pen = 2.0 * f["clip_hi"] + 1.5 * f["clip_lo"] + 0.8 * abs(f["mean"] - 0.45)
            scenic = min(f.get("sky", [0, 0, 0, 0])[k] * 1.6, 1.0)  # open sky in the top third = scenic
            q = (0.40 * rank(sharp, f["sharp"]) + 0.25 * rank(colf, f["colorful"])
                 + 0.20 * scenic + 0.15 * (1 - abs(rank(mot, f["motion"]) - 0.5) * 2) - expo_pen)
            f["score"] = round(q, 4)
        fr = r.get("frames", [])
        r["quality"] = round(float(np.mean(sorted(f["score"] for f in fr)[-3:])), 4) if fr else 0.0


def _write_thumbs(cfg, media, results, look):
    for m in media:
        r = results.get(m["id"])
        if not r or "frames" not in r:
            continue
        d = os.path.join(cfg.work_folder, "thumbs", m["id"])
        os.makedirs(d, exist_ok=True)
        for f in glob.glob(os.path.join(d, "*.jpg")):
            os.remove(f)
        for i, f in enumerate(r["frames"]):
            a = np.asarray(Image.open(f["file"]).convert("RGB"), dtype=np.float64) / 255.0
            g = rotate_cw(color.grade(a, m["profile"], look, r["stops"], r["wb"]), r.get("rotate_cw", 0))
            p = os.path.join(d, f"{i:03d}.jpg")
            Image.fromarray((g * 255 + 0.5).astype(np.uint8)).save(p, quality=88)
            f["thumb"] = p


def best_window(rec, m, dur, speed=1.0, skip_start=2.0, avoid=(), block=()):
    """Best (in_point, score) for a shot of `dur` output seconds from this clip."""
    if m["kind"] == "image":
        return 0.0, rec.get("quality", 0.0)
    need = dur * speed
    total = m["duration"]
    if total < need + 0.15:
        return None
    fr = rec.get("frames", [])
    lo = skip_start if total - skip_start > need + 0.3 else 0.1
    cands = sorted(set([round(f["t"], 3) for f in fr] + [lo]))
    best = None
    for t0 in cands:
        t0 = min(max(t0, lo), total - need - 0.1)
        if t0 < 0:
            continue
        inside = [f["score"] for f in fr if t0 - 0.75 <= f["t"] <= t0 + need + 0.75]
        sc = float(np.mean(inside)) if inside else rec.get("quality", 0.0) - 0.2
        if any(a < t0 + need and t0 < b for a, b in avoid):
            sc -= 1.0  # already used
        if any(a < t0 + need and t0 < b for a, b in block):
            sc -= 3.0  # flagged as bad by the vision model
        if best is None or sc > best[1]:
            best = (round(t0, 3), sc)
    return best
