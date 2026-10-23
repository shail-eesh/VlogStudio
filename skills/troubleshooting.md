---
name: troubleshooting
description: Common failures (slow decode, short clips, rotation, fonts, LLM JSON errors) and fixes.
---
# Troubleshooting

- **"rendered N/M frames — clip too short"**: the in-point is too close to the end of the clip.
  Choose an earlier `in` (in + dur × speed must be ≤ clip duration − 0.1 s).
- **Everything sideways**: wrong rotation flag. `[orientation] mode = "auto"` or `"ignore_flag"`.
- **Some clips upside down**: `[overrides."ID"] rotate = 180`.
- **Very slow analysis**: 4K ProRes decode is CPU heavy. Raise `[analysis] workers` (≈ CPU cores),
  or raise `sample_interval`. Renders: `[output] render_workers` 2–4; preview first (720p).
- **Blown-out first frames**: phone auto-exposure. `[analysis] skip_start = 2.0` avoids them.
- **Fonts missing**: point `[titles] font_*` to any .ttf/.ttc/.otf ("path:index" for collections).
- **LLM returns invalid JSON**: the tool retries and then falls back to the automatic storyboard.
  Use an instruct model ≥ 7B; set `[llm] json_mode = true`; lower `temperature`.
- **LLM can't see images**: use a vision model (qwen2.5vl, gemma3/gemma4, llava, minicpm-v) and keep
  `[llm] vision = true`; otherwise captions/orientation checks are skipped automatically.
- **Tool calling unsupported**: `[llm] tool_mode = "json"` makes the agent use a JSON protocol.
- **Context too small**: in Ollama set `OLLAMA_CONTEXT_LENGTH=16384` (or `num_ctx` in a Modelfile).
- **Audio too quiet/loud**: final mix is −14 LUFS by design; change `[music] ambient_level`.
- **Re-run from scratch**: delete `<output>/work` (cache) — or just the step's file
  (analysis.json, music.json, plan.json, captions.json).

## Windows
- **"ffmpeg was not found"**: `winget install Gyan.FFmpeg`, then close and reopen the terminal (PATH refresh).
- **Windows protected your PC (SmartScreen) on `Vlog Studio.vbs`**: click *More info → Run anyway* (it only
  starts the local app). Or use `Start Vlog Studio.bat`.
- **NVENC not used** (`vlog doctor` says CPU): update the NVIDIA Studio/Game Ready driver; NVENC needs a
  recent driver for the ffmpeg build. Force CPU with `[output] encoder = "cpu"` if a driver is broken.
- **Fonts**: macOS fonts in vlog.toml don't exist on Windows; Segoe UI Black / Segoe Script are used instead.
  Point `[titles] font_*` at any `.ttf` in `C:/Windows/Fonts` (forward slashes are fine).
- **Paths in vlog.toml**: use forward slashes (`"D:/Trips/Japan"`) or single quotes (`'D:\Trips\Japan'`).
- **The app won't open**: see `vlogstudio_app.log` in the tool folder.
