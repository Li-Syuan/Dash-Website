"""Private Windows process host. Not a user command or configurable runner.

The parent starts this stdlib-only file with isolated Python (-I -S), assigns
its existing process handle to a Job Object, then sends GO. No target process
can start before assignment. The target receives DEVNULL stdin, not this pipe.
"""
import json
import os
import subprocess
import sys


def main():
    if len(sys.argv) < 4 or sys.argv[2] != '--':
        return 125
    status_path, argv = sys.argv[1], sys.argv[3:]
    if sys.stdin.buffer.readline(4) != b'GO\n':
        return 125
    result = {'returncode': None, 'launch_error': None}
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, shell=False)
        result['returncode'] = process.wait()
    except Exception as error:
        result['launch_error'] = type(error).__name__
    with open(status_path, 'w', encoding='utf-8') as stream:
        json.dump(result, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    return 0


if __name__ == '__main__':
    sys.exit(main())
