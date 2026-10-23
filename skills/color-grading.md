---
name: color-grading
description: How the cinematic "well lit, happy" grade works, the look presets, and when/how to tweak exposure, white balance and look parameters.
---
# Colour grading for happy, cinematic travel footage

## Pipeline (per shot, baked into one 3D LUT)
1. **Decode the camera profile to linear light**
   - `apple_log` (iPhone 15 Pro+/Blackmagic Cam ProRes Log, Rec.2020) — Apple Log curve.
   - `hlg` (iPhone default HDR video) — HLG inverse OETF.
   - `pq` (HDR10/Dolby Vision base) — ST.2084.
   - `bt709` (normal SDR video) and `srgb` (photos) — display referred, no tone-mapping.
   Profile is auto-detected from metadata (`com.apple.proapps.customgamma`, `color_transfer`).
   Override with `[overrides."CLIP_ID"] profile = "apple_log"` if detection is wrong.
2. **Auto balance**: exposure in stops so the log-average luminance hits `look.target_exposure`
   (0.17 scene-linear = bright and well lit); partial grey-world white balance that never cools a
   shot much (travel looks stay warm). Strong tungsten/fluorescent casts indoors (e.g. subway
   cars) get a stronger correction automatically.
3. **Gamut map** Rec.2020 → Rec.709 with soft compression (no neon clipping).
4. **Filmic tone map** (ACES-like curve, blend of per-channel punch and hue-preserving) → highlights
   roll off softly; skies keep colour.
5. **Creative look**: S-curve around a pivot, split-tone (golden highlights, hint of teal shadows),
   vibrance with skin-tone protection, tiny matte lift in the blacks.

## Presets (`[look] preset = ...`)
| preset | feel |
|---|---|
| golden_day | default. Bright, warm, happy, "happening". Used for the NYC vlog. |
| teal_orange | blockbuster contrast, cooler shadows |
| natural | clean, true to life |
| vivid_pop | extra saturation for social feeds |
| moody_film | softer, desaturated, more matte |

Fine-tune any parameter with `[look.overrides]`, e.g. `contrast = 0.3`, `warm = [0.03, 0.012, -0.03]`,
`vibrance = 0.4`, `matte = 0.0`, `exposure = 0.9` (scene exposure into the tone curve).
`strength` (0–1.5) scales the whole creative look.

## Diagnosing
- Whole video too dark/bright → change `look.target_exposure` (0.14 darker … 0.22 brighter).
- One shot off → `[overrides."ID"] stops = -0.5` (or tool `set_clip_override`).
- Indoor shot too yellow/green → `[overrides."ID"] wb = [0.8, 1.0, 1.25]` (R,G,B gains in linear).
- Hazy, flat skyline → lower that shot's stops by 0.4–0.8 (haze is bright; darker = more contrast).
- Skin looks orange → reduce `warm` red component or raise `skin_protect`.

## Deliverables
`vlog export-luts` writes 65³ master `.cube` LUTs per source profile (use in DaVinci Resolve:
Color page → LUTs → place in the LUT folder). `vlog grade-clips` writes graded, upright copies of
every clip.
