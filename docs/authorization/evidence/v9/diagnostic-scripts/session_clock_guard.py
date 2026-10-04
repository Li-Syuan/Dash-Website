"""Task-only, read-only clock validity monitor for a test subprocess.

Does not change system time, authentication, or test outcomes. A detected clock
backstep permanently marks this invocation infrastructure-invalid (exit 86),
even if the child succeeds. There is no retry. Compatible with Python 3.8.
"""
import argparse
import json
import subprocess
import threading
import time


_CLOCK_GETTIME_NS = getattr(time, 'clock_gettime_ns', None)
_REALTIME = getattr(time, 'CLOCK_REALTIME', None)
_MONOTONIC = getattr(time, 'CLOCK_MONOTONIC', None)
_TIME_NS = time.time_ns
_MONOTONIC_NS = time.monotonic_ns


def _sample():
    # Bracket the wall clock read: a descheduled thread must not look like a
    # clock step. interval endpoints bound the realtime-minus-monotonic offset.
    if _CLOCK_GETTIME_NS is not None:
        before = _CLOCK_GETTIME_NS(_MONOTONIC)
        realtime = _CLOCK_GETTIME_NS(_REALTIME)
        after = _CLOCK_GETTIME_NS(_MONOTONIC)
    else:
        before = _MONOTONIC_NS()
        realtime = _TIME_NS()
        after = _MONOTONIC_NS()
    return dict(realtime_ns=realtime, monotonic_before_ns=before,
                monotonic_after_ns=after, offset_low_ns=realtime-after,
                offset_high_ns=realtime-before)


def backward_step(previous, current, tolerance_ns):
    # A non-overlapping offset interval proves a backward adjustment larger
    # than tolerance, including adjustments hidden by a long scheduling gap.
    upper_step = current['offset_high_ns'] - previous['offset_low_ns']
    wall_backwards = current['realtime_ns'] < previous['realtime_ns']
    if wall_backwards or upper_step < -tolerance_ns:
        return dict(wall_delta_ns=current['realtime_ns']-previous['realtime_ns'],
                    offset_step_upper_bound_ns=upper_step,
                    offset_step_lower_bound_ns=current['offset_low_ns']-previous['offset_high_ns'])
    return None


class ClockGuard:
    def __init__(self, stream, interval_seconds=0.005, tolerance_ns=1_000_000):
        self.stream = stream
        self.interval_seconds = interval_seconds
        self.tolerance_ns = tolerance_ns
        self.backsteps = []
        self.monitor_errors = []
        self._stop = threading.Event()
        self._thread = None

    @property
    def invalid(self):
        return bool(self.backsteps or self.monitor_errors)

    def emit(self, event, **fields):
        self.stream.write(json.dumps(dict(event=event, **fields)) + '\n')
        self.stream.flush()

    def start(self):
        if self._thread is not None:
            raise RuntimeError('ClockGuard cannot be restarted or reset')
        initial = _sample()
        self.emit('clock_guard_start', sample=initial,
                  interval_seconds=self.interval_seconds, tolerance_ns=self.tolerance_ns)
        self._thread = threading.Thread(target=self._monitor, args=(initial,), daemon=True)
        self._thread.start()
        return self

    def _monitor(self, previous):
        try:
            while True:
                stopped = self._stop.wait(self.interval_seconds)
                current = _sample()
                evidence = backward_step(previous, current, self.tolerance_ns)
                if evidence is not None:
                    evidence.update(previous=previous, current=current)
                    self.backsteps.append(evidence)
                    self.emit('clock_backstep', **evidence)
                previous = current
                if stopped:
                    break
        except BaseException as error:
            self.monitor_errors.append(type(error).__name__)
            self.emit('clock_monitor_error', error_type=type(error).__name__)

    def finish(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self.emit('clock_guard_complete', sample=_sample(),
                  infrastructure_valid=not self.invalid, backsteps=len(self.backsteps),
                  monitor_errors=self.monitor_errors)
        return not self.invalid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('A child command is required')
    with open(args.output, 'w', encoding='utf-8') as stream:
        guard = ClockGuard(stream).start()
        try:
            result = subprocess.run(command)
        finally:
            guard.finish()
        stream.write(json.dumps(dict(event='child_result', exit_code=result.returncode,
                                     infrastructure_valid=not guard.invalid)) + '\n')
        return 86 if guard.invalid else result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
