"""A fake OpenAI-compatible local model server for testing the LLM plumbing offline.

It answers like a (very obedient) vision model: captions, orientation, storyboard, titles,
and scripted agent tool calls (native `tool_calls` or the JSON protocol).
Every request is appended to `requests.jsonl` so tests can inspect what was sent.

    python tests/mock_llm_server.py --port 11999 [--no-tools]
"""
import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AGENT_SCRIPT = [
    ("project_status", {}),
    ("scan_media", {}),
    ("analyze_media", {}),
    ("view_overview", {"page": 1}),
    ("read_skill", {"name": "viral-travel-edit"}),
    ("get_slots", {}),
    ("get_catalog", {}),
    ("set_storyboard", None),   # filled from the slots/catalog seen so far
    ("render_preview", {}),
]


def _text(msg):
    c = msg.get("content")
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if p.get("type") == "text")
    return c or ""


def _images(messages):
    return sum(1 for m in messages if isinstance(m.get("content"), list)
               for p in m["content"] if p.get("type") == "image_url")


class State:
    log_path = "requests.jsonl"
    native_tools = True
    lock = threading.Lock()


def reply_json(obj):
    return {"role": "assistant", "content": "Sure! ```json\n" + json.dumps(obj) + "\n```"}


def handle_chat(req):
    msgs = req["messages"]
    system = _text(msgs[0]) if msgs and msgs[0]["role"] == "system" else ""
    user = "\n".join(_text(m) for m in msgs if m["role"] == "user")
    if "precise film assistant" in system:
        ts = [float(x) for x in re.findall(r"duration=([\d.]+)s", user)] or [0]
        d = ts[0]
        part = re.search(r"covers only t=([\d.]+)s to ([\d.]+)s", user)
        t0, t1 = (float(part.group(1)), float(part.group(2))) if part else (0.0, d)
        return reply_json({"description": f"people walking past a landmark ({t0:.0f}-{t1:.0f}s)", "subjects": ["people"],
                           "place": "Liberty Island" if "C01" in user else "", "category": "landmark",
                           "mood": "joyful", "energy": "mid", "quality": 4, "hero": True,
                           "best_moments": [{"t": min(t0 + 1.0, max(d - 1, 0)), "dur": 6 if part else 2, "why": "clean frame"}],
                           "avoid": [{"t": t0 + 4.0, "why": "shaky"}] if part else [], "upright": True})
    if "check image orientation" in system:
        ids = re.search(r"Clip ids on this sheet: (.*)", user).group(1).split(", ")
        return reply_json({i: 0 for i in ids})
    if "expert video editor" in system:
        slots = re.findall(r"^(\d+) \| ([\d.]+)s \| ([\d.]+)s \| (\w+) \| (\w+)$", user, re.M)
        cat = re.findall(r"^(\S+) \| (video|image) \| ([\d.]+s|photo) \|", user, re.M)
        shots = []
        for k, (i, st, dur, sec, role) in enumerate(slots):
            cid, kind, d = cat[k % len(cat)]
            shots.append({"slot": int(i), "clip": cid, "in": 99999 if k == 1 else 0.5})  # slot 1: bad in-point
        shots.append({"slot": 0, "clip": "NOT_A_CLIP", "in": 1})  # invalid entry: must be repaired
        return reply_json({"shots": shots, "text": {"hook": {"line1": "MOCK HOOK", "line2": "TESTING LOCAL AI"},
                                                    "title": {"main": "MOCKVILLE"}}})
    if "short, punchy video titles" in system:
        return reply_json({"hook": {"line1": "MOCK TITLES"}, "title": {"main": "MOCK"}})
    # ---- agent ----
    n_results = sum(1 for m in msgs if m["role"] == "tool" or (m["role"] == "user" and _text(m).startswith("TOOL RESULT")))
    if n_results >= len(AGENT_SCRIPT):
        return {"role": "assistant", "content": json.dumps({"final": "Mock agent finished: preview rendered."})}
    name, args = AGENT_SCRIPT[n_results]
    if args is None:
        slots_txt = next((_text(m) for m in msgs if "| intro |" in _text(m) or "| intro | hook" in _text(m)), "")
        n = len(re.findall(r"^\d+ \|", slots_txt, re.M)) or 3
        args = {"storyboard": {"shots": [{"slot": i} for i in range(min(n, 3))],
                               "text": {"hook": {"line1": "AGENT HOOK"}}}}
    if State.native_tools and req.get("tools"):
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": f"call_{n_results}", "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args)}}]}
    return {"role": "assistant", "content": json.dumps({"tool": name, "args": args})}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.endswith("/models"):
            return self._send(200, {"object": "list", "data": [{"id": "mock-vl", "object": "model"}]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with State.lock:
            with open(State.log_path, "a") as f:
                f.write(json.dumps({"path": self.path, "model": req.get("model"), "n_messages": len(req["messages"]),
                                    "images": _images(req["messages"]), "tools": len(req.get("tools") or []),
                                    "response_format": req.get("response_format")}) + "\n")
        if req.get("tools") and not State.native_tools:
            return self._send(400, {"error": {"message": "this model does not support tools"}})
        msg = handle_chat(req)
        self._send(200, {"id": "mock", "object": "chat.completion", "model": req.get("model"),
                         "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}]})


def serve(port, log_path, native_tools=True):
    State.log_path, State.native_tools = log_path, native_tools
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11999)
    ap.add_argument("--log", default="requests.jsonl")
    ap.add_argument("--no-tools", action="store_true")
    a = ap.parse_args()
    State.log_path, State.native_tools = a.log, not a.no_tools
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
