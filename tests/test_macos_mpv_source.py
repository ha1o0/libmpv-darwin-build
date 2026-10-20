"""Source tracking must not update legacy mpv or leave partial pins on failure."""

import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/update-macos-mpv-source.py"
SPEC = importlib.util.spec_from_file_location("mpv_source", SCRIPT)
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)

LOCK = '''{
  mpv = {
    version = "legacy";
    url = "https://github.com/ha1o0/mpv/archive/legacy.tar.gz";
    sha256 = "legacy-digest";
  };
  mpvMacosNativeVideo = {
    repository = "https://github.com/ha1o0/mpv.git";
    branch = "zcopy";
    version = "old-version";
    url = "old-url";
    sha256 = "old-digest";
  };
}
'''
REVISION = "a" * 40
DIGEST = "b" * 64


class SourceTrackingTests(unittest.TestCase):
    def test_only_macos_video_follows_the_branch_and_gets_immutable_coordinates(self):
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / "packages.lock.nix"
            lock.write_text(LOCK, encoding="utf8")
            with patch.object(updater, "resolve_revision", return_value=REVISION) as resolve, \
                    patch.object(updater, "archive_digest", return_value=DIGEST) as digest:
                updater.refresh_lock(lock)
            resolve.assert_called_once_with("https://github.com/ha1o0/mpv.git", "zcopy")
            url = "https://github.com/ha1o0/mpv/archive/" + REVISION + ".tar.gz"
            digest.assert_called_once_with(url)
            actual = lock.read_text(encoding="utf8")
            self.assertEqual(actual.split("  mpvMacosNativeVideo")[0],
                             LOCK.split("  mpvMacosNativeVideo")[0])
            for value in ("zcopy-" + REVISION[:12], url, DIGEST):
                self.assertIn('"' + value + '"', actual)

    def test_remote_or_download_failure_keeps_the_original_lock(self):
        for failure in ("resolve_revision", "archive_digest"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                lock = Path(temp) / "packages.lock.nix"
                lock.write_text(LOCK, encoding="utf8")
                with patch.object(updater, "resolve_revision", return_value=REVISION), \
                        patch.object(updater, "archive_digest", return_value=DIGEST), \
                        patch.object(updater, failure, side_effect=OSError("offline")):
                    with self.assertRaises(OSError):
                        updater.refresh_lock(lock)
                self.assertEqual(lock.read_text(encoding="utf8"), LOCK)

    def test_another_ref_or_multiple_results_cannot_silently_select_a_source(self):
        for output in (REVISION + "\trefs/heads/main\n",
                       REVISION + "\trefs/heads/zcopy\n" + REVISION + "\trefs/heads/main\n"):
            with patch.object(updater.subprocess, "run", return_value=
                              subprocess.CompletedProcess([], 0, stdout=output)):
                with self.assertRaises(ValueError):
                    updater.resolve_revision("https://github.com/ha1o0/mpv.git", "zcopy")

    def test_source_checksum_is_for_the_downloaded_archive(self):
        data = b"compressed source archive bytes"
        with patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(data)):
            self.assertEqual(updater.archive_digest("https://example.invalid/archive.tar.gz"),
                             hashlib.sha256(data).hexdigest())


if __name__ == "__main__":
    unittest.main(verbosity=2)
