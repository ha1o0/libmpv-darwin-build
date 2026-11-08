# Release artifacts used by the app

The default CI artifact profile is `media-kit`. It builds four universal
XCFramework bundles, all using the **`full` flavor**:

```text
libmpv-xcframeworks_<version>_ios-universal-audio-full.tar.gz
libmpv-xcframeworks_<version>_ios-universal-video-full.tar.gz
libmpv-xcframeworks_<version>_macos-universal-audio-full.tar.gz
libmpv-xcframeworks_<version>_macos-universal-video-full.tar.gz
```

Each bundle still contains all its dependencies. macOS retains ARM64 and Intel
slices; iOS retains device and simulator slices. The existing individual targets
and full build matrix remain available.

## Consumer audit

Inspected the local `bota` application and its vendored `packages/media-kit`
on November 8, 2026:

- The app's `pubspec.yaml` depends on `media_kit_libs_video` and overrides its
  iOS/macOS plugins with local packages. The platform `Podfile.lock` files
  confirm the video plugins are in use.
- The four `libs/{ios,macos}/media_kit_libs_<platform>_<variant>/<platform>/Makefile`
  files download the four tar.gz bundles above from this repository's Releases.
- The corresponding `Package.swift` files download individual framework ZIPs
  from those same four bundle families.
- `tools/update_darwin_libmpv_artifacts_full.sh` refreshes all four packages.
  It requires the release asset SHA-256 digests for both the tar.gz bundles and
  the individual ZIPs, even when the consuming app currently uses CocoaPods.

Therefore the reduced profile retains all SwiftPM ZIPs from those four bundles.
Deleting them would break the current updater and SwiftPM integration. The
profile produces 4 tar.gz files plus 67 ZIPs with the current dependency set.
It excludes separate `libs` archives, `default`/`encodersgpl` flavors, and ZIPs
for those unused bundle families. Audio bundles are retained for the existing
four-package updater even though the app currently selects the video plugins.

## Uploads and size

The previous tag workflow uploaded every output twice: once to Actions artifact
storage and again as individual GitHub Release assets. Now:

- **Tag run:** upload selected outputs to the Release only.
- **Manual branch run:** upload selected outputs as an Actions artifact; no Release.

Self-hosted releases upload files sequentially (`preserve_order: true`) and
have a 30-minute Release-step timeout. Hosted runners retain concurrent uploads.

This applies to both local and GitHub-hosted runners. Files still need to upload
from the Mac to GitHub Releases because the consumer downloads them there.
Existing releases and already-running workflows are not modified by this change.

Measured from the local builds on November 8, 2026 (sizes vary with source and toolchain):

| Artifact set | File count | Compressed total |
| --- | ---: | ---: |
| Previous complete matrix | 279 | 1,192 MiB |
| Four tar.gz bundles in the new compact output | 4 | 101 MiB |
| Compact output including SwiftPM ZIPs | 71 | 203 MiB |

The previous matrix used an earlier resolved macOS mpv revision, so these are
example payload sizes rather than a controlled size benchmark. Removing the
duplicate Actions upload on tags reduces total upload traffic further. The new
Nix target also skips compilation of the unused flavors instead of just
filtering uploads.

## Choose a profile

Tag pushes use `LIBMPV_ARTIFACT_PROFILE` from repository Actions variables,
defaulting to `media-kit` when unset. Set it to **`all`** to restore the original
complete matrix for future tags. Invalid values fail the job before building.

Manual runs use the explicit `artifacts` input, which defaults to `media-kit`:

```sh
gh workflow run ci.yaml --ref my-sync-19 -f runner=self-hosted -f artifacts=media-kit
gh workflow run ci.yaml --ref my-sync-19 -f runner=github-hosted -f artifacts=all
```

For a local build, first run the usual macOS source resolver, then:

```sh
nix develop -c make XCODE_PATH=/Applications/Xcode.app VERSION=local-check TARGET=mk-out-media-kit
bash .github/scripts/verify-macos-native-video-artifacts.sh result
```

Omit `TARGET` to build the original complete matrix. The four selected archives
use the same underlying Nix derivations as the original targets: no codecs,
architectures, deployment targets, dependency contents, or archive names change.

For a hosted recovery run on a tag, pass the same `artifacts` profile used for
that release. Cancel a pending local attempt before starting another publisher.
Choose the profile before creating a release; rerunning with a smaller profile
does not delete any extra assets already published on that tag.

## Recover a partial upload

A failed Release step can leave a draft with some assets already uploaded.
Check the draft and compare its file names, sizes, and SHA-256 digests with the
completed build outputs before publishing. Missing files can be uploaded from
the same build; do not mix artifacts from separately resolved source revisions.

The `v2.26.1` attempt on November 9, 2026 started 71 uploads together. One
connection timed out after ten seconds, while other uploads continued for
another thirteen minutes. The final draft contained 68 assets. This motivated
sequential uploading on self-hosted runners; it is a mitigation for local
connection pressure, not a guarantee against all network failures.

Re-running an old tag still uses that tag's workflow, including its old upload
policy. A workflow fix must be included in a later tag to affect automatic
releases. Do not recreate an existing tag just to retry uploading. The local
build outputs remain under the runner checkout's `result` symlink; preserve
the resolved Nix output path before starting another job if a manual repair
is needed. Completing a draft manually does not change the failed Actions
run's status.

## Validation of the compact profile

- Built `mk-out-media-kit` locally with Xcode 27.2 and macOS mpv revision
  `d99d62cc036f269d272d060b32f2b8dd6f631167`; produced exactly 4 tar.gz files
  and 67 ZIPs. The cached build took 26.27 seconds; this is not a cold-build time.
- All 71 files are byte-identical to their existing individual archive targets.
  XCFramework platform/architecture metadata and all ZIP CRCs passed inspection.
- Ran the existing macOS artifact verifier, including the updated Floe cache v4,
  NUT and secondary-subtitle checks: passed.
- Ran the main project's unmodified `update_darwin_libmpv_artifacts_full.sh`
  in dry-run mode against a release manifest containing only these outputs.
  All 60 assets referenced by its Makefiles/SwiftPM manifests were present;
  the updater passed and consumer files were unchanged. The other 11 ZIPs
  accompany the complete macOS video dependency set.
- Compared Nix evaluation before/after for both supported host architectures:
  the original default full-matrix derivation is unchanged, and the added target
  depends on exactly the four existing archive derivations.
- Workflow syntax, profile selection, invalid-input rejection, runner routing,
  and separate tag/branch upload conditions passed focused checks. No release
  was published as part of this local validation.
