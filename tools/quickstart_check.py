"""Read-only local quickstart checks; app.py remains the only product launcher.

Never install dependencies, construct the app, create state, remove restore
markers, or contact providers. Existing stores require the operator's explicit
assertion that every writer is stopped. A pass is not deployment readiness.
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import stat
import sys

# Preserve read-only behavior even if the caller omitted python -B.
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_MODULES = {
    'dash': 'dash',
    'dash-bootstrap-components': 'dash_bootstrap_components',
    'Flask': 'flask',
    'Flask-Login': 'flask_login',
    'Werkzeug': 'werkzeug',
    'plotly': 'plotly',
    'setuptools': 'pkg_resources',
    'dash-mantine-components': 'dash_mantine_components',
    'openpyxl': 'openpyxl',
}
# This is a typo guard, not a second settings parser. Values always go through
# Settings.from_env; tests keep this allowlist aligned with that central parser.
_KNOWN_REPORTING_KEYS = frozenset({
    'REPORTING_MODE', 'REPORTING_SECRET_KEY', 'REPORTING_STATE_PATH',
    'REPORTING_SESSION_COOKIE_SECURE', 'REPORTING_MAX_CONTENT_LENGTH',
    'REPORTING_SESSION_LIFETIME_SECONDS', 'REPORTING_ENABLE_REPORT_TEMPLATE',
    'REPORTING_ENABLE_QUALITY_ACTIONS',
})
_SIDECARS = ('-journal', '-wal', '-shm')
_LIMITATIONS = [
    'Local demo preflight only; no app, worker, browser or provider was started.',
    'Permission checks are conservative metadata/access checks, not a write probe or Windows ACL review.',
    'Local path syntax does not certify filesystem locality, durability, free space or exclusive ownership.',
    'Existing-state quiescence is operator-asserted, not proven by this check.',
    'Exact target OS/Python patches, transitive dependencies and company integration remain unverified.',
]


class QuickstartError(RuntimeError):
    """Only fixed diagnostic codes may leave this tool."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes arbitrary argv, potentially including secrets.
        raise QuickstartError('arguments_invalid')


def _pins():
    pins = {}
    for name in ('requirements-demo.txt', 'requirements-qa-portal.txt'):
        for raw in (ROOT / name).read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or line == '-r requirements-demo.txt':
                continue
            match = re.fullmatch(r'([A-Za-z][A-Za-z0-9-]*)==([0-9]+(?:\.[0-9]+)*)', line)
            if not match or match.group(1) not in _MODULES or match.group(1) in pins:
                raise QuickstartError('dependency_manifest_invalid')
            pins[match.group(1)] = match.group(2)
    if set(pins) != set(_MODULES):
        raise QuickstartError('dependency_manifest_invalid')
    return pins


def _dependency_checks():
    # Import only stdlib metadata/spec APIs until every required pin is present.
    from importlib import metadata, util
    result = []
    for distribution, expected in sorted(_pins().items()):
        status = 'passed'
        try:
            actual = metadata.version(distribution)
            if actual != expected:
                status = 'version_mismatch'
            elif util.find_spec(_MODULES[distribution]) is None:
                status = 'module_missing'
        except metadata.PackageNotFoundError:
            status = 'missing'
        except Exception:
            status = 'unavailable'
        # Never print metadata-derived values, paths or exception text.
        result.append(dict(distribution=distribution, expected=expected, status=status))
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise QuickstartError('configuration_file_invalid')
        result[key] = value
    return result


def _environment(config_json):
    if config_json is None:
        source = {key: value for key, value in os.environ.items()
                  if key.startswith('REPORTING_')}
    else:
        try:
            path = Path(config_json)
            # Bounded read, including a concurrently grown configuration file.
            if not path.is_file() or path.stat().st_size > 1024 * 1024:
                raise QuickstartError('configuration_file_invalid')
            with path.open('rb') as stream:
                data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise QuickstartError('configuration_file_invalid')
            source = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object)
        except Exception:
            raise QuickstartError('configuration_file_invalid') from None
        if not isinstance(source, dict) or any(
                not isinstance(key, str) or not key.startswith('REPORTING_')
                for key in source):
            raise QuickstartError('configuration_file_invalid')
    if set(source) - _KNOWN_REPORTING_KEYS:
        raise QuickstartError('unknown_reporting_setting')
    if any(not isinstance(value, str) for value in source.values()):
        raise QuickstartError('configuration_values_must_be_text')
    return source


def _permission_check(path, directory=False, private=False):
    info = path.stat()
    required = os.R_OK | os.W_OK | (os.X_OK if directory else 0)
    masks = (0o444, 0o222, 0o111) if directory else (0o444, 0o222)
    if not os.access(str(path), required) or any(not info.st_mode & mask for mask in masks):
        raise QuickstartError('state_permissions_insufficient')
    if os.name == 'posix':
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & (0o077 if private else 0o022):
            raise QuickstartError('state_permissions_not_private')


def _present(path):
    try:
        path.lstat()
        return True
    except (FileNotFoundError, NotADirectoryError):
        return False


def _known_paths(main, deployment):
    return [main.parent / (main.name + name[len(deployment._MAIN):])
            for name in sorted(dict(deployment._REQUIRED, **deployment._OPTIONAL))]


def _no_sidecars(paths):
    if any(_present(Path(str(path) + suffix)) for path in paths for suffix in _SIDECARS):
        raise QuickstartError('state_journal_requires_offline_review')


def _signature(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _state_check(settings, new_demo, quiesced, deployment):
    main = deployment._local_path(settings.state_path)
    _permission_check(main.parent, directory=True, private=True)
    paths = _known_paths(main, deployment)
    roots = (main, Path(str(main) + '.qa'), Path(str(main) + '.operations'),
             Path(str(main) + '.etl.sqlite'))
    _no_sidecars(paths)
    if new_demo:
        if any(_present(path) for path in roots):
            raise QuickstartError('new_demo_destination_exists')
        return dict(status='passed', intent='new-demo', stores_created=0,
                    quiescence='not-applicable')
    if not quiesced:
        raise QuickstartError('existing_state_requires_quiesced')
    if not _present(main):
        raise QuickstartError('existing_state_missing_use_explicit_new_demo')
    try:
        stores = deployment._layout(main, 'demo')
    except Exception:
        raise QuickstartError('existing_demo_layout_invalid') from None
    for path, kind in stores.values():
        _permission_check(path)
        _permission_check(path.parent, directory=True)
        with path.open('rb') as stream:
            header = stream.read(100)
        # Reject WAL before SQLite opens it. immutable=1 below additionally
        # guarantees SQLite will not create journals, WAL or SHM sidecars.
        if len(header) != 100 or header[:16] != b'SQLite format 3\x00':
            raise QuickstartError('existing_demo_store_invalid')
        if header[18:20] != b'\x01\x01':
            raise QuickstartError('state_journal_requires_offline_review')
    before = {name: _signature(path) for name, (path, kind) in stores.items()}
    import sqlite3
    if sqlite3.sqlite_version_info < (3, 22, 0):
        raise QuickstartError('sqlite_immutable_support_required')
    try:
        for path, kind in stores.values():
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1',
                                         uri=True, isolation_level=None, timeout=.05)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute('PRAGMA query_only=ON')
                connection.execute('PRAGMA temp_store=MEMORY')
                deployment._validate(connection, kind, full=True)
    except Exception:
        raise QuickstartError('existing_demo_store_invalid') from None
    _no_sidecars(paths)
    if before != {name: _signature(path) for name, (path, kind) in stores.items()}:
        raise QuickstartError('state_changed_during_check')
    return dict(status='passed', intent='existing-demo', stores_checked=len(stores),
                quiescence='operator-asserted', live_readiness='not-certified')


def run_checks(args):
    result = dict(schema_version=1, status='blocked', profile=args.profile,
                  scope='local-demo-preflight-only', read_only=True,
                  checks={}, limitations=list(_LIMITATIONS))
    try:
        if sys.version_info[:2] not in ((3, 8), (3, 10)):
            raise QuickstartError('supported_python_minor_required')
        result['checks']['python'] = dict(status='passed', version='{}.{}.{}'.format(*sys.version_info[:3]))
        dependencies = _dependency_checks()
        result['checks']['dependencies'] = dependencies
        if any(item['status'] != 'passed' for item in dependencies):
            raise QuickstartError('dependencies_unavailable_or_mismatched')
        source = _environment(args.config_json)
        # All product imports are behind the dependency gate. These modules
        # validate contracts only; do not import app/application or construct it.
        from reporting_workspace.config import Settings
        from reporting_workspace import deployment
        try:
            settings = Settings.from_env(source)
        except Exception:
            raise QuickstartError('settings_invalid') from None
        if 'REPORTING_MODE' not in source or settings.state_path is None:
            raise QuickstartError('explicit_mode_and_state_path_required')
        expected_mode = 'demo' if args.profile == 'demo' else 'production'
        if settings.mode != expected_mode:
            raise QuickstartError('profile_mode_mismatch')
        preflight = deployment.configuration_preflight(source)
        if args.profile == 'company':
            # No CLI imports/provider flags can turn an unreviewed company
            # installation into a demo or a ready production deployment.
            raise QuickstartError('reviewed_company_providers_required')
        if preflight['status'] != 'valid':
            raise QuickstartError('configuration_or_restore_review_invalid')
        if not settings.enable_quality_actions:
            raise QuickstartError('quality_actions_flag_required')
        result['checks']['configuration'] = dict(status='passed', mode='demo',
                                                  quality_actions='enabled')
        result['checks']['state'] = _state_check(settings, args.new_demo, args.quiesced, deployment)
        result.update(status='passed', code='local_demo_preflight_passed')
    except QuickstartError as error:
        result['code'] = error.code
    except Exception:
        result['code'] = 'quickstart_check_failed'
    return result


def main(argv=None):
    parser = _SafeParser(description=__doc__)
    parser.add_argument('--config-json', help='Check this JSON mapping instead of the environment; never applies it to app.py.')
    parser.add_argument('--profile', choices=('demo', 'company'), default='demo')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--new-demo', action='store_true', help='Check an unused demo destination, without creating it.')
    group.add_argument('--quiesced', action='store_true', help='Assert all writers of an existing demo set are stopped.')
    try:
        args = parser.parse_args(argv)
        if args.profile == 'company' and args.new_demo:
            raise QuickstartError('arguments_invalid')
        result = run_checks(args)
    except QuickstartError as error:
        result = dict(schema_version=1, status='blocked', code=error.code, read_only=True)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'passed' else 2


if __name__ == '__main__':
    sys.exit(main())
