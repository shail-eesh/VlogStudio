---
name: titles-typography
description: On-screen text style, lengths, fonts, and the title kinds (hook, title, chapter, location, end).
---
# Titles & typography

Kinds rendered by the tool:
- **hook** – line1 big heavy caps (≤ 22 chars), line2 small caps (≤ 40). Over the first shot.
- **title** – small gold script ("welcome to"), huge tracked display word(s) ("NEW YORK"), small
  subline ("CITY · OCTOBER 2024").
- **chapter** – "DAY 01" + places line. Multi-day trips only.
- **location** – pin icon + place in caps + small subline, lower-left above the letterbox.
- **end** – "NEW YORK," + script line ("you were a vibe.") + "SEE YOU AGAIN SOON".

Style: warm white (#FFF6E4) + gold accent (#FFD68C), soft drop shadow and a faint dark scrim so
text reads on bright skies. Fonts (macOS built-ins, change in `[titles]`): Futura Bold (display),
Avenir Next Heavy/Demi (hook, tags), Snell Roundhand (script). Format `"path.ttc:index"`.

Copy rules: upbeat, specific, true to the footage. Numbers hook well ("48 HOURS IN …", "3 DAYS IN …").
"POV:" lines work for social. No emojis (fonts can't render them). Keep text off faces.
Place names come from `[[places]]` in vlog.toml (GPS radius match) or the vision model's captions.
