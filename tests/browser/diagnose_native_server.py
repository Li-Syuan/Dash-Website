"""Small, synthetic, loopback WSGI transport diagnostic; no product writes."""
import hashlib
import json
import os
from pathlib import Path
import sys
import socket
import threading
import time
from urllib.request import urlopen
from urllib.parse import parse_qs

import dash
from werkzeug.serving import make_server, WSGIRequestHandler

DEST = Path(sys.argv[1]).resolve()
ROOT = Path(__file__).resolve().parents[2]
if os.path.commonpath([str(DEST), str(ROOT / 'output' / 'playwright')]) != str(ROOT / 'output' / 'playwright'):
    raise ValueError('Diagnostic output must remain in browser evidence')
DEST.mkdir(parents=True, exist_ok=True)
lock = threading.Lock()
events = []


def event(**entry):
    entry['time'] = round(time.monotonic(), 6)
    with lock:
        events.append(entry)
        with (DEST / 'server-events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(entry) + '\n')


package = Path(dash.__file__).parent
bodies = {
    '/small.js': b'/* synthetic */\n' + b' ' * 4096,
    '/large.js': b'/* synthetic */\n' + b' ' * (4 * 1024 * 1024),
    '/table.js': (package / 'dash_table' / 'async-table.js').read_bytes(),
    '/plotly.js': (package / 'dcc' / 'async-plotlyjs.js').read_bytes(),
    '/': b'<!doctype html><title>Native loopback transport diagnostic</title>',
}


def application(environ, start_response):
    path = environ['PATH_INFO']
    body = bodies.get(path, b'Not found')
    chunk = int(parse_qs(environ.get('QUERY_STRING', '')).get('chunk', ['0'])[0]) or len(body)
    event(kind='response_start', path=path, bytes=len(body), chunk_size=chunk)
    start_response('200 OK' if path in bodies else '404 Not Found', [
        ('Content-Type', 'text/html' if path == '/' else 'application/javascript'),
        ('Content-Length', str(len(body))), ('Cache-Control', 'no-store')])
    try:
        for start in range(0, len(body), chunk):
            yield body[start:start + chunk]
        event(kind='iterator_completed', path=path, bytes=len(body))
    except BaseException as error:
        event(kind='iterator_error', path=path, error=type(error).__name__)
        raise
    finally:
        event(kind='iterator_closed', path=path)


class Handler(WSGIRequestHandler):
    protocol_version = os.environ.get('QA_DIAGNOSTIC_HTTP', 'HTTP/1.1')

    def connection_dropped(self, error, environ=None):
        event(kind='connection_dropped', path=(environ or {}).get('PATH_INFO', self.path),
              error=type(error).__name__, errno=getattr(error, 'errno', None),
              winerror=getattr(error, 'winerror', None))

    def log(self, kind, message, *args):
        event(kind='server_log', level=kind, path=self.path)

    def finish(self):
        event(kind='finish_before', path=getattr(self, 'path', ''),
            send_buffer=self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF),
            receive_buffer=self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF),
            socket_type=type(self.connection).__name__)
        delay = float(os.environ.get('QA_DIAGNOSTIC_FINISH_DELAY', '0'))
        if delay:
            time.sleep(delay)
        super().finish()
        event(kind='finish_after', path=getattr(self, 'path', ''), delay_seconds=delay)


httpd = make_server('127.0.0.1', 0, application, threaded=True, request_handler=Handler)
thread = threading.Thread(target=httpd.serve_forever, daemon=True)
thread.start()
manifest = {path: dict(bytes=len(body), sha256=hashlib.sha256(body).hexdigest()) for path, body in bodies.items()}
print(json.dumps(dict(event='ready', port=httpd.server_port, pid=os.getpid(), protocol=Handler.protocol_version,
    finish_delay=float(os.environ.get('QA_DIAGNOSTIC_FINISH_DELAY', '0')),
    shutdown_method=httpd.shutdown_request.__qualname__, socket_sendall=socket.socket.sendall.__qualname__,
    manifest=manifest, proxy_variables={name: bool(os.environ.get(name)) for name in
        ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY')})), flush=True)
try:
    for line in sys.stdin:
        command = json.loads(line)
        if command['action'] == 'shutdown':
            break
        if command['action'] != 'python_fetch':
            raise ValueError('Unsupported diagnostic action')
        target = command['path']
        if target.split('?')[0] not in bodies:
            raise ValueError('Unknown diagnostic path')
        record = dict(client='python urllib', path=target, received=0, chunks=[])
        digest = hashlib.sha256()
        started = time.monotonic()
        try:
            with urlopen('http://127.0.0.1:{}{}'.format(httpd.server_port, target), timeout=6) as response:
                record.update(status=response.status, headers=dict(response.headers))
                while True:
                    part = response.read(16384)
                    if not part:
                        break
                    record['received'] += len(part)
                    record['chunks'].append(len(part))
                    digest.update(part)
                record['complete'] = True
        except Exception as error:
            record.update(error=type(error).__name__, errno=getattr(error, 'errno', None), complete=False)
        record.update(sha256=digest.hexdigest(), milliseconds=round((time.monotonic()-started)*1000, 2))
        print(json.dumps(dict(event='reply', sequence=command['sequence'], result=record)), flush=True)
finally:
    httpd.shutdown()
    thread.join(timeout=5)
    httpd.server_close()
    event(kind='server_stopped', pid=os.getpid())
