#!/usr/bin/env python3
"""Check independent subtitle styling in the packaged native macOS CPU slice.

The caller checks both CPU slices and their dependencies separately. This
probe loads the packaged frameworks in a fresh process and checks the registered
style options plus independent text and bitmap HDR values.
"""

import argparse
import ctypes as C
import os
from pathlib import Path
import subprocess
import sys


def probe_library(library):
    lib = C.CDLL(str(library))
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
        raise ValueError("Could not create the subtitle capability probe")
    try:
        options = {
            "config": "no", "terminal": "no", "vo": "null", "ao": "null",
            "secondary-sub-style": "independent",
            "secondary-sub-font": "sans-serif", "secondary-sub-font-size": "32",
            "secondary-sub-color": "#FFFFFF00", "secondary-sub-bold": "yes",
            "secondary-sub-border-size": "2", "secondary-sub-border-color": "#FF000000",
            "secondary-sub-border-style": "outline-and-shadow",
            "secondary-sub-shadow-offset": "3", "secondary-sub-back-color": "#C0000000",
            "secondary-sub-ass-style-overrides": "Bold=1",
            "sub-hdr-peak": "100", "secondary-sub-hdr-peak": "300",
            "image-subs-hdr-peak": "400", "secondary-image-subs-hdr-peak": "1000",
        }
        for name, value in options.items():
            result = lib.mpv_set_option_string(handle, name.encode(), value.encode())
            if result < 0:
                raise ValueError("Missing or invalid {}: {}".format(name, result))
        result = lib.mpv_set_option_string(handle, b"load-scripts", b"no")
        if result not in (0, -5):
            raise ValueError("Could not disable probe scripts")
        if lib.mpv_initialize(handle) < 0:
            raise ValueError("Could not initialize the subtitle capability probe")
        for name, expected in {
            "secondary-sub-style": "independent", "sub-hdr-peak": "100",
            "secondary-sub-hdr-peak": "300", "image-subs-hdr-peak": "400",
            "secondary-image-subs-hdr-peak": "1000",
        }.items():
            pointer = lib.mpv_get_property_string(handle, name.encode())
            if not pointer:
                raise ValueError("Could not read {}".format(name))
            try:
                actual = C.string_at(pointer).decode("utf8")
            finally:
                lib.mpv_free(pointer)
            if actual != expected:
                raise ValueError("{} read back {} instead of {}".format(name, actual, expected))
    finally:
        lib.mpv_terminate_destroy(handle)
    print("Verified independent secondary subtitle options: {}".format(library))


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
        print("macOS secondary subtitle artifact verification failed: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
