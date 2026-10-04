"""Bound static WSGI writes for the pinned Windows development server.

Large single writes followed by socket close lost tail bytes on the tested
Windows/Werkzeug 2.2.3 stack. Smaller writes preserve the exact response while
avoiding that reproduced send/close failure. No sleeps, retries, buffering of
the whole response, header changes, or authentication changes are involved.
"""

STATIC_CHUNK_SIZE = 16 * 1024
_PREFIXES = ('/assets/', '/_dash-component-suites/')


def _parts(data):
    # Do not normalize an invalid WSGI chunk into a valid response.
    if not isinstance(data, bytes) or len(data) <= STATIC_CHUNK_SIZE:
        yield data
    else:
        for offset in range(0, len(data), STATIC_CHUNK_SIZE):
            yield data[offset:offset + STATIC_CHUNK_SIZE]


class _StaticChunks:
    """Close the original iterable even if the client never starts reading."""

    def __init__(self, source):
        self.source = source
        self.iterator = self._iterate()
        self.closed = False

    def _iterate(self):
        for data in self.source:
            yield from _parts(data)

    def __iter__(self):
        return self

    def __next__(self):
        if self.closed:
            raise StopIteration
        try:
            return next(self.iterator)
        except BaseException:
            self.close()
            raise

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.iterator.close()
            finally:
                close = getattr(self.source, 'close', None)
                if close is not None:
                    close()


class StaticTransport:
    """Preserve WSGI semantics; split only local static GET/HEAD responses."""

    def __init__(self, application):
        self.application = application

    def __call__(self, environ, start_response):
        if (environ.get('REQUEST_METHOD') not in ('GET', 'HEAD') or
                not environ.get('PATH_INFO', '').startswith(_PREFIXES)):
            return self.application(environ, start_response)

        def start(status, headers, exc_info=None):
            write = start_response(status, headers, exc_info)

            def write_parts(data):
                for part in _parts(data):
                    write(part)
            return write_parts

        return _StaticChunks(self.application(environ, start))
