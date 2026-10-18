#!/usr/bin/env python3
"""Run a command through Minis' real Android PRoot instrumentation.

Requires an isolated test app and same-signature instrumentation already installed.
The optional sync token file is on the Android device; its contents are never logged.
"""
import argparse
from pathlib import Path
import shlex
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adb', default='adb')
    p.add_argument('--serial', required=True)
    p.add_argument('--command-file', type=Path, required=True)
    p.add_argument('--log', type=Path, required=True)
    p.add_argument('--sync-token-file', help='Private file path on the isolated Android device')
    a = p.parse_args()
    args = ['am', 'instrument', '-w', '-r']
    if a.sync_token_file:
        args += ['-e', 'syncTokenFile', a.sync_token_file]
    args += ['-e', 'command', a.command_file.read_text(),
             'test.zhangyue.minis.clients/test.ClientRuntimeInstrumentation']
    result = subprocess.run([a.adb, '-s', a.serial, 'shell', shlex.join(args)],
                            capture_output=True, text=True)
    a.log.parent.mkdir(parents=True, exist_ok=True)
    a.log.write_text(result.stdout + result.stderr)
    # Instrumentation can fail while adb itself still exits zero.
    passed = (result.returncode == 0 and 'INSTRUMENTATION_CODE: -1' in result.stdout
              and 'exit=0' in result.stdout and 'FAIL ' not in result.stdout)
    print('command passed' if passed else 'command failed', 'log:', a.log)
    print('Independently verify code, tests and Dida readback; CLI exit 0 alone is insufficient.')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
