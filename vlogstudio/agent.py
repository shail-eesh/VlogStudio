"""Tool-calling agent loop driven by a local model.

tool_mode:
  native : OpenAI-style `tools` / `tool_calls` (Ollama, LM Studio, llama.cpp --jinja, vLLM)
  json   : the model replies with {"tool": ..., "args": {...}} or {"final": "..."} (any model)
  auto   : try native; switch to json if the server/model doesn't support tools
"""
import json
import os
import time

from . import tools as T
from .creative import prompt
from .llm import LLMError, extract_json
from .util import log

JSON_PROTOCOL = """
## How to call tools
Reply with exactly ONE JSON object and nothing else:
{"tool": "<tool name>", "args": {...}}
When everything is finished reply with:
{"final": "<short summary for the user>"}

Available tools (name: description; parameters):
{{tools}}
"""


def _tool_list_text():
    out = []
    for name, t in T.REGISTRY.items():
        f = t["schema"]["function"]
        params = ", ".join(f"{k}:{v.get('type', 'any')}" for k, v in f["parameters"]["properties"].items())
        out.append(f"- {name}: {f['description']} ({params or 'no args'})")
    return "\n".join(out)


def _project_summary(proj):
    c = proj.cfg
    pj = c["project"]
    return (f"media folder: {c.media_folder}\noutput folder: {c.output_folder}\n"
            f"title: {pj.get('title') or pj.get('name')}, target duration: {pj['target_duration']}s, "
            f"look: {c['look']['preset']}, music: {c['music']['mode']}/{c['music'].get('preset')}, "
            f"output: {c['output']['resolution']} letterbox {c['output']['letterbox']}")


def run(proj, goal, max_steps=40):
    llm = proj.llm()
    mode = llm.tool_mode if llm.tool_mode in ("native", "json") else "native"
    skills = ", ".join(n for n, _ in T.skill_index())
    system = prompt("system_agent.md", skills=skills, project=_project_summary(proj))
    log_path = proj.p("agent_log.jsonl")

    def sys_msg():
        s = system
        if mode == "json":
            s += prompt_inline(JSON_PROTOCOL, tools=_tool_list_text())
        return {"role": "system", "content": s}

    messages = [sys_msg(), {"role": "user", "content": goal}]
    tools_schema = T.schemas()
    nudges = 0
    for step in range(1, max_steps + 1):
        try:
            if mode == "native":
                msg = llm.chat(messages, tools=tools_schema)
            else:
                msg = llm.chat(messages, json_out=True)
        except LLMError as e:
            if mode == "native" and llm.tool_mode == "auto" and ("tool" in str(e).lower() or "HTTP 4" in str(e)):
                log("server/model rejected native tool calling; switching to JSON tool protocol", level="warn")
                mode = "json"
                messages[0] = sys_msg()
                continue
            raise
        _log(log_path, {"step": step, "mode": mode, "assistant": msg})
        calls = _parse_calls(msg, mode)
        if mode == "native" and not calls and llm.tool_mode == "auto":
            # some models print a JSON tool call as text instead of using tool_calls
            try:
                obj = extract_json(msg.get("content") or "")
                if isinstance(obj, dict) and "tool" in obj:
                    mode = "json"
                    messages[0] = sys_msg()
                    calls = [(None, obj["tool"], obj.get("args") or {})]
            except ValueError:
                pass
        if not calls:
            final = _final_text(msg, mode)
            if final is None and nudges < 2:
                nudges += 1
                messages += [{"role": "assistant", "content": msg.get("content") or ""},
                             {"role": "user", "content": "Continue: call the next tool, or reply with the final summary "
                                                         "if the video is rendered."}]
                continue
            log("agent finished", level="ok")
            print("\n" + (final or msg.get("content") or "").strip() + "\n")
            return final
        nudges = 0
        if mode == "native":
            messages.append({"role": "assistant", "content": msg.get("content") or "",
                             "tool_calls": msg.get("tool_calls")})
        else:
            messages.append({"role": "assistant", "content": msg.get("content") or ""})
        for call_id, name, args in calls:
            log(f"agent → {name}({json.dumps(args)[:160]})", level="step")
            t0 = time.time()
            res = T.call(proj, name, args)
            text = res.text if len(res.text) < 12000 else res.text[:12000] + "\n…(truncated)"
            _log(log_path, {"step": step, "tool": name, "args": args, "result": text[:4000],
                            "images": res.images, "secs": round(time.time() - t0, 1)})
            if mode == "native":
                messages.append({"role": "tool", "tool_call_id": call_id or name, "name": name, "content": text})
            else:
                messages.append({"role": "user", "content": f"TOOL RESULT ({name}):\n{text}"})
            if res.images:
                if llm.vision:
                    messages.append(llm.user(f"Images returned by {name} (in order): "
                                             + ", ".join(os.path.basename(p) for p in res.images), res.images))
                else:
                    messages.append({"role": "user", "content": f"({name} produced {len(res.images)} images, but this "
                                                                f"model has no vision; rely on the text.)"})
        messages = _trim(messages)
    log("agent stopped: step limit reached", level="warn")
    return None


def prompt_inline(t, **vals):
    for k, v in vals.items():
        t = t.replace("{{" + k + "}}", v)
    return t


def _parse_calls(msg, mode):
    if mode == "native":
        out = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    args = {}
            out.append((tc.get("id"), fn.get("name"), args))
        return out
    try:
        obj = extract_json(msg.get("content") or "")
    except ValueError:
        return []
    if isinstance(obj, dict) and obj.get("tool"):
        return [(None, obj["tool"], obj.get("args") or {})]
    return []


def _final_text(msg, mode):
    c = msg.get("content") or ""
    try:
        obj = extract_json(c)
        if isinstance(obj, dict) and "final" in obj:
            return str(obj["final"])
    except ValueError:
        pass
    if mode == "native" and c.strip():
        return c
    return None


def _trim(messages, keep_images=2, max_msgs=60):
    """Keep context small for local models: drop old images and very old turns."""
    img_msgs = [i for i, m in enumerate(messages) if isinstance(m.get("content"), list)]
    for i in img_msgs[:-keep_images]:
        texts = [p["text"] for p in messages[i]["content"] if p.get("type") == "text"]
        messages[i] = {"role": messages[i]["role"], "content": " ".join(texts) + " (image removed to save context)"}
    if len(messages) > max_msgs:
        head = messages[:2]
        tail = messages[-(max_msgs - 2):]
        while tail and tail[0].get("role") == "tool":  # don't start with an orphan tool result
            tail = tail[1:]
        messages = head + tail
    return messages


def _log(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, default=str)[:20000] + "\n")
