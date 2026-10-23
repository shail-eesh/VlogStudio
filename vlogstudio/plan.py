"""Storyboard: beat-synced shot slots + media assignment + titles/overlays.

The plan is deterministic and works without any AI model. An LLM can optionally
supply `assignments` (clip + in-point per slot) and title text; everything it returns
is validated and repaired here, so a small local model can't break the render.
"""
import math
import os
import random
from datetime import datetime

from .analyze import best_window
from .media import place_for
from .util import log, save_json

MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER",
          "OCTOBER", "NOVEMBER", "DECEMBER"]


# ---------------- slots ----------------
def _accelerate(r):
    """Beat lengths for a build-up of r beats: long shots first, rapid cuts last."""
    if r <= 0:
        return []
    if r < 8:
        out = [2] * (r // 2)
        if r % 2:
            out.append(1)
        if len(out) >= 2 and out[-1] == 2:
            out[-1:] = [1, 1]
        return out
    head = r - 8
    out = []
    while head >= 4:
        out.append(4)
        head -= 4
    if head:
        out.append(head)
    return out + ([3, 2, 1, 1, 1] if out else [4, 2, 1, 1])


# seconds per shot for the slower pacing styles ("viral" keeps the beat patterns below)
PACING = {
    "cinematic": {"hook": 4, "teaser": 2, "title": 6, "build": [4, 3, 2, 2, 1], "high": 2, "low": 4},
    "documentary": {"hook": 6, "teaser": 3, "title": 8, "build": [6, 4, 3], "high": 4, "low": 10},
}
PACING_NAMES = ["viral"] + list(PACING)


def make_slots(music, pacing="viral"):
    if pacing in PACING:
        return _slots_paced(music, PACING[pacing])
    beats = music["beats"]
    total = music["duration"]
    bl = (beats[-1] - beats[0]) / max(len(beats) - 1, 1) if len(beats) > 1 else 0.5
    unit = 1 if bl >= 0.4 else 2  # never cut faster than ~0.4s

    def t_of(i):
        if i < len(beats):
            return beats[i]
        return beats[-1] + (i - len(beats) + 1) * bl

    def idx_of(t):
        return int(round((t - beats[0]) / bl)) if bl else 0

    secs = music["sections"]
    plan = []  # (beats, section, role)
    carry = 0
    for si, sec in enumerate(secs):
        nb = max(idx_of(sec["end"]) - idx_of(sec["start"]), 1)
        nb -= carry
        carry = 0
        name, nxt = sec["name"], (secs[si + 1]["name"] if si + 1 < len(secs) else "")
        last = si == len(secs) - 1
        if si == 0 and sec["energy"] != "high" and nb >= 8:
            hook = 6 if nb >= 14 else 4
            plan.append((hook, name, "hook"))
            rem = nb - hook - 4
            while rem >= 2:
                plan.append((2, name, "teaser"))
                rem -= 2
            if rem:
                plan[-1] = (plan[-1][0] + rem, plan[-1][1], plan[-1][2])
            ext = 4 if not last else 0
            plan.append((4 + ext, name, "title"))
            carry = ext
        elif last and nb >= 6:
            first = nb - math.ceil(nb * 0.6)
            plan += [(first, name, "story"), (nb - first, name, "closer")]
        elif name.startswith("build") or (sec["energy"] == "mid" and nxt.startswith("drop")):
            plan += [(b, name, "story") for b in _accelerate(nb)]
        elif sec["energy"] == "high":
            seq = [4] + [2 * unit] * ((nb - 4) // (2 * unit)) if nb >= 6 else [nb]
            if sum(seq) < nb:
                seq[-1] += nb - sum(seq)
            plan += [(b, name, "story") for b in seq]
        else:  # low / mid: breathe
            seq = [4] * (nb // 4)
            if nb % 4:
                if seq:
                    seq[-1] += nb % 4
                else:
                    seq = [nb]
            plan += [(b, name, "story") for b in seq]
    if plan and plan[-1][2] != "closer":
        plan[-1] = (plan[-1][0], plan[-1][1], "closer")
    slots, i = [], 0
    for k, (nbeats, name, role) in enumerate(plan):
        t0 = 0.0 if k == 0 else round(t_of(i), 4)
        i += nbeats
        t1 = round(total if k == len(plan) - 1 else min(t_of(i), total), 4)
        if t1 - t0 < 0.2:
            if slots:  # too short to show: give the time to the previous shot
                slots[-1]["dur"] = round(t1 - slots[-1]["start"], 4)
            continue
        slots.append({"i": len(slots), "start": t0, "dur": round(t1 - t0, 4), "beats": nbeats,
                      "section": name, "role": role})
    return slots


def _slots_paced(music, P):
    """Seconds-based pacing: longer shots (up to ~10 s) for cinematic / documentary edits."""
    beats = music["beats"]
    total = music["duration"]
    bl = (beats[-1] - beats[0]) / max(len(beats) - 1, 1) if len(beats) > 1 else 0.5
    unit = 1 if bl >= 0.4 else 2

    def nb_of(sec):  # seconds -> beats, even when longer than a bar
        b = max(unit, int(round(sec / bl)))
        return b + (b % 2) if b > 2 else b

    def t_of(i):
        return beats[i] if i < len(beats) else beats[-1] + (i - len(beats) + 1) * bl

    def idx_of(t):
        return int(round((t - beats[0]) / bl)) if bl else 0

    secs = music["sections"]
    plan, carry = [], 0
    for si, sec in enumerate(secs):
        nb = max(idx_of(sec["end"]) - idx_of(sec["start"]), 1) - carry
        carry = 0
        if nb <= 0:
            continue
        name, last = sec["name"], si == len(secs) - 1
        seq, roles = [], []
        if si == 0 and sec["energy"] != "high" and nb >= nb_of(P["hook"]) + 4:
            title = nb_of(P["title"])
            half = 0 if last else title // 2
            hook = min(nb_of(P["hook"]), nb - (title - half))
            seq, roles = [hook], ["hook"]
            rem = nb - hook - (title - half)
            tz = nb_of(P["teaser"])
            while rem >= tz:
                seq.append(tz)
                roles.append("teaser")
                rem -= tz
            if rem > 0:
                seq[-1] += rem
            seq.append(title)
            roles.append("title")
            carry = half
        elif last:
            if nb < 8:
                seq, roles = [nb], ["closer"]
            else:
                closer = min(max(int(math.ceil(nb * 0.6)), 4), nb - 4)
                seq, roles = [nb - closer, closer], ["story", "closer"]
        else:
            if name.startswith("build") or sec["energy"] == "mid":
                pattern = [nb_of(x) for x in P["build"]]
            elif sec["energy"] == "high":
                pattern = [nb_of(P["high"]) * 2] + [nb_of(P["high"])]
            else:
                pattern = [nb_of(P["low"])]
            rem, k = nb, 0
            while rem > 0:
                b = pattern[min(k, len(pattern) - 1)]
                b = min(b, rem)
                if rem - b and rem - b < unit:
                    b = rem
                seq.append(b)
                rem -= b
                k += 1
            roles = ["story"] * len(seq)
        plan += [(b, name, r) for b, r in zip(seq, roles)]
    if plan and plan[-1][2] != "closer":
        plan[-1] = (plan[-1][0], plan[-1][1], "closer")
    slots, i = [], 0
    for k, (nbeats, name, role) in enumerate(plan):
        t0 = 0.0 if k == 0 else round(t_of(i), 4)
        i += nbeats
        t1 = round(total if k == len(plan) - 1 else min(t_of(i), total), 4)
        if t1 - t0 < 0.2:
            if slots:
                slots[-1]["dur"] = round(t1 - slots[-1]["start"], 4)
            continue
        slots.append({"i": len(slots), "start": t0, "dur": round(t1 - t0, 4), "beats": nbeats,
                      "section": name, "role": role})
    return slots


# ---------------- helpers ----------------
def _day(m):
    return (m.get("created") or "")[:10]


def _catalog(media, analysis):
    return [m for m in media if analysis.get(m["id"], {}).get("usable")]


def _fits(m, dur, speed=1.0):
    return m["kind"] == "image" or m["duration"] >= dur * speed + 0.25


def _quality(analysis, m):
    return analysis[m["id"]].get("quality", 0.0)


def _fits_at(m, t_in, dur):
    return m["kind"] == "image" or t_in + dur <= m["duration"] - 0.1


def avoid_ranges(cap, pad=1.0):
    """Time ranges the vision model flagged as bad (blurry, ground, covered lens…)."""
    out = []
    for a in (cap or {}).get("avoid") or []:
        try:
            t = float(a.get("t"))
            out.append((t - pad, t + pad))
        except (TypeError, ValueError, AttributeError):
            pass
    return out


def _in_avoid(t, dur, ranges):
    return any(a < t + dur and t < b for a, b in ranges)


def _moments(m, rec, cap, skip, max_n=4, smart=False):
    """Candidate story moments of one clip with their score (and preferred length when known)."""
    if m["kind"] == "image":
        return [{"m": m, "t": 0.0, "len": None, "score": rec.get("quality", 0.0)}]
    q_bonus = 0.0
    bad = avoid_ranges(cap) if smart else []
    if cap:
        q_bonus = (int(cap.get("quality") or 3) - 3) * 0.08
        if cap.get("best_moments"):
            mos = []
            for i, b in enumerate(cap["best_moments"][:max_n]):
                t, ln = float(b["t"]), float(b.get("dur") or 2.0)
                if smart and _in_avoid(t, min(ln, 1.0), bad):
                    continue
                mos.append({"m": m, "t": t, "len": ln if smart else None,
                            "score": rec.get("quality", 0.0) + 0.15 + q_bonus - 0.05 * i})
            if mos:
                return mos
    fr = sorted(rec.get("frames", []), key=lambda f: f["t"])
    groups, cur = [], []
    for f in fr:
        if smart and _in_avoid(f["t"], 0.01, bad):
            continue
        if cur and f["t"] - cur[0]["t"] > 3.0:
            groups.append(cur)
            cur = []
        cur.append(f)
    if cur:
        groups.append(cur)
    cap_n = max(1, min(max_n, int((m["duration"] - skip) // 6) + 1))
    mos = [{"m": m, "t": g[0]["t"], "len": (g[-1]["t"] - g[0]["t"] + 1.5) if smart else None,
            "score": sum(f["score"] for f in g) / len(g) + q_bonus} for g in groups]
    return sorted(mos, key=lambda x: -x["score"])[:cap_n]


def _select_moments(pool, n):
    """Top-n moments by score, at most one per clip until every good clip has had a turn."""
    by_score = sorted(pool, key=lambda x: -x["score"])
    if not by_score:
        return []
    cutoff = by_score[min(len(by_score) - 1, int(len(by_score) * 0.85))]["score"]  # drop the weakest 15 %
    chosen, used = [], {}
    for rnd in range(8):
        for mo in by_score:
            if len(chosen) >= n:
                return chosen
            cid = mo["m"]["id"]
            if mo in chosen or used.get(cid, 0) > rnd or (rnd < 3 and mo["score"] < cutoff):
                continue
            chosen.append(mo)
            used[cid] = used.get(cid, 0) + 1
    return chosen


class _Usage:
    def __init__(self):
        self.used = {}
        self.blocked = {}  # model-flagged bad ranges (not counted as uses)

    def avoid(self, mid):
        return self.used.get(mid, [])

    def block(self, mid):
        return self.blocked.get(mid, [])

    def add(self, mid, t0, t1):
        self.used.setdefault(mid, []).append((t0 - 0.3, t1 + 0.3))

    def count(self, mid):
        return len(self.used.get(mid, []))


def _pick_window(m, rec, dur, speed, cfg, usage):
    return best_window(rec, m, dur, speed, cfg["analysis"]["skip_start"], usage.avoid(m["id"]), usage.block(m["id"]))


# ---------------- assignment ----------------
def heuristic_assign(cfg, media, analysis, slots, captions=None):
    cat = _catalog(media, analysis)
    if not cat:
        raise RuntimeError("no usable media to edit")
    usage = _Usage()
    assign = {}
    captions = captions or {}
    smart = bool(cfg["analysis"].get("smart_moments", False))
    max_n = int(cfg["analysis"].get("max_moments_per_clip", 4))
    if smart:  # model-flagged bad moments are treated like already-used time
        for m in cat:
            for a, b in avoid_ranges(captions.get(m["id"])):
                usage.blocked.setdefault(m["id"], []).append((a, b))

    def score_clip(m, dur, prefer=None):
        w = _pick_window(m, analysis[m["id"]], dur, 1.0, cfg, usage)
        if not w:
            return None
        bonus = 0.0
        cap = captions.get(m["id"], {})
        if prefer and cap.get("category") in prefer:
            bonus += 0.25
        if prefer and not cap:
            rec = analysis[m["id"]]
            up = rec.get("rot_scores", [0, 0, 0, 0])[rec.get("rotate_cw", 0) // 90]
            bonus += 0.25 * min(max(up, 0.0), 0.6)  # bright sky on top → scenic/outdoor
        bonus -= 0.15 * usage.count(m["id"])
        return (w[1] + bonus, w[0])

    def take(slot, m, t_in):
        assign[slot["i"]] = {"clip": m["id"], "in": t_in}
        if m["kind"] == "video":
            usage.add(m["id"], t_in, t_in + slot["dur"])

    # title + hook: best long, high-quality moments (landmarks preferred when captions exist)
    special = [s for s in slots if s["role"] in ("title", "hook")]
    for s in sorted(special, key=lambda s: s["role"] != "title"):
        cands = [(score_clip(m, s["dur"], {"landmark", "establishing", "scenery"}), m) for m in cat
                 if _fits(m, s["dur"]) and m["kind"] == "video"] or \
                [(score_clip(m, s["dur"]), m) for m in cat if _fits(m, s["dur"])]
        cands = [(sc, m) for sc, m in cands if sc]
        if cands:
            (sc, t_in), m = max(cands, key=lambda x: x[0][0])
            take(s, m, t_in)
    # teasers: top moments from different clips, spread over the trip
    teasers = [s for s in slots if s["role"] == "teaser"]
    if teasers:
        pool = sorted(cat, key=lambda m: -_quality(analysis, m))
        used_ids = {a["clip"] for a in assign.values()}
        picks = [m for m in pool if m["id"] not in used_ids][: max(len(teasers) * 3, 6)]
        picks.sort(key=lambda m: m["index"])
        step = max(len(picks) / max(len(teasers), 1), 1)
        for k, s in enumerate(teasers):
            m = picks[min(int(k * step), len(picks) - 1)] if picks else cat[k % len(cat)]
            w = score_clip(m, s["dur"])
            if w:
                take(s, m, w[1])
    # story: pick the best moments across the whole trip, then play them chronologically
    story = [s for s in slots if s["i"] not in assign]
    skip = cfg["analysis"]["skip_start"]
    pool = []
    for m in cat:
        for mo in _moments(m, analysis[m["id"]], captions.get(m["id"]), skip, max_n, smart):
            if any(x - 0.3 < mo["t"] + 2 and mo["t"] < y for x, y in usage.avoid(m["id"]) + usage.block(m["id"])):
                mo["score"] -= 0.6  # already used for hook/title/teasers
            pool.append(mo)
    chosen = _select_moments(pool, len(story))
    chosen.sort(key=lambda mo: (mo["m"]["created"] or "", mo["m"]["index"], mo["t"]))
    left = list(range(len(chosen)))
    for k, s in enumerate(story):
        # nearest (in story order) remaining moment whose clip is long enough for this slot
        order = sorted(left, key=lambda j: abs(j - k * len(chosen) / max(len(story), 1)))
        fitting = [j for j in order if _fits(chosen[j]["m"], s["dur"])]
        if smart and fitting:
            # among the nearest few in story order, prefer a moment whose natural length matches the slot
            rank = {j: r for r, j in enumerate(fitting[:3])}
            near = sorted(rank, key=lambda j: rank[j] * 0.6 + (abs((chosen[j]["len"] or s["dur"]) - s["dur"])
                                                               / max(s["dur"], 0.5)))
            fitting = near + [j for j in fitting if j not in near]
        for pick in fitting:
            mo = chosen[pick]
            m = mo["m"]
            if m["kind"] == "video":
                lo = skip if m["duration"] - skip > s["dur"] + 0.3 else 0.1
                t_in = round(min(max(mo["t"], lo), m["duration"] - s["dur"] - 0.1), 3)
                if smart and _in_avoid(t_in, s["dur"], usage.blocked.get(m["id"], [])):
                    w = _pick_window(m, analysis[m["id"]], s["dur"], 1.0, cfg, usage)
                    if not w or _in_avoid(w[0], s["dur"], usage.block(m["id"])):
                        continue  # no clean window left in this clip: try the next moment
                    t_in = w[0]
            else:
                t_in = 0.0
            left.remove(pick)
            take(s, m, t_in)
            break
    # keep shots in story order when a slot had to borrow a later/earlier moment
    st = [s for s in story if s["i"] in assign]
    keyf = lambda a: (byid_c[a["clip"]]["created"] or "", byid_c[a["clip"]]["index"], a["in"])
    byid_c = {m["id"]: m for m in cat}
    for _ in range(len(st)):
        changed = False
        for x, y in zip(st, st[1:]):
            ax, ay = assign[x["i"]], assign[y["i"]]
            ok_x = _fits_at(byid_c[ay["clip"]], ay["in"], x["dur"]) and not (
                smart and _in_avoid(ay["in"], x["dur"], usage.blocked.get(ay["clip"], [])))
            ok_y = _fits_at(byid_c[ax["clip"]], ax["in"], y["dur"]) and not (
                smart and _in_avoid(ax["in"], y["dur"], usage.blocked.get(ax["clip"], [])))
            if keyf(ax) > keyf(ay) and ok_x and ok_y:
                assign[x["i"]], assign[y["i"]] = ay, ax
                changed = True
        if not changed:
            break
    # anything still empty: best remaining clip
    for s in slots:
        if s["i"] not in assign:
            cands = [(score_clip(m, s["dur"]), m) for m in cat if _fits(m, s["dur"])]
            cands = [(sc, m) for sc, m in cands if sc]
            if not cands:
                raise RuntimeError(f"no clip is long enough for a {s['dur']:.1f}s shot")
            (sc, t_in), m = max(cands, key=lambda x: x[0][0])
            take(s, m, t_in)
    return assign


def validate_assign(cfg, media, analysis, slots, proposed, captions=None):
    """Repair an externally proposed (LLM) assignment. Returns (assign, warnings)."""
    byid = {m["id"]: m for m in media}
    captions = captions or {}
    smart = bool(cfg["analysis"].get("smart_moments", False))
    fallback = heuristic_assign(cfg, media, analysis, slots, captions)
    out, warn = {}, []
    for s in slots:
        p = proposed.get(s["i"]) or proposed.get(str(s["i"]))
        if not p:
            out[s["i"]] = fallback[s["i"]]
            warn.append(f"slot {s['i']}: no proposal, used automatic pick")
            continue
        m = byid.get(p.get("clip"))
        if not m or not analysis.get(m["id"], {}).get("usable"):
            out[s["i"]] = fallback[s["i"]]
            warn.append(f"slot {s['i']}: unknown/unusable clip {p.get('clip')!r}, used automatic pick")
            continue
        speed = float(min(max(float(p.get("speed", 1.0) or 1.0), 0.5), 2.0))
        if not _fits(m, s["dur"], speed):
            out[s["i"]] = fallback[s["i"]]
            warn.append(f"slot {s['i']}: {m['id']} too short for {s['dur']:.1f}s, used automatic pick")
            continue
        if m["kind"] == "video":
            t_in = p.get("in")
            maxin = m["duration"] - s["dur"] * speed - 0.1
            if not isinstance(t_in, (int, float)):
                w = best_window(analysis[m["id"]], m, s["dur"], speed, cfg["analysis"]["skip_start"])
                t_in = w[0] if w else 0.0
            t_in = float(min(max(t_in, 0.0), maxin))
            bad = avoid_ranges(captions.get(m["id"])) if smart else []
            if bad and _in_avoid(t_in, s["dur"] * speed, bad):
                w = best_window(analysis[m["id"]], m, s["dur"], speed, cfg["analysis"]["skip_start"], (), bad)
                if w:
                    warn.append(f"slot {s['i']}: moved {m['id']} from {t_in:.1f}s to {w[0]:.1f}s (model flagged it)")
                    t_in = w[0]
        else:
            t_in = 0.0
        out[s["i"]] = {"clip": m["id"], "in": round(t_in, 3), "speed": speed}
        for k in ("zoom", "pos"):
            if isinstance(p.get(k), list) and len(p[k]) == 2:
                out[s["i"]][k] = [float(x) for x in p[k]]
    return out, warn


# ---------------- titles & overlays ----------------
def default_text(cfg, media, used_ids, places_by_id):
    pj = cfg["project"]
    dates = sorted({_day(m) for m in media if m["id"] in used_ids and _day(m)})
    days = max(len(dates), 1)
    title = (pj.get("title") or pj.get("name") or "MY TRIP").upper()
    if dates:
        d0 = datetime.fromisoformat(dates[0])
        month = f"{MONTHS[d0.month - 1]} {d0.year}"
    else:
        month = ""
    span = f"{days * 24} HOURS" if days <= 3 else f"{days} DAYS"
    try:
        auto_hook = (pj.get("hook_template") or "{span} IN {title}").format(span=span, title=title, days=days,
                                                                          month=month)
    except (KeyError, IndexError, ValueError):
        auto_hook = f"{span} IN {title}"
    hook = pj.get("hook") or auto_hook.upper()
    chapters = []
    for k, d in enumerate(dates):
        pl = []
        for m in media:
            if _day(m) == d and m["id"] in used_ids:
                p = places_by_id.get(m["id"])
                if p and p not in pl:
                    pl.append(p)
        chapters.append({"date": d, "main": f"DAY {k + 1:02d}", "sub": "  ·  ".join(pl[:2])})
    return {
        "hook": {"line1": hook, "line2": pj.get("hook_sub") or "A TRAVEL VLOG"},
        "title": {"script": pj.get("title_script", "welcome to"), "main": title,
                  "sub": pj.get("subtitle") or month},
        "chapters": chapters,
        "end": {"main": f"{title},", "script": pj.get("end_line", ""), "sub": pj.get("end_sub", "")},
    }


def build(cfg, media, analysis, music, assignments=None, text=None, captions=None):
    slots = make_slots(music, cfg["project"].get("pacing", "viral"))
    warnings = []
    if assignments:
        assign, warnings = validate_assign(cfg, media, analysis, slots, assignments, captions)
    else:
        assign = heuristic_assign(cfg, media, analysis, slots, captions)
    byid = {m["id"]: m for m in media}
    rnd = random.Random(int(cfg["music"].get("seed", 7)))
    shots = []
    push = True
    for s in slots:
        a = assign[s["i"]]
        m = byid[a["clip"]]
        beat_len = s["dur"] / max(s["beats"], 1)
        if s["role"] == "title":
            zoom, pos = [1.0, 1.10], [0.5, 0.35]
        elif s["role"] in ("hook", "closer"):
            zoom, pos = [1.0, 1.07], [0.5, 0.5]
        elif s["dur"] <= beat_len * 1.01 and s["section"] != "outro":
            zoom, pos = [1.05, 1.13], [0.5, 0.5]
        elif s["section"] in ("break", "outro", "intro"):
            zoom, pos = ([1.0, 1.05] if push else [1.06, 1.0]), [0.5, 0.5]
            push = not push
        else:
            zoom, pos = ([1.02, 1.09] if push else [1.10, 1.02]), [0.5, 0.5]
            push = not push
        if m["kind"] == "image":
            zoom = [1.0, 1.12] if push else [1.12, 1.0]
            pos = [round(rnd.uniform(0.3, 0.7), 2), round(rnd.uniform(0.3, 0.6), 2)]
        shots.append({"i": s["i"], "clip": m["id"], "kind": m["kind"], "in": a["in"], "dur": s["dur"],
                      "start": s["start"], "speed": a.get("speed", 1.0), "zoom": a.get("zoom", zoom),
                      "pos": a.get("pos", pos), "role": s["role"], "section": s["section"]})

    # places: config gazetteer first, then LLM caption guesses
    places_by_id = {}
    for m in media:
        p = place_for(m, cfg.get("places", []))
        if not p and captions and captions.get(m["id"], {}).get("place"):
            p = captions[m["id"]]["place"]
        if p:
            places_by_id[m["id"]] = p.upper()
    used = {s["clip"] for s in shots}
    tx = default_text(cfg, media, used, places_by_id)
    if text:  # LLM / user supplied copy: merge field by field
        for k in ("hook", "title", "end"):
            for f, v in (text.get(k) or {}).items():
                if isinstance(v, str) and v.strip() and len(v) <= 60:
                    tx[k][f] = v.strip()
        for i, ch in enumerate(text.get("chapters") or []):
            if i < len(tx["chapters"]) and isinstance(ch, dict):
                for f in ("main", "sub"):
                    if isinstance(ch.get(f), str) and len(ch[f]) <= 50:
                        tx["chapters"][i][f] = ch[f].strip()
    total = music["duration"]
    specs = [{"id": "hook", "kind": "hook", **tx["hook"]}, {"id": "title", "kind": "title", **tx["title"]},
             {"id": "end", "kind": "end", **tx["end"]}]
    overlays = []
    hook = next((s for s in shots if s["role"] == "hook"), None)
    if hook:
        overlays.append({"title": "hook", "start": round(hook["start"] + 0.12, 3),
                         "end": round(hook["start"] + hook["dur"] - 0.1, 3)})
    ts = next((s for s in shots if s["role"] == "title"), None)
    if ts:
        overlays.append({"title": "title", "start": round(ts["start"] + 0.3, 3),
                         "end": round(ts["start"] + min(3.5, ts["dur"] - 0.2), 3)})
    story = [s for s in shots if s["role"] in ("story", "closer")]
    busy = [(o["start"], o["end"]) for o in overlays] + [(shots[-1]["start"] + 0.1, total)]

    def free(a, b):
        return all(b <= x or a >= y for x, y in busy)

    if cfg["project"].get("chapter_cards", True) and len(tx["chapters"]) >= 2:
        for k, ch in enumerate(tx["chapters"]):
            first = next((s for s in story if _day(byid[s["clip"]]) == ch["date"]), None)
            if first and first["role"] != "closer":
                a = round(first["start"] + 0.1, 3)
                b = round(a + 1.8, 3)
                if free(a, b):
                    cid = f"day{k + 1}"
                    specs.append({"id": cid, "kind": "chapter", "main": ch["main"], "sub": ch["sub"]})
                    overlays.append({"title": cid, "start": a, "end": b})
                    busy.append((a, b + 0.3))
    if cfg["project"].get("location_tags", True):
        last_place = None
        n = 0
        for s in story:
            p = places_by_id.get(s["clip"])
            if p and p != last_place and s["role"] != "closer":
                a = round(s["start"] + 0.1, 3)
                b = round(a + max(1.6, min(2.4, s["dur"] + 1.0)), 3)
                while not free(a, b) and a < s["start"] + 4.0:
                    a, b = round(a + 0.5, 3), round(b + 0.5, 3)
                if free(a, b) and b < total - 1:
                    cid = f"loc{n}"
                    n += 1
                    specs.append({"id": cid, "kind": "location", "main": p, "sub": cfg["project"].get("location_sub", "")})
                    overlays.append({"title": cid, "start": a, "end": b})
                    busy.append((a, b + 0.3))
            if p:
                last_place = p
    closer = shots[-1]
    overlays.append({"title": "end", "start": round(closer["start"] + 0.4, 3), "end": round(total - 0.3, 3)})
    flashes = [round(sec["start"], 3) for sec in music["sections"]
               if sec["energy"] == "high" and sec["start"] > 0.5] if cfg["output"].get("flashes", True) else []
    plan = {"duration": total, "fps": cfg["output"]["fps"], "music": {k: v for k, v in music.items() if k != "beats"},
            "shots": shots, "titles": specs, "overlays": sorted(overlays, key=lambda o: o["start"]),
            "flashes": flashes, "warnings": warnings}
    save_json(os.path.join(cfg.work_folder, "plan.json"), plan)
    log(f"storyboard: {len(shots)} shots over {total:.1f}s, {len(overlays)} text overlays"
        + (f", {len(warnings)} repaired" if warnings else ""), level="ok")
    return plan


def describe(plan):
    lines = []
    for s in plan["shots"]:
        lines.append(f"{s['i']:>3}  {s['start']:6.2f}s  {s['dur']:4.2f}s  {s['section']:<7} {s['role']:<7} "
                     f"{s['clip']}  in={s['in']:.2f}" + (f" x{s['speed']}" if s.get("speed", 1) != 1 else ""))
    for o in plan["overlays"]:
        lines.append(f"     text '{o['title']}' {o['start']:.2f}–{o['end']:.2f}s")
    return "\n".join(lines)
