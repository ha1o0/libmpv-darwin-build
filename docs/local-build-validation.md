# Local build validation — 2026-11-08

The complete release matrix built successfully on the local Mac using Xcode
27.2. The existing macOS native video artifact checks passed for `default`,
`full`, and `encodersgpl`. GitHub runner registration and a hosted recovery run
have not yet been performed; automatic releases still default to GitHub.

## Environment and inputs

- Host: Apple M6, 12 CPU cores, 24 GiB RAM, macOS 27.0 (`26A425`).
- Xcode: 27.2 (`27B52`), `/Applications/Xcode.app`.
- Nix: Determinate Nix 3.24.2 / Nix 2.35.3, with Rosetta installed.
- Daemon: `sandbox = true`, `sandbox-fallback = false`, Xcode allowed through
  `extra-sandbox-paths`; restarted after installing Rosetta.
- Build parallelism: `max-jobs = 2`, `cores = 6`.
- Base: `my-sync-19` / `v2.25.0`, commit
  `06e04d8dd9a7d5c096b6b7cb12c7910a5bfb1a96`, plus the local runner and
  compatibility changes in this worktree.
- Build version: `local-check`.
- The existing source resolver selected macOS mpv `zcopy` commit
  `1d61fe06450ae0071638b99ffbf15f498de0eb2b`, archive SHA-256
  `07898b9313fbf90dcb101446587168eb051929de526b1fba85e4ff6213dbafe2`.
  This generated lock update was used for testing and is not a source-version
  change included in the implementation.

## Results

| Check | Result |
| --- | --- |
| Full Nix build, all platforms/variants/flavors | Passed |
| Release outputs | 54 tar.gz archives and 225 SwiftPM ZIPs; 279 files, about 1.16 GiB |
| Binary architecture and bundled dependency audit | Passed for 1,092 binaries / 1,542 CPU slices |
| XCFramework platform and architecture metadata | Passed for 225 XCFrameworks |
| SwiftPM ZIP integrity | All 225 passed CRC checks |
| Existing macOS video artifact verifier | Passed for default, full, and encodersgpl |
| Cache preview / NUT and independent secondary subtitles | Passed on native ARM64; also passed under Rosetta for the default Intel package |
| Runtime decoding smoke test | ARM64 and Intel default packages initialized mpv, loaded a generated WAV, and reached EOF using null output |
| Output-file integrity during verification | SHA-256 hashes of all 279 original output files were unchanged |
| Workflow syntax | `actionlint` v1.7.12 passed |

Compared the video-default Mpv XCFramework deployment metadata with the
published `v2.25.0` artifacts. Minimum versions were unchanged: macOS 11.0 for
both architectures, iOS device 9.0, Intel simulator 9.0, and ARM64 simulator
14.0. SDK and linker versions differ as expected.

The dependency audit checks that load commands do not point at `/nix/store`
and all bundled `@rpath` dependencies exist. Existing Nix search paths in
non-macOS-video packages were also observed in the previous iOS release;
cleaning up that pre-existing behavior is outside this change. The existing
macOS video verifier continues to require clean search paths for those packages.

## Timings

| Invocation | Wall-clock time |
| --- | --- |
| Full matrix after the initial macOS dependency/debugging runs | 991.27 seconds (16 min 31 sec) |
| Full matrix after the CoreMedia packaging correction | 101.18 seconds (1 min 41 sec); reused compiled libraries |
| Existing macOS video artifact verification | 29.72 seconds |
| Same full build with all outputs cached | 1.22 seconds |

These are not clean-room cold-build benchmarks. Earlier attempts populated
the Nix cache, and setup/network troubleshooting is excluded. The comparison
GitHub run for `v2.25.0` spent 5,255 seconds (87 min 35 sec) in its Build step,
but used a different toolchain, cache state, and resolved mpv source. Do not
interpret the ratio as a measured hardware-only speedup. The cached invocation
also skipped the upstream source resolver; a new upstream revision or release
version can require compilation or repackaging.

## Compatibility fixes verified

- HarfBuzz: include `math.h` while checking `sincosf`, preventing Clang's builtin
  from incorrectly enabling a function absent from the Apple SDK headers.
  Other function checks are unchanged; all five target configurations were
  checked, and all packages built.
- libpng: use `math.h` instead of the obsolete `fp.h` include selected by new
  Apple SDK headers. Dependency versions remain pinned.
- FFmpeg packaging: preserve CoreMedia C API dependencies when Xcode 27 maps
  old deployment targets to `@rpath/libswiftCoreMedia.dylib`. Inspected imports
  in 108 affected FFmpeg binaries: all were CoreMedia C symbols. Their packaged
  dependencies now point to the platform's system CoreMedia framework instead
  of an unbundled `SwiftCoreMedia.framework`.
- Artifact verification: check each architecture separately for compatibility
  with the local `lipo`; ad-hoc sign temporary extracted frameworks on ARM64
  before dynamic probes. Static checks still run first, and release archives
  are not signed or modified by verification.

No end-to-end rendering in the consuming app, GPU/Metal playback, iOS device
execution, or testing on older OS releases was performed. Those checks remain
necessary before treating the new toolchain as validated for production release.

## Local evidence

- Output symlink: `result/` in the local build worktree.
- Verified Nix output: `/nix/store/51c86l29cqdgs6in0m41f7c14n1qn9ak-mk-out-all`.
- Build/verification logs, resolved source lock, hashes, and audits:
  `/tmp/libmpv-local-build.qztsfL/` (temporary local evidence).

Follow [the runner setup guide](self-hosted-runner.md) to enable tag builds only
after the local runner and hosted recovery path have been tested.
