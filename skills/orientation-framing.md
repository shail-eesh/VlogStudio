---
name: orientation-framing
description: Rotation-flag problems, automatic upright detection, vertical footage on horizontal timelines, letterbox safe framing.
---
# Orientation & framing

## The rotation-flag trap
Phones store pixels in sensor orientation plus a *display rotation* flag. Some apps write the wrong
flag: in the NYC footage (Blackmagic Cam with a 9:16 guide) every clip said "rotate 90°" although
it was shot landscape → players showed it sideways/portrait. Four clips were also upside down.

`[orientation] mode`:
- `auto` (default): scores all four rotations of each clip ("sky/bright on top"), takes the
  consensus of all clips with the same flag, and lets single clips differ only with clear evidence.
- `honor_flag`: trust metadata (correct for most normal phone videos).
- `ignore_flag`: use stored pixels as-is.
Per clip: `[overrides."ID"] rotate = 180` (absolute clockwise rotation from stored pixels).
Always check `work/sheets/overview_*.jpg` (or tool `view_overview`) before the final render.

## Vertical shots / portrait photos in a horizontal film
`[orientation] portrait_fill = "blur"` (default): the vertical frame sits centred on a blurred,
darkened copy of itself. `"crop"` fills the frame by cropping the middle (loses top/bottom).

## Letterbox safe area (2.39:1 inside 16:9)
The bars remove ~12.8 % at the top and bottom. Tall subjects (statues, towers) need the shot's
`pos` y below 0.5 → frame moves up, e.g. `"pos": [0.5, 0.2]`; or choose a later in-point where the
subject sits lower. Push-ins zoom toward `pos`, so for the title shot use y≈0.3.
Set `[output] letterbox = 0` to disable bars.
