"""Configuration: defaults merged with vlog.toml."""
import copy
import os

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

DEFAULTS = {
    "project": {
        "name": "My Trip",
        "media_folder": "..",
        "output_folder": "../Vlog_Studio_Output",
        "include_images": True,
        "exclude": [],
        "target_duration": 68,
        "title": "",
        "title_script": "welcome to",
        "subtitle": "",
        "hook": "",
        "hook_sub": "",
        "end_line": "you were a vibe.",
        "end_sub": "SEE YOU AGAIN SOON",
        "chapter_cards": True,
        "location_tags": True,
        "location_sub": "",
        "style": "travel",
        "pacing": "viral",
        "hook_template": "{span} IN {title}",
    },
    "llm": {
        "enabled": False,
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5vl:7b",
        "vision": True,
        "api_key": "",
        "temperature": 0.4,
        "max_tokens": 4096,
        "timeout": 600,
        "tool_mode": "auto",
        "json_mode": True,
        "max_image_px": 1344,
        "images_first": True,
        "unload_before_render": True,
        "use_for": ["orientation", "captions", "storyboard", "titles"],
    },
    "look": {
        "preset": "golden_day",
        "strength": 1.0,
        "auto_exposure": True,
        "target_exposure": 0.17,
        "max_stops": 2.2,
        "min_stops": -1.5,
        "auto_white_balance": True,
        "lut_size": 33,
        "overrides": {},
    },
    "orientation": {
        "mode": "auto",
        "portrait_fill": "blur",
        "confidence": 0.06,
    },
    "analysis": {
        "sample_interval": 1.5,
        "skip_start": 2.0,
        "workers": 6,
        "smart_moments": False,
        "long_clip_chunk": 24,
        "max_moments_per_clip": 4,
    },
    "music": {
        "mode": "generate",
        "file": "",
        "preset": "tropical_house",
        "bpm": 0,
        "key": "",
        "seed": 7,
        "ambient_level": 1.0,
    },
    "output": {
        "resolution": "3840x2160",
        "preview_resolution": "1280x720",
        "fps": 24,
        "letterbox": 2.39,
        "grain": 3,
        "vignette": True,
        "flashes": True,
        "crf": 17,
        "x264_preset": "medium",
        "make_1080p": True,
        "export_graded_clips": False,
        "render_workers": 3,
        "basename": "Travel_Vlog",
        "encoder": "auto",
        "hw_decode": "auto",
    },
    "titles": {
        "font_display": "/System/Library/Fonts/Supplemental/Futura.ttc:2",
        "font_light": "/System/Library/Fonts/Supplemental/Futura.ttc:0",
        "font_heavy": "/System/Library/Fonts/Avenir Next.ttc:8",
        "font_medium": "/System/Library/Fonts/Avenir Next.ttc:2",
        "font_script": "/System/Library/Fonts/Supplemental/SnellRoundhand.ttc:1",
        "color_main": [255, 246, 228],
        "color_accent": [255, 214, 140],
    },
    "places": [],
    "overrides": {},
}


# Video-type presets. Applied on top of the defaults; anything set in vlog.toml still wins.
STYLES = {
    "travel": {},
    "wedding_event": {
        "project": {"pacing": "cinematic", "title_script": "celebrating", "hook_template": "THE BEST DAY EVER",
                    "hook_sub": "HIGHLIGHTS", "end_line": "forever starts now.", "end_sub": "THANK YOU FOR CELEBRATING WITH US",
                    "chapter_cards": False, "location_tags": False},
        "look": {"preset": "golden_day", "strength": 0.75},
        "music": {"preset": "cinematic_pop", "ambient_level": 0.6},
    },
    "family": {
        "project": {"pacing": "viral", "title_script": "our", "hook_template": "MAKING MEMORIES",
                    "hook_sub": "FAMILY TIME", "end_line": "love you all.", "end_sub": "UNTIL NEXT TIME"},
        "look": {"preset": "golden_day"},
        "music": {"preset": "upbeat_pop"},
    },
    "event_recap": {
        "project": {"pacing": "viral", "title_script": "recap", "hook_template": "WHAT A NIGHT", "hook_sub": "",
                    "end_line": "see you next year.", "end_sub": "", "chapter_cards": False},
        "look": {"preset": "vivid_pop"},
        "music": {"preset": "upbeat_pop"},
    },
    "real_estate": {
        "project": {"pacing": "documentary", "title_script": "welcome home", "hook_template": "JUST LISTED",
                    "hook_sub": "", "end_line": "book a viewing.", "end_sub": "", "chapter_cards": False,
                    "location_tags": False},
        "look": {"preset": "natural"},
        "music": {"preset": "chill_lofi", "ambient_level": 0.0},
        "output": {"letterbox": 0, "grain": 0, "vignette": False, "flashes": False},
    },
    "product": {
        "project": {"pacing": "cinematic", "title_script": "introducing", "hook_template": "MEET {title}",
                    "hook_sub": "", "end_line": "available now.", "end_sub": "", "chapter_cards": False,
                    "location_tags": False},
        "look": {"preset": "vivid_pop"},
        "music": {"preset": "upbeat_pop", "ambient_level": 0.0},
        "output": {"letterbox": 0, "grain": 0},
    },
}


class Config(dict):
    """dict with the file location attached; nested sections are plain dicts."""

    path = None
    root = None

    def resolve(self, p):
        """Resolve a path relative to the config file's folder."""
        if not p:
            return p
        p = os.path.expanduser(p)
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(self.root, p))

    @property
    def media_folder(self):
        return self.resolve(self["project"]["media_folder"])

    @property
    def output_folder(self):
        return self.resolve(self["project"]["output_folder"])

    @property
    def work_folder(self):
        return os.path.join(self.output_folder, "work")


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict) and k not in ("overrides",):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(path=None, overrides=None):
    cfg = Config(copy.deepcopy(DEFAULTS))
    if path is None:
        here = os.getcwd()
        cand = os.path.join(here, "vlog.toml")
        path = cand if os.path.exists(cand) else None
    if path:
        with open(path, "rb") as f:
            user = tomllib.load(f)
        style = (user.get("project") or {}).get("style") or (overrides or {}).get("project.style") or "travel"
        _merge(cfg, copy.deepcopy(STYLES.get(style, {})))
        _merge(cfg, user)
        cfg.path = os.path.abspath(path)
        cfg.root = os.path.dirname(cfg.path)
    else:
        cfg.root = os.getcwd()
    for dotted, value in (overrides or {}).items():
        sec, _, key = dotted.partition(".")
        cfg.setdefault(sec, {})[key] = value
    return cfg


def parse_res(s):
    w, h = str(s).lower().split("x")
    return int(w), int(h)
