#!/usr/bin/env python3
"""Check cached preview support in the packaged native macOS CPU slice.

The caller checks both CPU slices and their dependencies separately. This
probe loads the packaged frameworks in a fresh process so it tests FFmpeg's
registered NUT formats, rather than accepting the snapshot command alone.
"""

import argparse
import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import sys


def probe_library(library):
    lib = C.CDLL(str(library))
    lib.av_guess_format.argtypes = [C.c_char_p, C.c_char_p, C.c_char_p]
    lib.av_guess_format.restype = C.c_void_p
    lib.av_find_input_format.argtypes = [C.c_char_p]
    lib.av_find_input_format.restype = C.c_void_p
    if not lib.av_guess_format(b"nut", None, None):
        raise ValueError("FFmpeg is missing the NUT muxer required by cached previews")
    if not lib.av_find_input_format(b"nut"):
        raise ValueError("FFmpeg is missing the NUT demuxer required by cached previews")

    signatures = {
        "mpv_create": ([], C.c_void_p),
        "mpv_initialize": ([C.c_void_p], C.c_int),
        "mpv_set_option_string": ([C.c_void_p, C.c_char_p, C.c_char_p], C.c_int),
        "mpv_get_property_string": ([C.c_void_p, C.c_char_p], C.c_void_p),
        "mpv_free": ([C.c_void_p], None),
        "mpv_terminate_destroy": ([C.c_void_p], None),
    }
    for name, (arguments, result) in signatures.items():
        getattr(lib, name).argtypes = arguments
        getattr(lib, name).restype = result
    handle = lib.mpv_create()
    if not handle:
        raise ValueError("Could not create the cached preview capability probe")
    try:
        for name, value in ((b"config", b"no"), (b"terminal", b"no"),
                            (b"vo", b"null"), (b"ao", b"null")):
            result = lib.mpv_set_option_string(handle, name, value)
            if result < 0:
                raise ValueError("Could not set probe option {}: {}".format(name.decode(), result))
        # Builds without Lua/JavaScript have no load-scripts option.
        result = lib.mpv_set_option_string(handle, b"load-scripts", b"no")
        if result not in (0, -5):  # MPV_ERROR_OPTION_NOT_FOUND
            raise ValueError("Could not disable probe scripts: {}".format(result))
        result = lib.mpv_initialize(handle)
        if result < 0:
            raise ValueError("Could not initialize the cached preview probe: {}".format(result))
        pointer = lib.mpv_get_property_string(handle, b"floe-cache-version")
        if not pointer:
            raise ValueError("mpv is missing Floe disk cache v4")
        try:
            version = int(C.string_at(pointer).decode("ascii"))
        finally:
            lib.mpv_free(pointer)
        if version < 4:
            raise ValueError("Rebuild tracked zcopy source: Floe cache v4 is required")
        pointer = lib.mpv_get_property_string(handle, b"command-list")
        if not pointer:
            raise ValueError("Could not read mpv's command list")
        try:
            commands = json.loads(C.string_at(pointer).decode("utf8"))
        finally:
            lib.mpv_free(pointer)
        if not any(command.get("name") == "floe-cache-snapshot" for command in commands):
            raise ValueError("mpv is missing the floe-cache-snapshot command")
    finally:
        lib.mpv_terminate_destroy(handle)
    print("Verified Floe cache v4, cached snapshot and NUT muxer/demuxer: {}".format(library))


def verify_package(package):
    frameworks = sorted(framework for framework in package.rglob("*.framework")
                        if (framework / "Versions/A" / framework.stem).is_file())
    libraries = [framework / "Versions/A/Mpv" for framework in frameworks
                 if framework.stem == "Mpv"]
    if not libraries:
        raise ValueError("No Mpv.framework binary found in {}".format(package))
    environment = os.environ.copy()
    environment["DYLD_FRAMEWORK_PATH"] = ":".join(
        sorted({str(framework.parent) for framework in frameworks}))
    for name in ("DYLD_LIBRARY_PATH", "DYLD_FALLBACK_FRAMEWORK_PATH",
                 "DYLD_FALLBACK_LIBRARY_PATH"):
        environment.pop(name, None)
    for library in libraries:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--library", str(library)],
            env=environment, capture_output=True, text=True, timeout=30,
        )
        if result.returncode:
            raise ValueError("{}: {}".format(library, result.stderr.strip()))
        print(result.stdout.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", nargs="?", type=Path,
                        help="extracted directory containing the XCFrameworks")
    parser.add_argument("--library", type=Path, help="probe a native libmpv binary directly")
    args = parser.parse_args()
    if (args.package is None) == (args.library is None):
        parser.error("provide either a package directory or --library")
    try:
        if args.library is not None:
            probe_library(args.library.resolve())
        else:
            verify_package(args.package.resolve())
    except (OSError, AttributeError, ValueError, subprocess.TimeoutExpired) as error:
        print("macOS cache preview artifact verification failed: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
