"""LLM-assisted creative steps (all optional; everything falls back to heuristics)."""
import os

from .llm import LLMError
from .util import log, save_json

PROMPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")


def prompt(name, **vals):
    with open(os.path.join(PROMPTS, name), encoding="utf-8") as f:
        t = f.read()
    for k, v in vals.items():
        t = t.replace("{{" + k + "}}", str(v))
    return t


def _project_info(proj):
    pj = proj.cfg["project"]
    return (f"name={pj.get('name')!r}, title={pj.get('title')!r}, hook={pj.get('hook')!r}, "
            f"target_duration={pj.get('target_duration')}s")


def _meta(m):
    s = f"id={m['id']}, kind={m['kind']}, created={m.get('created')}"
    if m["kind"] == "video":
        s += f", duration={m['duration']:.1f}s"
    if m.get("lat") is not None:
        s += f", gps=({m['lat']:.5f},{m['lon']:.5f})"
    return s


def _reachable(llm):
    llm.list_models()  # raises LLMError with a clear message when the local server isn't running


def caption_all(proj, force=False):
    llm = proj.llm()
    _reachable(llm)
    if not llm.vision:
        log("captions need a vision model ([llm] vision = true); skipped", level="warn")
        return {}
    caps = {} if force else dict(proj.captions)
    media, res = proj.scan(), proj.analyze()
    todo = [m for m in media if res.get(m["id"], {}).get("usable") and m["id"] not in caps]
    acfg = proj.cfg["analysis"]
    smart = bool(acfg.get("smart_moments", False))
    chunk = float(acfg.get("long_clip_chunk", 24))
    max_n = int(acfg.get("max_moments_per_clip", 4))
    log(f"captioning {len(todo)} items with {llm.model}" + (" (smart moments: long clips in chunks)" if smart else "")
        + "…", level="step")
    from . import sheets as sh
    for k, m in enumerate(todo):
        rec = res[m["id"]]
        if smart and m["kind"] == "video" and m["duration"] > chunk * 1.25:
            # long clip: one sheet per chunk so the model sees a frame every ~1.5 s
            parts = []
            t = 0.0
            while t < m["duration"] - 1.0:
                p_ = sh.clip_sheet(proj.cfg, m, rec, t0=t, t1=t + chunk)
                if p_:
                    parts.append((t, min(t + chunk, m["duration"]), p_))
                t += chunk
        else:
            p_ = proj.clip_sheet(m["id"])
            parts = [(None, None, p_)] if p_ else []
        results = []
        for t0, t1, sheet in parts:
            meta = _meta(m) + (f", THIS SHEET covers only t={t0:.1f}s to {t1:.1f}s of the clip" if t0 is not None else "")
            try:
                obj = llm.ask_json("You are a precise film assistant. Output JSON only.",
                                   prompt("caption_clip.md", metadata=meta), [sheet])
                results.append(_clean_caption(obj, m))
            except LLMError as e:
                log(f"  {m['id']}: caption failed ({e})", level="warn")
        if not results:
            continue
        caps[m["id"]] = _merge_captions(results, max_n)
        save_json(proj.p("captions.json"), caps)
        log(f"  [{k + 1}/{len(todo)}] {m['id']}: {caps[m['id']].get('description', '')[:70]}")
    # vision feedback into orientation: clips the model says aren't upright get flagged
    bad = [cid for cid, c in caps.items() if c.get("upright") is False]
    if bad:
        log(f"model thinks these are not upright: {', '.join(bad)} — run `vlog orient --llm` or fix with "
            f"`vlog override ID rotate=…`", level="warn")
    return caps


def _merge_captions(parts, max_n=4):
    """Combine per-chunk captions of one long clip into a single record."""
    if len(parts) == 1:
        c = parts[0]
        c["best_moments"] = c.get("best_moments", [])[:max_n]
        return c
    best = max(parts, key=lambda c: c.get("quality", 3))
    out = dict(best)
    out["description"] = " / ".join(dict.fromkeys(p.get("description") or "" for p in parts if p.get("description")))[:300]
    out["subjects"] = list(dict.fromkeys(x for p in parts for x in (p.get("subjects") or [])))[:8]
    out["place"] = next((p["place"] for p in parts if p.get("place")), "")
    out["quality"] = max(p.get("quality", 3) for p in parts)
    out["hero"] = any(p.get("hero") for p in parts)
    out["upright"] = all(p.get("upright") is not False for p in parts)
    out["avoid"] = [a for p in parts for a in (p.get("avoid") or [])]
    # interleave chunk rankings (each chunk's #1 first), better chunks first
    ranked = sorted(parts, key=lambda c: -c.get("quality", 3))
    bm, depth = [], max(len(p.get("best_moments") or []) for p in parts)
    for d in range(depth):
        for p in ranked:
            lst = p.get("best_moments") or []
            if d < len(lst):
                bm.append(lst[d])
    out["best_moments"] = bm[:max_n]
    out["chunks"] = len(parts)
    return out


def _clean_caption(obj, m):
    out = {k: obj.get(k) for k in ("description", "subjects", "place", "category", "mood", "energy",
                                   "quality", "hero", "best_moments", "avoid", "upright")}
    try:
        out["quality"] = int(out.get("quality") or 3)
    except (TypeError, ValueError):
        out["quality"] = 3
    bm = []
    for b in out.get("best_moments") or []:
        try:
            t, d = float(b.get("t", 0)), float(b.get("dur", 2))
            if m["kind"] == "image" or 0 <= t < m["duration"]:
                d = min(max(d, 0.5), max(m.get("duration", 0) - t, 0.5)) if m["kind"] == "video" else d
                bm.append({"t": t, "dur": round(d, 2), "why": str(b.get("why", ""))[:80]})
        except (TypeError, ValueError, AttributeError):
            pass
    out["best_moments"] = bm
    out["place"] = (out.get("place") or "").strip()[:40]
    return out


def check_orientation(proj):
    """Ask the vision model to confirm every overview thumbnail is upright; apply fixes."""
    llm = proj.llm()
    _reachable(llm)
    if not llm.vision:
        log("orientation check needs a vision model; skipped", level="warn")
        return {}
    media, res = proj.scan(), proj.analyze()
    per_sheet = 24
    usable = [m for m in media if res.get(m["id"], {}).get("frames")]
    fixes = {}
    for s, path in enumerate(proj.ensure_sheets()):
        ids = [m["id"] for m in usable[s * per_sheet:(s + 1) * per_sheet]]
        try:
            obj = llm.ask_json("You check image orientation. Output JSON only.",
                               prompt("orientation.md", ids=", ".join(ids)), [path])
        except LLMError as e:
            log(f"orientation check failed: {e}", level="warn")
            continue
        for cid, deg in obj.items():
            try:
                deg = int(deg) % 360
            except (TypeError, ValueError):
                continue
            if cid in ids and deg in (90, 180, 270):
                fixes[cid] = deg
    for cid, deg in fixes.items():
        proj.set_override(cid, fix_rotation_cw=deg)
        log(f"  {cid}: rotated a further {deg}° (vision check)", level="warn")
    if not fixes:
        log("vision check: all clips upright", level="ok")
    return fixes


def _catalog_text(proj):
    media, res, caps = proj.scan(), proj.analysis, proj.captions
    lines = []
    for m in media:
        r = res.get(m["id"], {})
        if not r.get("usable"):
            continue
        c = caps.get(m["id"], {})
        if c.get("best_moments"):
            bm = ", ".join(f"{b['t']:.1f}:{b['dur']:.1f}" for b in c["best_moments"][:3])
        else:
            fr = sorted(r.get("frames", []), key=lambda f: -f.get("score", 0))[:3]
            bm = ", ".join(f"{f['t']:.1f}:2" for f in fr)
        dur = f"{m['duration']:.1f}s" if m["kind"] == "video" else "photo"
        if proj.cfg["analysis"].get("smart_moments") and c.get("avoid"):
            bm += "  avoid " + ", ".join(f"{a.get('t', 0):.0f}s" for a in c["avoid"][:4] if isinstance(a, dict))
        lines.append(" | ".join([m["id"], m["kind"], dur, (m.get("created") or "")[:16], c.get("place", ""),
                                 str(c.get("category", "")), str(c.get("quality", round(r.get("quality", 0) * 5, 1))),
                                 (c.get("description") or "")[:90], bm]))
    return "\n".join(lines)


def storyboard(proj, slots):
    llm = proj.llm()
    music = proj.music
    slot_txt = "\n".join(f"{s['i']} | {s['start']:.2f}s | {s['dur']:.2f}s | {s['section']} | {s['role']}" for s in slots)
    mus = f"{music.get('bpm', 0):.0f} BPM, {music['duration']:.1f}s, sections: " + ", ".join(
        f"{x['name']} {x['start']:.0f}-{x['end']:.0f}s" for x in music["sections"])
    text = prompt("storyboard.md", music=mus, slots=slot_txt, catalog=_catalog_text(proj), project=_project_info(proj))

    def validate(obj):
        if not isinstance(obj.get("shots"), list) or not obj["shots"]:
            raise ValueError("missing 'shots' list")

    log(f"asking {llm.model} for the storyboard ({len(slots)} slots)…", level="step")
    obj = llm.ask_json("You are an expert video editor. Output JSON only.", text, validate=validate)
    assign = {}
    for sh in obj["shots"]:
        try:
            assign[int(sh.get("slot"))] = {"clip": sh.get("clip"), "in": sh.get("in"), "speed": sh.get("speed", 1.0),
                                           **({"zoom": sh["zoom"]} if "zoom" in sh else {}),
                                           **({"pos": sh["pos"]} if "pos" in sh else {})}
        except (TypeError, ValueError):
            continue
    save_json(proj.p("llm_storyboard.json"), obj)
    return assign, obj.get("text")


def titles(proj):
    llm = proj.llm()
    caps = proj.captions
    media = proj.scan()
    summary = "\n".join(f"- {(m.get('created') or '')[:16]} {c.get('place', '')}: {c.get('description', '')}"
                        for m in media for c in [caps.get(m["id"], {})] if c) or "(no captions available)"
    return llm.ask_json("You write short, punchy video titles. Output JSON only.",
                        prompt("titles.md", summary=summary[:6000], project=_project_info(proj)))


def review(proj, frames, frame_map):
    llm = proj.llm()
    from .plan import describe
    text = prompt("review_preview.md", frames="\n".join(f"{t:.2f}s -> shot {i}" for t, i in frame_map),
                  plan=describe(proj.plan)[:5000])
    return llm.ask_json("You are a meticulous video reviewer. Output JSON only.", text, frames)
