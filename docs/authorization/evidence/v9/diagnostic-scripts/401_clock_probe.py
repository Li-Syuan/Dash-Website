"""Task-local WSL clock and cookie diagnostics; never changes clock or app code.

Run with WSL system Python. It samples the clock while extracting the approved
Python runtime archive, then executes only an in-memory Flask session probe.
No cookie, session payload, secret, report row or request body is emitted.
"""
import argparse
from collections import deque
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tarfile
import tempfile
import threading
import time


LOCK = threading.Lock()
START_MONO = time.monotonic()


def emit(event, **fields):
    with LOCK:
        print(json.dumps(dict(event=event, realtime_ns=time.time_ns(),
                              monotonic_ns=time.monotonic_ns(),
                              time_function=type(time.time).__name__, **fields)), flush=True)


def journal(phase):
    result = subprocess.run(['journalctl', '-b', '-u', 'systemd-timesyncd',
                             '--no-pager', '--output=json'], capture_output=True, text=True)
    previous = None
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)
            rt = int(item['__REALTIME_TIMESTAMP'])
            mono = int(item['__MONOTONIC_TIMESTAMP'])
            fields = dict(phase=phase, entry_realtime_us=rt, entry_monotonic_us=mono,
                          message=item.get('MESSAGE'), boot_id=item.get('_BOOT_ID'))
            if previous:
                fields['offset_step_us'] = (rt - previous[0]) - (mono - previous[1])
            emit('timesync_journal', **fields)
            previous = rt, mono
        except (ValueError, KeyError, TypeError):
            pass
    state = subprocess.run(['timedatectl', 'show', '--property=NTPSynchronized',
                            '--property=NTP', '--property=Timezone'],
                           capture_output=True, text=True)
    emit('timesync_status', phase=phase, status=state.stdout.strip(), returncode=state.returncode)


def monitor(stop):
    previous_rt, previous_mono = time.time_ns(), time.monotonic_ns()
    minimum = 0
    maximum = 0
    count = 0
    while not stop.wait(.005):
        rt, mono = time.time_ns(), time.monotonic_ns()
        wall_delta, mono_delta = rt - previous_rt, mono - previous_mono
        delta = wall_delta - mono_delta
        minimum = min(minimum, delta)
        maximum = max(maximum, delta)
        if wall_delta < 0 or abs(delta) > 50_000_000:
            emit('clock_backstep' if delta < 0 else 'clock_forwardstep', previous_realtime_ns=previous_rt,
                 observed_realtime_ns=rt, wall_delta_ns=wall_delta,
                 monotonic_delta_ns=mono_delta, offset_step_ns=delta)
            count += 1
        previous_rt, previous_mono = rt, mono
    emit('clock_monitor_complete', step_count=count, minimum_offset_step_ns=minimum,
         maximum_offset_step_ns=maximum)


def clock_only(duration):
    deadline = time.monotonic() + duration
    previous_sync = None
    while time.monotonic() < deadline:
        sync = subprocess.run(['timedatectl', 'show-timesync', '--property=NTPMessage',
                               '--property=PollIntervalUSec', '--property=Frequency'],
                              capture_output=True, text=True)
        emit('clock_progress', raw_monotonic_ns=time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW))
        if sync.stdout != previous_sync:
            emit('timesync_packet', properties=sync.stdout.strip(), returncode=sync.returncode)
            previous_sync = sync.stdout
        time.sleep(1)


def session_probe(duration):
    from flask import Flask, session
    from itsdangerous import BadSignature
    from itsdangerous.timed import TimestampSigner

    original = TimestampSigner.unsign
    counts = dict(rejects=0, http401=0, checks=0, issued=0)

    def observe(self, value, *args, **kwargs):
        try:
            return original(self, value, *args, **kwargs)
        except BadSignature as error:
            signed = getattr(error, 'date_signed', None)
            counts['rejects'] += 1
            emit('signature_rejected', reason=type(error).__name__,
                 signed_epoch=None if signed is None else signed.timestamp(),
                 current_epoch=int(time.time()), max_age=kwargs.get('max_age'))
            raise

    TimestampSigner.unsign = observe
    app = Flask('task-session-clock-probe')
    app.config.update(SECRET_KEY=secrets.token_bytes(32), TESTING=True)

    @app.route('/probe')
    def probe():
        return ('ok', 200) if session.get('_user_id') == 'synthetic' else ('anonymous', 401)

    serializer = app.session_interface.get_signing_serializer(app)
    client = app.test_client()
    retained = deque()
    deadline = time.monotonic() + duration
    last_emit = 0
    emit('session_probe_start', python=sys.version.split()[0], duration=duration)
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            while retained and retained[0][0] < now - 3:
                retained.popleft()
            # Retain recent signatures so a clock step cannot be hidden by
            # immediately signing a replacement cookie after the step.
            for _, token in retained:
                try:
                    serializer.loads(token, max_age=3600)
                except BadSignature:
                    pass
                counts['checks'] += 1
            retained.append((now, serializer.dumps({'_user_id': 'synthetic'})))
            counts['issued'] += 1
            with client.session_transaction() as cookie_session:
                cookie_session['_user_id'] = 'synthetic'
                cookie_session['_fresh'] = True
            time.sleep(.01)
            response = client.get('/probe')
            if response.status_code == 401:
                counts['http401'] += 1
                emit('http_session_rejected', status=401)
            if now - last_emit > 1:
                emit('session_progress', **counts)
                last_emit = now
    finally:
        TimestampSigner.unsign = original
    emit('session_probe_complete', **counts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--duration', type=float, default=90)
    parser.add_argument('--session-only', action='store_true')
    parser.add_argument('--clock-only', action='store_true')
    args = parser.parse_args()
    if args.session_only:
        session_probe(args.duration)
        return
    emit('supervisor_start', boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
         uptime=Path('/proc/uptime').read_text().strip(), python=sys.version.split()[0])
    stop = threading.Event()
    thread = threading.Thread(target=monitor, args=(stop,), daemon=True)
    thread.start()
    journal('start')
    try:
        if args.clock_only:
            clock_only(args.duration)
            return
        task = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix='401-session-probe-') as directory:
            root = Path(directory)
            with tarfile.open(task / '.test-runtimes' / 'regression-runtime.tar') as archive:
                for member in archive.getmembers():
                    target = (root / member.name).resolve()
                    if root not in target.parents:
                        raise ValueError('Archive member is outside diagnostic workspace')
                    if member.issym() or member.islnk():
                        if root not in (target.parent / member.linkname).resolve().parents:
                            raise ValueError('Archive link is outside diagnostic workspace')
                archive.extractall(root)
            emit('runtime_ready', elapsed_seconds=time.monotonic()-START_MONO)
            result = subprocess.run([str(root / 'python' / 'bin' / 'python3.8'), '-B',
                                     str(Path(__file__).resolve()), '--session-only',
                                     '--duration', str(args.duration)],
                                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
            emit('session_process_complete', returncode=result.returncode)
    finally:
        journal('end')
        stop.set()
        thread.join(2)
        emit('supervisor_complete', elapsed_seconds=time.monotonic()-START_MONO)


if __name__ == '__main__':
    main()
