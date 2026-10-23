---
name: beat-sync-music
description: How music is generated or analysed, how sections map to pacing, and how to use your own track.
---
# Music & beat sync

## Generated (default, royalty free)
`[music] mode = "generate"`, presets: `tropical_house` (120 BPM, A major, I–V–vi–IV — used for NYC),
`upbeat_pop` (124), `cinematic_pop` (100), `chill_lofi` (86, swung, maj7 chords).
`bpm`, `key` and `seed` override the preset. Length follows `project.target_duration`, snapped to
whole bars. Structure: intro → build → drop → break → build2 → drop2 → outro (scaled).

## Your own track
`[music] mode = "file"` and `file = "music/song.mp3"`. The tool detects tempo and beats (spectral
flux + autocorrelation), picks a downbeat, trims to the nearest bar near the target duration with a
fade-out, and labels 4-bar blocks low/mid/high energy → intro/build/drop/break/outro.
Use tracks you have the rights to. For Instagram/TikTok you can also export with the generated
track and replace the audio with a trending sound in the app (cuts are at the song's BPM).

## Pacing per section (beats)
- intro: hook 6 beats, teasers 2 beats, title shot 8 beats (spills 4 beats into the build)
- build: long → short, ending with 1-beat cuts (4,3,2,1,1,1)
- drop: first shot 4 beats (on the impact + white flash), then 2-beat shots
- break / low: 4 beats
- outro: two shots, the last one carries the end card
Cuts never go faster than ~0.4 s.

## Audio mix
Ambient sound from each clip runs under the music (louder in intro/break), dynamically normalised,
then the mix is loudness-normalised to −14 LUFS, true peak −1.5 dB (YouTube/Instagram standard).
`[music] ambient_level` scales the ambience (0 = music only).
