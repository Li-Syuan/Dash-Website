"""Explicit identity/report provider contracts and fail-closed validation.

Production providers are trusted application code, explicitly constructed by the
application owner. ``is_demo = False`` is an intent marker, not a security
certification. Providers must implement real authentication and authorization,
protect credentials, and be reviewed for the deployment. No plugin loader,
import-string configuration, credential cache, or external service is included.
"""
import secrets
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Mapping, MutableMapping

from demo_services import SyntheticReports


_IDENTITY_LIMITS = {'id': 255, 'role': 64, 'org': 128}
_MAX_PASSWORD_LENGTH = 4096


def _bounded_text(value, maximum):
    return (isinstance(value, str) and 0 < len(value) <= maximum and
            bool(value.strip()) and
            not any(unicodedata.category(char) in ('Cc', 'Cs') for char in value))


def validate_identity(value):
    """Return only bounded id/role/org strings, never provider credentials.

    Absence is represented by None outside this function. A malformed successful
    authentication result is a provider error, not an authenticated user. Extra
    provider fields are deliberately discarded; input is not mutated or cached.
    """
    if not isinstance(value, Mapping):
        raise ValueError('identity must be a mapping with id, role, and org')
    result = {}
    for name, maximum in _IDENTITY_LIMITS.items():
        field = value.get(name)
        if not _bounded_text(field, maximum):
            raise ValueError('identity {} must be non-empty text of at most {} characters'.format(name, maximum))
        result[name] = field
    return result


class IdentityProvider(ABC):
    """Return identity dictionaries or None; never retain supplied passwords."""

    is_demo = True

    @abstractmethod
    def authenticate(self, username, password):
        """Return an id/role/org dictionary on success, otherwise None."""
        raise NotImplementedError

    @abstractmethod
    def get_user(self, user_id):
        """Reload current role/org claims for a stored session user ID."""
        raise NotImplementedError


class DemoIdentityProvider(IdentityProvider):
    """Public, mutable test accounts. Never use for production identities."""

    is_demo = True

    def __init__(self, user_db=None):
        if user_db is None:
            user_db = {
                'demo-admin': dict(password='demo-only', role='admin', org='A'),
                'demo-user-a': dict(password='demo-only', role='user', org='A'),
                'demo-user-b': dict(password='demo-only', role='user', org='B'),
            }
        if not isinstance(user_db, MutableMapping):
            raise ValueError('demo user_db must be a mutable mapping')
        # Keep the same mapping so legacy patch.dict(user_db, ...) tests work.
        self.user_db = user_db

    def get_user(self, user_id):
        if not _bounded_text(user_id, _IDENTITY_LIMITS['id']):
            return None
        entry = self.user_db.get(user_id)
        if not isinstance(entry, Mapping):
            return None
        try:
            return validate_identity(dict(id=user_id, role=entry.get('role'), org=entry.get('org')))
        except ValueError:
            return None

    def authenticate(self, username, password):
        if (not _bounded_text(username, _IDENTITY_LIMITS['id']) or
                not isinstance(password, str) or not password or
                len(password) > _MAX_PASSWORD_LENGTH):
            return None
        entry = self.user_db.get(username)
        if not isinstance(entry, Mapping):
            return None
        expected = entry.get('password')
        if not isinstance(expected, str) or len(expected) > _MAX_PASSWORD_LENGTH:
            return None
        try:
            matches = secrets.compare_digest(password.encode('utf-8'), expected.encode('utf-8'))
        except UnicodeError:
            return None
        return self.get_user(username) if matches else None


class ReportProvider(ABC):
    """Server-owned report data; implementations must enforce user access."""

    is_demo = True
    columns = ()

    @abstractmethod
    def rows(self, user):
        raise NotImplementedError

    @abstractmethod
    def export(self, user):
        """Return CSV text for the same server-authorized report rows."""
        raise NotImplementedError


class DemoReportProvider(SyntheticReports, ReportProvider):
    """Adapter preserving the existing synthetic fixture and access checks."""

    is_demo = True


def validate_providers(settings, identity_provider=None, report_provider=None):
    """Return validated providers, defaulting to demo instances only in demo.

    No authentication calls are made during validation. An explicit False marker
    is required in production, including for duck-typed implementations. It does
    not prove provider quality or authorization policy. Provider strings/classes
    are never instantiated or imported here.
    """
    settings.validate()
    production = settings.mode == 'production'
    if identity_provider is None:
        if production:
            raise ValueError('production requires an explicit identity provider')
        identity_provider = DemoIdentityProvider()
    if report_provider is None:
        if production:
            raise ValueError('production requires an explicit report provider')
        report_provider = DemoReportProvider()
    for provider, kind, methods in (
            (identity_provider, 'identity', ('authenticate', 'get_user')),
            (report_provider, 'report', ('rows', 'export'))):
        if isinstance(provider, (str, bytes, type)):
            raise ValueError('{} provider must be an explicit instance'.format(kind))
        if any(not callable(getattr(provider, name, None)) for name in methods):
            raise ValueError('{} provider must implement {}'.format(kind, ', '.join(methods)))
        if production and getattr(provider, 'is_demo', True) is not False:
            raise ValueError('production requires a non-demo {} provider'.format(kind))
    columns = getattr(report_provider, 'columns', None)
    if (not isinstance(columns, (list, tuple)) or not columns or
            any(not _bounded_text(name, 128) for name in columns) or
            len(set(columns)) != len(columns)):
        raise ValueError('report provider columns must be distinct, non-empty strings')
    return identity_provider, report_provider
