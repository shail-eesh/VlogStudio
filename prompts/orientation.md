You are checking footage orientation before an edit. The image is an OVERVIEW SHEET: one frame per
clip, each labelled with its clip id in yellow. Every frame should be upright as a viewer would
expect (sky up, people standing, text readable, horizon level-ish).

For each frame decide whether it is upright, or needs to be rotated clockwise by 90, 180 or 270
degrees to become upright. Shots pointing straight down at the ground (feet, leaves, food) are
"upright" unless people/text are clearly sideways or upside down.

Clip ids on this sheet: {{ids}}

Reply with ONLY one JSON object mapping clip id -> clockwise degrees needed (0, 90, 180 or 270):
{"<clip id>": 0, ...}
Include every id listed above. When unsure, answer 0.
