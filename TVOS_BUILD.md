# tvOS libmpv maintenance

The tvOS producer is maintained in `ha1o0/libmpv-darwin-build`, on branch
`codex/tvos-live-compatibility`. Apple platforms share this repository:

| Platform | Maintenance branch |
| --- | --- |
| macOS | `darwin-macos-build` |
| iOS | `darwin-ios-build` |
| tvOS | `codex/tvos-live-compatibility` |

The tvOS branch imports the complete history through
`1f373569081251c2aeded5327196037c3846dc86` from the earlier
`ha1o0/libmpv-ios-build` fork. Its native source locks, build recipes, patches and
smoke fixtures are preserved. This repository migration does not change the
mpv/FFmpeg versions or the current software rendering pipeline.

Build the existing Apple TV ARM64 device and simulator packages explicitly:

```sh
make XCODE_PATH=/Applications/Xcode.app TARGET=mk-pkg-mpv-tvossimulator-arm64-video
python3 scripts/tvos_smoke.py result/lib/libmpv.2.dylib --runtime INSTALLED_TVOS_RUNTIME
make XCODE_PATH=/Applications/Xcode.app TARGET=mk-pkg-mpv-tvos-arm64-video
```

The source locks are in `mobile-sources.json` and `packages.lock.nix`. The
existing inherited release helpers describe the earlier iOS workflow; tvOS
builds use the explicit package targets above.

The bota consumer uses `platforms/tvos/toolchain.lock.json`. It continues to pin
the immutable recipe baseline `d6f17102fb3962c215ced496338b24077707bd74` and applies
`platforms/tvos/native/libmpv-tvos.patch` at that baseline. The baseline is retained
in this branch's imported history. The repository URL now points here, so the
consumer reconstructs its existing build inputs from the Darwin repository.
