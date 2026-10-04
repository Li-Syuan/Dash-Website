"""Read-only diagnostics around unchanged Flask session decisions in unittest."""
import datetime
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.getcwd())
from flask.sessions import SecureCookieSessionInterface
from itsdangerous import SignatureExpired

original = SecureCookieSessionInterface.open_session
def inspect_session(self, app, request):
    result = original(self, app, request)
    value = request.cookies.get(self.get_cookie_name(app))
    if value and not result:
        serializer = self.get_signing_serializer(app)
        try:
            serializer.loads(value, max_age=int(app.permanent_session_lifetime.total_seconds()))
        except SignatureExpired as error:
            print('SESSION_DIAGNOSTIC ' + json.dumps(dict(
                path=request.path, reason=type(error).__name__,
                detail=str(error), signed_at=error.date_signed.isoformat() if error.date_signed else None,
                observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())), file=sys.stderr)
        except Exception:
            pass
    return result

SecureCookieSessionInterface.open_session = inspect_session
if __name__ == '__main__':
    sys.argv = ['unittest', 'discover', '-s', 'tests', '-v']
    unittest.main(module=None)
