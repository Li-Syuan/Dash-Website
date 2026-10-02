"""Demo identity and callback transport authorization."""
import secrets
from flask import Flask, jsonify, request
from flask_login import LoginManager, UserMixin, current_user, login_user
from demo_services import SyntheticReports, AccessDenied, allowed

server = Flask(__name__)
server.config.update(SECRET_KEY=secrets.token_hex(32), SESSION_COOKIE_HTTPONLY=True,
                     SESSION_COOKIE_SAMESITE='Lax', DEMO_MODE=True)
user_db = {
    'demo-admin': dict(password='demo-only', role='admin', org='A'),
    'demo-user-a': dict(password='demo-only', role='user', org='A'),
    'demo-user-b': dict(password='demo-only', role='user', org='B'),
}


class User(UserMixin):
    def __init__(self, username):
        self.id = username
        self.role, self.org = user_db[username]['role'], user_db[username]['org']


login_manager = LoginManager(server)
login_manager.login_view = '/login'


@login_manager.user_loader
def load_user(username):
    return User(username) if username in user_db else None


def identity():
    return dict(id=current_user.id, role=current_user.role, org=current_user.org) if current_user.is_authenticated else None


def authenticate(username, password):
    if not isinstance(username, str) or not isinstance(password, str):
        return False
    entry = user_db.get(username)
    if entry and secrets.compare_digest(password.encode('utf-8'), entry['password'].encode('utf-8')):
        login_user(User(username))
        return True
    return False


@server.before_request
def callback_guard():
    if request.path.rstrip('/').endswith('/_dash-update-component'):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='Invalid callback request'), 400
        output = payload.get('output', '')
        if not isinstance(output, str) or not output:
            return jsonify(error='Invalid output'), 400
        if output in {'..redirectHome.pathname...login-alert.is_open..', 'popover.is_open', '..sidebar.style...page-content.style...side_click.data..'}:
            return None
        if output == '.._pages_content.children..._pages_store.data..':
            return None  # Every page layout enforces authentication and policy.
        if not identity():
            return jsonify(error='Login required'), 401
        if any(x in output for x in ['table.data', 'report-download.data', 'adapter-result.children']):
            if not allowed(identity(), ['admin']):
                return jsonify(error='Forbidden'), 403


reports = SyntheticReports()


@server.route('/demo-api/report.csv')
def export_report():
    if not identity():
        return jsonify(error='Login required'), 401
    try:
        content = reports.export(identity())
    except AccessDenied:
        return jsonify(error='Forbidden'), 403
    return server.response_class(content, mimetype='text/csv', headers={
        'Content-Disposition': 'attachment; filename=demo-report.csv', 'Cache-Control': 'no-store'})


@server.route('/healthz')
def health():
    return jsonify(status='ok', mode='offline-demo')
