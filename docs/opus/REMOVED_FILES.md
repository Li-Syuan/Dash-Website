# Removed obsolete entrypoints and global pages

Cleanup date: 2026-10-02. `app.py` is the only runnable application launcher.
It calls `reporting_workspace.application.create_app()` directly and exports the main
`app`, `server`, `runtime` and `user_db` for the existing transport tests. The launcher
binds `127.0.0.1:8050` with debug disabled. Persistent demo storage and the default
15 MiB upload-envelope limit are selected only during direct execution; explicit
`REPORTING_MAX_CONTENT_LENGTH` is preserved and the general factory default stays 1 MiB.

Before removal, repository Python import references were checked. The remaining
`tests/test_demo.py` imports and patches were moved to `app.py`. Only the test of
the removed `auth.role_permission` helper was retired; the actual main-session,
role/organization, callback, logout and export security tests remain. Added launcher
tests cover direct factory wiring, loopback/debug settings, import behavior and
preserving an explicit request limit.

No application data or databases were removed or migrated. `demo_services.py` is
retained because the current factory and tests use its synthetic adapters.
`reporting_workspace/legacy_*.py` and report-builder modules remain reusable internal
services/UI components used by the main integration and tests.
`wsgi.py` remains a factory-import adapter for an existing WSGI host; it has no
server object, CLI, `__main__` block or `run_server` call and is not a second launcher.

## Removal inventory

| Old path | Reason |
| --- | --- |
| `__pycache__/app_config.cpython-312.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `__pycache__/app_server.cpython-312.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `__pycache__/auth.cpython-310.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `__pycache__/auth.cpython-312.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `__pycache__/auth.cpython-38.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `__pycache__/demo_app.cpython-310.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `__pycache__/demo_app.cpython-38.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `__pycache__/demo_server.cpython-310.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `__pycache__/demo_server.cpython-38.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `__pycache__/qa_portal_demo.cpython-38.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `app_config.py` | Unused global Dash Pages shell superseded by reporting_workspace/web.py and app-scoped PageSpec navigation. |
| `app_server.py` | Unused pre-factory Flask server with historical test identities and database example configuration. |
| `auth.py` | Unused legacy decorator helper; active page and transport authorization use registry AccessPolicy and service checks. |
| `demo_app.py` | Obsolete launcher wrapper; app.py now creates and runs the main factory directly. |
| `demo_server.py` | Obsolete singleton wrapper; required app/server/runtime/user_db exports now live in app.py. |
| `my_crud_app.py` | Unreferenced standalone CRUD experiment with its own Dash application and separate dependency assumptions. |
| `pages/__pycache__/admin_page.cpython-311.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `pages/__pycache__/admin_page.cpython-312.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `pages/__pycache__/forbidden_403.cpython-311.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `pages/__pycache__/home.cpython-311.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `pages/__pycache__/home.cpython-312.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `pages/__pycache__/login.cpython-311.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `pages/__pycache__/login.cpython-312.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `pages/__pycache__/logout.cpython-311.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `pages/__pycache__/logout.cpython-312.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `pages/__pycache__/not_found_404.cpython-311.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `pages/__pycache__/not_found_404.cpython-312.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `pages/__pycache__/page-1.cpython-311.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `pages/__pycache__/page-1.cpython-312.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `pages/__pycache__/page-2.cpython-311.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `pages/__pycache__/page-2.cpython-312.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `pages/__pycache__/page-3.cpython-312.pyc` | Obsolete compiled bytecode for removed source; not part of the runnable source package. |
| `pages/admin_page.py` | Unused global Dash Pages module superseded by explicit reporting_workspace/ui_pages PageSpecs. |
| `pages/home.py` | Unused global Dash Pages module superseded by explicit reporting_workspace/ui_pages PageSpecs. |
| `pages/login.py` | Unused global Dash Pages module superseded by explicit reporting_workspace/ui_pages PageSpecs. |
| `pages/logout.py` | Unused global Dash Pages module superseded by explicit reporting_workspace/ui_pages PageSpecs. |
| `pages/not_found_404.py` | Unused global Dash Pages module superseded by explicit reporting_workspace/ui_pages PageSpecs. |
| `pages/page-1.py` | Unused global Dash Pages module superseded by explicit reporting_workspace/ui_pages PageSpecs. |
| `pages/page-2.py` | Unused global Dash Pages module superseded by explicit reporting_workspace/ui_pages PageSpecs. |
| `pages/page-3.py` | Unused global Dash Pages module superseded by explicit reporting_workspace/ui_pages PageSpecs. |
| `qa_portal_demo.py` | Separate 8051 standalone launcher removed; QSL is integrated under the main authenticated application. |
| `test_admin.py` | Historical database-writing example, not a supported unittest; removed to prevent accidental execution. |

## Partial source cleanup

`examples/registry_fixture.py` remains an importable PageSpec/layout/callback and
policy fixture. Only its separate `__main__` / `run_server` launch block and now
unused factory import were removed, and its module description was corrected.
The complete pre-edit file was separately archived with its SHA-256 hash in the
external archive's `modified_files` inventory; it is not a whole-file removal.

## Focused validation

- Python 3.8.20: `python -B -m unittest discover -s tests -p test_demo.py -q`,
  22 tests passed, including preserved main security/transport checks and 3 new
  launcher tests.
- Python 3.10.21: the same command, 22 tests passed.
- Python 3.8.20: `python -B -m unittest discover -s tests -p test_launcher.py -q`,
  3 storage-launcher tests passed.
- No remaining Python import references to the removed modules were found.
- A non-test runtime scan now finds `run_server` only in `app.py`.
- `git diff --check` passed. No browser or production validation is claimed here.

## Recovery and backout

Every removed file was copied byte-for-byte to an external cleanup archive before
deletion. The archive is deliberately excluded from the delivered repository and
contains a JSON manifest recording original relative paths, byte lengths and
SHA-256 hashes, including ignored local bytecode. Exact restored files can be
verified against those hashes. Only source files should be restored for a normal
code backout; bytecode should be regenerated by the selected Python runtime.

The supplied published base is `eaaf21a9d4bbe0a050069bb6a68a95458b687684`.
That object was not available in this local checkout during cleanup, so a backout
from it has not been verified here. In a checkout that contains that published
commit, first inspect `git ls-tree -r --name-only <base>` and restore only the
required paths from it. A newly staged file absent from that commit, such as the
standalone QA launcher in this working tree, must instead be recovered from the
external archive. No history was rewritten and no commit was made by this cleanup.

Removing source files does not migrate old standalone QA data. Existing local
data directories must be retained and reviewed separately before any migration.
