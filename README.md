# Vlog Studio

[![tests](https://github.com/shail-eesh/VlogStudio/actions/workflows/tests.yml/badge.svg)](https://github.com/shail-eesh/VlogStudio/actions/workflows/tests.yml)

**Turn a folder of travel videos and photos into a cinematic, beat-synced, horizontal vlog, fully
offline on your own computer.** It runs on Windows (with NVIDIA GPU acceleration) and macOS.
AI is optional: it works entirely on its own, or with a local model (Qwen-VL, Gemma, LLaVA…)
through Ollama, LM Studio or llama.cpp. Nothing is ever uploaded.

What you get from one click:
- **Colour:** a bright, warm, happy, well-lit cinematic grade.
  - Camera log and HDR footage is decoded properly (Apple Log, iPhone HLG/HDR, PQ, normal video,
    photos).
  - Every shot gets its exposure and white balance matched.
- **Orientation:** wrong rotation flags are found and fixed, and vertical shots and photos are
  placed on a blurred background.
- **Music:** an original, royalty-free soundtrack in four styles, or your own song with automatic
  tempo and beat detection.
- **Edit:** cuts land on the beat, with a viral structure: hook, teasers, a title shot, chapter
  cards ("DAY 01"), GPS location tags, flashes on the music drops and an end card.
- **Finish:** 2.39:1 letterbox, push-in moves, film grain, vignette, audio mixed to −14 LUFS.
- **Exports:** a 4K master, a 1080p copy, a thumbnail, `.cube` LUTs for DaVinci Resolve/Premiere,
  and graded copies of every clip.
- **App:** a point-and-click app (runs in your browser, locally), plus a command line.

---

## Install on Windows (e.g. a laptop with an RTX 4070)

1. Install **ffmpeg** and **Python** by opening *Terminal* / *PowerShell* and running:
   ```powershell
   winget install Gyan.FFmpeg
   ```
   ```powershell
   winget install Python.Python.3.12
   ```
   Close and reopen the terminal afterwards so both are on your PATH.
2. Download this repo: **Code → Download ZIP** and unzip it, or
   `git clone https://github.com/shail-eesh/VlogStudio`.
3. Double-click **`setup.bat`**. It creates a private Python environment, installs the packages,
   checks your GPU and puts a **Vlog Studio** shortcut on your Desktop.
4. Double-click **Vlog Studio** on the Desktop (or `Vlog Studio.vbs` in the folder). The app opens
   in your browser at `http://127.0.0.1:8765`. If you prefer a visible log window, use
   `Start Vlog Studio.bat`.

**GPU use on NVIDIA RTX:** with `encoder = "auto"` (the default), rendering, graded-clip export
and the final video use the card's **NVENC** encoder, and phone H.264/HEVC footage is decoded on
the GPU. The colour grading itself and Apple ProRes decoding run on the CPU, because no GPU decoder
exists for ProRes on any platform. Check what was detected with `vlog doctor`. If NVENC isn't found,
update the NVIDIA driver.

## Install on macOS

```bash
brew install ffmpeg python
```
```bash
git clone https://github.com/shail-eesh/VlogStudio && cd VlogStudio && ./setup.sh
```
Then double-click **`Vlog Studio.app`**. The first time, macOS may say the app is from an
unidentified developer: right-click it, choose **Open**, then **Open** again.
`Start Vlog Studio.command` is the same with a log window.

---

## Using the app

1. Choose your **footage folder** (Browse…), or use **＋ New project from a folder…** to keep
   separate settings per trip.
2. Optionally choose the **video type**: travel, wedding/event, family, event recap, real estate
   or product.
3. Optionally set the **pacing**, the length and the titles.
4. Press **Make my video**. It analyses, grades, composes music, builds the storyboard and renders.
   The finished video plays on the same page.

The other pages are only for tweaking:

| page | what you can do |
|---|---|
| **Footage** | every clip after grading and orientation fixes; click one to rotate it, change its exposure, skip it, or confirm it's upright |
| **Colour look** | 5 presets plus sliders with a live preview; export LUTs |
| **Music** | generated style or your own song; listen; tempo and sections |
| **Storyboard** | build automatically or with AI; change any shot's clip or start time and all the on-screen text |
| **Render & export** | 720p preview, final render, graded clips, LUTs, video players, *Show in folder* |
| **AI assistant** | connect a local model, test the connection, Smart moments, or let the AI agent make the whole video |
| **All settings** | everything, saved automatically |

Long jobs show live progress and can be cancelled.

> **Orientation tip:** without an AI model, the tool never flips a single clip on a guess. Clips
> that might be upside down are marked **check orientation** on the Footage page; fix them with
> one click. With a vision model switched on, this check is automatic.

## Command line (optional)

Use `vlog` on Windows and `./vlog` on macOS:

| command | what it does |
|---|---|
| `vlog init "D:\Trips\Japan"` | create `vlog.toml` for a folder |
| `vlog doctor` | check ffmpeg, GPU, fonts, Python and the AI server |
| `vlog make --preview` | the whole pipeline (same as the app's button) |
| `vlog analyze` / `vlog sheets --open` | analyse footage / open the contact sheets |
| `vlog override CLIP rotate=180 stops=-0.4 exclude=true` | per-clip fixes |
| `vlog music --preset chill_lofi` / `--file song.mp3` | soundtrack |
| `vlog plan` · `vlog preview` · `vlog render` | storyboard · 720p preview · final render |
| `vlog grade-clips` · `vlog export-luts` | graded clips · LUT files |
| `vlog agent "make a 60s happy vlog"` | the AI agent drives every tool |
| `vlog ui` | start the app |

Change any setting for one run: `vlog --set output.resolution=1920x1080 --set look.preset=vivid_pop make`

---

## Local AI (optional)

Any local server that speaks the OpenAI `/v1/chat/completions` API works.

- **Ollama** (easiest; Windows & macOS): install from <https://ollama.com>, then run
  `ollama pull qwen2.5vl:7b` and keep the server address `http://localhost:11434/v1`.
- **LM Studio**: load a vision model, start the local server, and use `http://localhost:1234/v1`.
- **llama.cpp**: `llama-server -m model.gguf --mmproj mmproj.gguf --jinja -c 16384`, then
  `http://localhost:8080/v1`.

In the app, open **AI assistant**, switch on *Use a local AI model*, press **Test connection**
and pick the model. Or set `[llm] enabled = true` in `vlog.toml`.

Which models fit your GPU:
- **RTX 4070 laptop (8 GB VRAM):** `gemma4:e4b` or `qwen2.5vl:7b` (recommended), `gemma3:4b`, `minicpm-v`.
  Avoid 12B+ vision models, which spill into system RAM and get slow.
- **RTX 4070 desktop (12 GB VRAM):** also `gemma4:12b` or `gemma3:12b` (better storyboards and agent).
- **Apple Silicon:** 7–8B models on 16 GB; 12B+ on 32 GB.

Give the model room for the storyboard step by setting the Ollama context length to about 16k:
`setx OLLAMA_CONTEXT_LENGTH 16384` on Windows, `export OLLAMA_CONTEXT_LENGTH=16384` on macOS.

What the model does:
- checks orientation;
- describes every clip from its contact sheet;
- picks the best moment for each slot;
- writes the titles.

Every answer is validated and repaired; if the model fails, the automatic choice is used, so a weak
model can't break the render. In agent mode the model calls the tools itself (scan, analyse, view
sheets, fix clips, set the look, make music, set the storyboard, preview, review frames, render).
This works with native tool calling or a JSON fallback.

## How shots are chosen

1. **Analysis:** every 1.5 s a frame is scored for sharpness, colour, exposure, open sky and
   motion. Blank clips are skipped.
2. **Cut points:** the music fixes them. `pacing` sets the shot lengths: `viral` 0.5–5 s,
   `cinematic` 1–6 s, `documentary` up to ~10 s.
3. **Moments:** each slot gets the best matching moment. One clip can give several
   (`max_moments_per_clip`, default 4).
4. **Smart moments** (`[analysis] smart_moments = true`; best with a vision model):
   - long clips are shown to the model in chunks, so it sees a frame every ~1.5 s;
   - its suggested moment lengths choose the slot each moment goes into;
   - its "avoid" times (shaky, blurry, lens covered) are never used.

## Customise everything

| what | where |
|---|---|
| look, exposure, white balance | `[look]` in vlog.toml, or the Colour look page |
| colour science / new presets | `vlogstudio/color.py` → `PRESETS` |
| pacing & structure | `vlogstudio/plan.py` → `PACING`, `make_slots` |
| video-type presets | `vlogstudio/config.py` → `STYLES` |
| music styles | `vlogstudio/music.py` → `PRESETS` |
| fonts & text layout | `[titles]` in vlog.toml; `vlogstudio/titles.py` (Windows/macOS/Linux font fallbacks) |
| GPU / encoder | `[output] encoder = auto / gpu / cpu`, `hw_decode = auto / on / off` |
| GPS place names (offline) | `[[places]]` in vlog.toml |
| **prompts** sent to the model | `prompts/*.md` |
| **skills** the agent can read | `skills/*.md` (add your own) |
| **tools** the agent can call | `vlogstudio/tools.py` (`@tool` decorator) |
| the app | `vlogstudio/ui/` (`server.py` settings schema, `app.js`, `app.css`) |

All options are documented in [`vlog.example.toml`](vlog.example.toml). Your own `vlog.toml` and
`projects/` hold your folders and places; they're in `.gitignore` and never committed.

## How it works

```
folder ─scan──► media.json  (apple_log/hlg/pq/bt709/srgb, rotation flag, GPS, date)
       ─analyze► frames → auto exposure/WB, orientation consensus, shot scores, contact sheets
       ─[AI]───► orientation check, captions (place, quality, best moments, avoid ranges)
       ─music──► music.wav + beats + sections (intro/build/drop/break/outro)
       ─plan───► beat slots → hook/teasers/title/story/closer → clip + in-point per slot
       ─render─► per-shot LUT (decode → balance → gamut map → filmic tone map → look)
                 → graded upright segments (NVENC/VideoToolbox/x264) → titles, flashes, grain,
                 vignette, letterbox, music + ambience at −14 LUFS → 4K + 1080p + thumbnail
```

## Tests

```powershell
python -m unittest discover -s tests -v
```
Prefix it with `VLOG_INTEGRATION=1` (on Windows, `set VLOG_INTEGRATION=1` first) for the full
pipeline: synthetic footage and photos, rendering, the app server, and a mock local AI model
(captions, storyboard repair, titles, orientation, agent in native and JSON tool modes). CI runs
all of it on Windows and macOS for every push.

Troubleshooting: [`skills/troubleshooting.md`](skills/troubleshooting.md).

## Requirements

- Python 3.11+
- ffmpeg 7+ (with libx264; NVENC builds such as Gyan's include GPU encoding)
- Windows 10/11 or macOS 12+ (Linux works from the command line)
- No internet needed after installation
