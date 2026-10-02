"""Explicit Flask/Dash application factory. No scheduler or network client starts here."""
import json
import hashlib
import secrets
import time

from flask import Flask, g, jsonify, request, session, has_request_context
from flask_login import LoginManager, UserMixin, current_user, login_user, logout_user
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from demo_services import AccessDenied, DemoLocks, DemoScheduler, MailSink, allowed, require
from .config import Settings
from .errors import ProviderUnavailable
from .notifications import NotifyService
from .crud import ReportDefinitions
from .governance import ManagedReports
from .crud import NotFound, StateUnavailable
from .providers import validate_identity, validate_providers



class SessionUser(UserMixin):
    def __init__(self, record):
        record = validate_identity(record)
        self.id, self.role, self.org = record['id'], record['role'], record['org']


class AuthorizedReports:
    """Policy is enforced before every provider invocation, including direct service use."""
    def __init__(self, provider, runtime):
        self.provider, self.runtime = provider, runtime
        self.columns = list(provider.columns)
        if self.columns != ['period', 'department', 'revenue', 'cost', 'profit']:
            raise ValueError('Report adapter must map to the documented report schema')

    def rows(self, user):
        require(user, ['admin'])
        try:
            return self.provider.rows(user)
        except AccessDenied:
            raise
        except Exception:
            self.runtime.audit('provider.error', actor=user['id'], outcome='failed')
            raise ProviderUnavailable() from None

    def export(self, user):
        require(user, ['admin'])
        try:
            content = self.provider.export(user)
            self.runtime.audit('report.export', actor=user['id'])
            return content
        except AccessDenied:
            raise
        except Exception:
            self.runtime.audit('provider.error', actor=user['id'], outcome='failed')
            raise ProviderUnavailable() from None


class Runtime:
    def __init__(self, settings, identities, report_provider, state=None):
        self.settings, self.identities, self.state = settings, identities, state
        self.is_demo = settings.mode == 'demo'
        self.locks, self.scheduler, self.mail = DemoLocks(), DemoScheduler(), MailSink()
        self.reports = AuthorizedReports(report_provider, self)
        self.notify = NotifyService(self.identity)
        self.definitions = ReportDefinitions(state) if state is not None else None
        self.managed = ManagedReports(state, identities, report_provider, is_demo=self.is_demo)

    def identity(self):
        if not current_user.is_authenticated:
            return None
        return dict(id=current_user.id, role=current_user.role, org=current_user.org)

    def audit(self, event, actor=None, outcome='ok'):
        # StateStore's strict schema retains no passwords, headers,
        # report rows, exception messages or arbitrary request bodies are retained.
        if self.state is not None:
            self.state.record_audit(event, actor_id=hashlib.sha256(actor.encode('utf-8')).hexdigest() if actor else None,
                                    outcome={'ok': 'success', 'failed': 'failure'}.get(outcome, outcome),
                                    request_id=getattr(g, 'request_id', None) if has_request_context() else None)

    def authenticate(self, username, password):
        if not isinstance(username, str) or not isinstance(password, str):
            self.audit('login.failed', outcome='denied')
            return False
        try:
            record = self.identities.authenticate(username, password)
            user = SessionUser(record) if record is not None else None
        except Exception:
            self.audit('provider.error', outcome='failed')
            raise ProviderUnavailable() from None
        if user is None:
            self.audit('login.failed', outcome='denied')
            return False
        self.audit('login.succeeded', actor=user.id)
        session.clear()
        login_user(user)
        session.permanent = True
        return True

    def logout(self):
        # Revocation must not depend on an available identity provider. Clear
        # before Flask-Login can resolve current_user, and clear again on error.
        user_id = session.get('_user_id')
        session.clear()
        try:
            logout_user()
        finally:
            session.clear()
        if isinstance(user_id, str):
            self.audit('logout.succeeded', actor=user_id)

    def run_simulation_result(self, user):
        require(user, ['admin'])
        if not self.is_demo:
            raise AccessDenied('Demo simulation is disabled')
        if self.state is None:
            acquired = self.locks.acquire('demo-report', user['id'])
            try:
                ran = self.scheduler.run_once('demo-mail', 'fixture-1', lambda: self.mail.send(
                    'Demo report ready', 'Synthetic fixture only', ['tester@example.invalid'])) if acquired else False
            finally:
                released = self.locks.release('demo-report', user['id']) if acquired else False
            captured = len(self.mail.messages)
        else:
            owner = hashlib.sha256(user['id'].encode('utf-8')).hexdigest()
            lease = self.state.acquire('demo-report', owner, 30)
            acquired, released, ran = bool(lease), False, False
            try:
                if lease:
                    token = self.state.claim_job('demo-mail', 'fixture-1', owner)
                    if token:
                        try:
                            self.mail.send('Demo report ready', 'Synthetic fixture only', ['tester@example.invalid'])
                        except Exception:
                            self.state.finish_job('demo-mail', 'fixture-1', owner, token, success=False, failure_code='execution_failed')
                            raise ProviderUnavailable() from None
                        # A failed completion write must remain running/uncertain;
                        # do not relabel a possibly completed effect as failed.
                        try:
                            finished = self.state.finish_job('demo-mail', 'fixture-1', owner, token, success=True)
                        except Exception:
                            raise ProviderUnavailable() from None
                        if not finished:
                            raise ProviderUnavailable()
                        ran = True
            finally:
                if lease:
                    released = self.state.release('demo-report', owner, lease.token)
            captured = len(self.mail.messages)
        return dict(acquired=acquired, released=released, ran=ran, captured=captured)

    @staticmethod
    def simulation_text(result):
        return ('Lease acquired: {}\nLease released: {}\nFixed job executed: {}\n'
                'Messages captured: {}\nNo external delivery.').format(
                    result['acquired'], result['released'], result['ran'], result['captured'])

    def run_simulation(self, user):
        """Compatibility text view over the structured, authorized result."""
        return self.simulation_text(self.run_simulation_result(user))



def create_app(settings=None, identity_provider=None, report_provider=None, extra_pages=()):
    """Create one independently configured WSGI application and its Dash UI.

    Call from the existing WSGI server's factory entrypoint. Background execution,
    adapter imports, outbound mail and credential provisioning are never implicit.
    """
    settings = settings or Settings.from_env()
    settings.validate()
    identities, report_provider = validate_providers(settings, identity_provider, report_provider)
    state = None
    if settings.state_path:
        from .state import StateStore
        state = StateStore(settings.state_path)
    runtime = Runtime(settings, identities, report_provider, state)
    server = Flask(__name__)
    server.config.update(
        SECRET_KEY=settings.secret_key, SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=settings.session_cookie_secure,
        PERMANENT_SESSION_LIFETIME=settings.session_lifetime,
        MAX_CONTENT_LENGTH=settings.max_content_length, DEMO_MODE=runtime.is_demo,
    )
    server.extensions['workspace'] = runtime
    manager = LoginManager(server)
    manager.login_view = '/login'

    @manager.user_loader
    def load_user(username):
        try:
            record = identities.get_user(username)
            return SessionUser(record) if record is not None else None
        except Exception:
            # A failed identity lookup never grants access or exposes its exception.
            runtime.audit('provider.error', outcome='failed')
            raise ProviderUnavailable() from None

    @server.before_request
    def request_boundary():
        g.request_id, g.started_at = secrets.token_hex(12), time.monotonic()
        origin = request.headers.get('Origin')
        cross_origin = ((origin is not None and origin != request.host_url.rstrip('/'))
                        or request.headers.get('Sec-Fetch-Site') == 'cross-site')
        if request.path.rstrip('/') == '/logout':
            if cross_origin:
                return jsonify(error='Cross-origin logout denied'), 403
            runtime.logout()
        if request.content_length is not None and request.content_length > settings.max_content_length:
            raise RequestEntityTooLarge()
        if request.path.rstrip('/').endswith('/_dash-update-component'):
            if cross_origin:
                return jsonify(error='Cross-origin callback denied'), 403
            if request.content_length is None:
                return jsonify(error='Callback body length is required'), 400
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify(error='Invalid callback request'), 400
            output = payload.get('output')
            if not isinstance(output, str) or not output:
                return jsonify(error='Invalid output'), 400
            registry = server.extensions.get('callback_registry')
            policy = registry.policy_for(output) if registry is not None else None
            if policy is None:
                return jsonify(error='Unregistered callback'), 403
            if output == '.._pages_content.children..._pages_store.data..':
                inputs = payload.get('inputs', [])
                if isinstance(inputs, list) and any(isinstance(item, dict) and item.get('id') == '_pages_location' and item.get('property') == 'pathname' and item.get('value') == '/logout' for item in inputs):
                    runtime.logout()
            user = runtime.identity() if policy.authenticated else None
            if policy.authenticated and user is None:
                return jsonify(error='Login required'), 401
            if not policy.allows(user):
                runtime.audit('access.denied', actor=user['id'] if user else None, outcome='denied')
                return jsonify(error='Forbidden'), 403

    @server.after_request
    def response_boundary(response):
        request_id = getattr(g, 'request_id', '')
        response.headers['X-Request-ID'] = request_id
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Referrer-Policy'] = 'same-origin'
        if not request.path.startswith(('/assets/', '/_dash-component-suites/')):
            response.headers['Cache-Control'] = 'no-store'
        # Log only fixed endpoint names, timing and status. Query/credential/body
        # values and provider exception messages are deliberately excluded.
        server.logger.info(json.dumps(dict(event='http.response', request_id=request_id,
            endpoint=request.endpoint or 'unknown', method=request.method,
            status=response.status_code, elapsed_ms=round((time.monotonic()-getattr(g, 'started_at', time.monotonic()))*1000, 2))))
        return response

    @server.errorhandler(AccessDenied)
    def denied(error):
        return jsonify(error='Forbidden' if runtime.identity() else 'Login required'), 403 if runtime.identity() else 401

    @server.errorhandler(ProviderUnavailable)
    def unavailable(error):
        return jsonify(error='Service temporarily unavailable', request_id=getattr(g, 'request_id', '')), 503

    @server.errorhandler(Exception)
    def unexpected(error):
        if isinstance(error, HTTPException):
            return jsonify(error=error.name, request_id=getattr(g, 'request_id', '')), error.code
        server.logger.error(json.dumps(dict(event='http.failure', request_id=getattr(g, 'request_id', ''), error_type=type(error).__name__)))
        return jsonify(error='Internal service error', request_id=getattr(g, 'request_id', '')), 500

    @server.route('/api/reports/export.csv')
    @server.route('/demo-api/report.csv')
    def export_report():
        user = runtime.identity()
        if not user:
            return jsonify(error='Login required'), 401
        content = runtime.reports.export(user)
        return server.response_class(content, mimetype='text/csv', headers={
            'Content-Disposition': 'attachment; filename=' + ('demo-report.csv' if runtime.is_demo else 'report.csv'), 'Cache-Control': 'no-store'})

    @server.route('/api/managed-reports/<identifier>/export.csv')
    def export_managed_report(identifier):
        user = runtime.identity()
        if not user:
            return jsonify(error='Login required'), 401
        try:
            content = runtime.managed.export(user, identifier)
        except NotFound:
            return jsonify(error='Report not found'), 404
        except StateUnavailable:
            raise ProviderUnavailable() from None
        return server.response_class(content, mimetype='text/csv', headers={
            'Content-Disposition': 'attachment; filename=managed-report.csv', 'Cache-Control': 'no-store'})

    @server.route('/healthz')
    def health():
        return jsonify(status='ok', mode='offline-demo' if runtime.is_demo else 'configured')

    @server.route('/readyz')
    def ready():
        if state is not None:
            try:
                state.list_uncertain_jobs(limit=1)
            except Exception:
                return jsonify(status='unavailable'), 503
        return jsonify(status='ready', storage='sqlite-local' if state else 'process-memory', scheduler='not-started')

    from .web import create_dash_app
    server.extensions['dash_app'] = create_dash_app(server, runtime, extra_pages=extra_pages)
    return server
