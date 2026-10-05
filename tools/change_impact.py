"""Read-only, offline change impact hints. Never import product code or run tests.

The AST graph is deliberately conservative, not call-graph/coverage evidence.
See docs/change-impact/README.md for the uncertainty and release contract.
"""
import argparse
import ast
from collections import deque
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
FULL_TEST = ['python', '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v']
ENTRYPOINT_TEST = ['python', '-B', 'tests/test_entrypoint_coverage.py',
                   '--report-template', '--quality-actions']
BROWSER_TEST = ['node', 'tests/browser/acceptance.cjs']
SOURCE_DIRS = ('reporting_workspace', 'tools', 'tests', 'examples', 'benchmarks')
CORE = {
    'app.py', 'wsgi.py', 'demo_services.py',
    'reporting_workspace/application.py', 'reporting_workspace/authorization.py',
    'reporting_workspace/config.py', 'reporting_workspace/providers.py',
    'reporting_workspace/registry.py', 'reporting_workspace/web.py',
    'reporting_workspace/state.py', 'reporting_workspace/admin_schema.py',
    'reporting_workspace/crud.py', 'reporting_workspace/legacy_demo_ui.py',
    'reporting_workspace/qa_enhancements_ui.py',
    'reporting_workspace/errors.py', 'reporting_workspace/notifications.py',
    'reporting_workspace/theme.py', 'reporting_workspace/static_transport.py',
    'reporting_workspace/definition_policy.py', 'reporting_workspace/legacy_policy.py',
    'reporting_workspace/lifecycle.py', 'reporting_workspace/launcher.py',
    'reporting_workspace/deployment.py', 'reporting_workspace/ui_pages/shared.py',
}
LIMITATIONS = [
    'Static module imports and reviewed runtime bindings are hints, not execution or coverage proof.',
    'Conditional/dead-code imports can over-report; reflection, data flow, plugins and private company adapters are not fully modeled.',
    'Default-off and demo-only declarations are included; this report does not say they are enabled or that a schedule is running.',
    'Suggested commands are inert argv data. Review trusted source and use an approved runtime; never execute commands from report JSON.',
    'Full regression is mandatory before publication; browser, target OS/Python patches and company integrations need separate evidence.',
]


class ImpactError(ValueError):
    """Input/repository cannot be inspected safely."""


def safe_path(value):
    if (not isinstance(value, str) or not value or len(value) > 512 or
            not re.fullmatch(r'[A-Za-z0-9_./-]+', value) or value.startswith(('/', '-')) or
            any(part in ('', '.', '..') for part in value.split('/'))):
        raise ImpactError('Use a canonical repository-relative path (letters, digits, _, -, ., /).')
    return value


def local_file(root, relative):
    relative = safe_path(relative)
    candidate = root / relative
    for parent in (candidate,) + tuple(candidate.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ImpactError('Symlink source paths are not read: ' + relative)
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        raise ImpactError('Source path leaves the repository.')
    return candidate


def git(root, *args):
    # Fixed read-only Git operations only. Disable external diff/textconv and
    # fsmonitor helpers; no shell, checkout, hooks, object fetching or writes.
    command = ['git', '-c', 'core.fsmonitor=false', '-c', 'core.quotePath=true'] + list(args)
    try:
        result = subprocess.run(command, cwd=str(root), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ImpactError('The local Git read failed or timed out.')
    if result.returncode:
        raise ImpactError('The local Git read failed; verify the repository and commit reference.')
    try:
        return result.stdout.decode('utf-8')
    except UnicodeDecodeError:
        raise ImpactError('Repository paths must be UTF-8.')


def changes_from_git(root, selected=None, base=None):
    actual = Path(git(root, 'rev-parse', '--show-toplevel').strip()).resolve()
    if actual != root.resolve():
        raise ImpactError('--repo must name the Git repository root.')
    reference = base or 'HEAD'
    if (len(reference) > 200 or reference.startswith('-') or
            not re.fullmatch(r'[A-Za-z0-9_./~^{}-]+', reference)):
        raise ImpactError('Invalid base commit reference.')
    commit = git(root, 'rev-parse', '--verify', '--end-of-options', reference + '^{commit}').strip()
    if not re.fullmatch(r'[0-9a-f]{40,64}', commit):
        raise ImpactError('The base did not resolve to a local commit.')
    tracked = set(filter(None, git(root, 'ls-files', '--cached', '-z').split('\0')))
    untracked = set(filter(None, git(root, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')))
    raw = git(root, 'diff', '--no-ext-diff', '--no-textconv', '--no-renames',
              '--name-status', '-z', commit, '--').split('\0')
    delta = {}
    for i in range(0, len(raw) - 1, 2):
        if i + 1 >= len(raw) or not raw[i + 1]:
            raise ImpactError('Unexpected Git diff path format.')
        delta[safe_path(raw[i + 1])] = raw[i]
    for path in untracked:
        delta[safe_path(path)] = 'A'
    explicit = selected is not None
    selected = sorted(set(safe_path(path) for path in selected)) if explicit else sorted(delta)
    changes = []
    for path in selected:
        candidate = local_file(root, path)
        status = delta.get(path, 'M' if path in tracked and candidate.is_file() else
                           'D' if path in tracked else '?')
        changes.append({'path': path, 'status': status})
    # Ignore unrelated output/temporary files; inspect tracked source and changed
    # Python sources only. Unsafe tracked names fail visibly rather than quote badly.
    sources = sorted(safe_path(path) for path in tracked | set(selected)
                     if path.endswith('.py'))
    return changes, sources, {'base_commit': commit,
                             'head_commit': git(root, 'rev-parse', 'HEAD').strip(),
                             'selection': 'explicit paths' if explicit else 'Git working tree against base'}


def dotted(path):
    parts = list(PurePosixPath(path).with_suffix('').parts)
    if parts[-1] == '__init__':
        parts.pop()
    return '.'.join(parts)


def call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return call_name(node.value) + '.' + node.attr
    return ''


def literal(node, constants):
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int)):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = literal(node.left, constants), literal(node.right, constants)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
    return None


def location(path, node, reason):
    return {'path': path, 'line': node.lineno, 'reason': reason}


class SourceIndex:
    def __init__(self, root, files):
        self.root = root
        self.trees, self.constants, self.graph = {}, {}, {}
        self.hashes, self.issues, self.edges = {}, [], {}
        self.modules = {dotted(path): path for path in files}
        for path in files:
            try:
                candidate = local_file(root, path)
                if not candidate.is_file():
                    continue
                if candidate.stat().st_size > 2 * 1024 * 1024:
                    raise ImpactError('Source exceeds the 2 MiB parser bound: ' + path)
                content = candidate.read_bytes()
                self.hashes[path] = hashlib.sha256(content).hexdigest()
                tree = ast.parse(content, filename=path, feature_version=(3, 8))
                self.trees[path] = tree
                constants = {}
                for node in tree.body:
                    if isinstance(node, ast.Assign):
                        value = literal(node.value, constants)
                        for target in node.targets:
                            if isinstance(target, ast.Name) and value is not None:
                                constants[target.id] = value
                self.constants[path] = constants
            except (OSError, SyntaxError, ValueError, RecursionError) as error:
                self.issues.append({'code': 'unreadable_source', 'path': path,
                                    'reason': 'Source unavailable, unsafe or not Python 3.8 syntax (' + type(error).__name__ + ').'})
        self.graph = {path: set() for path in self.trees}
        for path, tree in sorted(self.trees.items()):
            self._imports(path, tree)

    def edge(self, source, target, evidence):
        if target in self.trees and source != target:
            self.graph[source].add(target)
            self.edges.setdefault((source, target), evidence)

    def _imports(self, path, tree):
        module = dotted(path)
        package = module if path.endswith('/__init__.py') else module.rpartition('.')[0]
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    aliases[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    aliases[alias.asname or alias.name] = (node.module or '') + '.' + alias.name
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                else:
                    if node.level:
                        parts = package.split('.') if package else []
                        if node.level > len(parts):
                            self.issues.append({'code': 'unresolved_import', 'path': path,
                                                'reason': 'Relative import leaves the known package at line ' + str(node.lineno)})
                            continue
                        base = '.'.join(parts[:len(parts) - node.level + 1])
                        base = '.'.join(filter(None, (base, node.module)))
                    else:
                        base = node.module or ''
                    names = [base] + ['.'.join(filter(None, (base, a.name))) for a in node.names if a.name != '*']
                    if any(alias.name == '*' for alias in node.names):
                        self.issues.append({'code': 'wildcard_import', 'path': path,
                                            'reason': 'Wildcard import at line ' + str(node.lineno)})
                resolved = False
                for name in names:
                    candidates = [name]
                    # Existing CLI/tests import sibling modules after adding their
                    # directory to sys.path. Model both possibilities conservatively.
                    if not name.startswith('reporting_workspace.') and package:
                        candidates.append(package + '.' + name)
                    for candidate in candidates:
                        if candidate in self.modules and self.modules[candidate] in self.trees:
                            resolved = True
                            self.edge(path, self.modules[candidate], location(path, node, 'static import ' + name))
                            pieces = candidate.split('.')
                            for count in range(1, len(pieces)):
                                parent = self.modules.get('.'.join(pieces[:count]))
                                if parent and parent.endswith('/__init__.py'):
                                    self.edge(path, parent, location(path, node, 'package initialization for ' + name))
                if isinstance(node, ast.ImportFrom):
                    bases = [base, package + '.' + base] if package else [base]
                    for parent_name in bases:
                        parent_path = self.modules.get(parent_name)
                        if not parent_path or not parent_path.endswith('/__init__.py') or parent_path not in self.trees:
                            continue
                        exports = set()
                        for declaration in self.trees[parent_path].body:
                            if isinstance(declaration, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                                exports.add(declaration.name)
                            elif isinstance(declaration, (ast.Import, ast.ImportFrom)):
                                exports.update(item.asname or item.name.split('.')[0] for item in declaration.names)
                            elif isinstance(declaration, ast.Assign):
                                exports.update(target.id for target in declaration.targets if isinstance(target, ast.Name))
                            elif isinstance(declaration, ast.AnnAssign) and isinstance(declaration.target, ast.Name):
                                exports.add(declaration.target.id)
                        for alias in node.names:
                            member = parent_name + '.' + alias.name
                            if alias.name != '*' and alias.name not in exports and self.modules.get(member) not in self.trees:
                                self.issues.append({'code': 'unresolved_import', 'path': path,
                                                    'reason': 'Package member has no static export/module at line ' + str(node.lineno)})
                if not resolved and (isinstance(node, ast.ImportFrom) and node.level or
                                     any(name.split('.')[0] in ('reporting_workspace', 'tools', 'examples') for name in names)):
                    self.issues.append({'code': 'unresolved_import', 'path': path,
                                        'reason': 'Unresolved internal import at line ' + str(node.lineno)})
            elif isinstance(node, ast.Call):
                name = call_name(node.func)
                name = aliases.get(name, name)
                if name.rsplit('.', 1)[-1] in ('import_module', '__import__', 'exec_module', 'load_module', 'eval', 'exec'):
                    self.issues.append({'code': 'dynamic_code', 'path': path,
                                        'reason': 'Dynamic import/code at line ' + str(node.lineno) + ' is not executed or resolved.'})

    def chain(self, start, target):
        if start not in self.graph or target not in self.graph:
            return None
        queue, seen = deque([(start, [start])]), {start}
        while queue:
            node, chain = queue.popleft()
            if node == target:
                return chain
            for neighbor in sorted(self.graph[node]):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, chain + [neighbor]))
        return None

    def proof(self, chain):
        return [self.edges[(left, right)] for left, right in zip(chain, chain[1:])]


def discover_surfaces(index):
    surfaces = []
    for path, tree in sorted(index.trees.items()):
        if not path.startswith('reporting_workspace/'):
            continue
        constants = index.constants[path]
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                aliases.update({item.asname or item.name: item.name for item in node.names})
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = call_name(node.func).rsplit('.', 1)[-1]
            name = aliases.get(name, name)
            if name in ('register_page', 'add_url_rule'):
                index.issues.append({'code': 'unresolved_registration', 'path': path,
                                     'reason': 'Unsupported dynamic registration at line ' + str(node.lineno)})
            keywords = {item.arg: item.value for item in node.keywords}
            if (name in ('callback', 'clientside_callback') and 'callback_id' not in keywords and
                    path not in ('reporting_workspace/registry.py',
                                 'reporting_workspace/legacy_demo_ui.py',
                                 'reporting_workspace/qa_enhancements_ui.py')):
                index.issues.append({'code': 'unresolved_registration', 'path': path,
                                     'reason': 'Callback lacks reviewed registration metadata at line ' + str(node.lineno)})
            kind, identifier, route, state = None, None, None, 'declaration; activation depends on app configuration'
            if name == 'PageSpec':
                kind = 'page'
                identifier = literal(keywords.get('page_id', node.args[0] if node.args else None), constants)
                route = literal(keywords.get('path', node.args[1] if len(node.args) > 1 else None), constants)
                if not isinstance(route, str) or not route.startswith('/'):
                    index.issues.append({'code': 'unresolved_registration', 'path': path, 'reason': 'Page path cannot be resolved at line ' + str(node.lineno)})
            elif name == 'route':
                # The retired standalone login is not installed by this app.
                if path == 'reporting_workspace/legacy_demo_ui.py':
                    continue
                kind, route = 'http', literal(node.args[0] if node.args else None, constants)
                identifier = route
            elif name in ('callback', 'clientside_callback') and 'callback_id' in keywords and path != 'reporting_workspace/registry.py':
                kind = 'callback'
                identifier = literal(keywords['callback_id'], constants)
                if identifier is None and path == 'reporting_workspace/ui_pages/qa_maintenance.py':
                    value = keywords['callback_id']
                    if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == 'format':
                        template = literal(value.func.value, constants)
                        if template == 'qa-maintenance.action-{}':
                            identifier = 'qa-maintenance.action-*'
                            state = 'generated callback group; exact IDs require runtime registry inspection'
                route = '/_dash-update-component' if name == 'callback' else 'browser-only callback'
            elif name == 'JobSpec':
                kind = 'schedule'
                identifier = literal(node.args[0] if node.args else keywords.get('job_id'), constants)
                state = 'synthetic ETL declaration; disabled by default; launcher-owned worker'
            if kind:
                if not isinstance(identifier, str):
                    index.issues.append({'code': 'unresolved_registration', 'path': path,
                                         'reason': 'Unresolved ' + kind + ' declaration at line ' + str(node.lineno)})
                    continue
                surfaces.append({'kind': kind, 'id': identifier, 'route': route,
                                 'state': state, 'roots': [path],
                                 'declaration': location(path, node, 'AST ' + name + ' declaration')})
    return surfaces


def reviewed_bindings(index, surfaces):
    """Small explicit bridges for dependency injection, verified against AST names.

    These are reviewed repository contracts, not guessed function-name matches.
    Missing anchors fail to full regression rather than silently omit a mapping.
    """
    application = 'reporting_workspace/application.py'
    bindings = [
        ('reporting_workspace/ui_pages/admin_console.py', 'reporting_workspace/governance.py', application, 'ManagedReports'),
        ('reporting_workspace/ui_pages/administration.py', 'reporting_workspace/governance.py', application, 'ManagedReports'),
        ('reporting_workspace/ui_pages/managed_reports.py', 'reporting_workspace/governance.py', application, 'ManagedReports'),
        ('reporting_workspace/ui_pages/reports.py', 'reporting_workspace/providers.py', application, 'AuthorizedReports'),
    ]
    for source, target, anchor, symbol in bindings:
        tree = index.trees.get(anchor)
        matches = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and call_name(node.func) == symbol] if tree else []
        if source in index.trees and target in index.trees and matches:
            index.edge(source, target, location(anchor, matches[0], 'reviewed runtime service binding for ' + source))
        else:
            index.issues.append({'code': 'mapping_drift', 'path': anchor,
                                 'reason': 'Reviewed runtime binding anchor missing: ' + symbol})
    # HTTP handlers live beside the composition root, but do not each consume
    # every optional page. Use audited handler service boundaries for precision.
    http_roots = {
        '/api/reports/export.csv': ['reporting_workspace/providers.py', 'demo_services.py'],
        '/demo-api/report.csv': ['reporting_workspace/providers.py', 'demo_services.py'],
        '/api/managed-reports/<identifier>/export.csv': ['reporting_workspace/governance.py'],
        '/healthz': ['reporting_workspace/deployment.py'],
        '/readyz': ['reporting_workspace/deployment.py'],
    }
    for surface in surfaces:
        if surface['kind'] == 'http' and surface['declaration']['path'] == application:
            extra = http_roots.get(surface['id'])
            if extra is None:
                index.issues.append({'code': 'mapping_drift', 'path': application,
                                     'reason': 'New factory HTTP route needs reviewed service roots: ' + surface['id']})
            else:
                surface['roots'] = extra
                surface['binding'] = 'Reviewed handler runtime-service roots; composition-root changes trigger full regression.'
        if surface['kind'] == 'schedule':
            engine = 'reporting_workspace/etl_dispatch.py'
            tree = index.trees.get(engine)
            calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and call_name(node.func) == 'default_registry'] if tree else []
            if calls:
                surface['roots'].append(engine)
                surface['binding'] = 'ETLDispatch consumes default_registry; all declared jobs share the engine.'
                surface['binding_evidence'] = location(engine, calls[0], surface['binding'])
            else:
                index.issues.append({'code': 'mapping_drift', 'path': engine,
                                     'reason': 'Default ETL registry binding is missing.'})
    anchors = [
        ('reporting_workspace/job_monitor.py', 'JOB_ID', 'example-report-summary',
         'synthetic interval job; disabled by default; launcher-owned worker'),
        ('reporting_workspace/governance.py', 'simulate_schedule', 'managed-report-mail-simulation',
         'manual mock schedule simulation only; no running mail timer or SMTP'),
        ('reporting_workspace/legacy_jobs.py', 'run_mock', 'legacy-report-mail-simulation',
         'manual legacy mock job only; no running timer or SMTP'),
    ]
    for path, symbol, identifier, state in anchors:
        tree = index.trees.get(path)
        found = []
        if tree:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
                    found.append(node)
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == symbol for t in node.targets):
                    if literal(node.value, index.constants[path]) == identifier:
                        found.append(node)
        if found:
            surfaces.append({'kind': 'schedule', 'id': identifier, 'route': None,
                             'state': state, 'roots': [path],
                             'declaration': location(path, found[0], 'reviewed schedule boundary ' + symbol)})
        else:
            index.issues.append({'code': 'mapping_drift', 'path': path,
                                 'reason': 'Reviewed schedule anchor missing: ' + symbol})
    return surfaces


def is_core(path):
    return (path in CORE or path.startswith(('assets/', '.github/', 'reporting_workspace/ui_pages/')) or
            path.startswith('requirements') or path.endswith('/__init__.py') or
            path.endswith('/contract.py'))


def source_files(root):
    files = []
    for directory in SOURCE_DIRS:
        folder = root / directory
        if folder.is_symlink():
            raise ImpactError('Symlink source directory is not inspected: ' + directory)
        if folder.is_dir():
            files.extend(path.relative_to(root).as_posix() for path in folder.rglob('*.py'))
    files.extend(path.name for path in root.glob('*.py'))
    return sorted(set(files))


def analyze(root, changes, files=None):
    root = Path(root).resolve()
    changes = sorted(({'path': safe_path(item['path']), 'status': str(item.get('status', 'M'))}
                      for item in changes), key=lambda item: (item['path'], item['status']))
    index = SourceIndex(root, source_files(root) if files is None else files)
    surfaces = reviewed_bindings(index, discover_surfaces(index))
    reasons = []
    changed_paths = {item['path'] for item in changes}
    for change in changes:
        path = change['path']
        if change['status'] != 'M':
            reasons.append({'code': 'structural_change', 'path': path,
                            'reason': 'Added/deleted/renamed/untracked/unknown paths have incomplete historical edges.'})
        if is_core(path):
            reasons.append({'code': 'core_change', 'path': path,
                            'reason': 'Shared, configuration, authorization, lifecycle or asset boundary requires full regression.'})
        if path not in index.trees:
            reasons.append({'code': 'unknown_path', 'path': path,
                            'reason': 'No safely parsed Python dependency mapping; non-Python/data/docs are not assumed inert.'})
    # Runtime dynamic imports can hide consumers of ANY changed module. Existing
    # test/tool dynamic loaders are scoped to changes in that dependency region.
    for issue in index.issues:
        path = issue['path']
        if (issue['code'] in ('mapping_drift', 'unresolved_registration', 'unreadable_source') or
                path.startswith('reporting_workspace/') or path in changed_paths or
                any(index.chain(path, changed) for changed in changed_paths)):
            reasons.append(issue)
    impacts = []
    for surface in sorted(surfaces, key=lambda item: (item['kind'], item['id'], item['declaration']['path'])):
        evidence = []
        for changed in sorted(changed_paths):
            chains = [index.chain(start, changed) for start in sorted(set(surface['roots']))]
            chains = [chain for chain in chains if chain]
            if chains:
                chain = min(chains, key=lambda item: (len(item), item))
                evidence.append({'changed': changed, 'dependency_path': chain,
                                 'imports': index.proof(chain)})
        if evidence:
            item = dict(surface)
            item['evidence'] = evidence
            item['confidence'] = 'static candidate; not runtime coverage'
            impacts.append(item)
    tests = []
    for path in sorted(index.trees):
        if not re.fullmatch(r'tests/test_[A-Za-z0-9_]+\.py', path):
            continue
        evidence = []
        for changed in sorted(changed_paths):
            chain = index.chain(path, changed)
            if chain:
                evidence.append({'changed': changed, 'dependency_path': chain, 'imports': index.proof(chain)})
        if evidence:
            tests.append({'path': path, 'evidence': evidence,
                          'argv': ['python', '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', Path(path).name, '-v']})
    mapped = {entry['changed'] for item in impacts + tests for entry in item['evidence']}
    for changed in sorted(changed_paths - mapped):
        reasons.append({'code': 'unmapped_change', 'path': changed,
                        'reason': 'No static surface or test consumer found; absence of an edge is not proof of no impact.'})
    # Stable order/dedup; no timestamp, absolute host path or environment secrets.
    reasons = [json.loads(value) for value in sorted({json.dumps(item, sort_keys=True) for item in reasons})]
    digest = hashlib.sha256(json.dumps(index.hashes, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    full = bool(reasons)
    return {
        'schema_version': 1, 'status': 'FULL_REGRESSION_REQUIRED' if full else 'NO_CHANGES' if not changes else 'FOCUSED_HINTS_ONLY',
        'changes': changes, 'source_fingerprint': digest,
        'analysis': {'method': 'Python 3.8 AST module imports plus reviewed runtime service/schedule bindings',
                     'parsed_files': len(index.trees), 'coverage_complete': False,
                     'tests_executed': False, 'source_imported': False},
        'impacts': impacts, 'suggested_tests': tests,
        'fallback_reasons': reasons, 'full_regression_required_now': full,
        'conservative_scope': 'all pages, APIs, callbacks and schedules; current static candidates are not exhaustive' if full else 'reported candidates only; not a coverage guarantee',
        'full_regression_before_publication': True,
        'required_checks': [
            {'argv': FULL_TEST, 'when': 'now' if full else 'before publication', 'reason': 'Mandatory full regression; suggestions never waive this gate.'},
            {'argv': ENTRYPOINT_TEST, 'when': 'before publication or page/API/authorization changes', 'reason': 'Both optional reports must be included in the entrypoint matrix.'},
            {'argv': BROWSER_TEST, 'env': {'QA_REPORT_TEMPLATE': '1', 'QA_QUALITY_ACTIONS': '1'},
             'when': 'UI/callback/export/shared changes and before publishing affected UI',
             'reason': 'Real browser evidence is separate; run only in a permitted existing environment.'},
            {'argv': ['git', 'diff', '--check'], 'when': 'before publication', 'reason': 'Whitespace/error check.'},
        ],
        'limitations': LIMITATIONS,
    }


def render_text(report):
    lines = ['Change impact: ' + report['status'], 'Analysis only; no product imports, tests or jobs executed.',
             'Full regression before publication: REQUIRED', 'Coverage complete: NO',
             'Source fingerprint: ' + report['source_fingerprint'], '', 'Changed files:']
    lines.extend('  {status} {path}'.format(**item) for item in report['changes'])
    if not report['changes']:
        lines.append('  (none)')
    lines.append('\nCandidate pages / APIs / callbacks / schedules:')
    for item in report['impacts']:
        declaration = item['declaration']
        lines.append('  {kind}: {id}{route} [{path}:{line}]'.format(
            kind=item['kind'], id=item['id'], route=' (' + item['route'] + ')' if item['route'] else '', **declaration))
        lines.append('    ' + item['state'])
        for evidence in item['evidence']:
            lines.append('    dependency: ' + ' -> '.join(evidence['dependency_path']))
    if not report['impacts']:
        lines.append('  No static candidate found. This is not proof of no impact.')
    if report['fallback_reasons']:
        lines.append('\nFULL regression fallback:')
        for item in report['fallback_reasons']:
            lines.append('  {code}: {path}: {reason}'.format(**item))
    lines.append('\nSuggested focused tests (review first; not a replacement for full regression):')
    for item in report['suggested_tests']:
        lines.append('  ' + shlex.join(item['argv']))
        for evidence in item['evidence']:
            lines.append('    dependency: ' + ' -> '.join(evidence['dependency_path']))
    lines.append('\nRequired checks (recommendations only):')
    for item in report['required_checks']:
        prefix = ' '.join(key + '=' + shlex.quote(value) for key, value in sorted(item.get('env', {}).items()))
        lines.append('  ' + ((prefix + ' ') if prefix else '') + shlex.join(item['argv']) + ' [' + item['when'] + ']')
    lines.append('\nLimits:')
    lines.extend('  - ' + item for item in report['limitations'])
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=ROOT, help='Existing local Git repository root; default is this checkout.')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--changed', nargs='+', metavar='PATH', help='Explicit repository-relative paths; actual Git status is retained.')
    selection.add_argument('--base', help='Local commit to compare against the current working tree; includes staged, unstaged and untracked paths.')
    parser.add_argument('--format', choices=('text', 'json'), default='text')
    args = parser.parse_args(argv)
    try:
        root = args.repo.resolve()
        changes, files, provenance = changes_from_git(root, args.changed, args.base)
        report = analyze(root, changes, files)
        report['git'] = provenance
    except (ImpactError, OSError) as error:
        print('change-impact: ' + str(error), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) if args.format == 'json' else render_text(report), end='\n' if args.format == 'json' else '')
    # 0 means report generated, not tests passed. Consumers must read status.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
