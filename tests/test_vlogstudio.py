"""Tests for Vlog Studio.

    .venv/bin/python -m unittest discover -s tests -v                       # fast unit tests
    VLOG_INTEGRATION=1 .venv/bin/python -m unittest discover -s tests -v    # + full pipeline with a mock local LLM
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from vlogstudio import color, config, music, plan  # noqa: E402
from vlogstudio.llm import extract_json  # noqa: E402


def _read(p):
    with open(p) as f:
        return f.read()


class TestJSON(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})
        self.assertEqual(extract_json('Sure!\n```json\n{"a": [1, 2]}\n```\nhope this helps'), {"a": [1, 2]})
        self.assertEqual(extract_json('<think>hmm {not json}</think>{"ok": true}'), {"ok": True})
        self.assertEqual(extract_json('result: {"a": {"b": "x}y"}, "c": 2,} trailing'), {"a": {"b": "x}y"}, "c": 2})
        self.assertEqual(extract_json('[{"slot": 0}]'), [{"slot": 0}])
        with self.assertRaises(ValueError):
            extract_json("no json here")


class TestLLMMessages(unittest.TestCase):
    def test_images_before_text(self):
        from PIL import Image
        from vlogstudio.llm import LLM
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.jpg")
            Image.new("RGB", (64, 36), (10, 20, 30)).save(p)
            cfg = config.load(os.path.join(ROOT, "vlog.example.toml"))
            kinds = [part["type"] for part in LLM(cfg).user("describe", [p, p])["content"]]
            self.assertEqual(kinds, ["image_url", "image_url", "text"])
            cfg["llm"]["images_first"] = False
            kinds = [part["type"] for part in LLM(cfg).user("describe", [p])["content"]]
            self.assertEqual(kinds, ["text", "image_url"])


class TestColor(unittest.TestCase):
    def test_applelog_mid_grey(self):
        # Apple Log encodes 18% grey at ~0.488
        self.assertAlmostEqual(float(color.applelog_decode(0.4883)), 0.18, delta=0.003)
        self.assertAlmostEqual(float(color.applelog_decode(color._PT)), 0.01, delta=1e-3)

    def test_hlg_pq_scale(self):
        self.assertAlmostEqual(float(color.hlg_decode(0.38)), 0.18, delta=0.01)
        self.assertAlmostEqual(float(color.pq_decode(0.58)), 1.0, delta=0.08)  # ~203 nits

    def test_grade_monotonic_and_bounded(self):
        for prof in ("apple_log", "hlg", "pq", "bt709", "srgb"):
            for name in color.PRESETS:
                p = color.look_params({"preset": name})
                ramp = np.linspace(0, 1, 64)[:, None].repeat(3, 1)
                out = color.grade(ramp, prof, p)
                y = out @ color.LUMA709
                self.assertTrue(np.all(out >= 0) and np.all(out <= 1), (prof, name))
                self.assertTrue(np.all(np.diff(y) >= -1e-3), f"non-monotonic grey ramp {prof}/{name}")

    def test_cube_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.cube")
            color.write_cube(p, "apple_log", color.look_params({"preset": "golden_day"}), size=9)
            lines = open(p).read().splitlines()
            self.assertIn("LUT_3D_SIZE 9", lines)
            self.assertEqual(len([l for l in lines if l and l[0].isdigit()]), 9 ** 3)

    def test_auto_balance(self):
        dark = [np.full((20, 20, 3), 0.30)]  # under-exposed Apple Log grey
        stops, wb, info = color.auto_balance(dark, "apple_log", {"target_exposure": 0.17})
        self.assertGreater(stops, 0.5)
        blank = [np.zeros((20, 20, 3))]
        self.assertTrue(color.auto_balance(blank, "bt709", {})[2].get("black"))


class TestMusic(unittest.TestCase):
    def test_structure(self):
        for bars in (8, 12, 20, 34, 50):
            self.assertEqual(sum(n for _, n in music.structure_bars(bars)), bars)

    def test_tempo_detection(self):
        sr, bpm, dur = 44100, 100.0, 20
        x = np.zeros(sr * dur)
        for i in range(int(dur * bpm / 60)):
            k = int(i * 60 / bpm * sr)
            x[k:k + 2000] += np.sin(2 * np.pi * 60 * np.arange(2000) / sr) * np.exp(-np.arange(2000) / 300)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "click.wav")
            from scipy.io import wavfile
            wavfile.write(p, sr, (x * 20000).astype(np.int16))
            a = music.analyze_track(p)
        self.assertAlmostEqual(a["bpm"], bpm, delta=0.5)
        err = np.abs(((np.array(a["beats"]) + 0.3) % 0.6) - 0.3).mean()
        self.assertLess(err, 0.02)


class TestPlan(unittest.TestCase):
    def _music(self, total=68.0, bpm=120):
        beat = 60 / bpm
        lay = music.structure_bars(int(total / (4 * beat)))
        secs, t = [], 0.0
        en = {"intro": "low", "build": "mid", "build2": "mid", "drop": "high", "drop2": "high", "break": "low",
              "outro": "low"}
        for n, b in lay:
            secs.append({"name": n, "start": t, "end": t + b * 4 * beat, "energy": en[n]})
            t += b * 4 * beat
        return {"duration": t, "bpm": bpm, "beats": [i * beat for i in range(int(t / beat) + 1)], "sections": secs,
                "wav": "x.wav"}

    def test_slots_cover_timeline(self):
        for total, bpm in ((68, 120), (45, 100), (90, 86), (30, 124)):
            mu = self._music(total, bpm)
            slots = plan.make_slots(mu)
            self.assertAlmostEqual(slots[-1]["start"] + slots[-1]["dur"], mu["duration"], places=3)
            for a, b in zip(slots, slots[1:]):
                self.assertAlmostEqual(a["start"] + a["dur"], b["start"], places=3)
            self.assertEqual(slots[0]["role"], "hook")
            self.assertEqual(slots[-1]["role"], "closer")
            self.assertTrue(all(s["dur"] >= 0.2 for s in slots))
            self.assertIn("title", [s["role"] for s in slots])

    def test_pacing_styles(self):
        for pacing in plan.PACING_NAMES:
            for total, bpm in ((68, 120), (45, 100), (90, 86)):
                mu = self._music(total, bpm)
                slots = plan.make_slots(mu, pacing)
                self.assertAlmostEqual(slots[-1]["start"] + slots[-1]["dur"], mu["duration"], places=3)
                self.assertEqual(slots[-1]["role"], "closer")
                self.assertTrue(all(s["dur"] >= 0.4 for s in slots), (pacing, [s["dur"] for s in slots]))
        mu = self._music(90, 120)
        self.assertGreaterEqual(max(s["dur"] for s in plan.make_slots(mu, "documentary")), 8.0)
        self.assertLessEqual(max(s["dur"] for s in plan.make_slots(mu, "viral") if s["role"] == "story"), 5.5)
        self.assertGreater(len(plan.make_slots(mu, "viral")), 2 * len(plan.make_slots(mu, "documentary")))

    def test_smart_moments(self):
        cfg = config.load(os.path.join(ROOT, "vlog.example.toml"))
        cfg.root = tempfile.mkdtemp()
        cfg["project"]["output_folder"] = cfg.root
        cfg["analysis"]["smart_moments"] = True
        media, analysis = self._fake()
        caps = {m["id"]: {"quality": 4, "best_moments": [{"t": 2.5, "dur": 6.0}, {"t": 9.0, "dur": 1.5}],
                          "avoid": [{"t": 6.0}]} for m in media}
        mu = self._music()
        p = plan.build(cfg, media, analysis, mu, captions=caps)
        for s in p["shots"]:
            if s["role"] == "story":
                self.assertFalse(plan._in_avoid(s["in"], s["dur"], [(5.0, 7.0)]), s)
        # an LLM proposal inside an avoid range gets moved
        bad = {i: {"clip": "clip07", "in": 5.5} for i in range(3)}
        p2 = plan.build(cfg, media, analysis, mu, assignments=bad, captions=caps)
        self.assertTrue(any("model flagged" in w for w in p2["warnings"]), p2["warnings"])
        shutil.rmtree(cfg.root, ignore_errors=True)

    def test_merge_captions(self):
        from vlogstudio.creative import _merge_captions
        a = {"description": "a", "quality": 3, "best_moments": [{"t": 1}, {"t": 2}], "avoid": [{"t": 5}], "place": ""}
        b = {"description": "b", "quality": 5, "best_moments": [{"t": 30}], "avoid": [], "place": "Park"}
        m = _merge_captions([a, b], 3)
        self.assertEqual([x["t"] for x in m["best_moments"]], [30, 1, 2])
        self.assertEqual(m["place"], "Park")
        self.assertEqual(m["quality"], 5)
        self.assertEqual(len(m["avoid"]), 1)

    def _fake(self, n=12):
        media, analysis = [], {}
        for i in range(n):
            mid = f"clip{i:02d}"
            media.append({"id": mid, "kind": "video", "duration": 6.0 + i % 4 * 5, "created": f"2024-10-2{2 + i // 7}T1{i % 10}:00:00",
                          "index": i, "lat": None, "lon": None})
            analysis[mid] = {"usable": True, "quality": 0.5, "rotate_cw": 0, "rot_scores": [0.3, 0, 0, 0],
                             "frames": [{"t": t, "score": 0.3 + 0.05 * ((i + t) % 5)} for t in np.arange(2, 5.5 + i % 4 * 5, 1.5)]}
        return media, analysis

    def test_heuristic_and_repair(self):
        cfg = config.load(os.path.join(ROOT, "vlog.example.toml"))
        cfg.root = tempfile.mkdtemp()
        cfg["project"]["output_folder"] = cfg.root
        media, analysis = self._fake()
        mu = self._music()
        p = plan.build(cfg, media, analysis, mu)
        byid = {m["id"]: m for m in media}
        for s in p["shots"]:
            self.assertLessEqual(s["in"] + s["dur"] * s["speed"], byid[s["clip"]]["duration"] - 0.05, s)
        self.assertIn("day2", [o["title"] for o in p["overlays"]])
        bad = {0: {"clip": "nope"}, 1: {"clip": "clip00", "in": 999}, 2: {"clip": "clip03", "in": 1.0, "speed": 9}}
        p2 = plan.build(cfg, media, analysis, mu, assignments=bad)
        self.assertTrue(any("unknown" in w for w in p2["warnings"]))
        for s in p2["shots"]:
            self.assertLessEqual(s["in"] + s["dur"] * s["speed"], byid[s["clip"]]["duration"] - 0.05, s)
            self.assertLessEqual(s["speed"], 2.0)
        shutil.rmtree(cfg.root, ignore_errors=True)


class TestPortability(unittest.TestCase):
    """Windows / NVIDIA code paths (exercised on every OS)."""

    def test_concat_and_font_paths(self):
        from vlogstudio import util
        from vlogstudio.titles import _split
        self.assertEqual(util.concat_line("C:\\Trips\\Sarah's day\\a.mov"), "file 'C:/Trips/Sarah'\\''s day/a.mov'\n")
        self.assertEqual(_split("C:/Windows/Fonts/seguibl.ttf"), ("C:/Windows/Fonts/seguibl.ttf", 0))
        self.assertEqual(_split("/System/Library/Fonts/Avenir Next.ttc:8"), ("/System/Library/Fonts/Avenir Next.ttc", 8))

    def test_encoder_choice(self):
        from vlogstudio import render
        cfg = config.load(os.path.join(ROOT, "vlog.example.toml"))
        orig = render.gpu_kind
        try:
            render.gpu_kind = lambda: "nvidia"
            self.assertIn("h264_nvenc", render._final_enc(cfg))
            self.assertIn("hevc_nvenc", render._venc(3840, 2160, True, cfg))
            self.assertEqual(render._hw_decode(cfg, {"kind": "video", "codec": "hevc"}),
                             [] if render.IS_MAC else ["-hwaccel", "auto"])
            self.assertEqual(render._hw_decode(cfg, {"kind": "video", "codec": "prores"}), [])
            cfg["output"]["encoder"] = "cpu"
            self.assertIn("libx264", render._final_enc(cfg))
            render.gpu_kind = lambda: None
            cfg["output"]["encoder"] = "auto"
            self.assertIn("libx264", render._final_enc(cfg))
            self.assertIn("libx264", render._venc(1920, 1080, True, cfg))
        finally:
            render.gpu_kind = orig

    def test_lut_names_are_filter_safe(self):
        from vlogstudio import render
        cfg = config.load(os.path.join(ROOT, "vlog.example.toml"))
        cfg.root = tempfile.mkdtemp()
        # spaces, quotes, commas, brackets (and a colon where the OS allows it) must not break ffmpeg filters
        cfg["project"]["output_folder"] = os.path.join(cfg.root, "out dir, it's (v2)" + ("" if os.name == "nt" else ": x"))
        p = render.lut_for(cfg, {"id": "My clip, v2 (final)", "profile": "bt709"}, {"stops": 0, "wb": [1, 1, 1]}, size=5)
        self.assertRegex(os.path.basename(p), r"^lut_[0-9a-f]+\.cube$")
        shutil.rmtree(cfg.root, ignore_errors=True)


class TestApp(unittest.TestCase):
    """The local app server: settings round-trip, state, file access rules."""

    def setUp(self):
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        from vlogstudio.ui import server as srv
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "media"))
        self.cfg = os.path.join(self.tmp, "vlog.toml")
        with open(self.cfg, "w") as f:
            f.write('[project]\nname = "App Test"\nmedia_folder = "media"\noutput_folder = "out"\n'
                    '[overrides."clipA"]\nrotate = 180\n[[places]]\nname = "HOME"\nlat = 1.0\nlon = 2.0\n')
        srv.Handler.app = srv.App(self.cfg)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.u = urllib.request

    def tearDown(self):
        self.httpd.shutdown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def get(self, path):
        with self.u.urlopen(self.base + path) as r:
            return json.loads(r.read())

    def post(self, path, body):
        req = self.u.Request(self.base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with self.u.urlopen(req) as r:
            return json.loads(r.read())

    def test_settings_roundtrip(self):
        meta = self.get("/api/meta")
        self.assertTrue(any(f["path"] == "analysis.smart_moments" for f in meta["fields"]))
        self.assertIn("documentary", next(f for f in meta["fields"] if f["path"] == "project.pacing")["options"])
        self.post("/api/config", {"project": {"pacing": "documentary", "title": "TÔKYO \"23\""},
                                  "look": {"preset": "teal_orange", "overrides": {"contrast": 0.3}},
                                  "analysis": {"smart_moments": True}})
        c = self.get("/api/config")
        self.assertEqual(c["project"]["pacing"], "documentary")
        self.assertEqual(c["project"]["title"], 'TÔKYO "23"')
        self.assertEqual(c["look"]["overrides"]["contrast"], 0.3)
        self.assertTrue(os.path.isabs(c["project"]["media_folder"]))
        reloaded = config.load(self.cfg)  # file on disk is valid TOML and keeps places/overrides
        self.assertTrue(reloaded["analysis"]["smart_moments"])
        self.assertEqual(reloaded["overrides"]["clipA"]["rotate"], 180)
        self.assertEqual(reloaded["places"][0]["name"], "HOME")
        self.assertTrue(os.path.exists(self.cfg + ".bak"))

    def test_state_and_file_rules(self):
        st = self.get("/api/state")
        self.assertFalse(st["job"]["running"])
        from vlogstudio import util
        self.post("/api/cancel", {})  # Cancel with nothing running is ignored
        self.assertFalse(util.CANCEL.is_set())
        self.assertEqual(st["projects"][0]["name"], "App Test")
        with self.assertRaises(Exception):  # files outside the project's output folder are refused
            self.get("/file?path=" + "/etc/hosts")


class TestCancel(unittest.TestCase):
    """The app's Cancel button must stop every kind of job, not just running ffmpeg."""

    def setUp(self):
        from vlogstudio import util
        self.util = util
        util.CANCEL.clear()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        self.util.CANCEL.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_not_swallowed_by_except_exception(self):
        try:
            raise self.util.Cancelled("cancelled")
        except Exception:
            self.fail("Cancelled must not be caught by `except Exception`")
        except self.util.Cancelled:
            pass

    def test_pmap_stops_early(self):
        done = []

        def fn(x):
            if x == 3:
                self.util.CANCEL.set()
            done.append(x)
        for workers in (1, 4):
            done.clear()
            self.util.CANCEL.clear()
            with self.assertRaises(self.util.Cancelled):
                self.util.pmap(fn, range(200), workers)
            self.assertLess(len(done), 50, f"workers={workers}")

    def test_scan_stops_instead_of_skipping_files(self):
        from PIL import Image
        from vlogstudio import media
        os.makedirs(os.path.join(self.tmp, "media"))
        for i in range(20):
            Image.new("RGB", (64, 48), (i * 10, 80, 120)).save(os.path.join(self.tmp, "media", f"p{i:02d}.jpg"))
        with open(os.path.join(self.tmp, "vlog.toml"), "w") as f:
            f.write('[project]\nmedia_folder = "media"\noutput_folder = "out"\n')
        cfg = config.load(os.path.join(self.tmp, "vlog.toml"))
        real, calls = media.prepare_image, []

        def prep(path, work):
            calls.append(path)
            if len(calls) == 2:
                self.util.CANCEL.set()
            return real(path, work)
        media.prepare_image = prep
        try:
            with self.assertRaises(self.util.Cancelled):
                media.scan(cfg)
        finally:
            media.prepare_image = real
        self.assertLess(len(calls), 20)

    def test_tool_errors_are_kept_but_cancel_propagates(self):
        from vlogstudio import tools as T

        @T.tool("_test_cancel", "test only")
        def _t(proj):
            raise self.util.Cancelled("cancelled")

        @T.tool("_test_error", "test only")
        def _e(proj):
            raise ValueError("boom")
        try:
            self.assertTrue(T.call(None, "_test_error", {}).text.startswith("ERROR"))
            with self.assertRaises(self.util.Cancelled):
                T.call(None, "_test_cancel", {})
        finally:
            T.REGISTRY.pop("_test_cancel", None)
            T.REGISTRY.pop("_test_error", None)

    def test_agent_stops_before_next_tool(self):
        from vlogstudio import agent
        util, tmp = self.util, self.tmp

        class FakeLLM:
            tool_mode, vision, n = "native", False, 0

            def chat(self, messages, **kw):
                self.n += 1
                if self.n == 2:
                    util.CANCEL.set()  # user presses Cancel while the model is thinking
                return {"content": "", "tool_calls": [{"id": f"c{self.n}", "function": {
                    "name": "list_skills", "arguments": "{}"}}]}

        class FakeProject:
            def llm(self):
                return FakeLLM()

            def p(self, name):
                return os.path.join(tmp, name)
        real = agent._project_summary
        agent._project_summary = lambda proj: "test"
        try:
            with self.assertRaises(util.Cancelled):
                agent.run(FakeProject(), "make it", max_steps=10)
        finally:
            agent._project_summary = real
        log = [json.loads(l) for l in _read(os.path.join(tmp, "agent_log.jsonl")).splitlines()]
        self.assertEqual(len([e for e in log if "tool" in e]), 1)

    def test_waiting_for_model_reply_is_cancellable(self):
        import threading
        import time
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from vlogstudio.llm import LLM

        class Slow(BaseHTTPRequestHandler):
            def do_POST(self):
                time.sleep(5)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"choices": [{"message": {"content": "late"}}]}')

            def log_message(self, *a):
                pass
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        llm = LLM({"llm": {"base_url": f"http://127.0.0.1:{httpd.server_address[1]}/v1", "model": "m"}})
        threading.Timer(0.3, self.util.CANCEL.set).start()
        t0 = time.time()
        try:
            with self.assertRaises(self.util.Cancelled):
                llm.chat([{"role": "user", "content": "hi"}])
            self.assertLess(time.time() - t0, 2.0)
        finally:
            httpd.shutdown()


class TestFreeGPUBeforeRender(unittest.TestCase):
    """Ollama keeps the model in GPU memory; it must be unloaded before NVENC rendering."""

    def test_unload_request_goes_to_ollama_native_api(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from vlogstudio.llm import LLM
        got = []

        class Ollama(BaseHTTPRequestHandler):
            def do_POST(self):
                got.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Ollama)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            llm = LLM({"llm": {"base_url": f"http://127.0.0.1:{httpd.server_address[1]}/v1", "model": "gemma4:e4b"}})
            self.assertTrue(llm.unload())
        finally:
            httpd.shutdown()
        self.assertEqual(got, [("/api/generate", {"model": "gemma4:e4b", "keep_alive": 0})])

    def test_unload_never_fails_without_ollama(self):
        from vlogstudio.llm import LLM
        self.assertFalse(LLM({"llm": {"base_url": "http://127.0.0.1:9/v1", "model": "m"}}).unload())

    def test_render_steps_free_the_model_only_when_enabled(self):
        from vlogstudio.project import Project
        tmp = tempfile.mkdtemp()
        try:
            cfg_path = os.path.join(tmp, "vlog.toml")
            for enabled, unload, expect in ((True, True, 1), (True, False, 0), (False, True, 0)):
                with open(cfg_path, "w") as f:
                    f.write(f'[project]\nmedia_folder = "."\noutput_folder = "out"\n'
                            f'[llm]\nenabled = {str(enabled).lower()}\nunload_before_render = {str(unload).lower()}\n')
                proj = Project(config.load(cfg_path))
                calls = []
                proj._llm = type("FakeLLM", (), {"unload": lambda self: calls.append(1)})()
                proj.free_llm_memory()
                self.assertEqual(len(calls), expect, (enabled, unload))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipUnless(os.environ.get("VLOG_INTEGRATION"), "set VLOG_INTEGRATION=1 for the full pipeline test")
class TestIntegration(unittest.TestCase):
    """Synthetic footage + photos → full pipeline, with the mock local LLM."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vlogtest_")
        media = os.path.join(cls.tmp, "media")
        os.makedirs(media)
        for i in range(6):
            dur = 40 if i == 2 else 7  # one long clip exercises chunked captions (smart moments)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=24:duration={dur}",
                            "-f", "lavfi", "-i", f"sine=frequency={300 + 50 * i}:duration={dur}", "-shortest",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                            "-metadata", f"creation_time=2024-10-2{2 + i // 3}T1{i}:00:00Z",
                            os.path.join(media, f"clip{i}.mp4")], check=True)
        from PIL import Image
        rng = np.random.default_rng(1)
        for name, (w, h) in (("photo_land.jpg", (1600, 1200)), ("photo_portrait.png", (900, 1600))):
            g = np.linspace(0, 1, h)[:, None, None] * np.array([90, 150, 220]) + rng.normal(0, 25, (h, w, 3))
            Image.fromarray(np.clip(g + 20, 0, 255).astype(np.uint8)).save(os.path.join(media, name))
        cls.port = 11000 + os.getpid() % 900
        from mock_llm_server import serve
        cls.reqlog = os.path.join(cls.tmp, "requests.jsonl")
        cls.srv = serve(cls.port, cls.reqlog, native_tools=True)
        with open(os.path.join(cls.tmp, "vlog.toml"), "w") as f:
            f.write(f'''[project]
name = "Test"
media_folder = "media"
output_folder = "out"
target_duration = 20
[llm]
enabled = true
base_url = "http://127.0.0.1:{cls.port}/v1"
model = "mock-vl"
use_for = ["orientation", "captions", "storyboard", "titles"]
[analysis]
skip_start = 0.5
smart_moments = true
long_clip_chunk = 12
[output]
resolution = "640x360"
preview_resolution = "640x360"
make_1080p = false
x264_preset = "ultrafast"
''')

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def vlog(self, *args):
        r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {ROOT!r}); "
                            "from vlogstudio.cli import main; main()", "-c", os.path.join(self.tmp, "vlog.toml"), *args],
                           capture_output=True, text=True, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
        return r.stdout

    def reqs(self):
        return [json.loads(l) for l in _read(self.reqlog).splitlines()]

    def test_1_make_with_llm(self):
        out = self.vlog("make")
        self.assertIn("MOCK", _read(os.path.join(self.tmp, "out", "work", "plan.json")))
        vid = os.path.join(self.tmp, "out", "Travel_Vlog_360p.mp4")
        self.assertTrue(os.path.exists(vid), out)
        n = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                            "stream=nb_read_packets", "-of", "csv=p=0", vid], capture_output=True, text=True).stdout
        p = json.loads(_read(os.path.join(self.tmp, "out", "work", "plan.json")))
        self.assertEqual(int(n.strip().split(",")[0]), round(p["duration"] * 24))
        self.assertTrue(p["warnings"], "invalid mock proposals should have been repaired")
        caps = json.loads(_read(os.path.join(self.tmp, "out", "work", "captions.json")))
        self.assertEqual(len(caps), 8)
        self.assertGreaterEqual(caps["clip2"].get("chunks", 1), 3, "long clip should be captioned in chunks")
        self.assertTrue(any(r["images"] > 0 for r in self.reqs()), "vision requests must carry images")
        kinds = {m["kind"] for m in json.loads(_read(os.path.join(self.tmp, "out", "work", "media.json")))}
        self.assertEqual(kinds, {"video", "image"})
        from mock_llm_server import State
        self.assertGreaterEqual(State.unloads, 1, "the model must be unloaded before rendering")

    def test_2_agent_native_tools(self):
        self.vlog("agent", "make the video", "--max-steps", "15")
        log = [json.loads(l) for l in _read(os.path.join(self.tmp, "out", "work", "agent_log.jsonl")).splitlines()]
        tools = [e["tool"] for e in log if "tool" in e]
        self.assertIn("set_storyboard", tools)
        self.assertIn("render_preview", tools)
        self.assertFalse([e for e in log if "tool" in e and e["result"].startswith("ERROR")],
                         [e["result"] for e in log if "tool" in e and e["result"].startswith("ERROR")])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "out", "preview.mp4")))

    def test_3_agent_json_fallback(self):
        from mock_llm_server import State
        State.native_tools = False  # server now rejects `tools` → agent must switch to the JSON protocol
        os.remove(os.path.join(self.tmp, "out", "work", "agent_log.jsonl"))
        try:
            self.vlog("agent", "--max-steps", "15")
        finally:
            State.native_tools = True
        log = [json.loads(l) for l in _read(os.path.join(self.tmp, "out", "work", "agent_log.jsonl")).splitlines()]
        self.assertTrue(any(e.get("mode") == "json" for e in log))
        self.assertIn("render_preview", [e["tool"] for e in log if "tool" in e])


if __name__ == "__main__":
    unittest.main()
