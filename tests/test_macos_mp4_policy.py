#!/usr/bin/env python3
"""Apply the macOS video patch stack and compile the shipped MP4 read policy.

Requires Python 3.9+, Git, patch, CMake and a C11 compiler. Pass unpatched FFmpeg 7.1.5
and mpv sources selected by packages.lock.nix. Only temporary copies are edited;
this does not build a framework or change an installed player.
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def run(command, cwd=None):
    env = os.environ.copy()
    if cwd:
        env['GIT_CEILING_DIRECTORIES'] = str(Path(cwd).resolve().parent)
        for name in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'):
            env.pop(name, None)
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                            text=True, encoding='utf8', errors='replace', timeout=180)
    if result.returncode:
        raise RuntimeError(' '.join(map(str, command)) + '\n' + result.stdout + result.stderr)
    return result.stdout


def apply_stack(repo, original, destination, patches, patch_command, bash_command):
    shutil.copytree(original, destination, ignore=shutil.ignore_patterns('.git', '__pycache__'))
    for relative in patches:
        patch = str(repo / relative)
        if relative == 'patches/mpv-macos-mp4-bounded-interleave.patch':
            print(run([bash_command, str(repo / 'nix/utils/apply-mpv-mp4-patch.sh'),
                       patch, patch_command], destination).strip(), flush=True)
            continue
        # The recipe uses patch, including its existing compatibility hunks.
        # Require strict context for the new MP4 patches themselves.
        extra = []
        if 'bounded-interleave' in relative:
            run(['git', 'apply', '--check', '--whitespace=error-all', patch], destination)
            extra = ['--fuzz=0']
        run([patch_command, '-p1', '--batch', '--forward', '-i', patch] + extra, destination)
    print('Applied {} macOS video patches to {}'.format(len(patches), destination.name), flush=True)


def check_mpv_patch_guard(repo, source, scratch, patch_command, bash_command):
    command = [bash_command, str(repo / 'nix/utils/apply-mpv-mp4-patch.sh'),
               str(repo / 'patches/mpv-macos-mp4-bounded-interleave.patch'), patch_command]
    names = ('demux/demux_lavf.c', 'demux/lavf_mp4_policy.h')
    expected = {name: (source / name).read_bytes() for name in names}
    assert 'already present' in run(command, source)
    assert expected == {name: (source / name).read_bytes() for name in names}

    # A partial copy and a changed implementation must fail before touching
    # files, rather than be accepted just because the option/header exists.
    for case in ('partial', 'changed'):
        fixture = scratch / ('guard-' + case)
        (fixture / 'demux').mkdir(parents=True)
        contents = dict(expected)
        if case == 'partial':
            contents.pop('demux/lavf_mp4_policy.h')
        else:
            old = b'.mp4_preload_secs = 5,'
            assert contents[names[0]].count(old) == 1
            contents[names[0]] = contents[names[0]].replace(old, b'.mp4_preload_secs = 7,')
        for name, data in contents.items():
            (fixture / name).write_bytes(data)
        try:
            run(command, fixture)
        except RuntimeError as error:
            assert 'MP4 policy is neither absent nor already applied' in str(error)
        else:
            raise AssertionError(case + ' implementation was silently accepted')
        actual = {str(path.relative_to(fixture)).replace('\\', '/'): path.read_bytes()
                  for path in fixture.rglob('*') if path.is_file()}
        assert actual == contents, case + ' check modified the source'
    print('MP4 patch guard: duplicate is unchanged; partial/changed copies fail without writes.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg-source', type=Path, required=True)
    parser.add_argument('--mpv-source', type=Path, required=True)
    parser.add_argument('--build-dir', type=Path)
    parser.add_argument('--cmake', default='cmake')
    parser.add_argument('--patch', default='patch')
    parser.add_argument('--bash', default='bash')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    assert (args.ffmpeg_source / 'RELEASE').read_text().strip() == '7.1.5'
    parent = args.build_dir.resolve() if args.build_dir else Path(tempfile.gettempdir()).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='macos-mp4-', dir=parent)).resolve()

    ffmpeg_recipe = (repo / 'nix/packages/mk-pkg-ffmpeg/default.nix').read_text(encoding='utf8')
    # Common FFmpeg patches plus both macOS video conditionals. Commented and
    # disabled HLS patches must remain excluded, just as in the build recipe.
    ffmpeg_patches = re.findall(r'(?m)^\s*patch -p1 <\$\{\.\./\.\./\.\./(patches/ffmpeg-[\w-]+\.patch)\}', ffmpeg_recipe)
    assert 'patches/ffmpeg-macos-mov-bounded-interleave.patch' in ffmpeg_patches
    ffmpeg = scratch / 'ffmpeg'
    apply_stack(repo, args.ffmpeg_source, ffmpeg, ffmpeg_patches, args.patch, args.bash)

    mpv_recipe = (repo / 'nix/packages/mk-pkg-mpv/default.nix').read_text(encoding='utf8')
    native_branch = mpv_recipe.split("${if macosNativeVideo then ''", 1)[1].split("'' else ''", 1)[0]
    assert 'utils/apply-mpv-mp4-patch.sh' in native_branch
    mpv_patches = re.findall(r'patches/[\w-]+\.patch', native_branch)
    assert 'patches/mpv-macos-mp4-bounded-interleave.patch' in mpv_patches
    mpv_patches += ['patches/mpv-fix-coreaudio-channel-map.patch']
    mpv = scratch / 'mpv'
    apply_stack(repo, args.mpv_source, mpv, mpv_patches, args.patch, args.bash)
    check_mpv_patch_guard(repo, mpv, scratch, args.patch, args.bash)

    mov = (ffmpeg / 'libavformat/mov.c').read_text(encoding='utf8')
    assert 'mov->frag_index.nb_items || mov->fragment.moof_offset || mov->next_root_atom' in mov
    assert '!mov->interleaved_read' in mov
    seek = mov[mov.index('static int mov_read_seek('):]
    assert 'ff_mov_bounded_reset(&mc->bounded_interleave);' in seek
    assert 'AVIndexEntry *entry = mov_find_next_sample_legacy(s, &st);' in seek
    assert 'ff_mov_bounded_record(&mov->bounded_interleave, st->index, pkt->size);' in mov
    demux = (mpv / 'demux/demux_lavf.c').read_text(encoding='utf8')
    opening = demux.index('static int demux_open_lavf(')
    assert demux.index('    setup_mp4_bounded_interleave(demuxer);', opening) > \
        demux.index('avformat_find_stream_info(avfc, NULL)', opening)

    # Compile the actual version-specific adapter functions, not rewritten
    # Python equivalents. Only the unchanged upstream signed loop warning is
    # suppressed; new policy code is compiled with warnings as errors.
    begin = mov.index('static AVIndexEntry *mov_find_next_sample_legacy(')
    adapter = mov.index('static int mov_bounded_get_sample(', begin)
    end = mov.index('static int should_retry(', begin)
    prefix = '''#ifdef _MSC_VER
#pragma warning(push)
#pragma warning(disable:4018)
#else
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wsign-compare"
#endif
'''
    suffix = '''#ifdef _MSC_VER
#pragma warning(pop)
#else
#pragma GCC diagnostic pop
#endif
'''
    (scratch / 'mov_adapter.inc').write_text(prefix + mov[begin:adapter] + suffix + mov[adapter:end], encoding='utf8')
    begin = demux.index('static void setup_mp4_bounded_interleave(')
    end = demux.index('static int demux_open_lavf(', begin)
    (scratch / 'mpv_adapter.inc').write_text(demux[begin:end], encoding='utf8')
    for name in ('lavf_mp4_read.c', 'lavf_mp4_adapter.c'):
        shutil.copy2(repo / 'tests' / name, scratch)
    cmake = '''cmake_minimum_required(VERSION 3.21)
project(macos_mp4_policy_test C)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
add_executable(lavf_mp4_read_test lavf_mp4_read.c)
add_executable(lavf_mp4_adapter_test lavf_mp4_adapter.c)
add_executable(lavf_mp4_old_ffmpeg_test lavf_mp4_adapter.c)
target_compile_definitions(lavf_mp4_old_ffmpeg_test PRIVATE TEST_OLD_FFMPEG=1)
foreach(target lavf_mp4_read_test lavf_mp4_adapter_test lavf_mp4_old_ffmpeg_test)
  target_include_directories(${target} PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/mpv/demux" "${CMAKE_CURRENT_SOURCE_DIR}/ffmpeg/libavformat" "${CMAKE_CURRENT_SOURCE_DIR}")
  if(MSVC)
    target_compile_options(${target} PRIVATE /W4 /WX)
  else()
    target_compile_options(${target} PRIVATE -Wall -Wextra -Werror -fsanitize=address,undefined)
    target_link_options(${target} PRIVATE -fsanitize=address,undefined)
  endif()
endforeach()
'''
    with (scratch / 'CMakeLists.txt').open('w', encoding='utf8', newline='\n') as output:
        output.write(cmake)
    run([args.cmake, '-S', str(scratch), '-B', str(scratch / 'build')])
    run([args.cmake, '--build', str(scratch / 'build'), '--config', 'Release'])
    for name in ('lavf_mp4_read_test', 'lavf_mp4_adapter_test', 'lavf_mp4_old_ffmpeg_test'):
        paths = [scratch / ('build/Release/' + name + '.exe'),
                 scratch / ('build/' + name + '.exe'), scratch / ('build/' + name)]
        print(run([str(next(path for path in paths if path.exists()))]).strip(), flush=True)
    print('Applied macOS video patch stack; compiled production policy. Artifacts: ' + str(scratch))


if __name__ == '__main__':
    main()
