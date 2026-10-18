#!/usr/bin/env python3
"""Run a command through Minis' real Android PRoot instrumentation.

Requires an isolated test app and same-signature instrumentation already installed.
Temporary sync credentials can arrive through a private device file or a one-shot
USB loopback connection. Neither option logs the credential or bundles it in an APK.
"""
import argparse
from contextlib import contextmanager
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import shlex
import subprocess
import threading


@contextmanager
def token_server(token):
    if not 1 <= len(token) <= 512:
        raise ValueError('invalid temporary token length')
    path = '/' + secrets.token_urlsafe(32)
    lock = threading.Lock()
    used = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            nonlocal used
            with lock:
                accepted = hmac.compare_digest(self.path, path) and not used
                if accepted:
                    used = True
            if not accepted:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(token)))
            self.end_headers()
            self.wfile.write(token)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], path
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def instrument(a, token_url=None):
    args = ['am', 'instrument', '-w', '-r']
    if a.sync_token_file:
        args += ['-e', 'syncTokenFile', a.sync_token_file]
    if token_url:
        args += ['-e', 'syncTokenUrl', token_url]
    args += ['-e', 'command', a.command_file.read_text(),
             'test.zhangyue.minis.clients/test.ClientRuntimeInstrumentation']
    return subprocess.run([a.adb, '-s', a.serial, 'shell', shlex.join(args)],
                          capture_output=True, text=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adb', default='adb')
    p.add_argument('--serial', required=True)
    p.add_argument('--command-file', type=Path, required=True)
    p.add_argument('--log', type=Path, required=True)
    group = p.add_mutually_exclusive_group()
    group.add_argument('--sync-token-file', help='Private file path on the isolated Android device')
    group.add_argument('--host-sync-token-file', type=Path, help='Private host temporary token, transferred once through adb reverse; no root needed')
    a = p.parse_args()
    adb = [a.adb, '-s', a.serial]
    if a.host_sync_token_file:
        existing = subprocess.run(adb + ['reverse', '--list'], capture_output=True, text=True, check=True)
        if 'tcp:18769' in existing.stdout:
            p.error('device port 18769 is already forwarded; preserve the existing mapping')
        with token_server(a.host_sync_token_file.read_bytes().strip()) as (port, path):
            subprocess.run(adb + ['reverse', 'tcp:18769', 'tcp:' + str(port)], check=True, capture_output=True)
            try:
                result = instrument(a, 'http://127.0.0.1:18769' + path)
            finally:
                subprocess.run(adb + ['reverse', '--remove', 'tcp:18769'], capture_output=True)
    else:
        result = instrument(a)
    a.log.parent.mkdir(parents=True, exist_ok=True)
    a.log.write_text(result.stdout + result.stderr)
    a.log.chmod(0o600)
    # Instrumentation can fail while adb itself still exits zero.
    passed = (result.returncode == 0 and 'INSTRUMENTATION_CODE: -1' in result.stdout
              and 'exit=0' in result.stdout and 'FAIL ' not in result.stdout)
    print('command passed' if passed else 'command failed', 'log:', a.log)
    print('Independently verify code, tests and Dida readback; CLI exit 0 alone is insufficient.')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
