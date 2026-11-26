"""Compile the production read-only RPU helper against real FFmpeg headers."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DoviSourceSpecTest(unittest.TestCase):
    def test_mel_fel_unknown_and_bounds(self):
        include = os.environ.get('FFMPEG_INCLUDE_DIR')
        if not include or not (Path(include) / 'libavutil/dovi_meta.h').exists():
            self.skipTest('set FFMPEG_INCLUDE_DIR to the pinned FFmpeg headers')
        patch = (ROOT / 'patches/mpv-dovi-source-spec.patch').read_text()
        section = patch.split('+++ b/video/dovi_source_spec.h\n', 1)[1]
        header = '\n'.join(line[1:] for line in section.splitlines()
                           if line.startswith('+')) + '\n'
        with tempfile.TemporaryDirectory(prefix='floe-dv-spec-') as directory:
            directory = Path(directory)
            (directory / 'dovi_source_spec.h').write_text(header)
            binary = directory / 'check'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-I', include, '-I', str(directory),
                            str(ROOT / 'tests/dovi_source_spec.c'),
                            '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
