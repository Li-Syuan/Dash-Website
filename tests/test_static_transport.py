"""Exact-byte and WSGI lifetime contracts for the static transport fix."""
import hashlib
from pathlib import Path
import sys
import unittest

from reporting_workspace.static_transport import STATIC_CHUNK_SIZE, StaticTransport


class Body:
    def __init__(self, chunks, error=None):
        self.chunks = chunks
        self.error = error
        self.closes = 0

    def __iter__(self):
        yield from self.chunks
        if self.error is not None:
            raise self.error

    def close(self):
        self.closes += 1


class StaticTransportTests(unittest.TestCase):
    def invoke(self, body, path='/_dash-component-suites/dash/dcc/async-plotlyjs.js',
               method='GET', status='200 OK', headers=None, prefix=None):
        captured = dict(writes=[])
        headers = [] if headers is None else headers

        def start_response(received_status, received_headers, exc_info=None):
            captured.update(status=received_status, headers=received_headers, exc_info=exc_info)
            return captured['writes'].append

        def application(environ, start):
            writer = start(status, headers)
            if prefix is not None:
                writer(prefix)
            return body

        output = StaticTransport(application)(dict(PATH_INFO=path, REQUEST_METHOD=method), start_response)
        return output, captured

    def test_large_multi_chunk_response_preserves_exact_bytes_and_headers(self):
        expected = bytes(range(256)) * 17000 + b'final tail'
        body = Body([b'', expected[:17], expected[17:]])
        headers = [('Content-Length', str(len(expected))), ('ETag', 'unchanged')]
        output, captured = self.invoke(body, headers=headers)
        parts = list(output)
        self.assertEqual(b''.join(parts), expected)
        self.assertTrue(all(len(part) <= STATIC_CHUNK_SIZE for part in parts))
        self.assertIs(captured['headers'], headers)
        self.assertEqual(captured['status'], '200 OK')
        output.close()
        self.assertEqual(body.closes, 1)

    def test_close_before_first_read_closes_original_once(self):
        body = Body([b'not read'])
        output, _ = self.invoke(body)
        output.close()
        output.close()
        self.assertEqual(body.closes, 1)
        self.assertEqual(list(output), [])

    def test_client_abort_during_large_chunk_closes_original(self):
        body = Body([b'x' * (STATIC_CHUNK_SIZE * 3)])
        output, _ = self.invoke(body)
        self.assertEqual(len(next(output)), STATIC_CHUNK_SIZE)
        output.close()
        self.assertEqual(body.closes, 1)
        self.assertEqual(list(output), [])

    def test_iteration_failure_propagates_and_closes_original(self):
        failure = RuntimeError('synthetic iterator failure')
        body = Body([b'first'], failure)
        output, _ = self.invoke(body)
        self.assertEqual(next(output), b'first')
        with self.assertRaises(RuntimeError) as caught:
            next(output)
        self.assertIs(caught.exception, failure)
        self.assertEqual(body.closes, 1)

    def test_nonstatic_urls_and_mutation_requests_are_untouched(self):
        for path, method in (('/_dash-update-component', 'POST'), ('/api/reports/export.csv', 'GET'),
                             ('/assets/synthetic.js', 'POST'), ('/assets-other/file', 'GET'),
                             ('/prefix/assets/file', 'GET'), ('/_dash-component-suites', 'GET')):
            with self.subTest(path=path, method=method):
                body = Body([b'x' * 50000])
                output, _ = self.invoke(body, path=path, method=method)
                self.assertIs(output, body)
                self.assertEqual(body.closes, 0)
                output.close()

    def test_error_status_and_empty_head_body_are_preserved(self):
        for status in ('401 Unauthorized', '403 Forbidden', '404 Not Found', '500 Internal Server Error'):
            output, captured = self.invoke(Body([b'denied']), status=status)
            self.assertEqual(b''.join(output), b'denied')
            self.assertEqual(captured['status'], status)
        headers = [('Content-Length', '500000')]
        output, captured = self.invoke(Body([]), path='/assets/file.js', method='HEAD', headers=headers)
        self.assertEqual(list(output), [])
        self.assertIs(captured['headers'], headers)

    def test_legacy_wsgi_write_callable_retains_order_and_exact_bytes(self):
        prefix = b'a' * 50000
        output, captured = self.invoke(Body([b'tail']), prefix=prefix)
        self.assertEqual(b''.join(captured['writes']) + b''.join(output), prefix + b'tail')
        self.assertTrue(all(len(part) <= STATIC_CHUNK_SIZE for part in captured['writes']))

    def test_start_response_exception_info_is_forwarded_unchanged(self):
        received = []
        try:
            raise ValueError('synthetic WSGI replacement')
        except ValueError:
            info = sys.exc_info()

        def application(environ, start):
            start('500 Internal Server Error', [], info)
            return []

        def start(status, headers, exc_info):
            received.append(exc_info)
            return lambda data: None

        list(StaticTransport(application)(dict(PATH_INFO='/assets/file', REQUEST_METHOD='GET'), start))
        self.assertIs(received[0], info)

    def test_factory_serves_original_plotly_bytes_in_bounded_chunks(self):
        import dash.dcc
        from reporting_workspace.application import create_app
        from reporting_workspace.lifecycle import dispose_app
        server = create_app()
        self.addCleanup(dispose_app, server)
        expected = (Path(dash.dcc.__file__).parent / 'async-plotlyjs.js').read_bytes()
        response = server.test_client().get('/_dash-component-suites/dash/dcc/async-plotlyjs.js')
        self.addCleanup(response.close)
        parts = list(response.response)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(int(response.headers['Content-Length']), len(expected))
        self.assertTrue(all(len(part) <= STATIC_CHUNK_SIZE for part in parts))
        self.assertEqual(hashlib.sha256(b''.join(parts)).digest(), hashlib.sha256(expected).digest())
        head = server.test_client().head('/_dash-component-suites/dash/dcc/async-plotlyjs.js')
        self.addCleanup(head.close)
        self.assertEqual(head.data, b'')
        self.assertEqual(int(head.headers['Content-Length']), len(expected))


if __name__ == '__main__':
    unittest.main()
