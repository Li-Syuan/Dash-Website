"""Private browser-test fixture. Imports the sole product entrypoint, app.py.

Controlled through the parent's stdin only; no fixture/admin HTTP routes exist.
Never calls app.run(), starts schedulers, or touches the normal instance directory.
"""
import json
import os
from pathlib import Path
import sys
import threading
import base64
import hashlib
import subprocess
from urllib.request import urlopen
from importlib.metadata import version

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
STATE = Path(sys.argv[1]).resolve()
EVIDENCE = (ROOT / 'output' / 'playwright').resolve()
if STATE.name != 'state' or os.path.commonpath([str(STATE), str(EVIDENCE)]) != str(EVIDENCE):
    raise ValueError('Fixture state must be in an isolated browser evidence run directory')
STATE.mkdir(parents=True, exist_ok=True)
for key in tuple(os.environ):
    if key.startswith('REPORTING_'):
        del os.environ[key]
os.environ.update(REPORTING_MODE='demo', REPORTING_STATE_PATH=str(STATE / 'workspace.sqlite'),
                  REPORTING_MAX_CONTENT_LENGTH=str(15 * 1024 * 1024))

from app import server, runtime
import dash
from types import SimpleNamespace
from werkzeug.serving import make_server, WSGIRequestHandler

accounts = {
    'browser-owner-a': dict(password='demo-only', role='user', org='A'),
    'browser-peer-a': dict(password='demo-only', role='user', org='A'),
    'browser-admin-b': dict(password='demo-only', role='admin', org='B'),
    'browser-revoked': dict(password='demo-only', role='user', org='A'),
}
runtime.identities.user_db.update(accounts)
owner = runtime.identities.get_user('browser-owner-a')
tenant_b = runtime.identities.get_user('browser-admin-b')
records = []
for index in range(25):
    records.append(runtime.definitions.create(owner, dict(name='Browser seed A {:02d}'.format(index),
        description='Synthetic browser acceptance fixture', cadence='manual', enabled=True)))
foreign = runtime.definitions.create(tenant_b, dict(name='Browser tenant B private',
    description='Synthetic B isolation sentinel', cadence='manual', enabled=True))
qsl = server.extensions['qa_demo_crud']
qsl_user = SimpleNamespace(id='demo-admin', orgcode='ORG_QA01', is_authenticated=True, is_dev=True, is_admin=False)
for index in range(3, 15):
    qsl.create(qsl_user, dict(Material_Type='IC', Vendor_Code='BROWSER{:03d}'.format(index),
        Vendor_Name='Browser synthetic vendor {:02d}'.format(index), Country='TW', City='Taipei',
        Rev='A', Supplier_Level='LEVEL 1'))

class BrowserRequestHandler(WSGIRequestHandler):
    protocol_version = 'HTTP/1.0'


httpd = make_server('127.0.0.1', 0, server, threaded=True,
    request_handler=BrowserRequestHandler if os.environ.get('QA_HTTP10') == '1' else None)
worker = threading.Thread(target=httpd.serve_forever, daemon=True)
worker.start()
print(json.dumps(dict(event='ready', port=httpd.server_port, pid=os.getpid(),
    seed_id=records[0]['id'], foreign_id=foreign['id'], python=sys.version.split()[0],
    packages={name: version(name) for name in ('dash', 'Flask', 'Werkzeug', 'dash-bootstrap-components', 'dash-mantine-components')},
    plotly_asset=dict(path='/_dash-component-suites/dash/dcc/async-plotlyjs.js',
        size=(Path(dash.__file__).parent / 'dcc' / 'async-plotlyjs.js').stat().st_size,
        sha256=hashlib.sha256((Path(dash.__file__).parent / 'dcc' / 'async-plotlyjs.js').read_bytes()).hexdigest()),
    source_hashes={str(item.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in [ROOT / 'app.py'] + sorted((ROOT / 'reporting_workspace').rglob('*.py')) +
        sorted((ROOT / 'assets').glob('*.js')) + sorted((ROOT / 'assets').glob('*.css'))})), flush=True)
try:
    for line in sys.stdin:
        command = json.loads(line)
        action = command['action']
        if action == 'shutdown':
            print(json.dumps(dict(event='stopped', pid=os.getpid())), flush=True)
            break
        if action == 'revoke':
            runtime.identities.user_db.pop(command['user'], None)
            result = dict(revoked=True)
        elif action == 'definitions':
            user = runtime.identities.get_user(command.get('user', 'browser-owner-a'))
            result = runtime.definitions.list(user, q=command.get('q', ''), limit=100, include_deleted=True)
        elif action == 'qsl':
            result = qsl.query(qsl_user)
        elif action == 'static_asset':
            target = command['path']
            if not target.startswith(('/_dash-component-suites/', '/assets/')) or '\\' in target:
                raise ValueError('Only local application static assets are allowed')
            try:
                url = 'http://127.0.0.1:{}{}'.format(httpd.server_port, target)
                if os.environ.get('QA_STATIC_READER') == 'powershell':
                    transfer = STATE / 'static-transfer.tmp'
                    environment = dict(os.environ, QA_BROWSER_STATIC_URL=url, QA_BROWSER_STATIC_FILE=str(transfer))
                    script = ('$ErrorActionPreference="Stop"; '
                              '$r=Invoke-WebRequest -UseBasicParsing -Uri $env:QA_BROWSER_STATIC_URL '
                              '-OutFile $env:QA_BROWSER_STATIC_FILE -PassThru -TimeoutSec 8; '
                              '@{status=[int]$r.StatusCode;content_type=[string]$r.Headers["Content-Type"]} '
                              '| ConvertTo-Json -Compress')
                    for attempt in range(3):
                        try:
                            fetched = subprocess.run([os.environ.get('QA_POWERSHELL', 'powershell'), '-NoProfile', '-NonInteractive', '-Command', script],
                                env=environment, capture_output=True, text=True, timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW)
                        except subprocess.TimeoutExpired:
                            if attempt == 2:
                                raise
                            continue
                        if fetched.returncode == 0:
                            break
                    fetched.check_returncode()
                    result = json.loads(fetched.stdout)
                    content = transfer.read_bytes()
                    transfer.unlink()
                    result['body'] = base64.b64encode(content).decode('ascii')
                    result['attempts'] = attempt + 1
                else:
                    with urlopen(url, timeout=30) as response:
                        content = response.read()
                        result = dict(status=response.status, content_type=response.headers.get('Content-Type'),
                            body=base64.b64encode(content).decode('ascii'))
                result.update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
            except Exception as error:
                result = dict(error=type(error).__name__)
        else:
            raise ValueError('Unsupported fixture command')
        print(json.dumps(dict(event='reply', sequence=command['sequence'], result=result)), flush=True)
finally:
    httpd.shutdown()
    worker.join(timeout=5)
    httpd.server_close()
