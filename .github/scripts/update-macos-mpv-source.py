#!/usr/bin/env python3
"""Resolve the tracked macOS video mpv branch before a reproducible Nix build."""

import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request


SOURCE = re.compile(r"(?ms)^  mpvMacosNativeVideo = \{\n.*?^  \};")


def configuration(text):
    matches = list(SOURCE.finditer(text))
    if len(matches) != 1:
        raise ValueError("Expected one mpvMacosNativeVideo source in packages.lock.nix")
    fields = dict(re.findall(r'^    (\w+) = "([^"\n]*)";$', matches[0].group(), re.M))
    repository, branch = fields["repository"], fields["branch"]
    if not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+(?:\.git)?", repository):
        raise ValueError("The tracked mpv repository must be a GitHub HTTPS URL")
    if not re.fullmatch(r"[\w.-]+(?:/[\w.-]+)*", branch):
        raise ValueError("Invalid tracked mpv branch")
    return matches[0], repository, branch


def resolve_revision(repository, branch):
    ref = "refs/heads/" + branch
    output = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--refs", repository, ref],
        check=True, capture_output=True, text=True, timeout=60,
    ).stdout.splitlines()
    if len(output) != 1:
        raise ValueError("Expected exactly one matching mpv branch")
    fields = output[0].split()
    if len(fields) != 2 or fields[1] != ref or not re.fullmatch(r"[0-9a-f]{40}", fields[0]):
        raise ValueError("The remote did not return the requested mpv branch")
    return fields[0]


def archive_digest(url):
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "libmpv-darwin-source-resolver"})
    with urllib.request.urlopen(request, timeout=60) as response:
        while True:
            data = response.read(1024 * 1024)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()


def refresh_lock(path):
    original = path.read_text(encoding="utf8")
    match, repository, branch = configuration(original)
    revision = resolve_revision(repository, branch)
    url = repository.removesuffix(".git") + "/archive/" + revision + ".tar.gz"
    digest = archive_digest(url)
    values = {
        "version": branch.replace("/", "-") + "-" + revision[:12],
        "url": url,
        "sha256": digest,
    }
    block = match.group()
    for name, value in values.items():
        block, count = re.subn(r'^    ' + name + r' = "[^"\n]*";$',
                               '    ' + name + ' = "' + value + '";', block, flags=re.M)
        if count != 1:
            raise ValueError("Missing or duplicate macOS mpv field: " + name)
    updated = original[:match.start()] + block + original[match.end():]
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf8", dir=path.parent,
                                         prefix="." + path.name + ".", delete=False) as output:
            temporary = Path(output.name)
            output.write(updated)
        temporary.chmod(path.stat().st_mode & 0o777)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return repository, branch, revision, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock-file", type=Path,
                        default=Path(__file__).resolve().parents[2] / "packages.lock.nix")
    args = parser.parse_args()
    try:
        repository, branch, revision, digest = refresh_lock(args.lock_file)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("Unable to resolve macOS video mpv source: {}".format(error), file=sys.stderr)
        return 1
    message = "macOS video mpv: {} @ {} -> {} (archive sha256 {})".format(
        repository, branch, revision, digest)
    print(message)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf8") as output:
            output.write(message + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
