"""Project state: runs pipeline steps with on-disk caching (work/ folder)."""
import os

from . import analyze as an
from . import color, music as mu, plan as pl, render as rd, sheets
from .util import load_json, log, save_json


class Project:
    def __init__(self, cfg):
        self.cfg = cfg
        self.work = cfg.work_folder
        os.makedirs(self.work, exist_ok=True)
        self._llm = None

    # ---------- paths / caches ----------
    def p(self, name):
        return os.path.join(self.work, name)

    @property
    def media(self):
        return load_json(self.p("media.json"))

    @property
    def analysis(self):
        return load_json(self.p("analysis.json"))

    @property
    def captions(self):
        return load_json(self.p("captions.json"), {}) or {}

    @property
    def music(self):
        return load_json(self.p("music.json"))

    @property
    def plan(self):
        return load_json(self.p("plan.json"))

    @property
    def overrides(self):
        return load_json(self.p("overrides.json"), {}) or {}

    def merged_overrides(self):
        """vlog.toml [overrides] + work/overrides.json (set by tools/CLI; these win)."""
        merged = {k: dict(v) for k, v in (self.cfg.get("overrides") or {}).items()}
        for k, v in self.overrides.items():
            merged[k] = {**merged.get(k, {}), **v}
        return merged

    def llm(self):
        if self._llm is None:
            from .llm import LLM
            self._llm = LLM(self.cfg)
        return self._llm

    def llm_enabled(self, use):
        c = self.cfg["llm"]
        return bool(c.get("enabled")) and use in c.get("use_for", [])

    # ---------- steps ----------
    def scan(self, force=False):
        if self.media is not None and not force:
            return self.media
        from .media import scan
        items = scan(self.cfg)
        save_json(self.p("media.json"), items)
        return items

    def analyze(self, force=False):
        if self.analysis is not None and not force:
            return self.analysis
        media = self.scan()
        merged = self.merged_overrides()
        for m in media:
            m["override"] = merged.get(m["id"], {})
            if "profile" in m["override"]:
                m["profile"] = m["override"]["profile"]
        save_json(self.p("media.json"), media)
        res = an.analyze(self.cfg, media)
        sheets.overview_sheets(self.cfg, media, res)
        return res

    def ensure_sheets(self):
        media, res = self.scan(), self.analyze()
        paths = sorted(f for f in os.listdir(self.p("sheets")) if f.startswith("overview_")) \
            if os.path.isdir(self.p("sheets")) else []
        if not paths:
            return sheets.overview_sheets(self.cfg, media, res)
        return [self.p(os.path.join("sheets", f)) for f in paths]

    def clip_sheet(self, cid):
        m = self.get_media(cid)
        return sheets.clip_sheet(self.cfg, m, self.analysis[cid])

    def get_media(self, cid):
        for m in self.scan():
            if m["id"] == cid:
                return m
        raise KeyError(f"unknown clip id {cid!r}")

    def set_override(self, cid, **kw):
        """Persist a per-clip override and apply it to the cached analysis immediately."""
        m = self.get_media(cid)
        res = self.analysis or self.analyze()
        rec = res[cid]
        ov = self.overrides
        cur = ov.get(cid, {})
        if kw.get("fix_rotation_cw") is not None:
            kw["rotate"] = (rec.get("rotate_cw", 0) + int(kw.pop("fix_rotation_cw"))) % 360
        kw.pop("fix_rotation_cw", None)
        for k, v in kw.items():
            if v is None:
                continue
            cur[k] = v
            if k == "rotate":
                rec["rotate_cw"] = int(v) % 360
                rec["orientation_suspect"] = False  # a person (or the vision model) confirmed it
            elif k == "stops":
                rec["stops"] = float(v)
            elif k == "wb":
                rec["wb"] = [float(x) for x in v]
            elif k == "exclude":
                rec["usable"] = not bool(v)
                rec["reason"] = "excluded" if v else ""
        ov[cid] = cur
        save_json(self.p("overrides.json"), ov)
        an._write_thumbs(self.cfg, [m], res, color.look_params(self.cfg["look"]))
        save_json(self.p("analysis.json"), res)
        sheets.overview_sheets(self.cfg, self.scan(), res)
        return cur

    def make_music(self, force=False):
        st = self.music
        want = (self.cfg["music"].get("mode"), self.cfg["music"].get("preset"), self.cfg["music"].get("file"),
                self.cfg["music"].get("bpm"), float(self.cfg["project"]["target_duration"]))
        if st and not force and st.get("_key") == list(want) and os.path.exists(st.get("wav", "")):
            return st
        st = mu.make(self.cfg)
        st["_key"] = list(want)
        save_json(self.p("music.json"), st)
        return st

    def caption(self, force=False):
        from . import creative
        return creative.caption_all(self, force)

    def llm_orientation(self):
        from . import creative
        return creative.check_orientation(self)

    def make_plan(self, use_llm=None, assignments=None, text=None):
        media, res, music = self.scan(), self.analyze(), self.make_music()
        caps = self.captions
        if use_llm is None:
            use_llm = self.llm_enabled("storyboard")
        if use_llm and assignments is None:
            from . import creative
            try:
                assignments, t2 = creative.storyboard(self, pl.make_slots(music, self.cfg["project"].get("pacing", "viral")))
                text = text or t2
            except Exception as e:
                log(f"LLM storyboard failed ({e}); using the automatic storyboard", level="warn")
                assignments = None
        if text is None and self.llm_enabled("titles"):
            from . import creative
            try:
                text = creative.titles(self)
            except Exception as e:
                log(f"LLM titles failed ({e}); using default text", level="warn")
        return pl.build(self.cfg, media, res, music, assignments=assignments, text=text, captions=caps)

    def make_all(self, preview=False, preview_only=False, graded=False, rescan=False):
        """The whole pipeline (same as `vlog make`). Returns list of output files."""
        import time
        t0 = time.time()
        cfg = self.cfg
        self.scan(force=rescan)
        self.analyze(force=rescan)
        if cfg["llm"]["enabled"]:
            try:
                self.llm().list_models()
            except Exception as e:
                log(f"local model not reachable ({e}); continuing in automatic mode", level="warn")
                cfg["llm"]["enabled"] = False
        if cfg["llm"]["enabled"] and "orientation" in cfg["llm"].get("use_for", []):
            self.llm_orientation()
        if cfg["llm"]["enabled"] and "captions" in cfg["llm"].get("use_for", []):
            self.caption()
        self.make_music(force=rescan)
        self.make_plan()
        if preview_only:
            return [self.render_preview()]
        outs = [self.render_preview()] if preview else []
        outs += self.render_final()
        outs += self.export_luts()
        if graded or cfg["output"].get("export_graded_clips"):
            self.export_graded()
        log(f"done in {(time.time() - t0) / 60:.1f} min", level="ok")
        return outs

    def free_llm_memory(self):
        """Unload the local model before rendering so the GPU encoder has room (the model stays
        loaded otherwise, and on an 8 GB card several 4K NVENC encodes then run out of memory)."""
        lc = self.cfg["llm"]
        if lc["enabled"] and lc.get("unload_before_render", True):
            self.llm().unload()

    def render_preview(self):
        plan = self.plan or self.make_plan()
        self.free_llm_memory()
        return rd.render_preview(self.cfg, self.scan(), self.analysis, plan)

    def render_final(self):
        plan = self.plan or self.make_plan()
        self.free_llm_memory()
        outs = rd.render_final(self.cfg, self.scan(), self.analysis, plan)
        if self.cfg["output"].get("export_graded_clips"):
            rd.export_graded_clips(self.cfg, self.scan(), self.analysis)
        return outs

    def export_graded(self):
        self.free_llm_memory()
        return rd.export_graded_clips(self.cfg, self.scan(), self.analyze())

    def export_luts(self):
        return rd.export_luts(self.cfg, self.scan())

    def status(self):
        a = self.analysis
        st = {
            "media_folder": self.cfg.media_folder,
            "output_folder": self.cfg.output_folder,
            "scanned": self.media is not None,
            "items": len(self.media or []),
            "analyzed": a is not None,
            "usable": sum(1 for r in (a or {}).values() if r.get("usable")),
            "orientation_fixed": sum(1 for r in (a or {}).values() if r.get("orientation_fixed")),
            "orientation_suspect": [k for k, r in (a or {}).items() if r.get("orientation_suspect")],
            "captions": len(self.captions),
            "music": bool(self.music),
            "plan_shots": len((self.plan or {}).get("shots", [])),
            "preview": os.path.exists(os.path.join(self.cfg.output_folder, "preview.mp4")),
            "final": sorted(f for f in os.listdir(self.cfg.output_folder) if f.endswith(".mp4") and f != "preview.mp4")
            if os.path.isdir(self.cfg.output_folder) else [],
            "llm": {"enabled": self.cfg["llm"]["enabled"], "model": self.cfg["llm"]["model"],
                    "base_url": self.cfg["llm"]["base_url"]},
        }
        return st
