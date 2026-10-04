"""Read-only clock precision/backstep contracts and lexical output-path safety."""
import ctypes
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from tools.acceptance_core import safe_output_path


_SPEC = importlib.util.spec_from_file_location(
    'test_readonly_clock_guard', str(Path(__file__).parent / 'probes' / 'clock_guard.py'))
guard_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(guard_module)


def sample(wall, before, after=None, uncertainty=0):
    after = before if after is None else after
    return dict(realtime_ns=wall, monotonic_before_ns=before, monotonic_after_ns=after,
                offset_low_ns=wall-after-uncertainty, offset_high_ns=wall-before+uncertainty,
                wall_uncertainty_ns=uncertainty)


class StopSequence:
    def __init__(self, values):
        self.values = iter(values)

    def wait(self, interval):
        return next(self.values)

    def set(self):
        pass


class ClockGuardTests(unittest.TestCase):
    def test_windows_sampling_uses_precise_utc_bracketed_by_qpc_not_coarse_clocks(self):
        with patch.object(guard_module, '_WINDOWS', True), \
                patch.object(guard_module, '_PERF_COUNTER_NS', side_effect=[10000, 10100]) as qpc, \
                patch.object(guard_module, '_precise_windows_time_ns', return_value=50000) as utc, \
                patch.object(guard_module, '_QPC_RESOLUTION_NS', 50), \
                patch.object(guard_module, '_MONOTONIC_NS', side_effect=AssertionError('coarse clock used')), \
                patch.object(guard_module, '_TIME_NS', side_effect=AssertionError('coarse wall clock used')):
            result = guard_module._sample()
        self.assertEqual((qpc.call_count, utc.call_count), (2, 1))
        self.assertEqual(result['offset_low_ns'], 38850)
        self.assertEqual(result['offset_high_ns'], 41050)
        self.assertEqual((result['wall_uncertainty_ns'], result['offset_uncertainty_ns']), (1000, 1050))

    def test_posix_clock_gettime_read_order_and_bounds_are_unchanged(self):
        with patch.object(guard_module, '_WINDOWS', False), \
                patch.object(guard_module, '_MONOTONIC', 1), \
                patch.object(guard_module, '_REALTIME', 2), \
                patch.object(guard_module, '_CLOCK_GETTIME_NS', side_effect=[10000, 50000, 10100]) as reader:
            result = guard_module._sample()
        self.assertEqual([call.args[0] for call in reader.call_args_list], [1, 2, 1])
        self.assertEqual(result, dict(realtime_ns=50000, monotonic_before_ns=10000,
                                     monotonic_after_ns=10100, offset_low_ns=39900, offset_high_ns=40000))

    def test_filetime_epoch_conversion_preserves_integer_nanoseconds(self):
        class FileTime(ctypes.Structure):
            _fields_ = [('dwLowDateTime', ctypes.c_uint32), ('dwHighDateTime', ctypes.c_uint32)]

        def read(pointer):
            value = 116444736000000000 + 12345
            pointer._obj.dwLowDateTime = value & 0xffffffff
            pointer._obj.dwHighDateTime = value >> 32

        with patch.object(guard_module, '_FILETIME', FileTime), \
                patch.object(guard_module, '_PRECISE_TIME', read):
            self.assertEqual(guard_module._precise_windows_time_ns(), 1234500)

    def test_missing_precise_windows_api_fails_closed_instead_of_using_coarse_fallback(self):
        with patch.object(guard_module, '_WINDOWS', True), \
                patch.object(guard_module, '_PRECISE_TIME', None), \
                patch.object(guard_module, '_TIME_NS', side_effect=AssertionError('fallback used')):
            with self.assertRaises(RuntimeError):
                guard_module._sample()
            self.assertFalse(guard_module.precision_metadata()['available'])

    def test_true_wall_backstep_and_hidden_offset_step_remain_detectable(self):
        previous = sample(10000, 1000)
        evidence = guard_module.backward_step(previous, sample(9999, 1100), 1000)
        self.assertEqual(evidence['wall_delta_ns'], -1)
        # Wall time advanced, but by less than the known interval elapsed.
        evidence = guard_module.backward_step(previous, sample(11000, 5000), 1000)
        self.assertLess(evidence['offset_step_upper_bound_ns'], -1000)
        # Windows precision margin does not conceal a material true backstep.
        self.assertIsNotNone(guard_module.backward_step(
            sample(10000000, 1000, uncertainty=1000),
            sample(9000000, 2000, uncertainty=1000), 1000000))

    def test_quantization_uncertainty_and_descheduling_are_not_false_backsteps(self):
        self.assertIsNone(guard_module.backward_step(
            sample(10000000, 1000, uncertainty=1000),
            sample(9999900, 1100, uncertainty=1000), 1000000))
        self.assertIsNone(guard_module.backward_step(
            sample(10000, 1000, 1200), sample(12000, 2000, 5000), 1000))
        self.assertIsNone(guard_module.backward_step(sample(10000, 1000), sample(12000, 3000), 1000))
        self.assertIsNone(guard_module.backward_step(sample(10000, 1000), sample(15000, 3000), 1000))

    def test_backstep_permanently_invalidates_even_after_clock_recovers(self):
        stream = io.StringIO()
        guard = guard_module.ClockGuard(stream, tolerance_ns=100)
        guard._stop = StopSequence([False, True])
        with patch.object(guard_module, '_sample', side_effect=[sample(9000, 2000),
                                                               sample(12000, 3000), sample(13000, 4000)]):
            guard._monitor(sample(10000, 1000))
            self.assertFalse(guard.finish())
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual(len(guard.backsteps), 1)
        self.assertTrue(guard.invalid)
        self.assertFalse(events[-1]['infrastructure_valid'])

    def test_monitor_and_final_sample_errors_are_invalid_and_sanitized(self):
        stream = io.StringIO()
        guard = guard_module.ClockGuard(stream)
        guard._stop = StopSequence([True])
        with patch.object(guard_module, '_sample', side_effect=OSError('PRIVATE-CLOCK-DETAIL')):
            guard._monitor(sample(10000, 1000))
            self.assertFalse(guard.finish())
        self.assertEqual(guard.monitor_errors, ['OSError', 'OSError'])
        self.assertNotIn('PRIVATE-CLOCK-DETAIL', stream.getvalue())
        self.assertIsNone(json.loads(stream.getvalue().splitlines()[-1])['sample'])

    @unittest.skipUnless(os.name == 'nt', 'Native Windows precise-clock regression')
    def test_native_windows_guard_reports_precise_backend_without_false_backsteps(self):
        stream = io.StringIO()
        guard = guard_module.ClockGuard(stream).start()
        try:
            with self.assertRaises(RuntimeError):
                guard.start()
            time.sleep(0.15)
        finally:
            valid = guard.finish()
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        metadata = events[0]['precision']
        self.assertEqual(metadata['wall_clock'], 'GetSystemTimePreciseAsFileTime')
        self.assertEqual(metadata['interval_clock'], 'time.perf_counter_ns/QueryPerformanceCounter')
        self.assertEqual(metadata['wall_representation_ns'], 100)
        self.assertTrue(metadata['available'])
        self.assertTrue(valid, stream.getvalue())


class SafeOutputPathTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_missing_output_ancestry_is_checked_without_creating_or_resolving(self):
        path = self.root / 'output' / 'new' / 'run'
        with patch.object(Path, 'resolve', side_effect=AssertionError('resolved before checking')):
            self.assertEqual(safe_output_path(path, directory=True), path)
        self.assertFalse(path.exists())
        with self.assertRaises(ValueError):
            safe_output_path(self.root / 'output' / '..' / 'escape')
        existing = self.root / 'file'
        existing.write_text('synthetic')
        with self.assertRaises(ValueError):
            safe_output_path(existing, directory=True)
        with self.assertRaises(ValueError):
            safe_output_path(existing / 'child')

    @unittest.skipUnless(os.name == 'nt', 'Native Windows junction fixture')
    def test_existing_junction_ancestor_is_rejected_before_missing_output_creation(self):
        import _winapi
        target = self.root / 'target'
        target.mkdir()
        junction = self.root / 'junction'
        _winapi.CreateJunction(str(target), str(junction))
        try:
            with patch.object(Path, 'resolve', side_effect=AssertionError('resolved before checking')):
                with self.assertRaises(ValueError):
                    safe_output_path(junction / 'missing' / 'run', directory=True)
            self.assertEqual(list(target.iterdir()), [])
        finally:
            junction.rmdir()

    @unittest.skipIf(os.name == 'nt', 'Symlink fixture uses POSIX permissions')
    def test_existing_symlink_ancestor_is_rejected_even_when_leaf_is_missing(self):
        target = self.root / 'target'
        target.mkdir()
        link = self.root / 'link'
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            safe_output_path(link / 'missing' / 'run', directory=True)
        self.assertEqual(list(target.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
