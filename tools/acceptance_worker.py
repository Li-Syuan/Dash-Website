"""Fixed unittest producer for the local acceptance tool (Python 3.8)."""
import argparse
import gc
import json
from pathlib import Path
import sys
import threading
import time
import unittest


AUTHORIZATION_MODULES = (
    'test_authorization_boundaries', 'test_service_isolation',
    'test_registry_identity_boundary', 'test_definition_repository',
    'test_unified_portal', 'test_report_template', 'test_v11_integration',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=('full', 'authorization', 'self'), required=True)
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    project = args.project_root.resolve()
    if args.output.exists():
        parser.error('Result already exists; choose a new invocation')
    sys.path.insert(0, str(project))
    sys.path.insert(0, str(project / 'tests'))
    loader = unittest.TestLoader()
    if args.suite == 'authorization':
        suite = loader.loadTestsFromNames(AUTHORIZATION_MODULES)
    else:
        pattern = 'test_*acceptance*.py' if args.suite == 'self' else 'test_*.py'
        suite = loader.discover(str(project / 'tests'), pattern=pattern)
    runtime_errors = []
    original_hook = sys.unraisablehook
    original_thread_hook = threading.excepthook

    def unraisable(event):
        runtime_errors.append(getattr(event.exc_type, '__name__', 'UnraisableError'))
        original_hook(event)

    sys.unraisablehook = unraisable
    def thread_error(event):
        runtime_errors.append('thread:' + getattr(event.exc_type, '__name__', 'ThreadError'))
        original_thread_hook(event)

    threading.excepthook = thread_error
    class RecordedResult(unittest.TextTestResult):
        def __init__(self, *values, **kwargs):
            super().__init__(*values, **kwargs)
            self.started_ids = []
            self.successful_ids = []

        def startTest(self, test):
            self.started_ids.append(test.id())
            super().startTest(test)

        def addSuccess(self, test):
            self.successful_ids.append(test.id())
            super().addSuccess(test)

    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult).run(suite)
    gc.collect()
    counts = dict(tests=result.testsRun, failures=len(result.failures),
                  errors=len(result.errors), skipped=len(result.skipped),
                  expected_failures=len(result.expectedFailures),
                  unexpected_successes=len(result.unexpectedSuccesses))
    def parent_id(test):
        return getattr(test, 'test_case', test).id()

    affected = {parent_id(test) for test, _ in result.failures + result.errors + result.skipped + result.expectedFailures}
    affected.update(parent_id(test) for test in result.unexpectedSuccesses)
    counts['failed_methods'] = len({parent_id(test) for test, _ in result.failures + result.errors})
    counts['passed'] = max(0, counts['tests'] - len(affected))
    failed = bool(result.failures or result.errors or result.unexpectedSuccesses or runtime_errors)
    incomplete = result.testsRun == 0 or bool(result.skipped or result.expectedFailures)
    status = 'FAILED' if failed else 'INCOMPLETE' if incomplete else 'PASSED'
    record = dict(schema=1, producer='acceptance_worker', run_id=args.run_id,
                  suite=args.suite, status=status, counts=counts,
                  failed_test_ids=[test.id() for test, _ in result.failures + result.errors],
                  failed_parent_ids=[parent_id(test) for test, _ in result.failures + result.errors],
                  started_test_ids=result.started_ids, successful_test_ids=result.successful_ids,
                  expected_failure_ids=[test.id() for test, _ in result.expectedFailures],
                  unexpected_success_ids=[test.id() for test in result.unexpectedSuccesses],
                  skipped=[dict(test=test.id(), reason=reason) for test, reason in result.skipped],
                  runtime_errors=runtime_errors, elapsed_seconds=time.monotonic()-started)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2, ensure_ascii=True)
        stream.write('\n')
    return 1 if failed else 3 if incomplete else 0


if __name__ == '__main__':
    raise SystemExit(main())
