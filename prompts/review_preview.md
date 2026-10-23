You are reviewing frames from a PREVIEW render of a cinematic travel vlog (letterboxed 2.39:1,
titles burned in). Each image is one frame; its timestamp is given below. Point out real problems
only.

Frames (timestamp -> shot index):
{{frames}}

Storyboard summary:
{{plan}}

Look for: sideways/upside-down footage, important subject cut off by the black bars (e.g. top of a
landmark), very dark/blurry/overexposed shots, text that is hard to read, the same moment repeated,
shots that don't fit the mood.

Reply with ONLY one JSON object:
{"ok": true/false,
 "issues": [{"shot": <shot index>, "problem": "short", "fix": "one of: change_in, change_clip, move_frame_up, move_frame_down, rotate_180, darker, brighter, none", "suggest_in": <seconds or null>}]}
