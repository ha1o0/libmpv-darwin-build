#!/usr/bin/env python3
"""Verify real simulator libmpv byte-cache reuse and strict HTTPS transport."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import ssl
import subprocess
import tempfile
import threading


def run(arguments, **kwargs):
    return subprocess.check_output(arguments, text=True, stderr=subprocess.STDOUT, **kwargs).strip()


class Origin:
    def __init__(self, video, certificate=None, private_key=None):
        self.video = video
        self.offline = False
        self.requests = []
        origin = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_args):
                pass

            def do_HEAD(self):
                self.respond(False)

            def do_GET(self):
                self.respond(True)

            def respond(self, body):
                origin.requests.append({'method': self.command, 'path': self.path,
                                        'range': self.headers.get('Range')})
                if origin.offline:
                    self.send_response(503)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                match = re.fullmatch(r'bytes=(\d+)-(\d*)', self.headers.get('Range', ''))
                start = int(match[1]) if match else 0
                end = min(int(match[2]) + 1, len(origin.video)) if match and match[2] else len(origin.video)
                if start >= end:
                    self.send_response(416)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                self.send_response(206 if match else 200)
                if match:
                    self.send_header('Content-Range', f'bytes {start}-{end - 1}/{len(origin.video)}')
                self.send_header('ETag', '"ios-curl-byte-fixture-v1"')
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Content-Length', str(end - start))
                self.end_headers()
                if body:
                    try:
                        self.wfile.write(origin.video[start:end])
                    except (BrokenPipeError, ConnectionResetError):
                        pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        if certificate:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certificate, private_key)
            self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.server.server_port

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def certificates(directory):
    openssl = shutil.which('openssl')
    if not openssl:
        raise RuntimeError('An existing openssl command is required')
    root = directory / 'root.pem'
    root_key = directory / 'root.key'
    leaf = directory / 'localhost.pem'
    leaf_key = directory / 'localhost.key'
    root_config = directory / 'root.cnf'
    root_config.write_text('[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ca\n'
                          '[dn]\nCN=Floe temporary simulator TLS root\n'
                          '[ca]\nbasicConstraints=critical,CA:TRUE\n'
                          'keyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n')
    leaf_config = directory / 'leaf.cnf'
    leaf_config.write_text('[req]\nprompt=no\ndistinguished_name=dn\nreq_extensions=leaf\n'
                          '[dn]\nCN=localhost\n[leaf]\nbasicConstraints=critical,CA:FALSE\n'
                          'keyUsage=critical,digitalSignature,keyEncipherment\n'
                          'extendedKeyUsage=serverAuth\nsubjectAltName=DNS:localhost\n')
    run([openssl, 'req', '-new', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
         '-sha256', '-config', str(root_config), '-keyout', str(root_key), '-out', str(root)])
    request = directory / 'localhost.csr'
    run([openssl, 'req', '-new', '-newkey', 'rsa:2048', '-nodes', '-sha256',
         '-config', str(leaf_config), '-keyout', str(leaf_key), '-out', str(request)])
    run([openssl, 'x509', '-req', '-in', str(request), '-CA', str(root), '-CAkey', str(root_key),
         '-CAserial', str(directory / 'root.srl'), '-CAcreateserial', '-days', '1', '-sha256', '-extfile', str(leaf_config),
         '-extensions', 'leaf', '-out', str(leaf)])
    root_key.chmod(0o600)
    leaf_key.chmod(0o600)
    return root, leaf, leaf_key


def compile_reader(root, directory):
    frameworks = directory / 'Frameworks'
    frameworks.mkdir()
    for xcframework in sorted(root.glob('*.xcframework')):
        metadata = plistlib.loads((xcframework / 'Info.plist').read_bytes())
        entries = [entry for entry in metadata['AvailableLibraries']
                   if entry.get('SupportedPlatformVariant') == 'simulator']
        if len(entries) != 1:
            raise RuntimeError(f'Expected one simulator slice in {xcframework.name}')
        entry = entries[0]
        destination = frameworks / entry['LibraryPath']
        shutil.copytree(xcframework / entry['LibraryIdentifier'] / entry['LibraryPath'], destination)
        for file in destination.rglob('*'):
            file.chmod(file.stat().st_mode | 0o200)
        subprocess.run(['codesign', '--force', '--sign', '-', str(destination)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    source = Path(__file__).with_suffix('.c')
    sdk = run(['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-path'])
    arch = 'arm64' if platform.machine() == 'arm64' else 'x86_64'
    executable = directory / 'reader'
    subprocess.run(['xcrun', '--sdk', 'iphonesimulator', 'clang', '-target',
                    f'{arch}-apple-ios15.0-simulator', '-isysroot', sdk,
                    '-Wl,-rpath,@executable_path/Frameworks', '-F', str(frameworks),
                    '-framework', 'Mpv', str(source), '-o', str(executable)], check=True)
    subprocess.run(['codesign', '--force', '--sign', '-', str(executable)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return executable


def read(device, executable, mode, url, cache, key, ca=None):
    environment = dict(os.environ)
    environment.update({'SIMCTL_CHILD_NO_PROXY': 'localhost,127.0.0.1',
                        'SIMCTL_CHILD_http_proxy': '', 'SIMCTL_CHILD_https_proxy': '',
                        'SIMCTL_CHILD_HTTP_PROXY': '', 'SIMCTL_CHILD_HTTPS_PROXY': '',
                        'SIMCTL_CHILD_ALL_PROXY': '', 'SIMCTL_CHILD_SSL_CERT_FILE': '',
                        'SIMCTL_CHILD_SSL_CERT_DIR': ''})
    try:
        completed = subprocess.run(['xcrun', 'simctl', 'spawn', device, str(executable), mode, url,
                                    str(cache) if cache else '-', key, str(ca) if ca else '-'],
                                   text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   timeout=45, env=environment)
    except subprocess.TimeoutExpired as error:
        processes = run(['ps', '-axo', 'pid=,command='])
        for process in processes.splitlines():
            pid, command = process.strip().split(None, 1)
            if command.startswith(f'{executable} '):
                subprocess.run(['kill', '-TERM', pid], check=False)
        raise RuntimeError(f'Simulator transport reader timed out:\n{error.stdout or ""}') from error
    marker = 'IOS_HTTP_SMOKE '
    reports = [json.loads(line.split(marker, 1)[1]) for line in completed.stdout.splitlines()
               if marker in line]
    if completed.returncode or len(reports) != 1:
        raise RuntimeError(f'Native iOS transport check failed ({completed.returncode}):\n{completed.stdout}')
    return reports[0], completed.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='Consumer *.xcframework directory')
    parser.add_argument('--device', required=True, help='Already booted simulator; never creates, resets, or deletes one')
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--public-https', help='Optional public MP4 URL to exercise native Apple trust without a CA override')
    args = parser.parse_args()
    video = args.video.read_bytes()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Keep executables in the producer workspace. macOS can suspend dyld while
    # checking Downloads access for a new simulator executable.
    scratch = Path(__file__).resolve().parent.parent / '.cache' / 'ios-transport-validation'
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='run-', dir=scratch) as temporary:
        directory = Path(temporary)
        executable = compile_reader(args.root.resolve(), directory)
        cache = directory / 'cache'
        cache.mkdir()
        key = hashlib.sha256(b'ios-curl-byte-cache-fixture').hexdigest()
        origin = Origin(video)
        try:
            url = f'http://127.0.0.1:{origin.port}/movie.mp4'
            online, online_log = read(args.device, executable, 'read', url, cache, key)
            metadata = list(cache.rglob(f'{key}.meta'))
            if len(metadata) != 1:
                raise RuntimeError(f'Native cache did not persist one real metadata file: {metadata}')
            data = json.loads(metadata[0].read_text())
            if data.get('length') != len(video):
                raise RuntimeError('Persisted cache length does not match the actual MP4')
            chunks = []
            pattern = re.compile(rf'{key}\.([0-9a-f]{{64}})\.(\d+)\.(\d+)\.([0-9a-f]{{64}})\.chunk')
            for file in cache.rglob(f'{key}.*.chunk'):
                match = pattern.fullmatch(file.name)
                if not match:
                    raise RuntimeError(f'Invalid published native chunk: {file.name}')
                payload = file.read_bytes()
                start, length = int(match[2]), int(match[3])
                if len(payload) != length or hashlib.sha256(payload).hexdigest() != match[4]:
                    raise RuntimeError('Persisted native chunk checksum/length mismatch')
                if payload != video[start:start + length]:
                    raise RuntimeError('Persisted native bytes differ from the actual origin MP4')
                chunks.append({'start': start, 'end': start + length, 'bytes': length})
            cursor = 0
            for chunk in sorted(chunks, key=lambda value: value['start']):
                if chunk['start'] > cursor:
                    raise RuntimeError(f'Native byte cache has an unfilled hole at {cursor}')
                cursor = max(cursor, chunk['end'])
            if cursor != len(video):
                raise RuntimeError(f'Native cache did not fill all MP4 bytes ({cursor}/{len(video)})')
            online_requests = list(origin.requests)
            origin.offline = True
            origin.requests.clear()
            offline, offline_log = read(args.device, executable, 'read', url, cache, key)
            if origin.requests:
                raise RuntimeError(f'Offline reopen unexpectedly contacted the origin: {origin.requests}')
        finally:
            origin.close()
        root, leaf, private_key = certificates(directory)
        secure = Origin(video, leaf, private_key)
        try:
            https = f'https://localhost:{secure.port}/movie.mp4'
            valid, valid_log = read(args.device, executable, 'read', https, None, key, root)
            trusted_requests = list(secure.requests)
            secure.requests.clear()
            unknown, unknown_log = read(args.device, executable, 'reject', https, None, key)
            if secure.requests:
                raise RuntimeError('Untrusted TLS completed an HTTP request')
            mismatch, mismatch_log = read(args.device, executable, 'reject',
                f'https://127.0.0.1:{secure.port}/movie.mp4', None, key, root)
            if secure.requests:
                raise RuntimeError('Wrong-hostname TLS completed an HTTP request')
        finally:
            secure.close()
        public = None
        if args.public_https:
            public, public_log = read(args.device, executable, 'read', args.public_https, None, key)
            args.output.with_name('public-https.log').write_text(public_log)
        logs = [online_log, offline_log, valid_log, unknown_log, mismatch_log]
        for label, log in zip(['online-cache', 'offline-cache', 'trusted-https', 'untrusted-https', 'wrong-hostname'], logs):
            args.output.with_name(f'{label}.log').write_text(log)
        report = {'simulator': args.device, 'fixtureBytes': len(video), 'metadata': data,
                  'chunks': chunks, 'cachedBytes': cursor, 'completeCache': True,
                  'onlineRequests': online_requests, 'offlineRequests': [],
                  'trustedHttpsRequests': trusted_requests, 'online': online, 'offline': offline,
                  'trustedHttps': valid, 'untrustedHttps': unknown, 'wrongHostname': mismatch,
                  'publicHttps': public, 'publicHttpsUrl': args.public_https}
        args.output.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
