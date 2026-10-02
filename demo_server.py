"""Compatibility exports for the runnable demo; factory users import application directly."""
from reporting_workspace.application import create_app, SessionUser

server = create_app()
runtime = server.extensions['workspace']
user_db = getattr(runtime.identities, 'user_db', {})
identity, authenticate, reports = runtime.identity, runtime.authenticate, runtime.reports
login_manager = server.login_manager


def User(username):
    record = runtime.identities.get_user(username)
    return SessionUser(record) if record is not None else None


def load_user(username):
    return User(username)
