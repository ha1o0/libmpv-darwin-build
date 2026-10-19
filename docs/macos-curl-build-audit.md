# macOS video curl build audit

Reviewed on 2026-10-19. The change applies to macOS `video` builds on the `curl`
branch. The pinned native-video mpv source remains
`f21c602c3b379734c8bb5ccdc437e52514d2288c`, and FFmpeg remains **7.1.5**.

## Completed implementation

- Build curl **8.23.0**, nghttp2 **1.70.0**, and OpenSSL **3.5.9** as static
  libraries for x86_64 and arm64. Require HTTP/2, Apple SecTrust, OpenSSL, and
  asynchronous DNS at build time, and link a test dylib to check the complete
  static dependency graph for each target architecture.
- Enable curl in macOS video libmpv. Preserve explicit proxy/direct routing,
  `network-timeout`, the application's existing TLS defaults, and explicit TLS
  verification settings. `curl-enabled=no` retains the FFmpeg networking path.
- Backport FFmpeg's normal-close `io_close2` fix. FFmpeg 7.1.5 already contains
  the failed-open cleanup fix; both are needed for curl-backed HLS resource
  ownership. Keep HLS connection adjustments conditional on curl being enabled.
- Initialize the consumer's stream position before starting the curl thread.
  A completed short response must not advance the consumer past buffered data;
  explicitly requested HLS byte-range offsets remain intact.
- Transfer `reconnect`, `reconnect_streamed`, `reconnect_on_network_error`, and
  `reconnect_delay_max` from nested HTTP request dictionaries into the curl
  transport. Preserve FFmpeg's connection/read retry gates and 0/1/3/... delay
  threshold. Progress starts a fresh consecutive-failure budget; cancellation
  removes queued retries. Requests without those options retain curl's policy.
- Bundle the three static transport licences in library archives and in
  `Mpv.framework/Versions/A/Resources/Licenses/curl-transport`. Remove build-only
  Nix rpaths from macOS video libraries before framework packaging. The release
  verifier requires both CPU slices, the transport features, bundled licences,
  and system-only/relocatable load commands.

The library deployment target remains macOS **11.0**. Bota's application minimum
remains macOS **12.0**; integrating this transport does not change it.

## Validation results

Validation used the final recipes with the installed Xcode toolchain on an
x86_64 Mac. Re-evaluating and building the final recipes returned the same Nix
derivations as the successfully compiled final products.

| Check | Result |
| --- | --- |
| Complete x86_64 video-full libmpv and framework set | Built successfully |
| x86_64 and arm64 static curl/nghttp2/OpenSSL transport | Built and linked successfully |
| Actual libmpv playback and teardown | **19/19** isolated cases passed |
| HLS cases in `curl-enabled=yes`, `no`, and unchanged default | EOF, open failure, cancellation, interrupted segment recovery, AES-128 playback passed |
| Local and ordinary HTTP MPEG-TS | Passed with curl enabled and disabled |
| Deterministic short-response consumer-position regression | **12/12** cases passed, including offsets above 4 GiB |
| Production policy functions and local HTTP/proxy servers | Timeout, backpressure, range recovery, redirects, retry rejection, proxy routing, and cancellation passed |
| Per-request reconnect parser and retry queue | Precedence, validation, fallback guards, delay limits, progress reset, error exclusions, and cancellation passed |
| Curl default version boundaries, HLS fallback, and TLS defaults/overrides | Passed |
| Release artifact verifier tests | **18/18** passed |
| Actual x86_64 framework inspection | **29** frameworks passed dependency closure and load-command checks; static transport features, licences, macOS 11.0 target, and custom Render API ABI verified |
| Unrelated build recipe comparison against HEAD | **16/16** derivation paths identical |
| Python parsing, shell syntax, and `git diff --check` | Passed |

The 16 unchanged derivations cover FFmpeg, mpv, library packaging, and framework
packaging for macOS audio on both architectures, iOS arm64 video, and iOS
simulator x86_64 video. This checks that the macOS-video-only conditions leave
those dependency graphs and build scripts unchanged.

The persistent playback test now creates encrypted segments from the supplied
plain fixture using `openssl enc -aes-128-cbc`. It checks that the playlist, key,
and both encrypted segments were requested, playback reached EOF, and native
teardown finished in each curl mode. This replaces reliance on an external,
temporary encrypted fixture.

See the [README](../README.md#macos-video-networking) for reproducible policy and
playback commands. `--mpv-source` must identify the unpatched pinned source; the
policy tests apply the recipe patches in a temporary directory.

## Release checks

The full arm64 libmpv/framework build and universal archive verification still
need the GitHub macOS runner. The local arm64 result covers the static network
transport and its link check, not native arm64 playback. The local playback
tests use null video/audio outputs and software decoding; they do not exercise
the application's UI, GPU output, or hardware decoding.

The existing workflow verifies both slices of the packaged universal video
XCFrameworks before uploading or releasing them. Pushing only the `curl` branch
does not trigger it: the workflow runs when a tag matching `v*` is pushed.

Ship the complete matching framework set. In particular, the new
`Mpv.framework` must be paired with this build's `Avformat.framework`; an older
FFmpeg build without the normal-close fix can crash when a playlist is closed.

Stage the new curl package, six patch files (five mpv patches and one FFmpeg
patch), verifier, tests, and this document along with the modified tracked
files. The `.DS_Store` files are unrelated local files and are not part of this
change. The work is left uncommitted for the maintainer's GitHub build.
