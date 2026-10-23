---
name: photos-in-video
description: How still photos (JPG/PNG/HEIC) from the folder are used in the vlog.
---
# Photos in the vlog

- Any JPG/JPEG/PNG/HEIC/HEIF/TIFF/WEBP in the media folder is included (`project.include_images`).
- HEIC is converted with macOS `sips`; EXIF orientation is applied; EXIF date/GPS are read so photos
  sort into the story chronologically and match `[[places]]`.
- Photos are treated as sRGB and get the same creative look (no log decode) + auto exposure/WB.
- Each photo becomes a Ken Burns shot: slow zoom (0→12 %) toward a random point. Great for break
  and outro sections; fine as 1-beat flashes in drops.
- Portrait photos sit on a blurred background (or are cropped, `portrait_fill = "crop"`).
- Photos have no sound; ambience is silent for that shot.
- Exclude a photo: `[overrides."IMG_1234"] exclude = true` or `project.exclude = ["*.png"]`.
