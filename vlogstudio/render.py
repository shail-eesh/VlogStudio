"""Rendering: per-shot LUTs, graded segments, final assembly, graded clip export."""
import hashlib
import json
import os
import threading

from . import color, titles
from .config import parse_res
from .util import IS_MAC, concat_line, gpu_kind, load_json, log, pmap, run, save_json

ROT = {90: "transpose=1", 180: "hflip,vflip", 270: "transpose=2"}
_LUT_LOCK = threading.Lock()


def lut_for(cfg, m, rec, size=None):
    """Per-shot LUT (look + exposure/WB balance), cached by parameters."""
    size = size or cfg["look"].get("lut_size", 33)
    p = color.look_params(cfg["look"])
    key = json.dumps([m["profile"], rec.get("stops", 0), rec.get("wb", [1, 1, 1]), p, size], sort_keys=True, default=str)
    h = hashlib.sha1(key.encode()).hexdigest()[:10]
    d = os.path.join(cfg.work_folder, "luts")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"lut_{h}.cube")  # plain name: safe inside ffmpeg filter strings on every OS
    with _LUT_LOCK:  # several shots of one clip may render in parallel
        if not os.path.exists(path):
            color.write_cube(path + ".tmp", m["profile"], p, rec.get("stops", 0), rec.get("wb", [1, 1, 1]), size,
                             title=f"{cfg['look']['preset']} {m['id']}")
            os.replace(path + ".tmp", path)
    return path


def _bitrate(W, H):
    px = W * H
    return "70M" if px >= 3840 * 2000 else ("24M" if px >= 1920 * 1000 else "10M")


def _use_gpu(cfg):
    mode = cfg["output"].get("encoder", "auto")
    return None if mode == "cpu" else gpu_kind()


def _venc(W, H, final=True, cfg=None):
    """Encoder for intermediate (per-shot) segments: high quality, fast."""
    g = _use_gpu(cfg) if cfg else gpu_kind()
    if g == "apple":
        return ["-c:v", "hevc_videotoolbox", "-b:v", _bitrate(W, H), "-tag:v", "hvc1"]
    if g == "nvidia":
        return ["-c:v", "hevc_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "15" if final else "22", "-b:v", "0",
                "-tag:v", "hvc1"]
    return ["-c:v", "libx264", "-crf", "12" if final else "18", "-preset", "veryfast"]


def _final_enc(cfg, final=True):
    """Encoder for the delivered H.264 file. encoder = auto | cpu | gpu."""
    o = cfg["output"]
    mode = o.get("encoder", "auto")
    g = None if mode == "cpu" else gpu_kind()
    crf = int(o.get("crf", 17))
    if g == "nvidia" and mode in ("auto", "gpu"):  # RTX: NVENC is fast and very good at high presets
        return ["-c:v", "h264_nvenc", "-preset", "p7" if final else "p3", "-tune", "hq", "-rc", "vbr",
                "-cq", str(crf + 2 if final else 28), "-b:v", "0", "-spatial-aq", "1", "-temporal-aq", "1",
                "-profile:v", "high"]
    if g == "apple" and mode == "gpu":
        return ["-c:v", "h264_videotoolbox", "-q:v", "70" if final else "50", "-profile:v", "high"]
    return (["-c:v", "libx264", "-preset", o.get("x264_preset", "medium"), "-crf", str(crf), "-profile:v", "high"]
            if final else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-profile:v", "high"])


def _hw_decode(cfg, m):
    """GPU decoding for phone H.264/HEVC (NVDEC/D3D11). ProRes has no GPU decoder anywhere, so it stays on CPU."""
    mode = cfg["output"].get("hw_decode", "auto")
    if mode is False or mode == "off" or m.get("kind") != "video" or m.get("codec") in ("prores",):
        return []
    if mode == "auto" and (IS_MAC or gpu_kind() != "nvidia"):
        return []
    return ["-hwaccel", "auto"]


def _fit_graph(m, rec, W, H, cfg, lut):
    """Filter graph from decoded source to a graded, upright WxH yuv420p frame."""
    cw = rec.get("rotate_cw", 0)
    sw, sh = (m["height"], m["width"]) if cw in (90, 270) else (m["width"], m["height"])
    pre = [ROT[cw]] if cw in ROT else []
    conv = f"in_color_matrix={m['matrix']}:in_range={m['range']}"
    grade = (f"format=gbrp16le,lut3d=file={os.path.basename(lut)}:interp=tetrahedral,"
             "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p")
    portrait = sw / sh < (W / H) * 0.8
    if portrait and cfg["orientation"].get("portrait_fill", "blur") == "blur":
        chain = ",".join(pre + [f"scale=-2:{H}:{conv}:flags=bicubic", grade])
        return (f"[0:v]{chain},split[fg][bgs];"
                f"[bgs]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"boxblur=luma_radius={max(H // 40, 4)}:luma_power=3:chroma_radius={max(H // 80, 2)},"
                f"eq=brightness=-0.06:saturation=0.9[bg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[fit]")
    chain = ",".join(pre + [f"scale={W}:{H}:force_original_aspect_ratio=increase:{conv}:flags=bicubic",
                            f"crop={W}:{H}", grade, "setsar=1"])
    return f"[0:v]{chain}[fit]"


def shot_frames(shot, fps):
    """Frames for a shot from cumulative rounding, so the total length is exact."""
    return max(1, round((shot["start"] + shot["dur"]) * fps) - round(shot["start"] * fps))


def render_shot(cfg, m, rec, shot, out, W, H, fps, final=True):
    nf = shot_frames(shot, fps)
    speed = float(shot.get("speed", 1.0))
    lut = lut_for(cfg, m, rec)
    z0, z1 = shot.get("zoom", [1.0, 1.07])
    px, py = shot.get("pos", [0.5, 0.5])
    graph = _fit_graph(m, rec, W, H, cfg, lut)
    zp = (f"zoompan=z='{z0}+({z1}-{z0})*on/{max(nf - 1, 1)}':x='(iw-iw/zoom)*{px}':y='(ih-ih/zoom)*{py}'"
          f":d=1:s={W}x{H}:fps={fps}")
    if m["kind"] == "image":
        inp = ["-loop", "1", "-framerate", str(fps), "-t", f"{nf / fps + 0.5:.3f}", "-i", m["source_image"]]
        tail = f"[fit]{zp}[v]"
    else:
        inp = [*_hw_decode(cfg, m), "-noautorotate", "-display_rotation", "0", "-ss", f"{shot['in']:.3f}",
               "-i", m["path"]]
        tail = f"[fit]setpts=(PTS-STARTPTS)/{speed},fps={fps},{zp}[v]"
    run(["ffmpeg", "-v", "error", "-y", *inp, "-filter_complex", f"{graph};{tail}", "-map", "[v]",
         "-frames:v", str(nf), *_venc(W, H, final, cfg), "-pix_fmt", "yuv420p",
         "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-an", out], quiet=True,
        cwd=os.path.dirname(lut))
    got = _count_frames(out)
    if got < nf:
        raise RuntimeError(f"shot {shot['i']} ({m['id']} @ {shot['in']:.2f}s) rendered {got}/{nf} frames — "
                           f"clip too short for this in-point")
    # matching ambient audio (silence for photos / silent clips)
    aout = out[:-4] + ".wav"
    dur = nf / fps
    if m["kind"] == "video" and m.get("has_audio"):
        at = f"atempo={speed}" if speed <= 2 else f"atempo=2,atempo={speed / 2}"
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{shot['in']:.3f}", "-i", m["path"], "-t", f"{dur * speed:.3f}",
             "-vn", "-af", f"{at},aresample=48000,afade=t=in:d=0.04,apad,atrim=0:{dur:.4f},"
                           f"afade=t=out:st={max(dur - 0.06, 0):.4f}:d=0.06", "-ac", "2", aout], quiet=True)
    else:
        run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{dur:.4f}",
             aout], quiet=True)
    return out


def _count_frames(path):
    r = run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
             "stream=nb_read_packets", "-of", "csv=p=0", path], capture=True, check=False)
    try:
        return int((r.stdout or "0").strip().split(",")[0].split()[0])
    except (ValueError, IndexError):
        return 0


def render_segments(cfg, media, analysis, plan, W, H, final=True):
    fps = plan["fps"]
    byid = {m["id"]: m for m in media}
    d = os.path.join(cfg.work_folder, f"segs_{W}x{H}")
    os.makedirs(d, exist_ok=True)
    look_key = json.dumps(color.look_params(cfg["look"]), sort_keys=True, default=str)

    def job(shot):
        m = byid[shot["clip"]]
        rec = analysis[m["id"]]
        out = os.path.join(d, f"{shot['i']:03d}.mov")
        sig = {k: shot[k] for k in ("clip", "in", "dur", "start", "speed", "zoom", "pos")}
        sig.update(stops=rec.get("stops"), wb=rec.get("wb"), rot=rec.get("rotate_cw", 0), look=look_key,
                   fill=cfg["orientation"].get("portrait_fill"), final=final)
        if load_json(out + ".json") == sig and os.path.exists(out[:-4] + ".wav"):
            return out
        render_shot(cfg, m, rec, shot, out, W, H, fps, final)
        save_json(out + ".json", sig)
        return out

    log(f"rendering {len(plan['shots'])} graded shots at {W}x{H}…", level="step")
    done = [0]

    def wrapped(shot):
        r = job(shot)
        done[0] += 1
        if done[0] % 10 == 0 or done[0] == len(plan["shots"]):
            log(f"  {done[0]}/{len(plan['shots'])} shots")
        return r

    return pmap(wrapped, plan["shots"], cfg["output"].get("render_workers", 3)), d


def assemble(cfg, media, analysis, plan, W, H, out_path, final=True):
    segs, d = render_segments(cfg, media, analysis, plan, W, H, final)
    ocfg = cfg["output"]
    total = plan["duration"]
    fps = plan["fps"]
    tdir = os.path.join(cfg.work_folder, f"titles_{W}x{H}")
    pngs = titles.render(cfg, plan["titles"], W, H, tdir)
    with open(os.path.join(d, "v.txt"), "w", encoding="utf-8") as f:
        f.writelines(concat_line(s) for s in segs)
    with open(os.path.join(d, "a.txt"), "w", encoding="utf-8") as f:
        f.writelines(concat_line(s[:-4] + ".wav") for s in segs)
    args = ["ffmpeg", "-v", "error", "-stats", "-y",
            "-noautorotate", "-display_rotation", "0", "-f", "concat", "-safe", "0", "-i", f"{d}/v.txt",
            "-f", "concat", "-safe", "0", "-i", f"{d}/a.txt", "-i", plan["music"]["wav"]]
    ovs = [o for o in plan["overlays"] if o["title"] in pngs and o["end"] > o["start"] + 0.2]
    for o in ovs:
        args += ["-loop", "1", "-framerate", str(fps), "-t", f"{o['end'] - o['start']:.3f}", "-i", pngs[o["title"]]]
    fc = ["[0:v]setpts=PTS-STARTPTS,format=yuv420p[v0]"]
    cur = "v0"
    for k, o in enumerate(ovs):
        dd = o["end"] - o["start"]
        fi = 0.25 if o["title"].startswith("loc") else 0.35
        fc.append(f"[{3 + k}:v]format=yuva420p,fade=t=in:st=0:d={fi}:alpha=1,"
                  f"fade=t=out:st={max(dd - 0.3, 0):.3f}:d=0.3:alpha=1,setpts=PTS-STARTPTS+{o['start']}/TB[t{k}]")
        fc.append(f"[{cur}][t{k}]overlay=eof_action=pass:format=auto[o{k}]")
        cur = f"o{k}"
    for j, ft in enumerate(plan.get("flashes", [])):
        fc.append(f"color=white:s={W}x{H}:r={fps}:d=0.45,format=yuva420p,colorchannelmixer=aa=0.8,"
                  f"fade=t=out:st=0:d=0.4:alpha=1,setpts=PTS-STARTPTS+{ft}/TB[wf{j}]")
        fc.append(f"[{cur}][wf{j}]overlay=eof_action=pass:format=auto[fl{j}]")
        cur = f"fl{j}"
    post = []
    if ocfg.get("vignette", True):
        post.append("vignette=angle=PI/6:mode=forward")
    if ocfg.get("grain", 0):
        post.append(f"noise=c0s={int(ocfg['grain'])}:c0f=t")
    if ocfg.get("letterbox"):
        bar = int(round((H - W / float(ocfg["letterbox"])) / 2))
        if bar > 0:
            post += [f"drawbox=x=0:y=0:w=iw:h={bar}:color=black:t=fill",
                     f"drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:color=black:t=fill"]
    post += ["fade=t=in:st=0:d=0.2", f"fade=t=out:st={total - 1.4:.3f}:d=1.4", "format=yuv420p"]
    fc.append(f"[{cur}]{','.join(post)}[vout]")
    amb = float(cfg["music"].get("ambient_level", 1.0))
    expr = "0.16"
    for sec in reversed(plan["music"]["sections"]):
        lvl = {"break": 0.50, "intro": 0.30}.get(sec["name"], 0.30 if sec["energy"] == "low" else None)
        if lvl:
            expr = f"if(between(t,{sec['start']},{sec['end']}),{lvl},{expr})"
    fc.append(f"[1:a]highpass=f=90,dynaudnorm=f=250:g=15,volume='{amb}*({expr})':eval=frame[amb]")
    fc.append("[2:a]aresample=48000[mus]")
    fc.append(f"[amb][mus]amix=inputs=2:normalize=0:duration=longest,atrim=0:{total},"
              f"afade=t=out:st={max(total - 2.5, 0):.3f}:d=2.5,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    venc = _final_enc(cfg, final)
    log(f"assembling {os.path.basename(out_path)}…", level="step")
    run(args + ["-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]", *venc,
                "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709",
                "-colorspace", "bt709", "-c:a", "aac", "-b:a", "320k" if final else "160k",
                "-movflags", "+faststart", "-t", f"{total:.3f}", out_path], quiet=True)
    n = _count_frames(out_path)
    exp = round(total * fps)
    if abs(n - exp) > 1:
        log(f"frame count {n} != expected {exp} — beat sync may drift", level="warn")
    log(f"wrote {out_path}", level="ok")
    return out_path


def render_final(cfg, media, analysis, plan):
    W, H = parse_res(cfg["output"]["resolution"])
    base = cfg["output"].get("basename", "Travel_Vlog")
    out = os.path.join(cfg.output_folder, f"{base}_{'4K' if W >= 3840 else f'{H}p'}.mp4")
    assemble(cfg, media, analysis, plan, W, H, out, final=True)
    outs = [out]
    if cfg["output"].get("make_1080p", True) and W > 1920:
        o2 = os.path.join(cfg.output_folder, f"{base}_1080p.mp4")
        run(["ffmpeg", "-v", "error", "-y", "-noautorotate", "-display_rotation", "0", "-i", out,
             "-vf", "scale=1920:1080:flags=lanczos", *_final_enc(cfg, True),
             "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
             "-c:a", "copy", "-movflags", "+faststart", o2], quiet=True)
        outs.append(o2)
    thumb = os.path.join(cfg.output_folder, f"{base}_Thumbnail.jpg")
    title_ov = next((o for o in plan["overlays"] if o["title"] == "title"), None)
    t = (title_ov["start"] + 1.6) if title_ov else 1.0
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", out, "-frames:v", "1", "-q:v", "2", thumb], quiet=True)
    outs.append(thumb)
    return outs


def render_preview(cfg, media, analysis, plan):
    W, H = parse_res(cfg["output"].get("preview_resolution", "1280x720"))
    out = os.path.join(cfg.output_folder, "preview.mp4")
    return assemble(cfg, media, analysis, plan, W, H, out, final=False)


def preview_frames(cfg, video, times, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for t in times:
        p = os.path.join(out_dir, f"frame_{t:06.2f}.jpg")
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", "-vf", "scale=960:-2",
             "-q:v", "3", p], quiet=True, check=False)
        if os.path.exists(p):
            paths.append(p)
    return paths


def export_graded_clips(cfg, media, analysis):
    out_dir = os.path.join(cfg.output_folder, "Graded_Clips")
    os.makedirs(out_dir, exist_ok=True)
    vids = [m for m in media if m["kind"] == "video" and analysis.get(m["id"], {}).get("usable")]

    def one(m):
        rec = analysis[m["id"]]
        out = os.path.join(out_dir, f"{m['id']}_graded.mp4")
        if os.path.exists(out):
            return out
        lut = lut_for(cfg, m, rec)
        cw = rec.get("rotate_cw", 0)
        vf = ",".join(([ROT[cw]] if cw in ROT else []) + [
            f"scale=in_color_matrix={m['matrix']}:in_range={m['range']}", "format=gbrp16le",
            f"lut3d=file={os.path.basename(lut)}:interp=tetrahedral", "scale=out_color_matrix=bt709:out_range=tv",
            "format=p010le"])
        g = _use_gpu(cfg)
        if g == "apple":
            enc = ["-c:v", "hevc_videotoolbox", "-profile:v", "main10", "-b:v", "60M", "-tag:v", "hvc1"]
        elif g == "nvidia":
            enc = ["-c:v", "hevc_nvenc", "-profile:v", "main10", "-preset", "p6", "-rc", "vbr", "-cq", "18",
                   "-b:v", "0", "-tag:v", "hvc1"]
        else:
            enc = ["-c:v", "libx265", "-crf", "16", "-preset", "fast", "-tag:v", "hvc1"]
        tmp = out + ".part.mp4"
        try:
            run(["ffmpeg", "-v", "error", "-y", *_hw_decode(cfg, m), "-noautorotate", "-display_rotation", "0",
                 "-i", m["path"], "-vf", vf, *enc,
                 "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                 "-c:a", "aac", "-b:a", "256k", "-map", "0:v:0", "-map", "0:a?", "-movflags", "+faststart", tmp],
                quiet=True, cwd=os.path.dirname(lut))
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)  # never leave half-written files behind (e.g. after Cancel)
            raise
        os.replace(tmp, out)
        log(f"  graded {m['id']}")
        return out

    log(f"exporting {len(vids)} graded clips → {out_dir}", level="step")
    return pmap(one, vids, 4)


def export_luts(cfg, media):
    """Master LUTs (no per-shot balancing) for every source profile in the project."""
    d = os.path.join(cfg.output_folder, "LUTs")
    os.makedirs(d, exist_ok=True)
    p = color.look_params(cfg["look"])
    names = {"apple_log": "AppleLog", "hlg": "HLG", "pq": "PQ", "bt709": "Rec709", "srgb": "sRGB"}
    out = []
    for prof in sorted({m["profile"] for m in media} or {"bt709"}):
        path = os.path.join(d, f"{cfg['look']['preset']}_{names.get(prof, prof)}_to_Rec709.cube")
        color.write_cube(path, prof, p, size=65, title=f"Vlog Studio {cfg['look']['preset']} ({prof})")
        out.append(path)
    log(f"exported {len(out)} master LUTs → {d}", level="ok")
    return out
