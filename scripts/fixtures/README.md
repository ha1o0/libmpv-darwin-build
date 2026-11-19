# HEVC Main 10 regression fixture

`hevc10-3840x1600.mp4` is a two-second synthetic clip (solid red, then solid
blue), not from the user's media. Regenerate it with an FFmpeg build that has
libx265:

```sh
ffmpeg -f lavfi -i 'color=c=red:s=3840x1600:r=8:d=1' \
  -f lavfi -i 'color=c=blue:s=3840x1600:r=8:d=1' \
  -filter_complex '[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p10le' \
  -an -c:v libx265 -preset ultrafast \
  -x265-params 'pools=2:frame-threads=2:keyint=8:min-keyint=8:bframes=0:scenecut=0:log-level=error' \
  -tag:v hvc1 -map_metadata -1 scripts/fixtures/hevc10-3840x1600.mp4
```
