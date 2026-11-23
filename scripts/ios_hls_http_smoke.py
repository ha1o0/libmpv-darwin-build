#!/usr/bin/env python3
"""Probe short chunked/no-range HLS with the real iOS libmpv framework set."""
import argparse
import base64
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import struct
import tempfile
import threading

from ios_http_cache_smoke import compile_reader, read


def fixture(source):
    data = source.read_text()
    block = data.split('base64Decode(', 1)[1].split('),', 1)[0]
    compressed = ''.join(re.findall(r"'([^']+)'", block))
    return gzip.decompress(base64.b64decode(compressed))


def wrap(payload):
    prefix = bytearray(bytes.fromhex('5249464600000000574542505650384c000000002f00000010071011118888fe07'))
    result = prefix + payload + b'\x00'
    struct.pack_into('<I', result, 4, len(result) - 8)
    struct.pack_into('<I', result, 16, len(result) - 21)
    return bytes(result)


class Origin:
    def __init__(self, plain):
        self.requests = []
        wrapped = wrap(plain)
        range_offset = 4096
        range_data = b'\x00' * range_offset + wrapped
        image = base64.b64decode('UklGRh4AAABXRUJQVlA4TBEAAAAvAUAAAAdQwAIWsP+BiOh/AAA=')
        origin = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_args):
                pass

            def do_GET(self):
                path = self.path.split('?', 1)[0]
                if path == '/live':
                    payload = ('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n'
                               '#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:2,\n/proxy_ts?sequence=0\n'
                               '#EXT-X-DISCONTINUITY\n#EXTINF:2,\n/proxy_ts?sequence=1\n'
                               '#EXT-X-ENDLIST\n').encode()
                    content_type = 'application/vnd.apple.mpegurl'
                elif path == '/range-playlist':
                    payload = (f'#EXTM3U\n#EXT-X-VERSION:4\n#EXT-X-TARGETDURATION:2\n'
                               f'#EXTINF:2,\n#EXT-X-BYTERANGE:{len(wrapped)}@{range_offset}\n'
                               '/range-data.bin\n#EXT-X-ENDLIST\n').encode()
                    content_type = 'application/vnd.apple.mpegurl'
                elif path == '/plain.ts':
                    payload, content_type = plain, 'video/mp2t'
                elif path == '/image.webp':
                    payload, content_type = image, 'image/webp'
                else:
                    payload, content_type = wrapped, 'application/octet-stream'
                if path == '/range-data.bin':
                    requested = re.fullmatch(r'bytes=(\d+)-(\d*)', self.headers.get('Range', ''))
                    start = int(requested[1]) if requested else 0
                    end = int(requested[2]) + 1 if requested and requested[2] else len(range_data)
                    end = min(end, len(range_data))
                    payload = range_data[start:end]
                    self.send_response(206 if requested else 200)
                    if requested:
                        self.send_header('Content-Range', f'bytes {start}-{end - 1}/{len(range_data)}')
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Content-Length', str(len(payload)))
                else:
                    # Intentionally ignore Range, omit both length and range
                    # support, and finish a small response before demux probing.
                    self.send_response(200)
                    self.send_header('Transfer-Encoding', 'chunked')
                self.send_header('Content-Type', content_type)
                self.end_headers()
                origin.requests.append({'path': self.path, 'requestedRange': self.headers.get('Range'),
                                        'bytes': len(payload), 'chunked': path != '/range-data.bin'})
                try:
                    if path == '/range-data.bin':
                        self.wfile.write(payload)
                    else:
                        for offset in range(0, len(payload), 256):
                            chunk = payload[offset:offset + 256]
                            self.wfile.write(f'{len(chunk):x}\r\n'.encode() + chunk + b'\r\n')
                        self.wfile.write(b'0\r\n\r\n')
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--device', required=True)
    parser.add_argument('--fixture-source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plain = fixture(args.fixture_source)
    scratch = Path(__file__).resolve().parent.parent / '.cache' / 'ios-transport-validation'
    scratch.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='hls-', dir=scratch) as temporary:
        executable = compile_reader(args.root.resolve(), Path(temporary))
        origin = Origin(plain)
        try:
            for endpoint, mode, codec, size in [('/plain.ts', 'read', 'mpeg2video', 32),
                                               ('/wrapped.webp', 'read', 'mpeg2video', 32),
                                               ('/live?fixture=chunked', 'hls', 'mpeg2video', 32),
                                               ('/range-playlist', 'hls', 'mpeg2video', 32),
                                               ('/image.webp', 'image', 'webp', 2)]:
                origin.requests.clear()
                report, log = read(args.device, executable, mode,
                    f'http://127.0.0.1:{origin.server.server_port}{endpoint}', None, 'unused')
                args.output.with_name(endpoint.split('?', 1)[0].strip('/').replace('.', '-') + '.log').write_text(log)
                if report['codec'] != codec or report['width'] != size or report['height'] != size:
                    raise RuntimeError(f'Wrong actual decoder/geometry for {endpoint}: {report}')
                results.append({'endpoint': endpoint, 'result': report, 'requests': list(origin.requests)})
        finally:
            origin.close()
    args.output.write_text(json.dumps({'simulator': args.device, 'fixtureBytes': len(plain), 'checks': results}, indent=2))
    print(args.output.read_text())


if __name__ == '__main__':
    main()
