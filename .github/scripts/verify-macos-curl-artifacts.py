#!/usr/bin/env python3
"""Check the packaged macOS network backend without loading either CPU slice.

Build-time checks validate curl's configuration. These release checks ensure
both packaged Mpv slices retain that backend and its static dependencies, and
that no framework load command depends on the build machine's Nix store.
"""

import argparse
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ARCHITECTURES = {"arm64", "x86_64"}
NETWORK_LICENSES = ("curl.txt", "nghttp2.txt", "openssl.txt")
NETWORK_DEPENDENCY = re.compile(
    r"(?:^|/)(?:lib)?(?:curl|nghttp2|ssl|crypto|openssl)"
    r"(?:\.\d+)*\.(?:dylib|framework)(?:/|$)", re.IGNORECASE)


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError("{} failed: {}".format(" ".join(map(str, args)),
                                               result.stderr.strip()))
    return result.stdout


def load_commands(output):
    """Read paths and deployment metadata from one architecture's otool -l."""
    paths = []
    rpaths = []
    minimum_macos = None
    for block in re.split(r"(?m)^Load command \d+\s*$", output):
        command = re.search(r"(?m)^\s*cmd (LC_\S+)\s*$", block)
        if not command:
            continue
        command = command.group(1)
        if command == "LC_RPATH":
            path = re.search(r"(?m)^\s*path (.*?) \(offset \d+\)\s*$", block)
            if path:
                rpaths.append(path.group(1))
        elif "DYLIB" in command:
            path = re.search(r"(?m)^\s*name (.*?) \(offset \d+\)\s*$", block)
            if path:
                paths.append(path.group(1))
        elif command == "LC_BUILD_VERSION":
            platform = re.search(r"(?m)^\s*platform (\S+)\s*$", block)
            version = re.search(r"(?m)^\s*minos ([\d.]+)\s*$", block)
            if platform and platform.group(1) in ("1", "MACOS", "macos") and version:
                minimum_macos = version.group(1)
        elif command == "LC_VERSION_MIN_MACOSX":
            version = re.search(r"(?m)^\s*version ([\d.]+)\s*$", block)
            if version:
                minimum_macos = version.group(1)
    return paths, rpaths, minimum_macos


def check_load_commands(output, label, is_mpv=False):
    paths, rpaths, minimum_macos = load_commands(output)
    if not paths:
        raise ValueError("{}: no dylib load commands were found".format(label))
    for path in paths + rpaths:
        if path == "/nix/store" or "/nix/store/" in path:
            raise ValueError("{}: load command refers to the Nix store: {}".format(label, path))
    for path in paths:
        if NETWORK_DEPENDENCY.search(path):
            raise ValueError("{}: network dependency must be linked statically: {}".format(label, path))
    if is_mpv:
        if not minimum_macos or tuple(map(int, minimum_macos.split("."))) not in (
                (11, 0), (11, 0, 0)):
            raise ValueError("{}: expected minimum macOS 11.0, got {}".format(label, minimum_macos))
        if not any("/Security.framework/" in path for path in paths):
            raise ValueError("{}: Apple SecTrust's Security.framework dependency is missing".format(label))


def check_curl_slice(data, label):
    # These strings belong to the compiled mpv AVIO class and curl 8.23's
    # conditional features_table, which curl_version_info() retains. Checking
    # each thin slice avoids accepting features present on only one CPU.
    # Do not require a leading NUL: Clang may merge string literal suffixes.
    markers = (
        (b"mpv_curl_avio\0", "mpv's libcurl AVIO backend"),
        (b"HTTP2\0", "curl HTTP/2 support"),
        (b"AppleSecTrust\0", "curl Apple SecTrust verification"),
        # curl formats "%s/%s" at runtime; "OpenSSL/" is not a literal.
        # Its TLS backend name also covers SSL, which may share this suffix.
        (b"OpenSSL\0", "the OpenSSL TLS backend"),
    )
    for marker, description in markers:
        if marker not in data:
            raise ValueError("{}: missing {}".format(label, description))


def check_curl_licenses(framework_version):
    licenses = framework_version / "Resources" / "Licenses" / "curl-transport"
    for name in NETWORK_LICENSES:
        license_file = licenses / name
        if not license_file.is_file() or not license_file.read_bytes().strip():
            raise ValueError("Missing or empty static transport licence: {}".format(license_file))


def check_mkv_startup_slice(data, label):
    # These are the option-table names, without the demuxer-mkv prefix added
    # by mpv's configuration groups. Require them in both packaged CPU slices.
    for option in (b"skip-tags", b"defer-cues"):
        if option + b"\0" not in data:
            raise ValueError("{}: missing MKV startup option demuxer-mkv-{}".format(
                label, option.decode("ascii")))


def verify_package(package):
    binaries = sorted(
        framework / "Versions" / "A" / framework.stem
        for framework in package.rglob("*.framework")
        if (framework / "Versions" / "A" / framework.stem).is_file())
    if not any(binary.name == "Mpv" for binary in binaries):
        raise ValueError("No Mpv.framework binary found in {}".format(package))
    with tempfile.TemporaryDirectory(prefix="mpv-curl-artifact-") as temp:
        for binary in binaries:
            is_mpv = binary.name == "Mpv"
            if is_mpv:
                check_curl_licenses(binary.parent)
            architectures = set(run("lipo", "-archs", str(binary)).split())
            if architectures != ARCHITECTURES:
                raise ValueError("{}: expected arm64 and x86_64, got {}".format(
                    binary, ", ".join(sorted(architectures))))
            for architecture in sorted(ARCHITECTURES):
                label = "{} ({})".format(binary, architecture)
                commands = run("otool", "-arch", architecture, "-l", str(binary))
                check_load_commands(commands, label, is_mpv=is_mpv)
                if is_mpv:
                    thin = Path(temp) / ("Mpv-" + architecture)
                    run("lipo", str(binary), "-thin", architecture, "-output", str(thin))
                    data = thin.read_bytes()
                    check_curl_slice(data, label)
                    check_mkv_startup_slice(data, label)
    print("Verified static curl, HTTP/2, OpenSSL, Apple SecTrust, MKV startup options and bundled licences: {}".format(package))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, help="extracted directory containing the XCFrameworks")
    args = parser.parse_args()
    try:
        verify_package(args.package)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print("macOS curl artifact verification failed: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
