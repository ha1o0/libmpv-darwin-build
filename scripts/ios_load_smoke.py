#!/usr/bin/env python3
"""Load the actual bundled simulator frameworks and initialize libmpv, no stubs."""
import argparse
import hashlib
from urllib.request import urlopen
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile


def run(args, **kwargs):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, **kwargs).strip()


def simulator_selection(runtime_id=None):
    runtimes = json.loads(run(['xcrun', 'simctl', 'list', 'runtimes', '--json']))['runtimes']
    candidates = [r for r in runtimes if r.get('isAvailable') and
                  (r['identifier'].startswith('com.apple.CoreSimulator.SimRuntime.iOS-') if runtime_id == 'auto' else
                   r['identifier'] == runtime_id if runtime_id else
                   r['identifier'].startswith('com.apple.CoreSimulator.SimRuntime.iOS-18-'))]
    if not candidates:
        raise RuntimeError(f'An available {runtime_id or "iOS 18"} simulator runtime is required; no runtime is downloaded automatically')
    runtime = min(candidates, key=lambda r: (tuple(map(int, r['version'].split('.'))), len(r['identifier'])))
    devices = json.loads(run(['xcrun', 'simctl', 'list', 'devicetypes', '--json']))['devicetypes']
    types = [d for d in devices if d.get('productFamily') == 'iPhone' and 'iPhone-16' in d['identifier']]
    if not types:
        raise RuntimeError('No compatible iPhone 16 simulator device type is installed')
    return runtime['identifier'], types[0]['identifier']


SOURCE = r'''
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
int main(void) {
  void *library = dlopen("@rpath/Mpv.framework/Mpv", RTLD_NOW | RTLD_LOCAL);
  if (!library) { fprintf(stderr, "dlopen: %s\n", dlerror()); return 1; }
  void *avformat = dlopen("@rpath/Avformat.framework/Avformat", RTLD_NOW | RTLD_LOCAL);
  const void *(*guess_format)(const char *, const char *, const char *) =
      avformat ? dlsym(avformat, "av_guess_format") : NULL;
  const void *(*find_input)(const char *) =
      avformat ? dlsym(avformat, "av_find_input_format") : NULL;
  if (!guess_format || !find_input || !guess_format("nut", NULL, NULL) ||
      !find_input("nut")) {
    fprintf(stderr, "Missing NUT cache snapshot muxer/demuxer\n"); return 8;
  }
  dlclose(avformat);
  unsigned long (*version)(void) = dlsym(library, "mpv_client_api_version");
  void *(*create)(void) = dlsym(library, "mpv_create");
  int (*option)(void *, const char *, const char *) = dlsym(library, "mpv_set_option_string");
  int (*initialize)(void *) = dlsym(library, "mpv_initialize");
  void (*destroy)(void *) = dlsym(library, "mpv_terminate_destroy");
  int (*get)(void *, const char *, int, void *) = dlsym(library, "mpv_get_property");
  if (!version || !create || !option || !initialize || !destroy) return 2;
  void *handle = create();
  if (!handle) return 3;
  if (option(handle, "config", "no") < 0 || option(handle, "vo", "null") < 0 || option(handle, "ao", "null") < 0) return 4;
  // Confirm the zcopy secondary-subtitle API, not just a loadable upstream mpv.
  if (option(handle, "secondary-sub-style", "independent") < 0 ||
      option(handle, "secondary-sub-scale", "1.25") < 0) return 6;
  int result = initialize(handle);
  int64_t cache_version = 0;
  if (!get || get(handle, "floe-cache-version", 4, &cache_version) < 0 ||
      cache_version != 4) {
    fprintf(stderr, "Missing working Floe cache v4 / libcurl backend\n"); return 9;
  }
  int64_t threads = 0, frames = 0;
  if (!get || get(handle, "options/hwdec-threads", 4, &threads) < 0 ||
      get(handle, "options/hwdec-extra-frames", 4, &frames) < 0 ||
      threads != 1 || frames != 2) return 7;
  destroy(handle);
  if (result < 0) { fprintf(stderr, "mpv_initialize: %d\n", result); return 5; }
  printf("LIBMPV_IOS_LOAD_OK api=%lu cache=%lld\n", version(), (long long)cache_version);
  dlclose(library);
  return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?', type=Path)
    parser.add_argument('--preflight', action='store_true')
    parser.add_argument('--runtime', help='Exact installed runtime ID for local validation; CI defaults to iOS 18')
    parser.add_argument('--iosurface', action='store_true', help='Verify real gpu-next output into a BGRA pixel buffer')
    parser.add_argument('--device', help='Use an already booted iOS simulator instead of creating and deleting one')
    args = parser.parse_args()
    if args.device and (args.runtime or args.preflight):
        parser.error('--device cannot be combined with --runtime or --preflight')
    if not args.device:
        runtime, device_type = simulator_selection(args.runtime)
    if args.preflight:
        print(f'Simulator smoke runtime: {runtime}; device type: {device_type}')
        return
    if args.root is None:
        parser.error('root is required unless --preflight is used')
    root = args.root.resolve()
    with tempfile.TemporaryDirectory(prefix='mpv-ios-smoke-') as temp:
        directory = Path(temp)
        frameworks = directory / 'Frameworks'
        frameworks.mkdir()
        for xcframework in sorted(root.glob('*.xcframework')):
            metadata = plistlib.loads((xcframework / 'Info.plist').read_bytes())
            entries = [e for e in metadata['AvailableLibraries'] if e.get('SupportedPlatformVariant') == 'simulator']
            if len(entries) != 1:
                raise ValueError('Missing simulator slice')
            item = entries[0]
            shutil.copytree(xcframework / item['LibraryIdentifier'] / item['LibraryPath'], frameworks / item['LibraryPath'])
            for file in (frameworks / item['LibraryPath']).rglob('*'):
                file.chmod(file.stat().st_mode | 0o200)
            subprocess.run(['codesign', '--force', '--sign', '-', str(frameworks / item['LibraryPath'])], check=True)
        source = directory / 'smoke.c'
        extra = []
        samples = [[]]
        marker = 'LIBMPV_IOS_LOAD_OK api='
        if args.iosurface:
            source = Path(__file__).with_name('ios_surface_smoke.c')
            extra = ['-F', str(frameworks), '-framework', 'Mpv',
                     '-framework', 'CoreVideo', '-framework', 'CoreFoundation']
            video = directory / 'red.y4m'
            # One 32x32 BT.601 limited-range red frame (Y=81, U=90, V=240).
            video.write_bytes(b'YUV4MPEG2 W32 H32 F25:1 Ip A1:1 C420jpeg\nFRAME\n' +
                              bytes([81]) * 1024 + bytes([90]) * 256 + bytes([240]) * 256)
            hevc = source.with_name('fixtures') / 'hevc10-3840x1600.mp4'
            samples = [[str(video)], [str(hevc), '--verify-seek']]
            marker = 'LIBMPV_IOS_IOSURFACE_OK'
        else:
            source.write_text(SOURCE)
        arch = 'arm64' if platform.machine() == 'arm64' else 'x86_64'
        executable = directory / 'smoke'
        subprocess.run(['xcrun', '--sdk', 'iphonesimulator', 'clang', '-target', f'{arch}-apple-ios15.0-simulator',
                        '-isysroot', run(['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-path']),
                        '-Wl,-rpath,@executable_path/Frameworks', str(source), *extra,
                        '-o', str(executable)], check=True)
        subprocess.run(['codesign', '--force', '--sign', '-', str(executable)], check=True)
        pip_executable = None
        if args.iosurface:
            pattern = directory / 'pip-pattern.y4m'
            # Left red, right blue; quarter-turn checks cannot pass on a
            # uniformly colored image or on a dummy renderer redraw.
            pattern.write_bytes(b'YUV4MPEG2 W32 H16 F25:1 Ip A1:1 C420jpeg\nFRAME\n' +
                (bytes([81]) * 16 + bytes([41]) * 16) * 16 +
                (bytes([90]) * 8 + bytes([240]) * 8) * 8 +
                (bytes([240]) * 8 + bytes([110]) * 8) * 8)
            subtitle = directory / 'pip.srt'
            subtitle.write_text('1\n00:00:00,000 --> 00:01:00,000\nPiP\n', encoding='utf-8')
            pip_executable = directory / 'pip-smoke'
            subprocess.run(['xcrun', '--sdk', 'iphonesimulator', 'clang', '-target', f'{arch}-apple-ios15.0-simulator',
                '-isysroot', run(['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-path']),
                '-Wl,-rpath,@executable_path/Frameworks', str(source.with_name('ios_pip_smoke.c')),
                *extra, '-o', str(pip_executable)], check=True)
            subprocess.run(['codesign', '--force', '--sign', '-', str(pip_executable)], check=True)
        dovi_executable = None
        if args.iosurface:
            # Apple provides an unencrypted real P5 sample. Pin every fetched
            # fragment; do not publish the media as part of the source repo.
            fixture = json.loads((source.with_name('fixtures') / 'apple-p5-source.json').read_text())
            parts = []
            for item in fixture['parts']:
                with urlopen(fixture['base'] + item['name'], timeout=60) as response:
                    data = response.read(item['size'] + 1)
                if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                    raise ValueError('Apple P5 fixture changed: ' + item['name'])
                parts.append(data)
            dovi_sample = directory / 'apple-real-p5.mp4'
            dovi_sample.write_bytes(b''.join(parts))
            dovi_executable = directory / 'dovi-pip-smoke'
            subprocess.run(['xcrun', '--sdk', 'iphonesimulator', 'clang', '-target', f'{arch}-apple-ios15.0-simulator',
                '-isysroot', run(['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-path']),
                '-Wl,-rpath,@executable_path/Frameworks', str(source.with_name('ios_dovi_pip_smoke.c')),
                *extra, '-o', str(dovi_executable)], check=True)
            subprocess.run(['codesign', '--force', '--sign', '-', str(dovi_executable)], check=True)
        shader_executable = None
        if args.iosurface:
            shader_pattern = directory / 'shader-pattern.y4m'
            # Edges and gradients make a restore CNN visibly differ from identity.
            luma = bytes(32 + ((x * 7 + y * 13 + (x // 8 % 2) * 50) % 180)
                         for y in range(64) for x in range(64))
            shader_pattern.write_bytes(b'YUV4MPEG2 W64 H64 F25:1 Ip A1:1 C420jpeg\nFRAME\n' +
                                       luma + bytes([128]) * 2048)
            diagnostic = directory / 'diagnostic.glsl'
            diagnostic.write_text('//!HOOK MAIN\n//!BIND HOOKED\nvec4 hook() { return vec4(1.0, 0.0, 0.0, 1.0); }\n')
            shader_executable = directory / 'shader-smoke'
            subprocess.run(['xcrun', '--sdk', 'iphonesimulator', 'clang', '-target', f'{arch}-apple-ios15.0-simulator',
                '-isysroot', run(['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-path']),
                '-Wl,-rpath,@executable_path/Frameworks', str(source.with_name('ios_shader_smoke.c')),
                *extra, '-o', str(shader_executable)], check=True)
            subprocess.run(['codesign', '--force', '--sign', '-', str(shader_executable)], check=True)
        hdr_executable = None
        target_executable = None
        if args.iosurface:
            hdr_pattern = directory / 'hdr-pq.y4m'
            # ST 2084 nits encoded into a limited-range 10-bit luma ramp.
            nits = [0, 1, 10, 50, 100, 203, 400, 1000, 4000]
            m1, m2 = 2610 / 16384, 2523 / 32
            c1, c2, c3 = 3424 / 4096, 2413 / 128, 2392 / 128
            codes = [round(64 + 876 * ((c1 + c2 * (n / 10000) ** m1) /
                                      (1 + c3 * (n / 10000) ** m1)) ** m2) for n in nits]
            row = b''.join(struct.pack('<H', code) * 32 for code in codes)
            # Add BT.2020 primaries above the neutral ramp. Gray alone cannot
            # detect swapped channels or a primaries/tagging mismatch.
            primaries = [(0.58069, 0, 0), (0, 0.58069, 0), (0, 0, 0.58069)]
            yuv = []
            for red, green, blue in primaries:
                luma = 0.2627 * red + 0.6780 * green + 0.0593 * blue
                yuv.append((round(64 + 876 * luma),
                            round(512 + 448 * (blue - luma) / (1 - 0.0593)),
                            round(512 + 448 * (red - luma) / (1 - 0.2627))))
            color_row = b''.join(struct.pack('<H', value[0]) * 32 for value in yuv) + row[192:]
            chroma = b''
            for component in (1, 2):
                color_chroma = b''.join(struct.pack('<H', value[component]) * 16 for value in yuv)
                color_chroma += struct.pack('<H', 512) * 96
                chroma += color_chroma * 4 + struct.pack('<H', 512) * (144 * 12)
            frame = color_row * 8 + row * 24 + chroma
            hdr_pattern.write_bytes(b'YUV4MPEG2 W288 H32 F25:1 Ip A1:1 C420p10\n' +
                                    (b'FRAME\n' + frame) * 4)
            hdr_executable = directory / 'hdr-smoke'
            subprocess.run(['xcrun', '--sdk', 'iphonesimulator', 'clang', '-target', f'{arch}-apple-ios15.0-simulator',
                '-isysroot', run(['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-path']),
                '-Wl,-rpath,@executable_path/Frameworks', str(source.with_name('ios_hdr_smoke.c')),
                *extra, '-o', str(hdr_executable)], check=True)
            subprocess.run(['codesign', '--force', '--sign', '-', str(hdr_executable)], check=True)
            target_executable = directory / 'mobile-target-smoke'
            subprocess.run(['xcrun', '--sdk', 'iphonesimulator', 'clang', '-target', f'{arch}-apple-ios15.0-simulator',
                '-isysroot', run(['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-path']),
                '-Wl,-rpath,@executable_path/Frameworks', str(source.with_name('ios_mobile_target_smoke.c')),
                *extra, '-o', str(target_executable)], check=True)
            subprocess.run(['codesign', '--force', '--sign', '-', str(target_executable)], check=True)
        device = args.device or run(['xcrun', 'simctl', 'create', f'libmpv-validation-{os.getpid()}', device_type, runtime])
        try:
            if not args.device:
                subprocess.run(['xcrun', 'simctl', 'boot', device], check=True, timeout=120)
                subprocess.run(['xcrun', 'simctl', 'bootstatus', device, '-b'], check=True, timeout=300)
            for sample in samples:
                cmd = ['xcrun', 'simctl', 'spawn', device, str(executable), *sample]
                completed = subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, timeout=120)
                if completed.returncode or marker not in completed.stdout:
                    raise RuntimeError(f'Simulator did not confirm {marker}:\n{completed.stdout}')
                print(next(line for line in completed.stdout.splitlines() if marker in line))
                if args.iosurface:
                    pip_marker = 'LIBMPV_IOS_PIP_OK'
                    if pip_marker not in completed.stdout:
                        raise RuntimeError('Simulator did not confirm same-context PiP composition')
                    print(next(line for line in completed.stdout.splitlines() if pip_marker in line))
                    if '--verify-seek' in sample:
                        cache_marker = 'LIBMPV_IOS_CACHE_PREVIEW_OK'
                        if cache_marker not in completed.stdout:
                            raise RuntimeError('Simulator did not confirm cached snapshot decoding')
                        print(next(line for line in completed.stdout.splitlines() if cache_marker in line))
            if pip_executable:
                completed = subprocess.run(['xcrun', 'simctl', 'spawn', device, str(pip_executable), str(pattern), str(subtitle)],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
                marker = 'LIBMPV_IOS_PIP_COMPOSITION_OK'
                if completed.returncode or marker not in completed.stdout:
                    raise RuntimeError(f'Simulator did not confirm {marker}:\n{completed.stdout}')
                print(next(line for line in completed.stdout.splitlines() if marker in line))
            if dovi_executable:
                completed = subprocess.run(['xcrun', 'simctl', 'spawn', device, str(dovi_executable),
                    str(dovi_sample), str(subtitle)], text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, timeout=180)
                marker = 'LIBMPV_IOS_DOVI_PIP_OK'
                if completed.returncode or marker not in completed.stdout:
                    raise RuntimeError(f'Simulator did not confirm {marker} (exit {completed.returncode}):\n{completed.stdout}')
                for line in completed.stdout.splitlines():
                    if marker in line or 'DV_PIP_COLOR' in line: print(line)
            if shader_executable:
                fixtures = source.with_name('fixtures') / 'anime4k'
                completed = subprocess.run(['xcrun', 'simctl', 'spawn', device, str(shader_executable),
                    str(shader_pattern), str(diagnostic), str(fixtures / 'Anime4K_Restore_CNN_S.glsl'),
                    str(fixtures / 'Anime4K_Upscale_CNN_x2_M.glsl')], text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
                marker = 'LIBMPV_IOS_ANIME4K_OK'
                if completed.returncode or marker not in completed.stdout:
                    raise RuntimeError(f'Simulator did not confirm {marker}:\n{completed.stdout}')
                print(next(line for line in completed.stdout.splitlines() if marker in line))
            if hdr_executable:
                completed = subprocess.run(['xcrun', 'simctl', 'spawn', device, str(hdr_executable), str(hdr_pattern)],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
                marker = 'LIBMPV_IOS_HDR_OK'
                if completed.returncode or marker not in completed.stdout:
                    raise RuntimeError(f'Simulator did not confirm {marker}:\n{completed.stdout}')
                print(next(line for line in completed.stdout.splitlines() if marker in line))
                contract_marker = 'LIBMPV_IOS_HDR_CONTRACT_OK'
                if contract_marker not in completed.stdout:
                    raise RuntimeError('Simulator did not confirm the HDR surface color contract')
                print(next(line for line in completed.stdout.splitlines() if contract_marker in line))
                packed_marker = 'LIBMPV_IOS_HDR_PACKED_OK'
                if packed_marker not in completed.stdout:
                    raise RuntimeError('Simulator did not confirm the packed HDR surface pixels')
                print(next(line for line in completed.stdout.splitlines() if packed_marker in line))
            if target_executable:
                completed = subprocess.run(['xcrun', 'simctl', 'spawn', device, str(target_executable),
                    str(pattern), str(hdr_pattern)], text=True, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, timeout=120)
                marker = 'LIBMPV_IOS_MOBILE_TARGET_OK'
                if completed.returncode or marker not in completed.stdout:
                    raise RuntimeError(f'Simulator did not confirm {marker}:\n{completed.stdout}')
                for line in completed.stdout.splitlines():
                    if marker in line or 'MOBILE_SDR_TARGET hdr=' in line: print(line)
        finally:
            if not args.device:
                subprocess.run(['xcrun', 'simctl', 'shutdown', device], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(['xcrun', 'simctl', 'delete', device], check=False)


if __name__ == '__main__':
    main()
