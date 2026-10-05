"""Offline impact hints: static graph, real mappings, Git inputs and no execution."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import change_impact as impact


ROOT = Path(__file__).resolve().parents[1]


class FixtureCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def index(self, sources):
        for path, text in sources.items():
            self.write(path, text)
        return impact.SourceIndex(self.root, sorted(sources))

    def report(self, path, status='M'):
        return impact.analyze(self.root, [{'path': path, 'status': status}])

    def assert_fallback(self, report, code, path):
        self.assertTrue(report['full_regression_required_now'])
        self.assertEqual(report['status'], 'FULL_REGRESSION_REQUIRED')
        self.assertIn((code, path), {(item['code'], item['path'])
                                    for item in report['fallback_reasons']})


class RealRepositoryImpactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        files = impact.source_files(ROOT)
        cls.reports = {}
        for name, path in (
                ('quality', 'reporting_workspace/quality_actions/service.py'),
                ('template', 'reporting_workspace/report_templates/service.py'),
                ('etl', 'reporting_workspace/etl_dispatch.py'),
                ('managed', 'reporting_workspace/governance.py'),
                ('auth', 'reporting_workspace/authorization.py')):
            cls.reports[name] = impact.analyze(ROOT, [{'path': path, 'status': 'M'}], files)

    def test_quality_service_maps_only_its_page_query_and_export(self):
        report = self.reports['quality']
        self.assertEqual({(item['kind'], item['id']) for item in report['impacts']}, {
            ('page', 'quality_actions'), ('callback', 'quality_actions.query'),
            ('callback', 'quality_actions.export')})
        page = next(item for item in report['impacts'] if item['kind'] == 'page')
        self.assertEqual(page['route'], '/QA_portal/quality-actions')
        self.assertEqual(report['status'], 'FOCUSED_HINTS_ONLY')
        self.assertEqual(report['fallback_reasons'], [])
        self.assertIn('tests/test_quality_actions.py',
                      {item['path'] for item in report['suggested_tests']})
        for item in report['impacts']:
            evidence = item['evidence'][0]
            self.assertEqual(evidence['dependency_path'][0],
                             'reporting_workspace/ui_pages/quality_actions.py')
            self.assertEqual(evidence['dependency_path'][-1],
                             'reporting_workspace/quality_actions/service.py')
            self.assertTrue(evidence['imports'])
            self.assertTrue(all(entry['line'] > 0 for entry in evidence['imports']))

    def test_template_service_does_not_inherit_quality_action_surfaces(self):
        report = self.reports['template']
        self.assertEqual({(item['kind'], item['id']) for item in report['impacts']}, {
            ('page', 'report_template'), ('callback', 'report_template.query'),
            ('callback', 'report_template.export')})
        self.assertEqual(next(item['route'] for item in report['impacts']
                              if item['kind'] == 'page'), '/QA_portal/report-template')
        self.assertEqual(report['status'], 'FOCUSED_HINTS_ONLY')
        self.assertIn('tests/test_report_template.py',
                      {item['path'] for item in report['suggested_tests']})

    def test_etl_engine_reaches_both_disabled_job_declarations(self):
        schedules = {item['id']: item for item in self.reports['etl']['impacts']
                     if item['kind'] == 'schedule'}
        self.assertEqual(set(schedules), {'synthetic-sales-daily',
                                         'synthetic-inventory-health'})
        for item in schedules.values():
            self.assertIn('disabled by default', item['state'])
            self.assertIn('launcher-owned', item['state'])
            self.assertEqual(item['evidence'][0]['dependency_path'][-1],
                             'reporting_workspace/etl_dispatch.py')

    def test_managed_service_reaches_audited_api_and_manual_schedule(self):
        impacts = self.reports['managed']['impacts']
        http = {item['id']: item for item in impacts if item['kind'] == 'http'}
        route = '/api/managed-reports/<identifier>/export.csv'
        self.assertIn(route, http)
        self.assertNotIn('/api/reports/export.csv', http)
        self.assertEqual(http[route]['evidence'][0]['dependency_path'],
                         ['reporting_workspace/governance.py'])
        schedule = next(item for item in impacts
                        if item['id'] == 'managed-report-mail-simulation')
        self.assertEqual(schedule['kind'], 'schedule')
        self.assertIn('manual', schedule['state'])
        self.assertIn('no running', schedule['state'])

    def test_shared_authorization_requires_full_regression_and_no_legacy_login(self):
        report = self.reports['auth']
        self.assertEqual(report['status'], 'FULL_REGRESSION_REQUIRED')
        self.assertTrue(report['full_regression_required_now'])
        self.assertIn(('core_change', 'reporting_workspace/authorization.py'),
                      {(item['code'], item['path']) for item in report['fallback_reasons']})
        self.assertTrue(any(item['id'] == 'quality_actions' for item in report['impacts']))
        self.assertTrue(any(item['id'] == 'report_template' for item in report['impacts']))
        self.assertFalse(any('demo-login' in (item['route'] or '')
                             for item in report['impacts']))
        self.assertFalse(any(item['declaration']['path'] ==
                             'reporting_workspace/legacy_demo_ui.py'
                             for item in report['impacts']))

    def test_recommendations_remain_argv_data_and_never_waive_release_gate(self):
        for report in self.reports.values():
            with self.subTest(status=report['status']):
                self.assertTrue(report['full_regression_before_publication'])
                self.assertFalse(report['analysis']['coverage_complete'])
                self.assertFalse(report['analysis']['tests_executed'])
                self.assertFalse(report['analysis']['source_imported'])
                for item in report['suggested_tests'] + report['required_checks']:
                    self.assertIsInstance(item['argv'], list)
                    self.assertTrue(all(isinstance(arg, str) for arg in item['argv']))
                    self.assertNotIn('command', item)
                checks = [item['argv'] for item in report['required_checks']]
                self.assertIn(['python', '-B', '-m', 'unittest', 'discover',
                               '-s', 'tests', '-v'], checks)
                self.assertIn(['python', '-B', 'tests/test_entrypoint_coverage.py',
                               '--report-template', '--quality-actions'], checks)


class StaticGraphTests(FixtureCase):
    def test_relative_imports_and_aliases_keep_transitive_line_evidence(self):
        index = self.index({
            'pkg/__init__.py': '', 'pkg/service.py': 'VALUE = 1\n',
            'pkg/ui/__init__.py': '',
            'pkg/ui/page.py': 'from ..service import VALUE as selected\n',
            'tests/test_page.py': 'import pkg.ui.page as subject\n'})
        chain = index.chain('tests/test_page.py', 'pkg/service.py')
        self.assertEqual(chain, ['tests/test_page.py', 'pkg/ui/page.py', 'pkg/service.py'])
        self.assertEqual([item['line'] for item in index.proof(chain)], [1, 1])
        self.assertEqual(index.chain('pkg/service.py', 'pkg/service.py'), ['pkg/service.py'])

    def test_package_reexports_are_followed_without_importing_package(self):
        index = self.index({
            'pkg/__init__.py': 'from .service import Service\n',
            'pkg/service.py': 'class Service: pass\n',
            'consumer.py': 'from pkg import Service as PublicService\n'})
        self.assertEqual(index.chain('consumer.py', 'pkg/service.py'),
                         ['consumer.py', 'pkg/__init__.py', 'pkg/service.py'])
        self.assertIn('pkg/__init__.py', index.graph['consumer.py'])

    def test_cycles_terminate_and_choose_a_stable_shortest_chain(self):
        index = self.index({
            'a.py': 'import b\nimport c\n', 'b.py': 'import a\nimport target\n',
            'c.py': 'import target\n', 'target.py': '', 'isolated.py': ''})
        self.assertEqual(index.chain('a.py', 'target.py'), ['a.py', 'b.py', 'target.py'])
        self.assertIsNone(index.chain('a.py', 'isolated.py'))
        self.assertIsNone(index.chain('missing.py', 'a.py'))

    def test_shared_dependency_does_not_create_reverse_or_sibling_edges(self):
        index = self.index({
            'left.py': 'import common\n', 'right.py': 'import common\n',
            'common.py': '', 'tests/test_left.py': 'import left\n'})
        self.assertIsNone(index.chain('left.py', 'right.py'))
        self.assertIsNone(index.chain('common.py', 'left.py'))
        self.assertIsNone(index.chain('tests/test_left.py', 'right.py'))
        report = self.report('right.py')
        self.assertNotIn('tests/test_left.py',
                         {item['path'] for item in report['suggested_tests']})

    def test_dynamic_import_aliases_trigger_visible_fallback_without_execution(self):
        variants = (
            'from importlib import import_module as load\nload("hidden")\n',
            'import importlib as loader\nloader.import_module("hidden")\n',
            'from builtins import __import__ as load\nload("hidden")\n')
        for source in variants:
            with self.subTest(source=source):
                self.write('reporting_workspace/dynamic.py', source)
                self.write('reporting_workspace/other.py', 'VALUE = 1\n')
                self.assert_fallback(self.report('reporting_workspace/other.py'),
                                     'dynamic_code', 'reporting_workspace/dynamic.py')

    def test_aliased_page_and_job_declarations_resolve_constant_identifiers(self):
        index = self.index({'reporting_workspace/feature.py':
            'from somewhere import PageSpec as Page, JobSpec as Job\n'
            'PAGE = "sample"\nPREFIX = "/QA_portal/"\n'
            'spec = Page(page_id=PAGE, path=PREFIX + "sample")\n'
            'job = Job("sample-job", "Synthetic job")\n'})
        surfaces = impact.discover_surfaces(index)
        self.assertEqual({(item['kind'], item['id']) for item in surfaces},
                         {('page', 'sample'), ('schedule', 'sample-job')})
        self.assertEqual(next(item['route'] for item in surfaces if item['kind'] == 'page'),
                         '/QA_portal/sample')

    def test_unresolved_relative_import_is_not_treated_as_no_impact(self):
        self.write('reporting_workspace/feature.py', 'from .missing import hidden\n')
        self.assert_fallback(self.report('reporting_workspace/feature.py'),
                             'unresolved_import', 'reporting_workspace/feature.py')

    def test_missing_member_of_known_package_is_not_resolved_by_parent_alone(self):
        self.write('reporting_workspace/pkg/__init__.py', 'from .service import Service\n')
        self.write('reporting_workspace/pkg/service.py', 'class Service: pass\n')
        self.write('reporting_workspace/consumer.py',
                   'from .pkg import missing_adapter\n')
        self.assert_fallback(self.report('reporting_workspace/consumer.py'),
                             'unresolved_import', 'reporting_workspace/consumer.py')

    def test_parse_failure_is_reported_and_never_proves_no_impact(self):
        self.write('reporting_workspace/broken.py', 'def broken(:\n')
        report = self.report('reporting_workspace/broken.py')
        self.assert_fallback(report, 'unreadable_source', 'reporting_workspace/broken.py')
        self.assert_fallback(report, 'unknown_path', 'reporting_workspace/broken.py')

    def test_added_deleted_and_unknown_statuses_require_full_regression(self):
        self.write('reporting_workspace/new.py', 'VALUE = 1\n')
        for path, status in (('reporting_workspace/new.py', 'A'),
                             ('reporting_workspace/removed.py', 'D'),
                             ('reporting_workspace/new.py', '?')):
            with self.subTest(path=path, status=status):
                self.assert_fallback(self.report(path, status), 'structural_change', path)

    def test_unknown_and_unmapped_paths_are_conservative(self):
        self.write('docs/guide.md', 'Documentation may describe a runtime contract.\n')
        self.assert_fallback(self.report('docs/guide.md'), 'unknown_path', 'docs/guide.md')
        self.write('reporting_workspace/orphan.py', 'VALUE = 1\n')
        self.assert_fallback(self.report('reporting_workspace/orphan.py'),
                             'unmapped_change', 'reporting_workspace/orphan.py')

    def test_unresolved_page_registration_has_an_explicit_fallback(self):
        self.write('reporting_workspace/feature.py', 'spec = PageSpec("feature", route())\n')
        self.assert_fallback(self.report('reporting_workspace/feature.py'),
                             'unresolved_registration', 'reporting_workspace/feature.py')

    def test_product_callback_without_registry_metadata_is_not_silently_ignored(self):
        self.write('reporting_workspace/feature.py',
                   '@app.callback(Output("result", "children"), Input("query", "n_clicks"))\n'
                   'def callback(clicks):\n    return clicks\n')
        self.assert_fallback(self.report('reporting_workspace/feature.py'),
                             'unresolved_registration', 'reporting_workspace/feature.py')

    def test_report_is_deterministic_under_file_and_change_order(self):
        sources = {'reporting_workspace/a.py': 'VALUE = 1\n',
                   'reporting_workspace/b.py': 'from .a import VALUE\n',
                   'tests/test_b.py': 'from reporting_workspace import b\n'}
        self.index(sources)
        changes = [{'path': path, 'status': 'M'} for path in sorted(sources)[:2]]
        first = impact.analyze(self.root, changes, sorted(sources))
        second = impact.analyze(self.root, list(reversed(changes)), sorted(sources, reverse=True))
        self.assertEqual(first, second)
        self.assertEqual(impact.render_text(first), impact.render_text(second))
        self.assertNotIn(str(self.root), json.dumps(first))
        self.write('reporting_workspace/a.py', 'VALUE = 2\n')
        self.assertNotEqual(first['source_fingerprint'],
                            impact.analyze(self.root, changes, sorted(sources))['source_fingerprint'])

    def test_product_modules_and_test_modules_are_only_parsed(self):
        marker = self.root / 'executed.txt'
        dangerous = 'from pathlib import Path\nPath({!r}).write_text("executed")\n'.format(str(marker))
        self.write('reporting_workspace/sentinel.py', dangerous)
        self.write('tests/test_sentinel.py',
                   'from reporting_workspace import sentinel\n' + dangerous +
                   'raise RuntimeError("test module executed")\n')
        with patch.object(impact.subprocess, 'run', side_effect=AssertionError('unexpected process')):
            report = self.report('reporting_workspace/sentinel.py')
        self.assertFalse(marker.exists())
        self.assertIn('tests/test_sentinel.py',
                      {item['path'] for item in report['suggested_tests']})
        self.assertFalse(report['analysis']['source_imported'])
        self.assertFalse(report['analysis']['tests_executed'])

    def test_traversal_absolute_and_shell_metacharacter_paths_are_rejected(self):
        paths = ('../outside.py', '/outside.py', 'pkg/../outside.py', './module.py',
                 'pkg//module.py', 'C:/outside.py', 'pkg\\module.py', '-option.py',
                 'module.py;touch marker', '$(touch marker).py', 'bad\npath.py', '')
        with patch.object(impact.subprocess, 'run', side_effect=AssertionError('unexpected process')):
            for path in paths:
                with self.subTest(path=path), self.assertRaises(impact.ImpactError):
                    self.report(path)

    def test_file_and_directory_symlinks_are_never_parsed(self):
        external = tempfile.TemporaryDirectory()
        self.addCleanup(external.cleanup)
        target = Path(external.name) / 'outside.py'
        target.write_text('SECRET_SENTINEL = "must not be read"\n', encoding='utf-8')
        folder = self.root / 'reporting_workspace'
        folder.mkdir()
        try:
            (folder / 'linked.py').symlink_to(target)
            (folder / 'linked').symlink_to(target.parent, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest('Host cannot create test symlinks: ' + type(error).__name__)
        for path in ('reporting_workspace/linked.py', 'reporting_workspace/linked/outside.py'):
            with self.subTest(path=path):
                with self.assertRaises(impact.ImpactError):
                    impact.local_file(self.root, path)
                index = impact.SourceIndex(self.root, [path])
                self.assertNotIn(path, index.trees)
                self.assertNotIn(path, index.hashes)
                self.assertEqual(index.issues[0]['code'], 'unreadable_source')

    def test_malicious_base_cannot_become_a_git_option_or_shell_command(self):
        for base in ('--upload-pack=bad', 'HEAD;touch marker', '$(touch marker)',
                     'HEAD\n--exec=bad', 'HEAD bad'):
            with self.subTest(base=base):
                with patch.object(impact, 'git', return_value=str(self.root)) as git:
                    with self.assertRaises(impact.ImpactError):
                        impact.changes_from_git(self.root, base=base)
                git.assert_called_once_with(self.root, 'rev-parse', '--show-toplevel')


@unittest.skipUnless(shutil.which('git'), 'Local Git is required for temporary repository fixtures')
class GitAndCliTests(FixtureCase):
    def setUp(self):
        super().setUp()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Synthetic Impact Fixture')
        self.git('config', 'user.email', 'impact-fixture@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        for path in ('staged.py', 'unstaged.py', 'renamed.py'):
            self.write(path, 'VALUE = 1\n')
        self.git('add', '--all')
        self.git('commit', '-qm', 'Synthetic base')

    def git(self, *args):
        return subprocess.run(['git'] + list(args), cwd=str(self.root), check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=30).stdout.decode('utf-8').strip()

    def test_default_git_selection_includes_staged_unstaged_and_untracked(self):
        self.write('staged.py', 'VALUE = 2\n')
        self.git('add', 'staged.py')
        self.write('unstaged.py', 'VALUE = 3\n')
        self.write('new.py', 'VALUE = 4\n')
        changes, files, provenance = impact.changes_from_git(self.root)
        self.assertEqual({item['path']: item['status'] for item in changes},
                         {'staged.py': 'M', 'unstaged.py': 'M', 'new.py': 'A'})
        self.assertIn('new.py', files)
        self.assertEqual(provenance['base_commit'], self.git('rev-parse', 'HEAD'))

    def test_rename_is_represented_as_addition_and_deletion(self):
        self.git('mv', 'renamed.py', 'replacement.py')
        changes, _, _ = impact.changes_from_git(self.root)
        self.assertEqual({item['path']: item['status'] for item in changes},
                         {'renamed.py': 'D', 'replacement.py': 'A'})
        report = impact.analyze(self.root, changes)
        for path in ('renamed.py', 'replacement.py'):
            self.assert_fallback(report, 'structural_change', path)

    def test_explicit_paths_retain_git_status_and_unknown_is_not_modified(self):
        self.write('new.py', 'VALUE = 1\n')
        changes, _, _ = impact.changes_from_git(
            self.root, selected=['staged.py', 'new.py', 'missing.py'])
        self.assertEqual({item['path']: item['status'] for item in changes},
                         {'staged.py': 'M', 'new.py': 'A', 'missing.py': '?'})

    def test_explicit_base_compares_current_tree_with_the_named_local_commit(self):
        base = self.git('rev-parse', 'HEAD')
        self.write('staged.py', 'VALUE = 2\n')
        self.git('add', 'staged.py')
        self.git('commit', '-qm', 'Synthetic second commit')
        self.write('unstaged.py', 'VALUE = 3\n')
        self.write('new.py', 'VALUE = 4\n')
        changes, _, provenance = impact.changes_from_git(self.root, base=base)
        self.assertEqual({item['path']: item['status'] for item in changes},
                         {'staged.py': 'M', 'unstaged.py': 'M', 'new.py': 'A'})
        self.assertEqual(provenance['base_commit'], base)
        self.assertNotEqual(provenance['head_commit'], base)

    def test_cli_json_only_runs_read_only_git_and_never_imports_or_runs_sources(self):
        marker = self.root / 'executed.txt'
        source = 'from pathlib import Path\nPath({!r}).write_text("executed")\n'.format(str(marker))
        self.write('reporting_workspace/sentinel.py', source)
        self.write('tests/test_sentinel.py', 'from reporting_workspace import sentinel\n' + source)
        original_run = subprocess.run
        commands = []

        def read_only_git(argv, **kwargs):
            self.assertIsInstance(argv, list)
            self.assertEqual(argv[0], 'git')
            self.assertFalse(kwargs.get('shell', False))
            self.assertTrue({'rev-parse', 'ls-files', 'diff'}.intersection(argv))
            self.assertIn('core.fsmonitor=false', argv)
            if 'diff' in argv:
                self.assertIn('--no-ext-diff', argv)
                self.assertIn('--no-textconv', argv)
                self.assertIn('--no-renames', argv)
            commands.append(argv)
            return original_run(argv, **kwargs)

        output = io.StringIO()
        with patch.object(impact.subprocess, 'run', side_effect=read_only_git):
            with contextlib.redirect_stdout(output):
                result = impact.main(['--repo', str(self.root), '--changed',
                                      'reporting_workspace/sentinel.py', '--format', 'json'])
        self.assertEqual(result, 0)
        self.assertTrue(commands)
        self.assertFalse(marker.exists())
        report = json.loads(output.getvalue())
        self.assertFalse(report['analysis']['source_imported'])
        self.assertFalse(report['analysis']['tests_executed'])
        self.assertTrue(report['full_regression_before_publication'])
        self.assertNotIn(str(self.root), output.getvalue())

    def test_cli_text_is_an_inert_recommendation_and_bad_input_returns_two(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = impact.main(['--repo', str(self.root), '--changed', 'staged.py'])
        self.assertEqual(result, 0)
        self.assertIn('recommendations only', output.getvalue())
        self.assertIn('Full regression before publication: REQUIRED', output.getvalue())
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            result = impact.main(['--repo', str(self.root), '--changed', '../outside.py'])
        self.assertEqual(result, 2)
        self.assertIn('canonical repository-relative path', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
