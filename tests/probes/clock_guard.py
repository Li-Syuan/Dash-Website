"""Task-only, read-only clock validity monitor for a test subprocess.

Does not change system time, authentication, or test outcomes. A detected clock
backstep permanently marks this invocation infrastructure-invalid (exit 86),
even if the child succeeds. There is no retry. Compatible with Python 3.8.
"""
import argparse
import ctypes
import json
import math
import os
import subprocess
import threading
import time


_CLOCK_GETTIME_NS = getattr(time, 'clock_gettime_ns', None)
_REALTIME = getattr(time, 'CLOCK_REALTIME', None)
_MONOTONIC = getattr(time, 'CLOCK_MONOTONIC', None)
_TIME_NS = time.time_ns
_MONOTONIC_NS = time.monotonic_ns
_PERF_COUNTER_NS = time.perf_counter_ns
_WINDOWS = os.name == 'nt'
_PRECISE_TIME = None
_FILETIME = None
_WINDOWS_CLOCK_ERROR = None
_QPC_RESOLUTION_NS = max(1, int(math.ceil(time.get_clock_info('perf_counter').resolution * 1e9)))

if _WINDOWS:
    try:
        from ctypes import wintypes
        _FILETIME = wintypes.FILETIME
        _PRECISE_TIME = ctypes.WinDLL('kernel32', use_last_error=True).GetSystemTimePreciseAsFileTime
        _PRECISE_TIME.argtypes = [ctypes.POINTER(_FILETIME)]
        _PRECISE_TIME.restype = None
    except (AttributeError, OSError) as error:
        # The old coarse pair is not a safe fallback for a 1ms guard.
        _WINDOWS_CLOCK_ERROR = type(error).__name__


def precision_metadata():
    if _WINDOWS:
        return dict(backend='windows_precise_filetime_qpc',
                    wall_clock='GetSystemTimePreciseAsFileTime',
                    interval_clock='time.perf_counter_ns/QueryPerformanceCounter',
                    wall_representation_ns=100, wall_uncertainty_ns=1000,
                    interval_resolution_ns=_QPC_RESOLUTION_NS,
                    offset_uncertainty_ns=1000 + _QPC_RESOLUTION_NS,
                    available=_PRECISE_TIME is not None,
                    initialization_error=_WINDOWS_CLOCK_ERROR)
    return dict(backend='posix_clock_gettime' if _CLOCK_GETTIME_NS is not None else 'portable_time',
                wall_clock='CLOCK_REALTIME' if _CLOCK_GETTIME_NS is not None else 'time.time_ns',
                interval_clock='CLOCK_MONOTONIC' if _CLOCK_GETTIME_NS is not None else 'time.monotonic_ns',
                wall_uncertainty_ns=0, offset_uncertainty_ns=0, available=True)


def _precise_windows_time_ns():
    if _PRECISE_TIME is None:
        raise RuntimeError('High-precision Windows clock is unavailable.')
    value = _FILETIME()
    _PRECISE_TIME(ctypes.byref(value))
    ticks = (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)
    return (ticks - 116444736000000000) * 100


def _sample():
    # Bracket the wall clock read: a descheduled thread must not look like a
    # clock step. interval endpoints bound the realtime-minus-monotonic offset.
    uncertainty, wall_uncertainty = 0, 0
    if _WINDOWS:
        # Python 3.10 monotonic_ns may use GetTickCount64 and time_ns may use
        # coarse FILETIME. Bracketing that pair cannot bound its quantization.
        # Read-only precise UTC + QPC avoids the mismatched coarse-clock ticks.
        before = _PERF_COUNTER_NS()
        realtime = _precise_windows_time_ns()
        after = _PERF_COUNTER_NS()
        wall_uncertainty = 1000  # API precision <1us; not a claim of UTC accuracy.
        uncertainty = wall_uncertainty + _QPC_RESOLUTION_NS
    elif _CLOCK_GETTIME_NS is not None:
        before = _CLOCK_GETTIME_NS(_MONOTONIC)
        realtime = _CLOCK_GETTIME_NS(_REALTIME)
        after = _CLOCK_GETTIME_NS(_MONOTONIC)
    else:
        before = _MONOTONIC_NS()
        realtime = _TIME_NS()
        after = _MONOTONIC_NS()
    if after < before:
        raise RuntimeError('Interval clock moved backwards during sampling.')
    result = dict(realtime_ns=realtime, monotonic_before_ns=before,
                  monotonic_after_ns=after, offset_low_ns=realtime-after-uncertainty,
                  offset_high_ns=realtime-before+uncertainty)
    if _WINDOWS:
        result.update(wall_uncertainty_ns=wall_uncertainty, offset_uncertainty_ns=uncertainty)
    return result


def backward_step(previous, current, tolerance_ns):
    # A non-overlapping offset interval proves a backward adjustment larger
    # than tolerance, including adjustments hidden by a long scheduling gap.
    upper_step = current['offset_high_ns'] - previous['offset_low_ns']
    wall_backwards = (current['realtime_ns'] + current.get('wall_uncertainty_ns', 0)
                      < previous['realtime_ns'] - previous.get('wall_uncertainty_ns', 0))
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
                  interval_seconds=self.interval_seconds, tolerance_ns=self.tolerance_ns,
                  precision=precision_metadata())
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
        try:
            final_sample = _sample()
        except BaseException as error:
            self.monitor_errors.append(type(error).__name__)
            self.emit('clock_monitor_error', error_type=type(error).__name__)
            final_sample = None
        self.emit('clock_guard_complete', sample=final_sample,
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
