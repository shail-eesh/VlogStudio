---
name: storyboard-json
description: Exact JSON schema accepted by set_storyboard, with an example.
---
# set_storyboard JSON

```json
{
  "shots": [
    {"slot": 0, "clip": "A001_10221618_C033", "in": 33.0},
    {"slot": 1, "clip": "A001_10221558_C029", "in": 0.5, "speed": 1.0},
    {"slot": 4, "clip": "A001_10221536_C016", "in": 1.0, "pos": [0.5, 0.2], "zoom": [1.0, 1.1]}
  ],
  "text": {
    "hook":  {"line1": "48 HOURS IN NYC", "line2": "POV: YOUR FIRST TRIP TO THE BIG APPLE"},
    "title": {"script": "welcome to", "main": "NEW YORK", "sub": "CITY · OCTOBER 2024"},
    "chapters": [{"main": "DAY 01", "sub": "DOWNTOWN · LIBERTY ISLAND"}, {"main": "DAY 02", "sub": "CENTRAL PARK"}],
    "end":   {"main": "NEW YORK,", "script": "you were a vibe.", "sub": "SEE YOU AGAIN SOON"}
  }
}
```
- `slot` = index from `get_slots`. Slots you omit are filled automatically.
- `in` = seconds into the source clip. Must satisfy `in + slot_dur × speed ≤ clip duration`
  (it is clamped if not). Omit `in` to let the tool choose the best window.
- `speed` 0.5–2.0 (default 1). `zoom` = [start, end] scale (1.0 = full frame). `pos` = [x, y]
  focus point 0–1 for the zoom (y < 0.5 keeps the top of the frame, useful under letterbox).
- `text` fields are optional; missing ones use defaults from vlog.toml.
