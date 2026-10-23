"""Music: synthesize an original royalty-free track, or analyse the user's own track.

Both paths produce music.wav + a structure: bpm, beat times, and sections with an energy label
(intro / build / drop / break / outro) that the storyboard cuts to.
"""
import os
import subprocess

from .util import _NO_WINDOW
import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

from .util import log, run, save_json

SR = 44100

# chord degrees are semitone offsets from the key root; melody uses chord tones + pentatonic
PRESETS = {
    "tropical_house": dict(bpm=120, key="A", prog=[[0, 4, 7], [7, 11, 14], [9, 12, 16], [5, 9, 12]],
                           roots=[0, 7, 9, 5], swing=0.0, pluck=True, pad=0.75, clap=True, offbass=True,
                           shaker=True, riser=True, pump=0.65, rhythm=[1, 0, 1, 1, 0, 1, 1, 1]),
    "upbeat_pop": dict(bpm=124, key="C", prog=[[0, 4, 7], [9, 12, 16], [5, 9, 12], [7, 11, 14]],
                       roots=[0, 9, 5, 7], swing=0.0, pluck=True, pad=0.6, clap=True, offbass=False,
                       shaker=True, riser=True, pump=0.5, rhythm=[1, 0, 1, 0, 1, 1, 0, 1]),
    "cinematic_pop": dict(bpm=100, key="D", prog=[[9, 12, 16], [5, 9, 12], [0, 4, 7], [7, 11, 14]],
                          roots=[9, 5, 0, 7], swing=0.0, pluck=True, pad=1.0, clap=True, offbass=False,
                          shaker=False, riser=True, pump=0.35, rhythm=[1, 0, 0, 1, 0, 0, 1, 0]),
    "chill_lofi": dict(bpm=86, key="F", prog=[[2, 5, 9, 12], [7, 11, 14, 17], [0, 4, 7, 11], [9, 12, 16, 19]],
                       roots=[2, 7, 0, 9], swing=0.12, pluck=True, pad=0.8, clap=True, offbass=False,
                       shaker=False, riser=False, pump=0.25, rhythm=[1, 0, 0, 1, 0, 1, 0, 0]),
}
KEYS = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6,
        "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}


def structure_bars(total_bars):
    """Section layout scaled to the requested length (multiples of 2 bars)."""
    if total_bars < 10:
        lay = [("intro", 2), ("drop", max(total_bars - 4, 2)), ("outro", 2)]
    elif total_bars < 16:
        lay = [("intro", 2), ("build", 2), ("drop", total_bars - 6), ("outro", 2)]
    else:
        f = {"intro": 4 / 34, "build": 4 / 34, "drop": 8 / 34, "break": 4 / 34, "build2": 2 / 34, "drop2": 8 / 34,
             "outro": 4 / 34}
        lay = [(k, max(2, int(round(v * total_bars / 2)) * 2)) for k, v in f.items()]
        diff = total_bars - sum(n for _, n in lay)
        lay[5] = ("drop2", max(2, lay[5][1] + diff))
    return lay


def _hz(n):
    return 440.0 * 2 ** ((n - 69) / 12)


class _Synth:
    def __init__(self, n, seed):
        self.N = n
        self.L = np.zeros(n)
        self.R = np.zeros(n)
        self.rng = np.random.default_rng(seed)

    def add(self, sig, t0, gain=1.0, pan=0.0, bus=None):
        i = int(t0 * SR)
        if i >= self.N or i < 0:
            return
        sig = sig[: self.N - i] * gain
        if bus is not None:
            bus[i:i + len(sig)] += sig
            return
        self.L[i:i + len(sig)] += sig * np.sqrt(0.5 * (1 - pan))
        self.R[i:i + len(sig)] += sig * np.sqrt(0.5 * (1 + pan))


def _lp(x, f, order=2):
    return sosfilt(butter(order, min(f, SR / 2 - 100) / (SR / 2), "low", output="sos"), x)


def _hp(x, f, order=2):
    return sosfilt(butter(order, f / (SR / 2), "high", output="sos"), x)


def _env(n, a=0.005, d=0.2, s=0.0, rel=0.05, hold=None):
    t = np.arange(n) / SR
    e = np.minimum(t / max(a, 1e-4), 1.0) * (s + (1 - s) * np.exp(-np.maximum(t - a, 0) / d))
    if hold is not None:
        e = e * np.clip((hold + rel - t) / rel, 0, 1)
    return e


def generate(cfg, out_wav, duration):
    mc = cfg["music"]
    pr = dict(PRESETS.get(mc.get("preset", "tropical_house"), PRESETS["tropical_house"]))
    bpm = float(mc.get("bpm") or pr["bpm"])
    root = 45 + KEYS.get((mc.get("key") or pr["key"]).strip(), 9) % 12  # around A2
    if root > 50:
        root -= 12
    beat = 60 / bpm
    bar = 4 * beat
    total_bars = max(8, int(round(duration / bar / 2)) * 2)
    lay = structure_bars(total_bars)
    total = total_bars * bar
    n = int(total * SR)
    syn = _Synth(n, int(mc.get("seed", 7)))
    rng = syn.rng

    def kick():
        k = int(0.45 * SR)
        t = np.arange(k) / SR
        f = 48 + 110 * np.exp(-t / 0.035)
        s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.16)
        click = _hp(rng.standard_normal(k), 3000) * np.exp(-t / 0.004) * 0.25
        return np.tanh(1.6 * (s + click)) * 0.9

    def clap():
        k = int(0.35 * SR)
        t = np.arange(k) / SR
        e = np.zeros(k)
        for j, dt in enumerate([0, 0.011, 0.022]):
            e += (t >= dt) * np.exp(-np.maximum(t - dt, 0) / (0.008 if j < 2 else 0.11))
        x = sosfilt(butter(2, [900 / (SR / 2), 5000 / (SR / 2)], "band", output="sos"), rng.standard_normal(k)) * e
        return (x * 0.8 + np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.05) * 0.3) * 0.7

    def hat(open_=False):
        k = int((0.25 if open_ else 0.06) * SR)
        t = np.arange(k) / SR
        return _hp(rng.standard_normal(k), 7000, 4) * np.exp(-t / (0.08 if open_ else 0.015)) * 0.35

    def shaker():
        k = int(0.09 * SR)
        t = np.arange(k) / SR
        x = sosfilt(butter(2, [5000 / (SR / 2), 12000 / (SR / 2)], "band", output="sos"), rng.standard_normal(k))
        return x * np.sin(np.pi * t / t[-1]) ** 2 * 0.25

    def saw(f, k, det=0.0):
        t = np.arange(k) / SR
        return 2 * ((f * (1 + det) * t + rng.random()) % 1.0) - 1

    def pad(notes, dur):
        k = int(dur * SR)
        x = np.zeros(k)
        for m in notes:
            for dd in (-0.012, -0.004, 0.004, 0.012):
                x += saw(_hz(m), k, dd)
        return _lp(x / (len(notes) * 4), 2400) * _env(k, a=0.08, d=10, s=1.0, rel=0.25, hold=dur - 0.25)

    def pluck(m, dur=0.35):
        k = int(dur * SR)
        t = np.arange(k) / SR
        f = _hz(m)
        x = (np.sin(2 * np.pi * f * t) + 0.45 * np.sin(4 * np.pi * f * t) * np.exp(-t / 0.05)
             + 0.2 * np.sin(6 * np.pi * f * t) * np.exp(-t / 0.03) + 0.15 * np.sin(2 * np.pi * 4.01 * f * t) * np.exp(-t / 0.02))
        return x * _env(k, a=0.002, d=0.16) * 0.5

    def bass(m, dur):
        k = int(dur * SR)
        t = np.arange(k) / SR
        f = _hz(m)
        x = np.sin(2 * np.pi * f * t) + 0.35 * _lp(saw(f, k), 900)
        return np.tanh(1.3 * x) * _env(k, a=0.004, d=0.18, s=0.55, rel=0.03, hold=max(dur - 0.03, 0.01)) * 0.55

    def riser(dur):
        k = int(dur * SR)
        t = np.arange(k) / SR
        noise = rng.standard_normal(k)
        out = np.zeros(k)
        for b in range(64):
            a, z = b * k // 64, (b + 1) * k // 64
            out[a:z] = _hp(noise[a:z], 400 * (30 ** (b / 64)))
        sweep = np.sin(2 * np.pi * np.cumsum(200 * (8 ** (t / dur))) / SR) * 0.15
        return (out * 0.35 + sweep) * (t / dur) ** 2

    def impact():
        k = int(2.5 * SR)
        t = np.arange(k) / SR
        boom = np.sin(2 * np.pi * np.cumsum(35 + 60 * np.exp(-t / 0.08)) / SR) * np.exp(-t / 0.6)
        return boom * 0.7 + _hp(rng.standard_normal(k), 4000) * np.exp(-t / 0.9) * 0.25

    # melody: chord tones on the preset rhythm, gentle contour, seeded
    scale = [0, 2, 4, 7, 9]
    melody = []
    for c, chord in enumerate(pr["prog"]):
        tones = sorted(set([(x % 12) for x in chord] + scale))
        bar_notes = []
        prev = 12 + chord[-1] % 12
        for slot, on in enumerate(pr["rhythm"]):
            if not on:
                bar_notes.append(None)
                continue
            if slot == 0:
                cand = [12 + (chord[2 % len(chord)] % 12), 12 + chord[0] % 12]
            else:
                cand = [12 + t for t in tones] + [24 + t for t in tones[:2]]
            cand = sorted(cand, key=lambda x: abs(x - prev) + rng.random() * 3)
            prev = cand[0]
            bar_notes.append(prev)
        melody.append(bar_notes)

    sections, t_cursor = [], 0.0
    bar_sec = []
    for name, nb in lay:
        sections.append({"name": name, "start": round(t_cursor, 4), "end": round(t_cursor + nb * bar, 4),
                         "energy": {"intro": "low", "build": "mid", "build2": "mid", "drop": "high",
                                    "drop2": "high", "break": "low", "outro": "low"}[name]})
        bar_sec += [name] * nb
        t_cursor += nb * bar

    pad_bus = np.zeros(n)
    pl_bus = np.zeros(n)
    eighth = beat / 2
    sw = pr["swing"] * eighth
    for b in range(total_bars):
        sec = bar_sec[b]
        t0 = b * bar
        c = b % 4
        drop = sec in ("drop", "drop2")
        chord = [root + 12 + x for x in pr["prog"][c]]
        last2 = b >= total_bars - 2
        if not last2 or b == total_bars - 2:
            syn.add(pad(chord, bar * (2 if b == total_bars - 2 else 1) + 0.1), t0,
                    pr["pad"] * (0.7 if drop else 1.0), bus=pad_bus)
        if not last2 and pr["pluck"]:
            for k, m in enumerate(melody[c]):
                if m is None:
                    continue
                tt = t0 + k * eighth + (sw if k % 2 else 0)
                syn.add(pluck(root + 24 + m), tt, 0.9 if drop else 0.7, bus=pl_bus)
                if sec == "drop2":
                    syn.add(pluck(root + 36 + m, 0.25), tt, 0.25, bus=pl_bus)
        for q in range(4):
            tb = t0 + q * beat
            build_gap = sec in ("build", "build2") and b + 1 < total_bars and bar_sec[b + 1].startswith("drop") and q == 3
            if drop or (sec in ("build",) and not build_gap):
                syn.add(kick(), tb, 0.95)
            if sec == "intro" and b >= 2 and q in (0, 2):
                syn.add(kick(), tb, 0.5)
            if drop and q in (1, 3) and pr["clap"]:
                syn.add(clap(), tb, 0.8, 0.05)
            if drop or sec == "build":
                syn.add(hat(open_=drop), tb + eighth + sw, 0.8, 0.25)
            if drop and pr["shaker"]:
                for s16 in range(4):
                    syn.add(shaker(), tb + s16 * beat / 4, 0.6 if s16 % 2 else 0.35, -0.3)
            if drop or sec == "build":
                r_ = root + pr["roots"][c] - (12 if pr["roots"][c] > 6 else 0)
                if pr["offbass"]:
                    syn.add(bass(r_, eighth - 0.02), tb + eighth, 0.9)
                    if drop:
                        syn.add(bass(r_ + 12, beat / 4), tb + 3 * beat / 4, 0.35)
                elif q in (0, 2):
                    syn.add(bass(r_, beat * 1.8), tb, 0.8)
        nxt = bar_sec[b + 1] if b + 1 < total_bars else ""
        if nxt.startswith("drop") and sec != nxt:
            steps = [0, 0.5, 1, 1.5, 2, 2.25, 2.5, 2.75, 3, 3.125, 3.25, 3.375]
            for i, s_ in enumerate(steps):
                syn.add(clap(), t0 + s_ * beat, 0.25 + 0.5 * i / len(steps))
            if pr["riser"]:
                rb = min(2, b + 1)
                syn.add(riser(rb * bar - beat * 0.5), t0 - (rb - 1) * bar, 0.7)
        if sec.startswith("drop") and (b == 0 or bar_sec[b - 1] != sec):
            syn.add(impact(), t0, 0.8)
        if sec == "break" and bar_sec[b - 1] != "break":
            syn.add(impact(), t0, 0.5)

    t = np.arange(n) / SR
    secs_per_sample = np.array(bar_sec)[np.minimum((t / bar).astype(int), total_bars - 1)]
    is_pump = np.isin(secs_per_sample, ["drop", "drop2", "build"])
    pump = np.where(is_pump, 1 - pr["pump"] * np.exp(-(t % beat) / 0.09), 1.0)
    quiet = np.isin(secs_per_sample, ["intro", "break", "outro"])
    pl_bus = np.where(quiet, _lp(pl_bus, 1800) * 1.1, pl_bus)
    dly = int(0.75 * beat * SR)
    pl_l, pl_r = pl_bus.copy(), pl_bus.copy()
    pl_r[dly:] += 0.35 * pl_bus[:-dly]
    pl_l[2 * dly:] += 0.2 * pl_bus[:-2 * dly]
    syn.L += (pad_bus * 0.32 + pl_l * 0.42) * pump
    syn.R += (pad_bus * 0.32 + pl_r * 0.42) * pump
    mix = np.stack([syn.L, syn.R], 1)
    mix = _hp(mix.T, 28).T
    qs = np.convolve(quiet.astype(float), np.ones(2205) / 2205, mode="same")
    mix = mix * (1 + 0.9 * qs)[:, None]
    mix = mix - 0.25 * _lp(mix.T, 120).T
    mix = mix * np.clip((total - t) / 3.0, 0, 1)[:, None]
    mix = np.tanh(mix * 1.1) / np.tanh(1.1)
    mix = mix / max(np.max(np.abs(mix)), 1e-6) * 0.89
    wavfile.write(out_wav, SR, (mix * 32767).astype(np.int16))
    beats = [round(i * beat, 4) for i in range(int(total / beat) + 1)]
    log(f"generated {mc.get('preset')} track: {bpm:.0f} BPM, {total_bars} bars, {total:.1f}s", level="ok")
    return {"source": "generated", "preset": mc.get("preset"), "bpm": bpm, "duration": round(total, 4),
            "beats": beats, "sections": sections}


# ---------------- user track analysis ----------------
def _decode_mono(path, sr=22050):
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True, check=True, creationflags=_NO_WINDOW)
    return np.frombuffer(p.stdout, dtype=np.float32).astype(np.float64), sr


def analyze_track(path):
    x, sr = _decode_mono(path)
    hop, nfft = 512, 2048
    nfr = max(1, (len(x) - nfft) // hop)
    win = np.hanning(nfft)
    idx = np.arange(nfft)[None, :] + hop * np.arange(nfr)[:, None]
    mag = np.log1p(10 * np.abs(np.fft.rfft(x[idx] * win, axis=1)))
    flux = np.maximum(np.diff(mag, axis=0), 0).sum(1)
    flux = np.concatenate([[0], flux])
    flux = flux - np.convolve(flux, np.ones(16) / 16, mode="same")
    onset = np.maximum(flux, 0)
    onset /= onset.max() + 1e-9
    fps = sr / hop
    # tempo: comb-filter alignment of onsets over the whole track, with a mild prior near 120 BPM
    def comb(bpm):
        per = 60 * fps / bpm
        n = int(len(onset) / per) - 1
        if n < 4:
            return 0.0, 0.0
        best_v, best_ph = 0.0, 0.0
        for ph in np.arange(0, per, 1.0):
            pos = ph + per * np.arange(n)
            idx = pos.astype(int)
            fr = pos - idx
            v = (onset[idx] * (1 - fr) + onset[np.minimum(idx + 1, len(onset) - 1)] * fr).sum()
            if v > best_v:
                best_v, best_ph = v, ph
        return best_v / n, best_ph

    def prior(bpm):
        return np.exp(-0.5 * (np.log2(bpm / 120) / 1.0) ** 2)

    coarse = np.arange(70, 180.01, 0.5)
    vals = [comb(b)[0] * prior(b) for b in coarse]
    best_bpm = float(coarse[int(np.argmax(vals))])
    fine = np.arange(best_bpm - 0.75, best_bpm + 0.751, 0.02)
    best_bpm = float(fine[int(np.argmax([comb(b)[0] for b in fine]))])
    period = 60 * fps / best_bpm
    ph = comb(best_bpm)[1]
    # produced music has a steady tempo: use the exact grid (onset frames are centred nfft/2 later)
    offset = (nfft / 2) / sr
    beats = []
    k = 0
    while ph + k * period < len(onset):
        beats.append(float((ph + k * period) / fps + offset))
        k += 1
    rms = np.sqrt(np.convolve(x ** 2, np.ones(hop) / hop, mode="same")[::hop] + 1e-12)
    return {"bpm": float(best_bpm), "beats": [round(b, 4) for b in beats], "rms": rms, "fps": fps,
            "duration": len(x) / sr}


def from_file(cfg, out_wav, duration):
    path = cfg.resolve(cfg["music"]["file"])
    if not os.path.exists(path):
        raise FileNotFoundError(f"music file not found: {path}")
    a = analyze_track(path)
    beats = a["beats"]
    bar = 4 * 60 / a["bpm"]
    # pick downbeat phase with the strongest energy, end on a bar line near the target
    rms, fps = a["rms"], a["fps"]
    phase = int(np.argmax([sum(rms[min(int(b * fps), len(rms) - 1)] for b in beats[k::4]) for k in range(4)]))
    bars = beats[phase::4]
    start = bars[0] if bars and bars[0] < 2 * bar else 0.0
    end_target = start + duration
    end = min([b for b in bars if b >= start + 8 * bar] or [a["duration"]], key=lambda b: abs(b - end_target))
    end = min(end, a["duration"])
    total = end - start
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{total:.3f}", "-i", path,
         "-af", f"afade=t=out:st={max(total - 2.5, 0):.3f}:d=2.5", "-ar", "44100", "-ac", "2", out_wav])
    # energy sections per 4 bars: low / mid / high relative to the track
    sections = []
    bl = [b - start for b in bars if start <= b < end]
    chunks = list(range(0, len(bl), 4))
    en = []
    for k in chunks:
        t0 = bl[k]
        t1 = bl[k + 4] if k + 4 < len(bl) else total
        seg = rms[int((t0 + start) * fps):int((t1 + start) * fps)]
        en.append(float(seg.mean()) if len(seg) else 0.0)
    if en:
        q1, q2 = np.percentile(en, [33, 70])
    for i, k in enumerate(chunks):
        t0 = bl[k]
        t1 = bl[k + 4] if k + 4 < len(bl) else total
        e = "high" if en[i] >= q2 else ("low" if en[i] <= q1 else "mid")
        name = {"high": "drop", "mid": "build", "low": "break"}[e]
        if i == 0:
            name = "intro" if e != "high" else "drop"
        if i == len(chunks) - 1:
            name = "outro"
        if sections and sections[-1]["name"] == name:
            sections[-1]["end"] = round(t1, 4)
        else:
            sections.append({"name": name, "start": round(t0, 4), "end": round(t1, 4), "energy": e})
    if sections:
        sections[0]["start"] = 0.0
        sections[-1]["end"] = round(total, 4)
    rel_beats = [round(float(b - start), 4) for b in beats if start <= b <= end]
    log(f"music file: {a['bpm']:.1f} BPM detected, using {total:.1f}s", level="ok")
    return {"source": "file", "file": path, "bpm": a["bpm"], "duration": round(total, 4), "beats": rel_beats,
            "sections": sections}


def make(cfg, duration=None):
    work = cfg.work_folder
    os.makedirs(work, exist_ok=True)
    out = os.path.join(work, "music.wav")
    duration = duration or float(cfg["project"]["target_duration"])
    if cfg["music"]["mode"] == "file" and cfg["music"].get("file"):
        st = from_file(cfg, out, duration)
    else:
        st = generate(cfg, out, duration)
    st["wav"] = out
    save_json(os.path.join(work, "music.json"), st)
    return st
