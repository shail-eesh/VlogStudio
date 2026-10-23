"""Color science: source transfer decode -> creative look -> Rec.709, and .cube LUT export.

Supported source profiles
  apple_log : iPhone 15 Pro / Blackmagic Cam "Apple Log" (Rec.2020 primaries)
  hlg       : iPhone default HDR video (Rec.2100 HLG)
  pq        : HDR10 / Dolby Vision base layer (Rec.2100 PQ)
  bt709     : normal SDR video
  srgb      : photos
"""
import numpy as np

# ---------------- transfer functions ----------------
# Apple Log (Apple Log Profile white paper)
_R0, _RT, _C = -0.05641088, 0.01, 47.28711236
_BETA, _GAMMA, _DELTA = 0.00964052, 0.08550479, 0.69336945
_PT = _C * (_RT - _R0) ** 2


def applelog_decode(p):
    p = np.asarray(p, dtype=np.float64)
    lin_log = 2.0 ** ((p - _DELTA) / _GAMMA) - _BETA
    lin_sq = np.sqrt(np.clip(p, 0, None) / _C) + _R0
    return np.where(p >= _PT, lin_log, lin_sq)


def hlg_decode(e):
    """HLG inverse OETF -> scene linear, scaled so 18% grey ~ 0.18."""
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    e = np.clip(np.asarray(e, dtype=np.float64), 0, 1)
    lin = np.where(e <= 0.5, e * e / 3.0, (np.exp((e - c) / a) + b) / 12.0)
    return lin * 3.75


def pq_decode(e):
    """ST.2084 -> nits, then 203 nit diffuse white = 1.0 (so mid grey ~0.18)."""
    m1, m2 = 2610 / 16384, 2523 / 4096 * 128
    c1, c2, c3 = 3424 / 4096, 2413 / 4096 * 32, 2392 / 4096 * 32
    e = np.clip(np.asarray(e, dtype=np.float64), 0, 1) ** (1 / m2)
    nits = 10000 * (np.maximum(e - c1, 0) / (c2 - c3 * e)) ** (1 / m1)
    return nits / 203.0


M2020_TO_709 = np.array([
    [1.6604910, -0.5876411, -0.0728499],
    [-0.1245505, 1.1328999, -0.0083494],
    [-0.0181508, -0.1005789, 1.1187297],
])
LUMA709 = np.array([0.2126, 0.7152, 0.0722])
WIDE = {"apple_log", "hlg", "pq"}
SCENE = {"apple_log", "hlg", "pq"}  # scene/HDR referred -> needs tone mapping

# ---------------- look presets ----------------
PRESETS = {
    # bright, warm, happy travel look (used for the NYC vlog)
    "golden_day": dict(exposure=0.82, tonemap_mix=0.55, contrast=0.25, pivot=0.42,
                       warm=(0.024, 0.010, -0.022), teal=(-0.012, 0.004, 0.016),
                       vibrance=0.30, saturation=0.10, skin_protect=0.55, matte=0.018),
    # blockbuster teal & orange
    "teal_orange": dict(exposure=0.78, tonemap_mix=0.6, contrast=0.32, pivot=0.42,
                        warm=(0.035, 0.012, -0.030), teal=(-0.030, 0.008, 0.035),
                        vibrance=0.20, saturation=0.05, skin_protect=0.6, matte=0.025),
    # clean, true-to-life
    "natural": dict(exposure=0.85, tonemap_mix=0.4, contrast=0.15, pivot=0.42,
                    warm=(0.008, 0.003, -0.008), teal=(0.0, 0.0, 0.0),
                    vibrance=0.15, saturation=0.03, skin_protect=0.5, matte=0.0),
    # punchy social-media colour
    "vivid_pop": dict(exposure=0.90, tonemap_mix=0.5, contrast=0.30, pivot=0.42,
                      warm=(0.015, 0.008, -0.012), teal=(-0.006, 0.002, 0.010),
                      vibrance=0.45, saturation=0.18, skin_protect=0.6, matte=0.0),
    # softer, desaturated film
    "moody_film": dict(exposure=0.70, tonemap_mix=0.6, contrast=0.28, pivot=0.40,
                       warm=(0.020, 0.006, -0.018), teal=(-0.020, 0.006, 0.022),
                       vibrance=0.05, saturation=-0.12, skin_protect=0.5, matte=0.045),
}


def look_params(look_cfg):
    p = dict(PRESETS.get(look_cfg.get("preset", "golden_day"), PRESETS["golden_day"]))
    for k, v in (look_cfg.get("overrides") or {}).items():
        p[k] = tuple(v) if isinstance(v, list) else v
    p["strength"] = float(look_cfg.get("strength", 1.0))
    return p


# ---------------- pipeline ----------------
def to_linear(rgb, profile, stops=0.0, wb=(1.0, 1.0, 1.0)):
    """Source code values [0,1] -> linear Rec.709 (scene-linear for log/HDR, display-linear for SDR)."""
    rgb = np.asarray(rgb, dtype=np.float64)
    if profile == "apple_log":
        lin = applelog_decode(rgb)
    elif profile == "hlg":
        lin = hlg_decode(rgb)
    elif profile == "pq":
        lin = pq_decode(rgb)
    else:
        lin = np.clip(rgb, 0, 1) ** 2.4 if profile == "bt709" else _srgb_decode(rgb)
    lin = lin * (2.0 ** stops) * np.asarray(wb)
    if profile in WIDE:
        lin = lin @ M2020_TO_709.T
        y = np.clip(lin @ LUMA709, 1e-6, None)[..., None]
        mn = lin.min(axis=-1, keepdims=True)
        k = np.where(mn < 0, y / np.maximum(y - mn, 1e-6), 1.0)  # soft gamut compression
        lin = y + (lin - y) * k
    return np.clip(lin, 0, None)


def _srgb_decode(v):
    v = np.clip(v, 0, 1)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def _aces(x):
    return np.clip((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0, 1)


def _smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def to_display(lin, profile, p):
    """Linear -> display-encoded Rec.709 (BT.1886 gamma 2.4), before creative grade."""
    if profile in SCENE:
        x = lin * p["exposure"]
        per_ch = _aces(x)
        mx = np.max(x, axis=-1, keepdims=True)
        hue = x * (_aces(mx) / np.maximum(mx, 1e-6))
        d = p["tonemap_mix"] * per_ch + (1 - p["tonemap_mix"]) * hue
    else:  # already display referred: soft shoulder only (exposure gain may push past 1)
        d = np.where(lin < 0.8, lin, 0.8 + 0.2 * np.tanh((lin - 0.8) / 0.2))
    return np.clip(d, 0, 1) ** (1 / 2.4)


def creative(v, p):
    s = p["strength"]
    luma = (v @ LUMA709)[..., None]
    sc = np.clip(luma, 0, 1)
    curve = sc + p["contrast"] * s * 4 * sc * (1 - sc) * (sc - p["pivot"])
    v = np.clip(v * (curve / np.maximum(luma, 1e-6)), 0, 1.2)
    luma = (v @ LUMA709)[..., None]
    hi = _smooth(0.35, 0.95, luma)
    sh = 1 - _smooth(0.05, 0.40, luma)
    v = v + hi * np.asarray(p["warm"]) * s + sh * np.asarray(p["teal"]) * s * (1 - luma)
    luma = (v @ LUMA709)[..., None]
    chroma = v - luma
    sat = np.sqrt((chroma ** 2).sum(-1, keepdims=True))
    r, g, b = v[..., 0:1], v[..., 1:2], v[..., 2:3]
    skin = np.clip(1 - np.abs((r - g) / np.maximum(r, 1e-4) - 0.28) * 3.5, 0, 1) * (r > g) * (g > b)
    vib = 1 + s * (p["vibrance"] * (1 - np.clip(sat * 3.0, 0, 1)) + p["saturation"])
    sk = p["skin_protect"]
    vib = vib * (1 - sk * skin) + sk * skin * (1 + 0.06 * s)
    v = np.clip(luma + chroma * vib, 0, 1)
    m = p["matte"] * s
    return np.clip(m + v * (0.985 - m), 0, 1)


def grade(rgb, profile, p, stops=0.0, wb=(1, 1, 1)):
    return creative(to_display(to_linear(rgb, profile, stops, wb), profile, p), p)


def write_cube(path, profile, p, stops=0.0, wb=(1, 1, 1), size=33, title="Vlog Studio Look"):
    g = np.linspace(0, 1, size)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")  # .cube order: red fastest
    rgb = np.stack([r, gg, b], -1).reshape(-1, 3)
    out = grade(rgb, profile, p, stops, wb)
    lines = "\n".join(f"{a:.6f} {c:.6f} {d:.6f}" for a, c, d in out)
    with open(path, "w") as f:
        f.write(f'TITLE "{title}"\n# source: {profile} -> Rec.709 gamma 2.4\n')
        f.write(f"LUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n{lines}\n")


# ---------------- automatic per-shot balancing ----------------
def auto_balance(arrs, profile, look_cfg):
    """Exposure (stops) and white-balance gains so every shot lands bright and neutral-warm.

    arrs: list of HxWx3 float arrays of *source* code values.
    Returns (stops, wb, info).
    """
    lin = np.concatenate([to_linear(a, profile).reshape(-1, 3) for a in arrs])
    y = lin @ LUMA709
    if y.size == 0 or float(np.percentile(y, 99)) < 0.004:
        return 0.0, [1.0, 1.0, 1.0], {"black": True}
    lo, hi = np.percentile(y, [2, 98])
    m = (y > lo) & (y < hi)
    if m.sum() < 50:
        m = np.ones_like(y, bool)
    logavg = float(np.exp(np.mean(np.log(y[m] + 1e-4))))
    target = look_cfg.get("target_exposure", 0.17)
    if profile not in SCENE:
        target *= 1.25  # SDR/photos are display-referred: aim a bit brighter
    stops = float(np.clip(np.log2(target / logavg), look_cfg.get("min_stops", -1.5), look_cfg.get("max_stops", 2.2)))
    if not look_cfg.get("auto_exposure", True):
        stops = 0.0
    gains = np.ones(3)
    strong = False
    if look_cfg.get("auto_white_balance", True):
        sel = m & (y > np.percentile(y, 20)) & (y < np.percentile(y, 85))
        mean = lin[sel].mean(0) if sel.sum() > 50 else lin.mean(0)
        mean = np.maximum(mean, 1e-6)
        cast = mean[0] / mean[2]
        if cast > 1.6 and logavg < 0.35:
            # strong tungsten/fluorescent cast (indoors): correct most of it, keep a little warmth
            strong = True
            gains = np.clip((mean[1] / mean) ** 0.75, 0.75, 1.35) * np.array([1.03, 1.0, 0.97])
        else:
            gains = np.clip((mean[1] / mean) ** 0.30, 0.94, 1.07)
            gains[0] = max(gains[0], 0.97)  # never cool a shot much: travel looks stay warm
            gains[2] = min(gains[2], 1.08)
    return stops, [round(float(g), 4) for g in gains], {"logavg": round(logavg, 4), "strong_wb": strong}
