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
in two Core Video targets; it does not replace physical-device HDR, VideoToolbox
or AVKit PiP acceptance.
