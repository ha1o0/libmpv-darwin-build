# Darwin iOS build branch

`darwin-ios-build` uses the independent iOS MoltenVK recipe imported from
`ha1o0/libmpv-ios-build` at `1df58af`. It builds only iOS targets. The original
`my-sync-19` history and the dedicated `darwin-macos-build` recipe are not
part of this build. The iOS video recipe pins `ha1o0/mpv/zcopy`, FFmpeg 9.0.2 and
MoltenVK; both device and simulator artifacts target iOS 15.0.

- Pushing the iOS branch only produces checked CI artifacts.
- For releases, push a new **lightweight** `vMAJOR.MINOR.PATCH-ios.SUFFIX` tag
  pointing to a commit on this branch. Do not put iOS tags on the legacy
  `my-sync-19` branch. The workflow on the tag builds and verifies only iOS,
  then publishes a prerelease without replacing an existing release or tag.
- The same registered Mac (`libmpv-builder`) can run both platform workflows.
  iOS uses the selected local Xcode or `LIBMPV_XCODE_PATH` and requires an
  installed iOS simulator with Metal available.
- Use the full iOS XCFramework archive plus the framework ZIPs from that release
  in the media-kit platform updater. A local artifact is not a published pin.

For local validation, set the local Xcode path in the Nix build config as in
`Makefile`, build `.#mk-out-archive-xcframeworks-ios-universal-video-full`, and
run the load and `--iosurface` smoke checks on the unpacked archive with
`scripts/ios_load_smoke.py --runtime auto`. The GPU smoke compares actual pixels
in two Core Video targets, including a 3840×1600 HEVC Main 10 fixture before
and after an exact seek. This is a simulator/software-decode test; it does not
replace physical-device HDR, VideoToolbox or AVKit PiP acceptance.

The iOS recipe requests Metal-compatible VideoToolbox buffers with the OpenGL
ES attribute preserved for the fallback. This follows the same Metal buffer
requirement as MPVKit, but keeps the `libmpv` IOSurface target for Flutter and
AVKit instead of rendering directly into MPVKit's `CAMetalLayer` swapchain.

### HDR10 / HLG IOSurface targets

The iOS video producer also accepts an RGBA16F IOSurface target through
`MPV_RENDER_PARAM_IOSURFACE_COLOR_SPACE`. `HDR10` and `HLG` target values keep
libmpv's gpu-next/libplacebo compositor active, so libass text, bitmap/PGS
subtitles and OSD stay in the same final frame. The existing 32BGRA target and
SDR path remain unchanged. A consumer must present the RGBA16F target through
an EDR-capable native Metal layer; Flutter's ordinary 8-bit texture contract
does not provide HDR presentation by itself.
The native HDR target's transfer/primaries are authoritative: generic SDR
`target-*` options cannot relabel PQ/HLG pixels. Matching HDR source/target
transfers retain source mastering luminance and bypass mpv tone curves and
peak detection. SDR surfaces and screenshots keep their existing tone-mapping
options. The host must select SDR on displays without usable EDR headroom and
tag only actual HDR pixels as HDR. CI checks absolute PQ samples, conflicting
SDR options, algorithm invariance and switching back to SDR.
The preferred native HDR target is `kCVPixelFormatType_ARGB2101010LEPacked`
(`bgr10a2` / `MTLPixelFormatBGR10A2Unorm`): normalized PQ/HLG at four bytes per
pixel, half the output memory/bandwidth of RGBA16F. The float target remains
supported. CI compares RGB primaries and gray samples across both formats.
libplacebo negotiates Vulkan portability and Metal import extensions. The
offscreen backend reuses at most four imported targets and retires them when
the target size changes or the renderer is destroyed. It still completes GPU
writes before handing the IOSurface to Flutter or AVKit, which use separate
Metal queues. The [MoltenVK runtime guide](https://github.com/KhronosGroup/MoltenVK/blob/v1.4.2/Docs/MoltenVK_Runtime_UserGuide.md)
describes the portability and integration requirements; its swapchain tuning
does not apply to this offscreen target.

## PiP on the existing decoder

The iOS video recipe exposes `MPV_RENDER_PARAM_IOS_PIP_FRAME` for AVKit
consumers. The host first peeks a retained VideoToolbox `CVPixelBuffer` with
`mpv_render_context_get_info()`. When backgrounded, it can consume subsequent
timed frames from the same libmpv render context without invoking Vulkan or
releasing/reselecting the video track. The complementary
`MPV_RENDER_PARAM_IOS_PIP_SW` composes a BGRA target up to 640 pixels per edge
from that same frame for subtitles, rotation and software-decoded output.
It maps supported VideoToolbox surfaces read-only instead of allocating a
full-resolution hardware download. Composition uses mpv's existing software
renderer and OSD; both PiP APIs defer all primary GPU work until inline
rendering resumes. Hosts must probe support before bypassing the GPU: old
libraries ignore unknown render parameters.

The software target keeps the source's transfer: a PQ or HLG frame is written
as PQ/HLG code values with BT.2020 primaries, because the software path only
applies the YCbCr matrix. A host that tags the target as HDR requests mode 2,
which maps sRGB OSD and subtitle bitmaps to that transfer at reference white
(203 nits for PQ, 75% signal for HLG). Mode 1 blends them unchanged, as before,
for hosts that present the target as SDR. `get_info()` reports the highest
supported mode. The target is 8-bit; primaries of coloured subtitles are not
converted. Dolby Vision profile 5 still needs the GPU path for its reshaping.

The simulator smoke verifies raw-export rejection without consuming a
software frame, same-context software composition while paused, return to
Vulkan, and the 4K HEVC Main 10 seek fixture. Physical-device VideoToolbox,
AVKit timing, HDR color and repeated foreground/background transitions still
need acceptance; a simulator result does not establish device smoothness.

## iOS memory pressure

The iOS video recipe defaults to one VideoToolbox decode worker and two extra
hardware frames. These remain overridable mpv options; software decode threads,
codec reference frames, output resolution and non-iOS recipes are unchanged.
The non-interpolating IOSurface backend skips libplacebo's single-frame mixing
cache. Screenshot conversion errors use the screenshot log scope, so a failed
optional capture does not masquerade as a cplayer playback failure.

The load smoke checks the hardware defaults. The render smoke forces a bounded
screenshot allocation failure after a paused frame, checks its diagnostic scope,
and verifies the same renderer still produces pixels afterwards. Existing HEVC
Main 10 seek and same-decoder PiP geometry/subtitle checks remain required.

### iOS Anime4K hooks

The iOS IOSurface render API now loads `glsl-shaders` into libplacebo's mpv
user-shader hooks. It retains only the active chain, redraws paused frames when
switching, handles both empty-list encodings, and keeps a complete working chain
when a new file cannot be parsed. The shader patch is applied only to iOS video
builds; existing memory-pressure, VideoToolbox and same-decoder PiP patches stay
in place. PiP continues its existing software composition without GPU effects,
and returning inline restores the enhanced frame.

Release validation runs the official Anime4K Restore S and Upscale x2 M shaders
against actual simulator IOSurfaces. It checks changed pixels for each appended
pass, unchanged pixels after clearing, repeated re-enabling, failed-load recovery
and PiP return. The same command also retains ordinary rendering, Main 10 seek,
subtitle and screenshot-error-isolation checks. An option-list readback alone
is not accepted as evidence that shaders executed.

### Buffered playback previews

The video FFmpeg recipe includes the NUT muxer and demuxer used by the existing
desktop cache snapshot service. The app reads buffered packets through
`floe-cache-snapshot`; enabling this format does not add a source fetch or move
the primary player's position. All source versions and iOS rendering patches
remain pinned to the previous build.

The loader smoke rejects libraries without either NUT direction. The IOSurface
smoke also snapshots the buffered HEVC Main 10 fixture, opens that local NUT in
an independent renderer, verifies the expected blue frame, and checks that the
primary position and output are unchanged. This runs on the existing remote
Mac runner as part of ordinary branch builds and tagged releases.
