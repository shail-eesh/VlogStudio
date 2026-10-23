"""Render title overlays as transparent PNGs at the output resolution."""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_WF = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts")
_MS = "/System/Library/Fonts/"
_DJ = "/usr/share/fonts/truetype/dejavu/"
# per-role fallbacks: macOS → Windows → Linux. A configured font that doesn't exist falls through these.
ROLE_FONTS = {
    "display": [_MS + "Supplemental/Futura.ttc:2", f"{_WF}/seguibl.ttf", f"{_WF}/segoeuib.ttf", f"{_WF}/arialbd.ttf",
                _DJ + "DejaVuSans-Bold.ttf"],
    "light": [_MS + "Supplemental/Futura.ttc:0", f"{_WF}/segoeui.ttf", f"{_WF}/arial.ttf", _DJ + "DejaVuSans.ttf"],
    "heavy": [_MS + "Avenir Next.ttc:8", f"{_WF}/seguibl.ttf", f"{_WF}/ariblk.ttf", f"{_WF}/arialbd.ttf",
              _DJ + "DejaVuSans-Bold.ttf"],
    "medium": [_MS + "Avenir Next.ttc:2", f"{_WF}/seguisb.ttf", f"{_WF}/segoeuib.ttf", f"{_WF}/arial.ttf",
               _DJ + "DejaVuSans.ttf"],
    "script": [_MS + "Supplemental/SnellRoundhand.ttc:1", f"{_WF}/segoesc.ttf", f"{_WF}/Gabriola.ttf",
               f"{_WF}/georgiai.ttf", _DJ + "DejaVuSerif-Italic.ttf"],
}
FALLBACK_FONTS = [_MS + "Helvetica.ttc", _MS + "Supplemental/Arial Bold.ttf", f"{_WF}/arialbd.ttf",
                  f"{_WF}/arial.ttf", _DJ + "DejaVuSans-Bold.ttf"]


def _split(spec):
    """'path:index' → (path, index); also handles Windows drive letters like C:/…"""
    m = __import__("re").match(r"^(.*?)(?::(\d+))?$", spec or "")
    return m.group(1), int(m.group(2) or 0)


class Fonts:
    def __init__(self, tcfg, scale):
        self.cfg, self.scale, self.cache = tcfg, scale, {}

    def resolve(self, role):
        """First font file that exists for this role: configured one, then OS fallbacks."""
        for spec in [self.cfg.get(f"font_{role}", "")] + ROLE_FONTS.get(role, []) + FALLBACK_FONTS:
            p, i = _split(spec)
            if p and os.path.exists(p):
                return p, i
        return None, 0

    def get(self, role, size):
        key = (role, size)
        if key in self.cache:
            return self.cache[key]
        px = max(8, int(size * self.scale))
        p, i = self.resolve(role)
        try:
            font = ImageFont.truetype(p, px, index=i) if p else ImageFont.load_default()
        except (OSError, ValueError):
            font = ImageFont.load_default()
        self.cache[key] = font
        return font


def _tw(d, txt, f, tr):
    return sum(d.textlength(c, font=f) for c in txt) + tr * max(len(txt) - 1, 0)


def _tracked(d, xy, txt, f, fill, tr=0, center=True, max_w=None, fonts=None, role=None, size=None):
    if max_w and fonts and _tw(d, txt, f, tr) > max_w:  # shrink to fit
        while size > 10 and _tw(d, txt, f, tr) > max_w:
            size = int(size * 0.92)
            tr *= 0.92
            f = fonts.get(role, size)
    x, y = xy
    if center:
        x -= _tw(d, txt, f, tr) / 2
    for c in txt:
        d.text((x, y), c, font=f, fill=fill, anchor="ls")
        x += d.textlength(c, font=f) + tr


def _finish(layer, out, shadow, alpha):
    a = layer.split()[3]
    sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    sh.putalpha(a.filter(ImageFilter.GaussianBlur(shadow)).point(lambda v: min(255, v * alpha // 255)))
    Image.alpha_composite(sh, layer).save(out)


def _scrim(layer, W, cy, rx, ry, a=115):
    m = Image.new("L", layer.size, 0)
    ImageDraw.Draw(m).ellipse((W / 2 - rx, cy - ry, W / 2 + rx, cy + ry), fill=a)
    m = m.filter(ImageFilter.GaussianBlur(max(4, int(ry / 2))))
    base = Image.new("RGBA", layer.size, (10, 6, 0, 0))
    base.putalpha(m)
    return Image.alpha_composite(base, layer)


def render(cfg, specs, W, H, out_dir):
    """specs: list of dicts {id, kind, ...text}. Returns {id: png_path}."""
    os.makedirs(out_dir, exist_ok=True)
    tcfg = cfg["titles"]
    s = H / 2160
    fonts = Fonts(tcfg, s)
    main = tuple(tcfg["color_main"]) + (255,)
    acc = tuple(tcfg["color_accent"]) + (255,)
    bar = int((H - W / cfg["output"]["letterbox"]) / 2) if cfg["output"].get("letterbox") else 0
    out = {}
    for sp in specs:
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        k = sp["kind"]
        mw = W * 0.9
        cy = H / 2
        if k == "hook":
            _tracked(d, (W / 2, cy + 60 * s), sp.get("line1", "").upper(), fonts.get("heavy", 230), main, 40 * s,
                     max_w=mw, fonts=fonts, role="heavy", size=230)
            if sp.get("line2"):
                _tracked(d, (W / 2, cy + 200 * s), sp["line2"].upper(), fonts.get("medium", 80), main, 18 * s,
                         max_w=mw, fonts=fonts, role="medium", size=80)
            im = _scrim(im, W, cy + 60 * s, 1500 * s, 330 * s)
            _finish(im, f"{out_dir}/{sp['id']}.png", int(10 * s) + 1, 220)
        elif k == "title":
            if sp.get("script"):
                d.text((W / 2 - 520 * s, cy - 230 * s), sp["script"], font=fonts.get("script", 210), fill=acc, anchor="ls")
            _tracked(d, (W / 2, cy + 170 * s), sp.get("main", "").upper(), fonts.get("display", 470), main, 70 * s,
                     max_w=mw, fonts=fonts, role="display", size=470)
            if sp.get("sub"):
                _tracked(d, (W / 2, cy + 330 * s), sp["sub"].upper(), fonts.get("light", 84), main, 34 * s,
                         max_w=mw, fonts=fonts, role="light", size=84)
            _finish(im, f"{out_dir}/{sp['id']}.png", int(24 * s) + 1, 170)
        elif k == "chapter":
            _tracked(d, (W / 2, cy + 90 * s), sp.get("main", "").upper(), fonts.get("display", 300), main, 60 * s,
                     max_w=mw, fonts=fonts, role="display", size=300)
            if sp.get("sub"):
                _tracked(d, (W / 2, cy + 230 * s), sp["sub"].upper(), fonts.get("light", 78), acc, 26 * s,
                         max_w=mw, fonts=fonts, role="light", size=78)
            im = _scrim(im, W, cy + 100 * s, 1300 * s, 300 * s, 110)
            _finish(im, f"{out_dir}/{sp['id']}.png", int(12 * s) + 1, 210)
        elif k == "location":
            x0, y0 = 250 * s, H - bar - 104 * s
            d.ellipse((x0, y0 - 92 * s, x0 + 56 * s, y0 - 36 * s), fill=acc)
            d.polygon([(x0 + 4 * s, y0 - 56 * s), (x0 + 52 * s, y0 - 56 * s), (x0 + 28 * s, y0 - 4 * s)], fill=acc)
            d.ellipse((x0 + 17 * s, y0 - 75 * s, x0 + 39 * s, y0 - 53 * s), fill=(40, 30, 20, 255))
            _tracked(d, (x0 + 95 * s, y0 - 16 * s), sp.get("main", "").upper(), fonts.get("heavy", 96), main, 16 * s,
                     center=False, max_w=W * 0.6, fonts=fonts, role="heavy", size=96)
            if sp.get("sub"):
                _tracked(d, (x0 + 97 * s, y0 + 62 * s), sp["sub"].upper(), fonts.get("medium", 54), acc, 14 * s,
                         center=False)
            _finish(im, f"{out_dir}/{sp['id']}.png", int(12 * s) + 1, 190)
        elif k == "end":
            _tracked(d, (W / 2, cy - 20 * s), sp.get("main", "").upper(), fonts.get("display", 330), main, 50 * s,
                     max_w=mw, fonts=fonts, role="display", size=330)
            if sp.get("script"):
                d.text((W / 2, cy + 250 * s), sp["script"], font=fonts.get("script", 240), fill=acc, anchor="ms")
            if sp.get("sub"):
                _tracked(d, (W / 2, cy + 420 * s), sp["sub"].upper(), fonts.get("light", 70), main, 30 * s)
            im = _scrim(im, W, cy + 150 * s, 1600 * s, 420 * s, 110)
            _finish(im, f"{out_dir}/{sp['id']}.png", int(20 * s) + 1, 190)
        else:
            continue
        out[sp["id"]] = f"{out_dir}/{sp['id']}.png"
    return out
