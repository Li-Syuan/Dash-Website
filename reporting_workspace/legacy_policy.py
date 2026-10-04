"""Exact legacy entry semantics, plus request-local service authorization.

A policy must be selected by server code. Do not populate it from Dash Store,
layout state, a query string, or a browser-provided role/organization claim.
"""
from dataclasses import dataclass
from functools import wraps
import unicodedata


def identity_text(value, maximum=255):
    """Validate an exact claim; editable-field trimming cannot identify actors."""
    if (not isinstance(value, str) or not 0 < len(value) <= maximum
            or not value.strip()
            or any(unicodedata.category(char) in ('Cc', 'Cs') for char in value)):
        raise ValueError('Invalid identity claim.')
    return value


def identity_id(value):
    # Numeric IDs are a supported legacy adapter contract, excluding booleans.
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('Invalid identity claim.')
    return identity_text(str(value))


def _strings(value, name, allow_string=False):
    if value is None:
        return ()
    if allow_string and isinstance(value, str):
        value = (value,)
    if not isinstance(value, (tuple, list)) or any(not isinstance(v, str) or not v for v in value):
        raise ValueError('{} must contain nonempty strings'.format(name))
    return tuple(dict.fromkeys(value))


@dataclass(frozen=True)
class Policy:
    orgcode: tuple = ()
    user_ids: tuple = ()
    user_roles: tuple = ()
    crud_user_ids: tuple = ()
    crud_roles: tuple = ()

    def __post_init__(self):
        for name in ('orgcode', 'user_ids', 'user_roles', 'crud_user_ids', 'crud_roles'):
            object.__setattr__(self, name, _strings(getattr(self, name), name, name == 'orgcode'))


def has_role(user, name):
    if not isinstance(name, str) or not name:
        return False
    attr = name if name.startswith('is_') else 'is_' + name
    value = getattr(user, attr, False)
    # Role claims are server-owned booleans, not methods or arbitrary strings.
    return value is True


def denied(user, orgcode=None, user_ids=None, user_roles=None):
    """True means denied, preserving the legacy empty-policy exception order."""
    if getattr(user, 'is_authenticated', False) is not True:
        return True
    try:
        identity_id(getattr(user, 'id', None))
    except ValueError:
        return True
    orgs = _strings(orgcode, 'orgcode', True)
    ids = _strings(user_ids, 'user_ids')
    roles = _strings(user_roles, 'user_roles')
    if not orgs and not ids and not roles:
        if has_role(user, 'test'):
            return False
        return not has_role(user, 'dev')
    if has_role(user, 'admin'):
        return False
    org = getattr(user, 'orgcode', None)
    org_match = isinstance(org, str) and any(org.startswith(prefix) for prefix in orgs)
    return not (org_match or getattr(user, 'id', None) in ids or any(has_role(user, r) for r in roles))


def scope(user, policy):
    if not isinstance(policy, Policy):
        raise TypeError('Server policy must be a Policy')
    ids = policy.user_ids + policy.crud_user_ids
    roles = policy.user_roles + policy.crud_roles
    if denied(user, policy.orgcode, ids, roles):
        return None
    if (getattr(user, 'id', None) in policy.crud_user_ids or
            any(has_role(user, role) for role in policy.crud_roles)):
        return 'crud'
    return 'read'


def authorize(user, policy, action):
    access = scope(user, policy)
    if action in ('read', 'export', 'template'):
        return access in ('read', 'crud')
    if action in ('crud', 'create', 'update', 'delete', 'upload'):
        return access == 'crud'
    return False


def require(user, policy, action):
    if not authorize(user, policy, action):
        raise PermissionError('Access denied')


def guard(identity_resolver, policy_resolver, action):
    """Callback wrapper: re-resolve identity AND server policy on each invocation.

    A Dash caller may catch PermissionError to return the callback's appropriate
    notification/no_update shape. Never return a generic component from this
    decorator: callbacks have different output arities.
    """
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            user = identity_resolver()
            require(user, policy_resolver(user), action)
            return function(*args, **kwargs)
        return wrapped
    return decorate


def check_user_org_or_id(orgcode=None, user_ids=None, user_roles=None):
    """Drop-in request helper; imports Flask-Login lazily for offline testing."""
    from flask_login import current_user
    return denied(current_user, orgcode, user_ids, user_roles)
