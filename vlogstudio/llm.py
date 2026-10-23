"""Client for a *local* OpenAI-compatible chat server.

Works with: Ollama (http://localhost:11434/v1), LM Studio (http://localhost:1234/v1),
llama.cpp `llama-server` (http://localhost:8080/v1), MLX-LM server, vLLM, Jan, etc.
Only the Python standard library is used; nothing leaves your machine unless you point
base_url somewhere else.
"""
import base64
import io
import json
import re
import time
import urllib.error
import urllib.request

from PIL import Image

from .util import log


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg):
        c = cfg["llm"]
        self.base = c["base_url"].rstrip("/")
        self.model = c["model"]
        self.key = c.get("api_key") or ""
        self.temperature = float(c.get("temperature", 0.4))
        self.max_tokens = int(c.get("max_tokens", 4096))
        self.timeout = int(c.get("timeout", 600))
        self.vision = bool(c.get("vision", True))
        self.json_mode = bool(c.get("json_mode", True))
        self.max_px = int(c.get("max_image_px", 1344))
        self.tool_mode = c.get("tool_mode", "auto")
        self.images_first = bool(c.get("images_first", True))
        self.calls = 0

    # ---------- transport ----------
    def _post(self, path, payload):
        req = urllib.request.Request(self.base + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {self.key}"} if self.key else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:800]
            raise LLMError(f"HTTP {e.code} from {self.base}{path}: {body}") from None
        except urllib.error.URLError as e:
            raise LLMError(f"cannot reach local model server at {self.base} ({e.reason}). "
                           f"Is Ollama / LM Studio running?") from None

    def _get(self, path):
        req = urllib.request.Request(self.base + path,
                                     headers={"Authorization": f"Bearer {self.key}"} if self.key else {})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())

    def list_models(self):
        try:
            return [m.get("id") for m in self._get("/models").get("data", [])]
        except Exception as e:
            raise LLMError(f"cannot list models at {self.base}: {e}") from None

    # ---------- messages ----------
    def image_part(self, path):
        im = Image.open(path).convert("RGB")
        im.thumbnail((self.max_px, self.max_px))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return {"type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}}

    def user(self, text, images=()):
        if images and self.vision:
            # images before the text: recommended for Gemma 4 and the usual layout for Qwen-VL / LLaVA
            imgs = [self.image_part(p) for p in images]
            txt = [{"type": "text", "text": text}]
            return {"role": "user", "content": imgs + txt if self.images_first else txt + imgs}
        return {"role": "user", "content": text}

    def chat(self, messages, tools=None, json_out=False, temperature=None):
        payload = {"model": self.model, "messages": messages, "stream": False,
                   "temperature": self.temperature if temperature is None else temperature,
                   "max_tokens": self.max_tokens}
        if tools:
            payload["tools"] = tools
        if json_out and self.json_mode and not tools:
            payload["response_format"] = {"type": "json_object"}
        t0 = time.time()
        try:
            r = self._post("/chat/completions", payload)
        except LLMError as e:
            if "response_format" in payload and "HTTP 4" in str(e):
                self.json_mode = False  # server doesn't support it; fall back to prompt-only JSON
                payload.pop("response_format")
                r = self._post("/chat/completions", payload)
            else:
                raise
        self.calls += 1
        try:
            msg = r["choices"][0]["message"]
        except (KeyError, IndexError):
            raise LLMError(f"unexpected response: {str(r)[:400]}") from None
        log(f"  llm {self.model} replied in {time.time() - t0:.1f}s")
        return msg

    # ---------- structured output ----------
    def ask_json(self, system, text, images=(), retries=2, validate=None):
        """Ask for a JSON object; repairs/retries on invalid output. Returns parsed object."""
        msgs = [{"role": "system", "content": system}, self.user(text, images)]
        last_err = None
        for attempt in range(retries + 1):
            msg = self.chat(msgs, json_out=True, temperature=self.temperature if attempt == 0 else 0.2)
            content = msg.get("content") or ""
            try:
                obj = extract_json(content)
                if validate:
                    validate(obj)
                return obj
            except (ValueError, KeyError, TypeError) as e:
                last_err = e
                msgs += [{"role": "assistant", "content": content[:4000]},
                         {"role": "user", "content": f"That was not valid ({e}). Reply again with ONLY the JSON "
                                                     f"object, no prose, no code fences."}]
        raise LLMError(f"model did not return valid JSON after {retries + 1} tries: {last_err}")


def extract_json(text):
    """Pull the first JSON object/array out of a model reply (handles ```json fences and chatter)."""
    if text is None:
        raise ValueError("empty reply")
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()  # reasoning models (Qwen3 etc.)
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start = min([i for i in (t.find("{"), t.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON found in reply")
    opener = t[start]
    closer = "}" if opener == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                frag = t[start:i + 1]
                try:
                    return json.loads(frag)
                except json.JSONDecodeError:
                    return json.loads(re.sub(r",\s*([}\]])", r"\1", frag))  # trailing commas
    raise ValueError("unterminated JSON in reply")
