"""Contact sheets: per-clip timelines and an overview grid (for humans and vision models)."""
import os

from PIL import Image, ImageDraw, ImageFont


def _font(size):
    wf = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts")
    for p in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc",
              "/Library/Fonts/Arial.ttf", os.path.join(wf, "arialbd.ttf"), os.path.join(wf, "segoeuib.ttf"),
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _label(d, xy, text, size=22):
    f = _font(size)
    x, y = xy
    box = d.textbbox((x, y), text, font=f)
    d.rectangle((box[0] - 4, box[1] - 3, box[2] + 4, box[3] + 3), fill=(0, 0, 0))
    d.text((x, y), text, font=f, fill=(255, 230, 0))


def clip_sheet(cfg, m, rec, max_frames=16, cols=4, tw=384, t0=None, t1=None):
    """Grid of graded, orientation-corrected frames with timestamps. Returns path.

    t0/t1 restrict the sheet to a time range (used for long clips: one sheet per chunk)."""
    frames = [f for f in rec.get("frames", []) if f.get("thumb")
              and (t0 is None or f["t"] >= t0) and (t1 is None or f["t"] < t1)]
    if not frames:
        return None
    if len(frames) > max_frames:
        step = len(frames) / max_frames
        frames = [frames[int(i * step)] for i in range(max_frames)]
    ims = [Image.open(f["thumb"]) for f in frames]
    th = int(tw * ims[0].height / ims[0].width)
    rows = (len(ims) + cols - 1) // cols
    head = 44
    sheet = Image.new("RGB", (cols * tw, head + rows * th), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    kind = "PHOTO" if m["kind"] == "image" else f"{m['duration']:.1f}s video"
    rng = f"  ·  part {t0:.0f}–{min(t1, m['duration']):.0f}s" if t0 is not None and t1 is not None else ""
    _label(d, (8, 8), f"{m['id']}  ·  {kind}{rng}  ·  {(m.get('created') or '')[:16]}", 24)
    for i, (im, f) in enumerate(zip(ims, frames)):
        x, y = (i % cols) * tw, head + (i // cols) * th
        sheet.paste(im.resize((tw, th)), (x, y))
        if m["kind"] == "video":
            _label(d, (x + 6, y + 6), f"t={f['t']:.1f}s", 20)
    suffix = f"_{int(t0):04d}" if t0 is not None else ""
    out = os.path.join(cfg.work_folder, "sheets", f"{m['id']}{suffix}.jpg")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    sheet.save(out, quality=86)
    return out


def overview_sheets(cfg, media, analysis, per_sheet=24, cols=6, tw=320):
    """One thumbnail (middle frame) per item, labelled with its id. Returns list of paths."""
    items = [(m, analysis[m["id"]]) for m in media if analysis.get(m["id"], {}).get("frames")]
    paths = []
    for s in range(0, len(items), per_sheet):
        chunk = items[s:s + per_sheet]
        th = int(tw * 9 / 16)
        rows = (len(chunk) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tw, rows * (th + 4)), (20, 20, 20))
        d = ImageDraw.Draw(sheet)
        for i, (m, r) in enumerate(chunk):
            fr = r["frames"][len(r["frames"]) // 2]
            im = Image.open(fr["thumb"])
            im.thumbnail((tw, th))
            x, y = (i % cols) * tw, (i // cols) * (th + 4)
            sheet.paste(im, (x + (tw - im.width) // 2, y + (th - im.height) // 2))
            tag = m["id"] + ("" if r.get("usable") else "  [SKIP]")
            _label(d, (x + 4, y + 4), tag, 16)
        out = os.path.join(cfg.work_folder, "sheets", f"overview_{s // per_sheet + 1:02d}.jpg")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        sheet.save(out, quality=85)
        paths.append(out)
    return paths
