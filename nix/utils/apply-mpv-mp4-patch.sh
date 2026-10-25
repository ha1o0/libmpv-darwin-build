#!/usr/bin/env bash
set -euo pipefail

patch_file=$1
patch_command=${2:-patch}

# A tracked mpv branch can already contain this companion patch. Check every
# hunk in reverse without modifying the source; a header/option name alone is
# insufficient to distinguish a complete implementation from a partial port.
if "$patch_command" --dry-run --force --reverse --fuzz=0 -p1 < "$patch_file" >/dev/null 2>&1; then
    echo "MP4 bounded interleave already present; keeping source unchanged."
elif "$patch_command" --dry-run --force --fuzz=0 -p1 < "$patch_file" >/dev/null 2>&1; then
    "$patch_command" --force --fuzz=0 -p1 < "$patch_file"
else
    echo "MP4 policy is neither absent nor already applied; review the companion patch against this mpv source." >&2
    exit 1
fi
