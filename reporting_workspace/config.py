"""Validated, dependency-free configuration for the reporting workspace.

Only the documented REPORTING_* variables are read. Provider instances are
passed by the application owner, never loaded from environment import strings.
"""
import os
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Mapping, Optional


DEFAULT_MAX_CONTENT_LENGTH = 1024 * 1024
DEFAULT_SESSION_LIFETIME = timedelta(hours=1)
# These are examples/public defaults, never acceptable deployment secrets.
_KNOWN_DEMO_SECRETS = frozenset({
    'demo-only', 'demo-secret', 'demo-secret-key', 'development', 'dev',
    'secret', 'secret-key', 'changeme', 'change-me', 'your-secret-key',
    'replace-me', '123456790', 'demo-secret-key-do-not-use-in-production',
    'change-this-secret-key-before-production',
    'replace-this-with-a-secure-secret-key',
})


def _environment_text(environ, key, default=None):
    value = environ.get(key, default)
    if value is not None and not isinstance(value, str):
        raise ValueError('{} must be text'.format(key))
    return value


def _environment_integer(environ, key, default):
    raw = _environment_text(environ, key)
    if raw is None:
        return default
    # int() also accepts whitespace, signs, and Unicode numerals; keep this
    # deployment contract unambiguous and reject accidental blank values.
    if not raw or not raw.isascii() or not raw.isdecimal():
        raise ValueError('{} must be a positive integer'.format(key))
    return int(raw)


def _environment_boolean(environ, key):
    raw = _environment_text(environ, key)
    if raw is None:
        return None
    if raw.lower() in ('true', '1'):
        return True
    if raw.lower() in ('false', '0'):
        return False
    raise ValueError('{} must be true, false, 1, or 0'.format(key))


@dataclass(frozen=True)
class Settings:
    """Immutable settings; invalid explicit settings fail at construction.

    A fresh demo instance gets an ephemeral random session key. Production
    requires an explicit key of at least 32 characters and an absolute local
    SQLite filename. Production always requires secure session cookies, so TLS
    termination must be configured by the deployment owner. Validating a path
    does not verify filesystem permissions, durability, or shared-host safety.
    """

    mode: str = 'demo'
    secret_key: Optional[str] = field(default=None, repr=False)
    state_path: Optional[str] = None
    session_cookie_secure: Optional[bool] = None
    max_content_length: int = DEFAULT_MAX_CONTENT_LENGTH
    session_lifetime: timedelta = DEFAULT_SESSION_LIFETIME

    def __post_init__(self):
        if self.mode not in ('demo', 'production'):
            raise ValueError('mode must be demo or production')
        if self.secret_key is None and self.mode == 'demo':
            object.__setattr__(self, 'secret_key', secrets.token_hex(32))
        if self.session_cookie_secure is None:
            object.__setattr__(self, 'session_cookie_secure', self.mode == 'production')
        self.validate()

    @classmethod
    def from_env(cls, environ=None):
        # An explicitly supplied empty mapping must not inherit host secrets.
        source = os.environ if environ is None else environ
        if not isinstance(source, Mapping):
            raise ValueError('environ must be a mapping')
        lifetime = _environment_integer(
            source, 'REPORTING_SESSION_LIFETIME_SECONDS',
            int(DEFAULT_SESSION_LIFETIME.total_seconds()))
        try:
            session_lifetime = timedelta(seconds=lifetime)
        except OverflowError:
            raise ValueError('REPORTING_SESSION_LIFETIME_SECONDS is too large') from None
        return cls(
            mode=_environment_text(source, 'REPORTING_MODE', 'demo'),
            secret_key=_environment_text(source, 'REPORTING_SECRET_KEY'),
            state_path=_environment_text(source, 'REPORTING_STATE_PATH'),
            session_cookie_secure=_environment_boolean(
                source, 'REPORTING_SESSION_COOKIE_SECURE'),
            max_content_length=_environment_integer(
                source, 'REPORTING_MAX_CONTENT_LENGTH', DEFAULT_MAX_CONTENT_LENGTH),
            session_lifetime=session_lifetime,
        )

    def validate(self):
        """Check invariants without exposing secret values; return this object."""
        if self.mode not in ('demo', 'production'):
            raise ValueError('mode must be demo or production')
        if not isinstance(self.secret_key, str) or not self.secret_key.strip():
            raise ValueError('secret_key must be non-empty text')
        try:
            self.secret_key.encode('utf-8')
        except UnicodeError:
            raise ValueError('secret_key must be valid UTF-8 text') from None
        if self.mode == 'production':
            if (len(self.secret_key) < 32 or
                    self.secret_key.strip().lower() in _KNOWN_DEMO_SECRETS):
                raise ValueError('production requires an explicit non-demo secret_key of at least 32 characters')
            if self.state_path is None:
                raise ValueError('production requires an absolute local state_path')
        if self.state_path is not None:
            if (not isinstance(self.state_path, str) or not self.state_path or
                    '\x00' in self.state_path or not os.path.isabs(self.state_path) or
                    self.state_path.startswith('//') or
                    os.path.basename(self.state_path) in ('', '.', '..')):
                raise ValueError('state_path must be an absolute local SQLite filename')
            try:
                self.state_path.encode('utf-8')
            except UnicodeError:
                raise ValueError('state_path must be valid UTF-8 text') from None
        if type(self.session_cookie_secure) is not bool:
            raise ValueError('session_cookie_secure must be a boolean')
        if self.mode == 'production' and not self.session_cookie_secure:
            raise ValueError('production requires secure session cookies')
        if type(self.max_content_length) is not int or self.max_content_length <= 0:
            raise ValueError('max_content_length must be a positive integer')
        if (not isinstance(self.session_lifetime, timedelta) or
                self.session_lifetime <= timedelta(0)):
            raise ValueError('session_lifetime must be a positive timedelta')
        return self
