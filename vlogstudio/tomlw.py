"""Minimal TOML writer for Vlog Studio configs (the app UI saves settings with it)."""
import json

ORDER = ["project", "llm", "look", "orientation", "analysis", "music", "output", "titles"]


def _val(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    if v is None:
        return '""'
    return json.dumps(str(v), ensure_ascii=False)


def _key(k):
    return k if k.replace("_", "").replace("-", "").isalnum() else json.dumps(k)


def dumps(cfg):
    out = ["# Vlog Studio settings (saved by the app; you can still edit this file by hand)", ""]
    for sec in ORDER + [k for k in cfg if k not in ORDER and k not in ("places", "overrides")]:
        body = cfg.get(sec)
        if not isinstance(body, dict):
            continue
        out.append(f"[{sec}]")
        subs = []
        for k, v in body.items():
            if isinstance(v, dict):
                subs.append((k, v))
            else:
                out.append(f"{_key(k)} = {_val(v)}")
        for k, v in subs:
            out += ["", f"[{sec}.{_key(k)}]"] + [f"{_key(a)} = {_val(b)}" for a, b in v.items()]
        out.append("")
    for p in cfg.get("places") or []:
        out.append("[[places]]")
        out += [f"{_key(a)} = {_val(b)}" for a, b in p.items()]
        out.append("")
    for cid, ov in (cfg.get("overrides") or {}).items():
        if ov:
            out.append(f"[overrides.{json.dumps(cid)}]")
            out += [f"{_key(a)} = {_val(b)}" for a, b in ov.items()]
            out.append("")
    return "\n".join(out)
