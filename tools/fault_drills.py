"""Run the four bounded, synthetic fault drills using existing regressions.

This is not a product launcher, scheduler, benchmark or deployment check.
Run under tests/probes/clock_guard.py for recorded clock-validity evidence.
"""
import argparse
from pathlib import Path
import sys
import unittest


ETL = 'test_etl_recovery_faults.ETLRecoveryFaultTests.'
XLSX = 'test_large_xlsx.LargeXlsxTests.'
SCENARIOS = {
    'etl-interruption': (
        ETL + 'test_kill_before_claim_commit_rolls_back_receipt_and_run',
        ETL + 'test_kill_during_adapter_preserves_claim_and_denies_unsafe_retry',
        ETL + 'test_kill_before_snapshot_commit_keeps_no_partial_snapshot',
        ETL + 'test_kill_before_publication_commit_keeps_previous_publication',
        ETL + 'test_kill_after_publication_commit_before_ack_replays_only_receipt',
        ETL + 'test_partial_backfill_restart_continues_only_unclaimed_dates',
    ),
    'duplicate-schedule': (
        ETL + 'test_two_scheduler_processes_claim_same_due_slot_once',
        'test_etl_dispatch_engine.ETLEngineTests.'
        'test_missed_ticks_coalesce_and_disabled_jobs_do_not_run',
    ),
    'database-lock': (
        ETL + 'test_writer_lock_timeout_creates_no_claim_then_same_request_succeeds',
        ETL + 'test_transient_writer_lock_waits_then_commits_once',
        ETL + 'test_writer_lock_after_adapter_keeps_prior_snapshot_without_replay',
        ETL + 'test_reader_lock_at_publication_commit_rolls_back_publication_and_success',
    ),
    'export-revocation': (
        XLSX + 'test_policy_revocation_during_snapshot_prevents_workbook_creation',
        XLSX + 'test_revocation_before_first_row_does_not_create_an_orphan_worksheet',
        XLSX + 'test_policy_revocation_during_serialization_prevents_publication_and_cleans_files',
        XLSX + 'test_policy_revocation_during_zip_save_is_checked_before_return',
        'test_unified_portal.UnifiedPortalTransportTests.'
        'test_revocation_after_export_prevents_download_publication',
        'test_unified_portal.UnifiedPortalTransportTests.'
        'test_revocation_during_xlsx_archive_prevents_http_download',
        'test_unified_portal.UnifiedPortalTransportTests.'
        'test_revocation_during_final_export_refresh_prevents_download',
    ),
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=tuple(SCENARIOS), action='append',
                        help='Run only the selected scenario; repeat to select more.')
    parser.add_argument('--list', action='store_true',
                        help='List exact regression IDs without importing the app or running faults.')
    args = parser.parse_args(argv)
    selected = tuple(dict.fromkeys(args.scenario or SCENARIOS))
    names = []
    for scenario in selected:
        print(scenario + ':', flush=True)
        for name in SCENARIOS[scenario]:
            names.append(name)
            if args.list:
                print('  ' + name, flush=True)
    if args.list:
        return 0
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / 'tests'))
    suite = unittest.TestLoader().loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    # unittest considers skips/expected failures successful. A drill cannot
    # claim completion when a requested boundary was not exercised.
    if not result.wasSuccessful():
        print('FAILED: one or more selected boundaries failed or could not load.', flush=True)
        return 1
    if result.testsRun != len(names) or result.skipped or result.expectedFailures:
        print('INCOMPLETE: some selected boundaries were not successfully exercised.', flush=True)
        return 2
    print('PASSED: selected synthetic fault scenarios only; not whole-project acceptance.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
