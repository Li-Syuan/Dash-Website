"""One unchanged full suite in a guarded, reversible WSL guest clock window."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import urlopen

TASK = Path(__file__).resolve().parent
SOURCE = TASK / 'work/QA_Portal_Integration'
OUTPUT = TASK / 'regression'
LABEL = 'wsl-python38-v9-final'
SERVICE = 'systemd-timesyncd.service'
sys.path.insert(0, str(TASK))
from session_clock_guard import ClockGuard

def state():
    result = subprocess.run(['systemctl', 'show', SERVICE, '--property=ActiveState',
                             '--property=SubState', '--property=UnitFileState'],
                            check=True, capture_output=True, text=True)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)

def workloads():
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal() or int(entry.name) == os.getpid():
            continue
        try:
            name = (entry / 'comm').read_text().strip()
            args = (entry / 'cmdline').read_bytes()
        except OSError:
            continue
        if (name.startswith(('python', 'node', 'java', 'dotnet', 'gunicorn', 'uvicorn')) and
                b'unattended-upgrades' not in args and b'wsl-pro-service' not in args):
            found.append(dict(pid=int(entry.name), name=name))
    return found

def extract(archive, root):
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            target = (root / member.name).resolve()
            assert root in target.parents
            if member.issym() or member.islnk():
                assert root in (target.parent / member.linkname).resolve().parents
        tar.extractall(root)

def terminate(signum, frame):
    raise SystemExit(128 + signum)

signal.signal(signal.SIGTERM, terminate)
signal.signal(signal.SIGINT, terminate)

with tempfile.TemporaryDirectory(prefix='dash-v9-regression-') as folder:
    root = Path(folder)
    print('Preparing task-owned native runtime and final source snapshot', flush=True)
    extract(TASK / '.test-runtimes/regression-runtime.tar', root)
    project = root / 'project'
    shutil.copytree(SOURCE, project, ignore=shutil.ignore_patterns(
        '__pycache__', 'output', '.git', '*.sqlite*', 'instance', '*-windows310.txt'))
    hashes = {p.relative_to(SOURCE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in SOURCE.rglob('*') if p.is_file() and p.suffix in ('.py', '.js', '.cjs', '.css')
              and 'output' not in p.relative_to(SOURCE).parts}
    for name, digest in hashes.items():
        assert hashlib.sha256((project / name).read_bytes()).hexdigest() == digest, name
    (OUTPUT / 'wsl-v9-source-hashes.json').write_text(json.dumps(hashes, indent=2), encoding='utf-8')
    version = 'v22.14.0'
    filename = 'node-' + version + '-linux-x64.tar.xz'
    url = 'https://nodejs.org/dist/' + version + '/'
    sums = urlopen(url + 'SHASUMS256.txt', timeout=60).read().decode('utf-8')
    expected = next(line.split()[0] for line in sums.splitlines() if line.split()[-1] == filename)
    archive = root / filename
    with urlopen(url + filename, timeout=60) as response, archive.open('wb') as stream:
        shutil.copyfileobj(response, stream)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected
    extract(archive, root)
    env = dict(os.environ, PATH=str(root / ('node-' + version + '-linux-x64') / 'bin') +
               os.pathsep + os.environ['PATH'], PYTHONIOENCODING='utf-8')
    command = [str(root / 'python/bin/python3.8'), '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v']
    before = state()
    other = workloads()
    if other:
        raise RuntimeError('Other language runtimes are active; no service change made: ' + json.dumps(other))
    assert before['ActiveState'] in ('active', 'inactive'), before
    unit = 'dash-qa-v9-timesync-restore-' + str(os.getpid())
    metadata = dict(before=before, other_application_workloads=other, modified=False,
                    watchdog_seconds=180, host_clock_modified=False, configuration_modified=False)
    lock = OUTPUT / '.wsl-v9-clock-window.lock'
    lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(lock_fd)
    guard = None
    child = None
    watchdog = False
    started = time.monotonic()
    try:
        if before['ActiveState'] == 'active':
            # A transient safety timer restores the service even if this runner is killed.
            subprocess.run(['systemd-run', '--quiet', '--unit=' + unit, '--on-active=180s',
                            '/usr/bin/systemctl', 'start', SERVICE], check=True, timeout=10)
            watchdog = True
            subprocess.run(['systemctl', 'stop', SERVICE], check=True, timeout=10)
            metadata['modified'] = True
        metadata['during'] = state()
        assert metadata['during']['ActiveState'] == 'inactive'
        print('Running full suite once; guest time sync temporarily paused with automatic restoration', flush=True)
        with (OUTPUT / (LABEL + '-clock.jsonl')).open('w', encoding='utf-8') as clock_log:
            guard = ClockGuard(clock_log).start()
            try:
                with (OUTPUT / (LABEL + '.txt')).open('w', encoding='utf-8') as log:
                    child = subprocess.Popen(command, cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT)
                    guard.emit('tests_child_start', pid=child.pid)
                    returncode = child.wait()
                    guard.emit('tests_child_exit', returncode=returncode)
            finally:
                if child is not None and child.poll() is None:
                    child.terminate()
                    child.wait(timeout=15)
                metadata['clock_valid'] = guard.finish()
    finally:
        if metadata['modified']:
            subprocess.run(['systemctl', 'start', SERVICE], check=True, timeout=10)
        metadata['after'] = state()
        metadata['restored'] = metadata['after'] == before
        if watchdog and metadata['restored']:
            subprocess.run(['systemctl', 'stop', unit + '.timer'], check=True, timeout=10)
        (OUTPUT / (LABEL + '-environment.json')).write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        lock.unlink()
        assert metadata['restored'], 'Guest time service state was not restored'
    content = (OUTPUT / (LABEL + '.txt')).read_text(encoding='utf-8', errors='replace')
    result = dict(label=LABEL, python='3.8.20', platform='MSI WSL Ubuntu native temporary filesystem',
        node=version, source_hashes_verified=True, command='python -B -m unittest discover -s tests -v',
        elapsed_seconds=round(time.monotonic()-started, 3), suite_exit_code=returncode,
        exit_code=86 if guard.invalid else returncode, clock_guard_valid=not guard.invalid,
        session_or_signer_modified=False, automatic_suite_retries=0, service_restored=metadata['restored'],
        summary_lines=[line for line in content.splitlines() if re.match(r'^(Ran \d+ tests|OK|FAILED|FAIL:|ERROR:)', line)],
        skipped=[line for line in content.splitlines() if '... skipped ' in line],
        unraisable_count=content.count('Exception ignored in:'),
        shutdown_tracebacks=content[content.rfind('\nRan '):].count('Traceback (most recent call last):'))
    (OUTPUT / (LABEL + '.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2), flush=True)
    raise SystemExit(result['exit_code'])
