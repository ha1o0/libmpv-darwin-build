# macOS bounded MP4 read policy

## Source and scope

The mpv implementation is taken from commit
`7c14ff487cf57c91a434053e7b9411d2013e695f` in `ha1o0/mpv`:
`demux/demux_lavf.c` and `demux/lavf_mp4_policy.h`. CI resolves the tracked
`zcopy` branch to an immutable revision and archive hash before building.
The branch can already contain that implementation. After the curl patches,
`nix/utils/apply-mpv-mp4-patch.sh` checks whether every companion-patch hunk can
be reversed in a dry run. If so, it leaves the source unchanged. If not, all
forward hunks must pass a dry run before applying the patch. Partial or changed
implementations fail without modifying files. Both checks disable fuzz.

This supports the older `f21c602c` and `ec8118be` sources that need the patch,
and `7c14ff48`, which already includes it. It preserves branch tracking and
does not pin CI back to an older source revision.

The Windows companion FFmpeg patch targets revision
`f1126f23380c805b4b8fd69351d189aeac8b892b` (`8.0.git`). This recipe still builds
FFmpeg **7.1.5**. `patches/ffmpeg-macos-mov-bounded-interleave.patch` adapts only
the patch context to the older MOV source and `MOVContext` layout. The new
`mov_bounded_interleave.h`, packet-selection wrapper, options and reset/accounting
logic are the same implementation, including the cumulative window byte-budget
fix. The legacy selector remains the one from FFmpeg 7.1.5.

Both new patches are conditional on `os == macos && variant == video`; the
macOS audio, iOS and iOS simulator derivations do not receive them. The FFmpeg
extension defaults to legacy scheduling. mpv opts in after header/stream
probing only when the existing Windows eligibility rules allow it.

## Behavior

- Default: `demuxer-lavf-mp4-read-strategy=auto`.
- `legacy` disables the automatic policy; `bounded` also permits eligible local
  files when explicitly requested.
- `demuxer-lavf-mp4-preload-secs=5` controls the fixed DTS window (0 to 30).
- `demuxer-lavf-mp4-preload-bytes=8MiB` controls the logical byte budget (0 to
  64 MiB), also clamped to one quarter of the forward demuxer cache. Setting
  either limit to zero disables the opt-in.
- Physical-position scheduling activates only when pending selected A/V
  positions are at least 1 MiB apart. Close/interleaved tracks remain legacy.
- Fragmented/live inputs, external IO, stream groups/IAMF, invalid clocks and
  selected stream indices of 64 or above fall back to legacy. Non-advancing DTS
  disables the policy until a seek. Seeking and selection changes reset its
  fixed window; seek cursor reconstruction uses the original selector.
- Mandatory catch-up packets, sparse subtitles and individual large packets
  are not dropped to meet a byte budget. No packet cache is allocated by this
  scheduler, and packet timestamps and contents are preserved.
- Explicit FFmpeg `interleaved_read`, `interleave_strategy`,
  `interleave_preload_duration` or `interleave_preload_max_bytes` options retain
  precedence over mpv's automatic setup.

The design reference is
[VLC's MP4 physical-position preference with bounded DTS preload](https://github.com/videolan/vlc/blob/master/modules/demux/mp4/mp4.c).
The implementation does not copy VLC/Hills source and does not claim to match
Hills' unpublished behavior exactly.

## Verification and release

`tests/test_macos_mp4_policy.py` applies the existing FFmpeg macOS video patches,
the curl mpv patches, the MP4 patches and the CoreAudio compatibility patch to
temporary copies of the locked sources. The new MP4 hunks require exact context
without fuzz. It compiles the actual production scheduler and adapter functions
with C11 and warnings as errors, and uses sanitizers on Clang/GCC hosts.
The test invokes the same shell helper as the Nix build. It also verifies that
reapplying the companion patch is a no-op, and partial or changed implementations
are rejected without writing files.

`tests/lavf_mp4_read.c` and `tests/lavf_mp4_adapter.c` are copied from the same mpv
commit, so the macOS port uses the Windows policy regression cases. They cover
ordinary interleaving fallback, a two-hour separated-track input, cumulative
byte limits with interspersed subtitles/audio, sparse subtitles, seek resets,
track changes, negative DTS, invalid timestamps, arithmetic limits, format
exclusions, cache clamping, explicit overrides and an unavailable FFmpeg
extension. The adapter benchmark preserves all 1,200 sample timestamps while
reducing physical runs from 1,100 to 6.

These checks can run on Windows and do not constitute a complete macOS libmpv
build or decoder/renderer test. Before release, build the complete macOS video
framework set and verify real MP4 startup, seek, resume and audio/subtitle track
switching on Intel and Apple Silicon, along with MKV, HLS and ISO regressions.
Use `legacy` as the same-build comparison when checking the affected MP4.

Nix includes interpolated patch paths in the derivation inputs, so FFmpeg and
mpv rebuild when these patches change. The recipe does not need a dependency
version bump or a manual cache reset. Ship the matching Avformat framework
with Mpv; keeping an old Avformat disables this optimization.
