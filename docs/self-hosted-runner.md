# Build releases on a local Mac

The existing `ci` workflow uses GitHub's `macos-15` runner by default. A local
Apple Silicon Mac can opt in to tag builds while reusing the same Nix recipes,
archives, artifact upload, and GitHub Release steps. Releases are tagged from
`my-sync-19`; its existing `v*` tag trigger is preserved. This change does not
add automatic builds for branch pushes or pull requests.

The full matrix has been built locally with Xcode 27.2; see the
[validation results and timing limitations](local-build-validation.md).
CI now defaults to the four media-kit `full` XCFramework bundles and their
SwiftPM ZIPs. Tags upload these files directly to Releases, without a duplicate
Actions artifact. See [artifact selection](release-artifacts.md) for details.

## Prepare the Mac

1. Install Nix using the [Determinate installer](https://github.com/DeterminateSystems/nix-installer).
   Installation requires administrator access. In a new terminal, verify that
   `nix --version` and `nix config show` work, with `nix-command` and `flakes`
   enabled. The workflow uses the existing installation on a self-hosted runner.
2. Use a full **Xcode** installation, including the macOS, iOS, and iOS Simulator
   SDKs. The local runner uses the Xcode selected by `xcode-select --print-path`
   by default; Xcode 16.1 is not required locally. To choose a different app
   without changing the system selection, set `LIBMPV_XCODE_PATH`. Open Xcode
   once to complete setup and accept its license. The hosted runner continues
   to use `/Applications/Xcode_16.1.0.app`.
3. Install Rosetta for the Intel macOS dependencies and verify it works:

   ```sh
   softwareupdate --install-rosetta
   arch -x86_64 /usr/bin/true
   ```

   Read and accept Apple's license when prompted. If Nix was already running
   before Rosetta was installed, restart its daemon so it detects
   `x86_64-darwin`. For a Determinate installation:

   ```sh
   sudo launchctl kickstart -k system/systems.determinate.nix-daemon
   ```

4. Configure the Nix daemon to use the same sandbox settings as CI. For a
   Determinate installation, add these settings to `/etc/nix/nix.custom.conf`
   with administrator access, then restart the daemon using the command above:

   ```ini
   sandbox = true
   sandbox-fallback = false
   extra-sandbox-paths = /Applications/Xcode.app
   ```

   Use the actual local Xcode app path in `extra-sandbox-paths`. Check
   `nix config show` afterwards. An untrusted Nix client cannot override these
   daemon settings through the Makefile; warnings that client sandbox settings
   were ignored must not be mistaken for confirmation that sandboxing is on.

5. In this repository's **Settings → Actions → Runners → New self-hosted runner**,
   choose **macOS / ARM64**. Follow GitHub's generated download and registration
   commands, and add the custom label **`libmpv-builder`**. Keep the default
   `self-hosted`, `macOS`, and `ARM64` labels. Install the runner outside this
   development checkout; let it use its own `_work` directory.
6. From the runner directory, install and start its service as the same user
   that can use Nix and Xcode:

   ```sh
   ./svc.sh install
   ./svc.sh start
   ./svc.sh status
   ```

   Confirm GitHub shows the runner as **Idle**. Keep the Mac awake and connected
   during builds. A job targeting an offline runner waits; GitHub does not
   automatically redirect it to a hosted runner.

For a public repository, use a dedicated macOS build account without personal
credentials. This release workflow does not run on PRs; do not add a PR trigger
that runs untrusted code on the local machine.

If GitHub access depends on a macOS system proxy, Git and Nix may also need
`http_proxy` and `https_proxy` in their process environment. Set them to your
actual proxy endpoint for local commands and the runner service; the workflow
does not hardcode a proxy for other machines. Confirm `git ls-remote` works
from the build account before starting a cold build.

Release assets use `uploads.github.com`, separately from the API and Git
domains. If the build succeeds but publishing fails with a connection timeout,
check that host from the runner's environment too. macOS system proxy settings
are not automatically inherited by the runner: configure `http_proxy` and
`https_proxy` in the runner's `.env` if needed, then restart the idle runner.
Never restart a runner while it is executing a job.

## Validate before enabling tag builds

First build one representative target in a clean checkout. The existing
Makefile temporarily writes and restores `.nix/config/*`, so do not run two
build commands in the same checkout at once or keep local edits in those files.

```sh
python3 .github/scripts/update-macos-mpv-source.py
nix develop -c make \
  XCODE_PATH=/Applications/Xcode.app \
  VERSION=local-check \
  TARGET=mk-out-archive-xcframeworks-macos-universal-video-default
bash .github/scripts/verify-macos-native-video-artifacts.sh result
```

After the workflow change is on `my-sync-19`, use the already registered `ci`
workflow to validate the four consumer bundles on that branch, with GitHub CLI authenticated:

```sh
gh workflow run ci.yaml --ref my-sync-19 -f runner=self-hosted -f artifacts=media-kit
```

This builds the selected bundles, runs the existing macOS artifact checks, and uploads
artifacts without publishing a release. If using the **Run workflow** UI,
select `my-sync-19`, not `main`; the UI button's availability depends on the
workflow on the default branch. The explicit CLI ref avoids selecting the
different build configuration on `main`.

Compare output names, architectures, deployment targets, and playback in the
consuming app with a hosted build of the same commit. Also confirm both job
summaries report the same resolved macOS mpv source revision: the existing
source resolver follows a moving upstream branch. A newer Xcode can change
compiler behavior or SDK compatibility, so a successful compiler smoke check
does not replace the full build and artifact validation. Verify the hosted
recovery path before enabling local releases:

```sh
gh workflow run ci.yaml --ref my-sync-19 -f runner=github-hosted
```

To validate the original complete matrix, add `-f artifacts=all`. Direct local
`make` still defaults to that complete matrix; pass `TARGET=mk-out-media-kit`
to match the normal CI release set.

The runner retains `/nix/store` between builds. Avoid clearing that store after
each run; unchanged dependencies can be reused. The first build still downloads
dependencies and compiles missing outputs. Use a distinct, versioned Xcode path
when upgrading the toolchain: replacing Xcode in place can reuse Nix outputs
whose path-based dependency has not changed.

## Enable local tag builds

In **Settings → Secrets and variables → Actions → Variables**, add:

| Repository variable | Value / default | Purpose |
| --- | --- | --- |
| `LIBMPV_TAG_RUNNER` | `self-hosted` | Route future tag pushes to the Mac. Unset or any other value keeps GitHub-hosted builds. |
| `LIBMPV_XCODE_PATH` | App selected by `xcode-select` | Optional local Xcode app path, e.g. `/Applications/Xcode.app`; does not affect GitHub-hosted builds. |
| `LIBMPV_MAX_JOBS` | `2` | Maximum independent Nix builds running at once on the Mac. |
| `LIBMPV_BUILD_CORES` | `6` | Parallel compilation jobs per Nix build on the Mac. |
| `LIBMPV_ARTIFACT_PROFILE` | `media-kit` | Build the four consumer bundles, or set `all` for the original complete matrix on tag pushes. Manual runs use their `artifacts` input. |

The `2 × 6` settings are a starting point for a 12-core, 24 GB Mac, not a measured
optimum. Adjust them after comparing build times and memory pressure. Increasing
both values can oversubscribe the CPU and memory. Hosted runner settings are
unchanged.

Push `v*` tags from `my-sync-19` as before. A tag containing this workflow will
build on the Mac and publish the selected release assets. Filenames and contents
of the selected bundles are unchanged. Older tags retain their original workflow.

Local releases upload one file at a time to avoid saturating the Mac's network
connection. Their Release step has a 30-minute timeout. GitHub-hosted releases
retain concurrent uploads and the normal job time budget.

## Recover using GitHub-hosted builds

If the Mac is offline or a local build fails:

1. Cancel the queued or running local attempt for that tag, to avoid two runs
   uploading assets to the same release concurrently.
2. Run `ci` manually for **the same tag**, choosing **`github-hosted`**. This
   choice overrides `LIBMPV_TAG_RUNNER` and uses the original hosted Nix install,
   cache, Xcode path, build, and release steps. With GitHub CLI authenticated:

   ```sh
   gh workflow run ci.yaml --ref YOUR_TAG -f runner=github-hosted -f artifacts=media-kit
   ```

   Selecting a branch instead of the tag only builds artifacts; it does not
   publish that tag's release. Re-running the failed job without changing the
   routing variable can select the local runner again.
   If the original release used `all`, use `-f artifacts=all` for recovery too.
3. Set `LIBMPV_TAG_RUNNER` to `github-hosted` or delete it to keep subsequent tag
   pushes on GitHub. No code revert or tag recreation is required.

Do not enable `LIBMPV_TAG_RUNNER=self-hosted` until the full local validation has
passed. Keeping it unset preserves the existing automatic GitHub workflow.

## References

- [Add a self-hosted runner](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)
- [Configure the runner service](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application)
- [Nix build parallelism](https://nix.dev/manual/nix/stable/command-ref/conf-file.html#conf-max-jobs)
