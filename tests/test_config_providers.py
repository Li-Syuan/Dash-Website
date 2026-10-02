"""Configuration and provider contracts without application integration."""
import ast
import csv
import io
import os
from dataclasses import FrozenInstanceError
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from demo_services import AccessDenied, SyntheticReports
from reporting_workspace.config import Settings
from reporting_workspace.providers import (
    DemoIdentityProvider, DemoReportProvider, IdentityProvider, ReportProvider,
    validate_identity, validate_providers,
)


PRODUCTION_KEY = 'unit-test-explicit-key-with-32-characters'


class SettingsTests(unittest.TestCase):
    def production(self, **changes):
        values = dict(mode='production', secret_key=PRODUCTION_KEY,
                      state_path='/tmp/reporting-workspace-tests.sqlite3')
        values.update(changes)
        return Settings(**values)

    def test_demo_defaults_are_immutable_and_secret_is_not_repr(self):
        settings, another = Settings(), Settings.from_env({})
        self.assertEqual(settings.mode, 'demo')
        self.assertEqual(settings.max_content_length, 1024 * 1024)
        self.assertEqual(settings.session_lifetime, timedelta(hours=1))
        self.assertIs(settings.session_cookie_secure, False)
        self.assertIsNone(settings.state_path)
        self.assertGreaterEqual(len(settings.secret_key), 32)
        self.assertNotEqual(settings.secret_key, another.secret_key)
        self.assertNotIn(settings.secret_key, repr(settings))
        self.assertNotIn('secret_key=', repr(settings))
        self.assertIs(settings.validate(), settings)
        with self.assertRaises(FrozenInstanceError):
            settings.mode = 'production'

    def test_explicit_empty_environment_does_not_read_process_environment(self):
        with patch.dict(os.environ, {'REPORTING_MODE': 'production'}, clear=True):
            self.assertEqual(Settings.from_env({}).mode, 'demo')
            with self.assertRaises(ValueError):
                Settings.from_env()

    def test_production_defaults_and_environment(self):
        settings = Settings.from_env({
            'REPORTING_MODE': 'production',
            'REPORTING_SECRET_KEY': PRODUCTION_KEY,
            'REPORTING_STATE_PATH': '/var/lib/reporting/state.sqlite3',
            'REPORTING_MAX_CONTENT_LENGTH': '2048',
            'REPORTING_SESSION_LIFETIME_SECONDS': '600',
            'REPORTING_SESSION_COOKIE_SECURE': 'TRUE',
            'REPORTING_IDENTITY_PROVIDER': 'does.not.get.imported',
        })
        self.assertEqual(settings.mode, 'production')
        self.assertIs(settings.session_cookie_secure, True)
        self.assertIs(self.production().session_cookie_secure, True)
        self.assertEqual(settings.state_path, '/var/lib/reporting/state.sqlite3')
        self.assertEqual(settings.max_content_length, 2048)
        self.assertEqual(settings.session_lifetime, timedelta(minutes=10))
        self.assertNotIn(PRODUCTION_KEY, repr(settings))

    def test_production_missing_short_or_public_secrets_are_rejected(self):
        for secret in [None, '', ' ' * 40, 'short', 'x' * 31, 123,
                       'demo-secret-key-do-not-use-in-production',
                       'change-this-secret-key-before-production']:
            with self.subTest(secret_type=type(secret).__name__):
                with self.assertRaises(ValueError) as caught:
                    self.production(secret_key=secret)
                if isinstance(secret, str) and len(secret) >= 32:
                    self.assertNotIn(secret, str(caught.exception))
        with patch('reporting_workspace.config.secrets.token_hex') as random_key:
            with self.assertRaises(ValueError):
                self.production(secret_key=None)
            random_key.assert_not_called()
        self.assertEqual(self.production(secret_key='x' * 32).secret_key, 'x' * 32)

    def test_production_state_and_cookie_requirements(self):
        for path in [None, '', 'state.sqlite3', ':memory:',
                     'file:/tmp/state.sqlite3', '//host/share/state.db',
                     '/tmp/', '/', '/tmp/..', '/tmp/\x00state.db', 12]:
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    self.production(state_path=path)
        for secure in [False, 'true', 1, 0]:
            with self.subTest(secure=secure):
                with self.assertRaises(ValueError):
                    self.production(session_cookie_secure=secure)

    def test_settings_reject_invalid_values_without_echoing_secrets(self):
        for mode in ['', 'prod', 'development', 'DEMO', None, 1]:
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    Settings(mode=mode)
        for length in [False, True, None, 0, -1, 1.5, '100']:
            with self.subTest(length=length):
                with self.assertRaises(ValueError):
                    Settings(max_content_length=length)
        for duration in [None, 3600, 0, timedelta(0), timedelta(seconds=-1)]:
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError):
                    Settings(session_lifetime=duration)
        with self.assertRaises(ValueError):
            Settings(secret_key='secret\ud800')
        with self.assertRaises(ValueError):
            Settings(state_path='/tmp/\ud800.sqlite3')

    def test_environment_rejects_malformed_types_booleans_and_numbers(self):
        for key, values in {
            'REPORTING_MODE': [1],
            'REPORTING_SECRET_KEY': [False],
            'REPORTING_STATE_PATH': [False],
            'REPORTING_SESSION_COOKIE_SECURE': ['yes', ' true ', '', 1],
            'REPORTING_MAX_CONTENT_LENGTH': ['0', '-1', '1.5', '', ' 1', '+1', '\u0661', 1],
            'REPORTING_SESSION_LIFETIME_SECONDS': ['0', '-1', 'NaN', '1.2', '9' * 30],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError):
                        Settings.from_env({key: value})
        for invalid in [[], 'environment', 1]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    Settings.from_env(invalid)
        for value, expected in [('true', True), ('false', False), ('1', True), ('0', False)]:
            self.assertIs(Settings.from_env({'REPORTING_SESSION_COOKIE_SECURE': value}).session_cookie_secure, expected)


class IdentityProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = DemoIdentityProvider()

    def test_demo_accounts_match_legacy_contract_and_are_credential_free(self):
        expected = {'demo-admin': ('admin', 'A'),
                    'demo-user-a': ('user', 'A'), 'demo-user-b': ('user', 'B')}
        self.assertEqual(set(self.provider.user_db), set(expected))
        for username, (role, org) in expected.items():
            with self.subTest(username=username):
                identity = dict(id=username, role=role, org=org)
                self.assertEqual(self.provider.authenticate(username, 'demo-only'), identity)
                self.assertEqual(self.provider.get_user(username), identity)
                self.assertEqual(self.provider.user_db[username]['password'], 'demo-only')
        self.assertEqual(set(vars(self.provider)), {'user_db'})
        self.assertNotIn('password', repr(self.provider))

    def test_mutable_database_reflects_claim_updates_and_deleted_users(self):
        db = {'person': dict(password='pass', role='admin', org='A')}
        provider = DemoIdentityProvider(db)
        self.assertIs(provider.user_db, db)
        db['person']['role'] = 'user'
        self.assertEqual(provider.get_user('person')['role'], 'user')
        result = provider.get_user('person')
        result['org'] = 'B'
        self.assertEqual(db['person']['org'], 'A')
        del db['person']
        self.assertIsNone(provider.get_user('person'))
        self.assertIsNone(provider.authenticate('person', 'pass'))
        self.assertEqual(DemoIdentityProvider({}).user_db, {})
        self.assertIsNot(self.provider.user_db, DemoIdentityProvider().user_db)
        with self.assertRaises(ValueError):
            DemoIdentityProvider([])

    def test_types_unicode_and_oversized_credentials_fail_safely(self):
        for value in [None, [], {}, 1, True, b'demo-admin', '', 'x' * 5000, '\ud800']:
            with self.subTest(value_type=type(value).__name__):
                self.assertIsNone(self.provider.get_user(value))
                self.assertIsNone(self.provider.authenticate(value, 'demo-only'))
                self.assertIsNone(self.provider.authenticate('demo-admin', value))
        self.assertIsNone(self.provider.authenticate('demo-admin', '\u00e9'))
        self.assertIsNone(self.provider.authenticate('unknown-user', 'demo-only'))
        self.assertIsNone(self.provider.authenticate('demo-admin', 'wrong'))
        provider = DemoIdentityProvider({'\u7528\u6237': dict(password='\u5bc6\u78bc\U0001f512', role='user', org='\u5317')})
        self.assertEqual(provider.authenticate('\u7528\u6237', '\u5bc6\u78bc\U0001f512'),
                         dict(id='\u7528\u6237', role='user', org='\u5317'))

    def test_bad_mutable_records_are_rejected(self):
        for value in [None, [], 1, dict(password=None, role='admin', org='A'),
                      dict(password='demo-only', role=[], org='A'),
                      dict(password='demo-only', role='admin', org=None),
                      dict(password='\ud800', role='admin', org='A')]:
            with self.subTest(value=value):
                self.provider.user_db['invalid'] = value
                self.assertIsNone(self.provider.authenticate('invalid', 'demo-only'))

    def test_identity_validation_copies_only_bounded_identity_claims(self):
        source = dict(id='account', role='analyst', org='North', password='do-not-retain', token='not-for-session')
        cleaned = validate_identity(source)
        self.assertEqual(cleaned, dict(id='account', role='analyst', org='North'))
        self.assertNotIn('password', cleaned)
        self.assertNotIn('token', cleaned)
        self.assertIn('password', source)
        for name, limit in [('id', 255), ('role', 64), ('org', 128)]:
            for value in [None, '', '  ', [], 3, 'x' * (limit + 1), 'bad\x00', 'bad\n', '\ud800']:
                candidate = dict(id='account', role='analyst', org='North')
                candidate[name] = value
                with self.subTest(name=name, value_type=type(value).__name__):
                    with self.assertRaises(ValueError):
                        validate_identity(candidate)
        for value in [None, [], 'account', {}, dict(id='account')]:
            with self.assertRaises(ValueError):
                validate_identity(value)


class ProviderValidationTests(unittest.TestCase):
    def setUp(self):
        self.production = Settings(mode='production', secret_key=PRODUCTION_KEY,
                                   state_path='/tmp/provider-test.sqlite3')
        self.identity = SimpleNamespace(is_demo=False, authenticate=lambda u, p: None,
                                        get_user=lambda user_id: None)
        self.report = SimpleNamespace(is_demo=False, columns=['total'],
                                      rows=lambda user: [], export=lambda user: 'total\r\n')

    def test_demo_defaults_and_existing_synthetic_adapter(self):
        identity, report = validate_providers(Settings())
        self.assertIsInstance(identity, IdentityProvider)
        self.assertIsInstance(report, ReportProvider)
        self.assertTrue(identity.is_demo)
        self.assertTrue(report.is_demo)
        admin = identity.authenticate('demo-admin', 'demo-only')
        self.assertEqual(report.rows(admin), SyntheticReports().rows(admin))
        self.assertEqual(report.export(admin), SyntheticReports().export(admin))
        self.assertEqual(len(list(csv.DictReader(io.StringIO(report.export(admin))))), 12)
        for user in [None, dict(id='a', role='user', org='A')]:
            with self.assertRaises(AccessDenied):
                report.rows(user)
            with self.assertRaises(AccessDenied):
                report.export(user)
        existing = SyntheticReports()
        self.assertIs(validate_providers(Settings(), identity, existing)[1], existing)

    def test_production_requires_both_explicit_non_demo_providers(self):
        for identity, report in [(None, None), (self.identity, None), (None, self.report),
                                 (DemoIdentityProvider(), self.report),
                                 (self.identity, DemoReportProvider()),
                                 (self.identity, SyntheticReports())]:
            with self.subTest(identity=type(identity).__name__, report=type(report).__name__):
                with self.assertRaises(ValueError):
                    validate_providers(self.production, identity, report)
        result = validate_providers(self.production, self.identity, self.report)
        self.assertIs(result[0], self.identity)
        self.assertIs(result[1], self.report)

    def test_false_marker_must_be_explicit_boolean_and_missing_methods_fail(self):
        for marker in [None, True, 0, '', 'false']:
            self.identity.is_demo = marker
            with self.subTest(marker=marker):
                with self.assertRaises(ValueError):
                    validate_providers(self.production, self.identity, self.report)
        del self.identity.is_demo
        with self.assertRaises(ValueError):
            validate_providers(self.production, self.identity, self.report)
        self.identity.is_demo = False
        for provider, method in [(self.identity, 'authenticate'), (self.identity, 'get_user'),
                                 (self.report, 'rows'), (self.report, 'export')]:
            original = getattr(provider, method)
            setattr(provider, method, None)
            with self.subTest(method=method):
                with self.assertRaises(ValueError):
                    validate_providers(self.production, self.identity, self.report)
            setattr(provider, method, original)

    def test_strings_classes_invalid_columns_and_abstract_interfaces_rejected(self):
        for provider in ['package.Class', b'package.Class', DemoIdentityProvider, object()]:
            with self.assertRaises(ValueError):
                validate_providers(Settings(), provider, self.report)
        for columns in [None, [], (), 'total', [''], ['a', 'a'], [None], ['a\n'], ['x' * 129]]:
            self.report.columns = columns
            with self.subTest(columns=columns):
                with self.assertRaises(ValueError):
                    validate_providers(Settings(), self.identity, self.report)
        with self.assertRaises(TypeError):
            IdentityProvider()
        with self.assertRaises(TypeError):
            ReportProvider()

    def test_provider_validation_does_not_authenticate_or_load_users(self):
        def unexpected(*args):
            raise AssertionError('provider methods must not execute at startup validation')
        self.identity.authenticate = self.identity.get_user = unexpected
        self.report.rows = self.report.export = unexpected
        self.assertEqual(validate_providers(self.production, self.identity, self.report),
                         (self.identity, self.report))

    def test_python38_syntax(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ['reporting_workspace/config.py', 'reporting_workspace/providers.py',
                         'tests/test_config_providers.py']:
            with self.subTest(filename=filename):
                ast.parse((root / filename).read_text(encoding='utf-8'), feature_version=(3, 8))


if __name__ == '__main__':
    unittest.main()
