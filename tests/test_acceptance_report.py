"""Offline report-rendering boundaries; fixtures do not execute project code."""
from copy import deepcopy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools.acceptance_core import artifact_manifest
from tools.acceptance_report import generate, redact


REQUIRED = ('regression', 'authorization', 'coverage_default', 'coverage_template',
            'browser', 'latency_before', 'latency_after', 'latency_compare',
            'xlsx_before', 'xlsx_after', 'xlsx_compare')
EXIT = dict(PASSED=0, FAILED=1, BLOCKED=2, INCOMPLETE=3, INVALID=86, TIMED_OUT=124)
REPOSITORY = Path(__file__).resolve().parents[1]


def strict_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))


class HtmlView(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tags, self.stack, self.text, self.roles, self.sections, self.headings = [], [], [], {}, {}, []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.tags.append((tag, values))
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.stack.append((tag, values))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.text.append(data)
        if any(tag == 'h2' for tag, _ in self.stack):
            self.headings.append(data)
        for _, attrs in self.stack:
            if attrs.get('data-evidence-role'):
                self.roles.setdefault(attrs['data-evidence-role'], []).append(data)
            if attrs.get('id'):
                self.sections.setdefault(attrs['id'], []).append(data)

    def role(self, name):
        return ' '.join(self.roles.get(name, []))

    def section(self, name):
        return ' '.join(self.sections.get(name, []))


class AcceptanceReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='acceptance-report-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'project'
        self.root.mkdir()
        self.api = SimpleNamespace(strict_json=strict_json, EXIT=EXIT, REQUIRED=REQUIRED,
                                   verify=Mock(return_value=dict(status='INCOMPLETE', exit_code=3,
                                               evidence_valid=True, whole_project_verified=False)))
        self.path, self.record = self.bundle('current')
        self.args = SimpleNamespace(report=self.path, project_root=self.root, baseline=None,
                                    history_report=[], output=self.root / 'output' / 'summary.html',
                                    max_age_seconds=86400)

    def bundle(self, name, status='INCOMPLETE', tests=17, source='a' * 64):
        directory = self.root / 'evidence' / name
        directory.mkdir(parents=True)
        now = time.time()
        counts = dict(tests=tests, passed=tests - 2, failures=1, errors=0, skipped=1,
                      expected_failures=0, unexpected_successes=0, failed_methods=1)
        steps = [dict(id=identifier, status='UNRUN', reason='not-selected', command=[], evidence=[])
                 for identifier in ('self',) + REQUIRED]
        regression = next(item for item in steps if item['id'] == 'regression')
        regression.update(status='FAILED', reason='test-failures', counts=counts,
                          evidence=['regression.json', 'regression.log'])
        worker = dict(schema=1, producer='acceptance_worker', run_id='fixture-' + name, suite='full',
                      counts=counts, runtime_errors=[], started_test_ids=['fixture.test_' + str(n) for n in range(tests)],
                      successful_test_ids=['fixture.test_' + str(n) for n in range(tests - 2)],
                      failed_test_ids=['test_etl_recovery_faults.ETLRecoveryFaultTests.test_partial_backfill_restart_continues_only_unclaimed_dates'],
                      failed_parent_ids=['test_etl_recovery_faults.ETLRecoveryFaultTests.test_partial_backfill_restart_continues_only_unclaimed_dates'],
                      expected_failure_ids=[], unexpected_success_ids=[],
                      skipped=[dict(test='fixture.test_platform_skip', reason='synthetic platform limitation')])
        (directory / 'regression.json').write_text(json.dumps(worker), encoding='utf-8')
        (directory / 'regression.log').write_text('ValueError: RAW-LOG-CONTENT-MUST-STAY-PRIVATE password=privateRawLogSecret\n', encoding='utf-8')
        wall, mono = time.time_ns(), time.monotonic_ns()
        sample = dict(realtime_ns=wall, monotonic_before_ns=mono, monotonic_after_ns=mono,
                      offset_low_ns=wall - mono, offset_high_ns=wall - mono)
        clock = dict(valid=True, backsteps=0, monitor_errors=[])
        events = [dict(event='clock_guard_start', tolerance_ns=1000000, sample=sample),
                  dict(event='clock_guard_complete', sample=sample, infrastructure_valid=True,
                       backsteps=0, monitor_errors=[])]
        (directory / 'clock.jsonl').write_text('\n'.join(json.dumps(item) for item in events) + '\n', encoding='utf-8')
        record = dict(schema=1, run_id='fixture-' + name, profile='unit', created_at=now - 2,
                      finished_at=now - 1, source=dict(digest=source, files={'app.py': 'b' * 64}),
                      toolchain={}, environment=dict(python='3.10.22', platform='Windows', release='unit-fixture',
                                                     machine='AMD64', packages={'Flask': '2.2.3'}),
                      baseline=None, source_changes=[], steps=steps, status=status, exit_code=EXIT[status],
                      selected_checks_passed=False, unverified=list(REQUIRED), invalid_reasons=[], clock=clock,
                      performance_budgets=dict(max_wall_regression_percent=None, max_rss_regression_percent=None))
        path = directory / 'report.json'
        self.seal(path, record)
        return path, record

    def seal(self, path=None, record=None, refresh_artifacts=True):
        path, record = path or self.path, record or self.record
        if refresh_artifacts:
            record['artifacts'] = artifact_manifest(path.parent, [item for item in path.parent.iterdir()
                                                   if item.name not in ('report.json', 'report.sha256')])
        path.write_text(json.dumps(record), encoding='utf-8')
        path.with_suffix('.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest() + '\n', encoding='ascii')

    def render(self, expected=None):
        with patch('subprocess.Popen', side_effect=AssertionError('report must never execute a process')), \
             patch('os.system', side_effect=AssertionError('report must never execute a shell')):
            result = generate(self.args, self.api)
        self.assertTrue(result['generated'], result)
        self.assertEqual(Path(result['report']).resolve(), self.args.output.resolve())
        self.assertTrue(self.args.output.is_file())
        if expected:
            self.assertEqual(result['status'], expected, result)
            self.assertEqual(result['exit_code'], EXIT[expected])
        return result, self.args.output.read_text(encoding='utf-8')

    def test_missing_input_generates_incomplete_without_claiming_whole_project_pass(self):
        self.args.report = None
        result, html = self.render('INCOMPLETE')
        self.assertFalse(result['whole_project_verified'])
        self.assertIn('INCOMPLETE', html)
        self.api.verify.assert_not_called()

    def test_missing_file_generates_incomplete(self):
        self.args.report = self.root / 'missing-report.json'
        result, _ = self.render('INCOMPLETE')
        self.assertFalse(result['whole_project_verified'])

    def test_canonical_verifier_controls_current_verdict_and_receives_bound_arguments(self):
        result, _ = self.render('INCOMPLETE')
        self.assertFalse(result['whole_project_verified'])
        self.api.verify.assert_called_once()
        args = self.api.verify.call_args[0][0]
        self.assertEqual(Path(args.report).resolve(), self.path.resolve())
        self.assertEqual(Path(args.project_root).resolve(), self.root.resolve())
        self.assertEqual(args.max_age_seconds, 86400)

    def test_canonical_failure_and_invalidity_cannot_be_relabelled_passed(self):
        for status in ('FAILED', 'INVALID', 'BLOCKED', 'TIMED_OUT'):
            with self.subTest(status=status):
                self.args.output = self.root / 'output' / (status + '.html')
                self.api.verify.return_value = dict(status=status, exit_code=EXIT[status],
                    evidence_valid=status != 'INVALID', whole_project_verified=False, reason='synthetic-verifier-reason')
                result, html = self.render(status)
                self.assertFalse(result['whole_project_verified'])
                display_status = 'FAIL' if status == 'FAILED' else status
                self.assertIn(display_status, ' '.join(HtmlView(html).headings))

    def test_malformed_json_and_duplicate_keys_generate_invalid(self):
        for index, content in enumerate(('{broken', '{"schema":1,"schema":1}', '[]')):
            self.args.output = self.root / 'output' / ('malformed-' + str(index) + '.html')
            self.path.write_text(content, encoding='utf-8')
            self.path.with_suffix('.sha256').write_text(hashlib.sha256(self.path.read_bytes()).hexdigest(), encoding='ascii')
            result, _ = self.render('INVALID')
            self.assertFalse(result['whole_project_verified'])
        self.api.verify.assert_not_called()

    def test_missing_or_wrong_public_seal_never_reaches_mock_success_verifier(self):
        self.api.verify.return_value = dict(status='PASSED', exit_code=0, evidence_valid=True, whole_project_verified=True)
        self.path.with_suffix('.sha256').unlink()
        self.render('INVALID')
        self.api.verify.assert_not_called()
        self.args.output = self.root / 'output' / 'wrong-seal.html'
        self.path.with_suffix('.sha256').write_text('0' * 64, encoding='ascii')
        self.render('INVALID')
        self.api.verify.assert_not_called()

    def test_tampered_artifact_is_invalid_even_when_report_seal_matches(self):
        (self.path.parent / 'regression.json').write_text('{}', encoding='utf-8')
        self.render('INVALID')
        self.api.verify.assert_not_called()

    def test_artifact_path_escape_is_invalid(self):
        self.record['artifacts'].append(dict(path='../outside.json', bytes=0, sha256='0' * 64))
        self.seal(refresh_artifacts=False)
        self.render('INVALID')
        self.api.verify.assert_not_called()

    def test_output_outside_project_output_and_non_html_extension_are_refused(self):
        for output in (self.root / 'escape.html', self.root.parent / 'escape.html',
                       self.root / 'output' / 'result.json', self.root / 'output' / '..' / 'escape.html',
                       self.root / 'output' / 'existing:report.html'):
            self.args.output = output
            with self.assertRaises(ValueError):
                generate(self.args, self.api)
            self.assertFalse(output.exists())

    def test_existing_output_is_preserved(self):
        self.args.output.parent.mkdir(parents=True)
        self.args.output.write_text('DO-NOT-OVERWRITE', encoding='utf-8')
        with self.assertRaises(ValueError):
            generate(self.args, self.api)
        self.assertEqual(self.args.output.read_text(encoding='utf-8'), 'DO-NOT-OVERWRITE')

    def test_html_has_restrictive_csp_no_scripts_and_no_active_event_attributes(self):
        _, html = self.render()
        view = HtmlView(html)
        policies = [attrs.get('content', '') for tag, attrs in view.tags
                    if tag == 'meta' and attrs.get('http-equiv', '').lower() == 'content-security-policy']
        self.assertTrue(policies)
        self.assertIn("default-src 'none'", policies[0])
        self.assertIn("script-src 'none'", policies[0])
        for tag, attrs in view.tags:
            self.assertNotIn(tag, ('script', 'iframe', 'object', 'embed', 'form'))
            self.assertFalse(any(name.lower().startswith('on') for name in attrs))
            self.assertFalse(any(str(value).strip().lower().startswith('javascript:') for value in attrs.values()))

    def test_report_ids_reasons_and_test_ids_cannot_inject_html(self):
        attack = '<script>alert("fixture-injection")</script><img src=x onerror=alert(1)>'
        self.record['run_id'] = attack
        self.record['steps'][1]['reason'] = attack
        worker_path = self.path.parent / 'regression.json'
        worker = strict_json(worker_path)
        worker['failed_test_ids'] = [attack]
        worker['failed_parent_ids'] = [attack]
        worker_path.write_text(json.dumps(worker), encoding='utf-8')
        self.seal()
        _, html = self.render()
        self.assertNotIn('<script>', html.lower())
        self.assertNotIn('<img src=x', html.lower())
        self.assertTrue(all(tag not in ('script', 'img') for tag, _ in HtmlView(html).tags))

    def test_metadata_commands_are_neither_rendered_nor_executed(self):
        marker = self.root / 'must-not-exist'
        malicious = ['python', '-c', 'METADATA_COMMAND_MUST_NOT_APPEAR; open({!r},"w").write("executed")'.format(str(marker))]
        self.record['command'] = malicious
        self.record['steps'][1]['command'] = malicious
        self.seal()
        _, html = self.render()
        self.assertNotIn('METADATA_COMMAND_MUST_NOT_APPEAR', html)
        self.assertFalse(marker.exists())
        self.assertIn('tools/agent_acceptance.py', html)

    def test_raw_logs_and_non_allowlisted_metadata_are_never_embedded(self):
        self.record['raw_log'] = 'ARBITRARY-RAW-METADATA-PRIVATE'
        self.seal()
        _, html = self.render()
        for private in ('RAW-LOG-CONTENT-MUST-STAY-PRIVATE', 'privateRawLogSecret', 'ARBITRARY-RAW-METADATA-PRIVATE'):
            self.assertNotIn(private, html)

    def test_reason_secrets_are_redacted_before_html_rendering(self):
        self.record['steps'][1]['reason'] = 'password=syntheticPassword123 token=syntheticToken456 https://private.example/path?token=secret789'
        self.seal()
        _, html = self.render()
        for secret in ('syntheticPassword123', 'syntheticToken456', 'private.example', 'secret789'):
            self.assertNotIn(secret, html)

    def test_common_secrets_urls_and_bearer_tokens_are_redacted(self):
        cases = (
            ('password=syntheticPassword123', 'syntheticPassword123'),
            ('token: syntheticToken456', 'syntheticToken456'),
            ('api_key=syntheticApiKey789', 'syntheticApiKey789'),
            ('Authorization: Bearer syntheticBearer123', 'syntheticBearer123'),
            ('https://alice:syntheticUrlPassword@private.example/path?token=urlSecret', 'private.example'),
            ('postgresql://alice:syntheticDsnPassword@private.database/app', 'syntheticDsnPassword'),
        )
        for value, secret in cases:
            with self.subTest(value=value):
                self.assertNotIn(secret, redact(value))
        self.assertEqual(redact('safe-test_identifier-123'), 'safe-test_identifier-123')

    def test_source_identifiers_times_runtime_and_fixed_sections_are_visible(self):
        _, html = self.render()
        view = HtmlView(html)
        for identifier in ('overview', 'findings', 'performance', 'reproduce', 'sources', 'limits'):
            self.assertIn(identifier, {attrs.get('id') for _, attrs in view.tags})
        self.assertIn('a' * 64, html)
        self.assertIn('fixture-current', html)
        self.assertIn('3.10.22', html)
        self.assertRegex(' '.join(view.text), r'20\d\d[-/]\d\d[-/]\d\d')

    def test_verified_regression_counts_failure_and_skip_ids_remain_visible(self):
        _, html = self.render('INCOMPLETE')
        text = ' '.join(HtmlView(html).text)
        self.assertRegex(text, r'\b17\b')
        self.assertRegex(text, r'\b15\b')
        self.assertIn('test_partial_backfill_restart_continues_only_unclaimed_dates', text)
        self.assertIn('fixture.test_platform_skip', text)
        self.assertIn('synthetic platform limitation', text)

    def test_unverified_primary_counts_are_not_presented_as_current_results(self):
        self.record['steps'][1]['counts']['tests'] = 8123456
        self.seal()
        self.api.verify.return_value = dict(status='INVALID', exit_code=86, evidence_valid=False,
                                           whole_project_verified=False, reason='unverified-fixture')
        _, html = self.render('INVALID')
        self.assertNotIn('8123456', html)

    def test_historical_pass_does_not_promote_current_incomplete_or_sum_counts(self):
        history_path, history = self.bundle('historical', status='PASSED', tests=999, source='c' * 64)
        self.args.history_report = [history_path]
        result, html = self.render('INCOMPLETE')
        view = HtmlView(html)
        self.assertFalse(result['whole_project_verified'])
        self.assertIn('INCOMPLETE', view.role('current'))
        self.assertIn('historical', view.role('history'))
        self.assertNotIn('999', view.role('current'))
        self.assertNotIn('1016', ' '.join(view.text))
        self.assertIn('c' * 64, html)

    def test_structurally_valid_but_clock_invalid_run_does_not_display_pass_counts(self):
        self.record['steps'][1]['counts']['passed'] = 8123456
        self.seal()
        self.api.verify.return_value = dict(status='INVALID', exit_code=86, evidence_valid=True,
                                           whole_project_verified=False)
        result, html = self.render('INVALID')
        self.assertFalse(result['whole_project_verified'])
        self.assertNotIn('8123456', HtmlView(html).role('current'))
        self.assertIn('regression', HtmlView(html).section('limits'))

    def test_nested_known_etl_conflict_has_safe_cause_without_raw_assertion_payload(self):
        (self.path.parent / 'regression.log').write_text(
            "AssertionError: {'error': 'Conflict', 'message': 'This ETL job is already running; no overlapping attempt was started.', 'row': 'PRIVATE-ROW-CANARY'}\n",
            encoding='utf-8')
        self.seal()
        _, html = self.render()
        self.assertIn('租約仍有效', html)
        self.assertIn('防重疊規則', html)
        self.assertNotIn('PRIVATE-ROW-CANARY', html)
        self.assertNotIn("'message':", html)

    def test_tampered_history_is_reported_without_changing_current_verdict(self):
        history_path, history = self.bundle('historical', status='PASSED')
        history_path.write_text(json.dumps(dict(history, run_id='tampered-history')), encoding='utf-8')
        self.args.history_report = [history_path]
        result, html = self.render('INCOMPLETE')
        self.assertFalse(result['whole_project_verified'])
        self.assertIn('INVALID', HtmlView(html).role('history'))

    def test_valid_history_with_inconsistent_clock_is_not_presented_as_valid(self):
        history_path, history = self.bundle('historical', status='PASSED')
        history['clock'] = dict(valid=False, backsteps=1, monitor_errors=[])
        self.seal(history_path, history)
        self.args.history_report = [history_path]
        _, html = self.render('INCOMPLETE')
        self.assertIn('INVALID', HtmlView(html).role('history'))

    def test_no_comparison_is_invented_from_unbound_metadata_or_numeric_budgets(self):
        self.record['performance_budgets'] = dict(max_wall_regression_percent=999, max_rss_regression_percent=999)
        self.record['comparisons'] = [dict(rows=100000, before_median_wall_ms=1.23456789,
                                          after_median_wall_ms=.123456789)]
        self.record['steps'][1]['measurement'] = {'status': 'PASSED', 'private': 'UNBOUND-METRIC-MUST-NOT-APPEAR'}
        self.seal()
        result, html = self.render('INCOMPLETE')
        self.assertFalse(result['whole_project_verified'])
        self.assertNotIn('UNBOUND-METRIC-MUST-NOT-APPEAR', html)
        self.assertNotIn('1.23456789', HtmlView(html).section('performance'))

    def performance_fixture(self):
        values = [json.loads((REPOSITORY / 'benchmarks' / 'evidence' / name).read_text(encoding='utf-8'))
                  for name in ('v11-baseline-large-xlsx.json', 'v11-candidate-large-xlsx.json',
                               'v11-large-xlsx-comparison.json')]
        before, after, comparison = values
        before['label'], after['label'] = 'before', 'after'
        for phase, value in (('before', before), ('after', after)):
            path = self.path.parent / ('xlsx_' + phase + '.json')
            path.write_text(json.dumps(value), encoding='utf-8')
            comparison[phase + '_file'] = path.name
            comparison[phase + '_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.path.parent / 'xlsx_compare.json').write_text(json.dumps(comparison), encoding='utf-8')
        self.record['profile'] = 'performance'
        self.record['source']['files'] = dict(after['source_hashes'], **{'benchmarks/large_xlsx.py': after['harness_sha256']})
        self.record['baseline'] = dict(digest='d' * 64, files=before['source_hashes'])
        self.seal()
        return before, after, comparison

    def test_bound_semantically_valid_comparison_renders_numbers_but_never_activates_a_budget(self):
        before, after, comparison = self.performance_fixture()
        result, html = self.render('INCOMPLETE')
        section = HtmlView(html).section('performance')
        row = comparison['comparisons'][0]
        self.assertIn('{:,.3f}'.format(row['before_median_wall_ms']), section)
        self.assertIn('{:,.3f}'.format(row['after_median_wall_ms']), section)
        self.assertIn('{:,.3f}'.format(row['before_median_peak_rss_bytes'] / 1048576), section)
        self.assertIn('INCOMPLETE', section)
        self.assertFalse(result['whole_project_verified'])

    def test_resealed_but_semantically_tampered_comparison_never_renders_numeric_success(self):
        before, after, comparison = self.performance_fixture()
        comparison['comparisons'][0]['before_median_wall_ms'] = 987654321.123
        (self.path.parent / 'xlsx_compare.json').write_text(json.dumps(comparison), encoding='utf-8')
        self.seal()
        _, html = self.render('INCOMPLETE')
        section = HtmlView(html).section('performance')
        self.assertIn('INVALID', section)
        self.assertNotIn('987,654,321.123', section)


if __name__ == '__main__':
    unittest.main()
