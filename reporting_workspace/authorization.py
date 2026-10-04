"""Resolve authoritative identities without caching or accepting caller claims."""
from demo_services import AccessDenied

from .errors import ProviderUnavailable
from .providers import _bounded_text, validate_identity


def lookup_identity(identities, user_id):
    """Load this exact identity ID, returning None only for an absent account.

    A provider returning another account is an invalid provider result, never
    permission to switch the session or service actor to that account.
    """
    if not _bounded_text(user_id, 255):
        raise AccessDenied('A valid identity is required.')
    try:
        record = identities.get_user(user_id)
        if record is None:
            return None
        current = validate_identity(record)
        if current['id'] != user_id:
            raise ValueError('Identity lookup returned a different account.')
        return current
    except Exception:
        raise ProviderUnavailable() from None


def current_identity(identities, supplied):
    """Require caller claims to match a fresh, canonical provider identity.

    Callers retain their own role, tenant and resource/action policy. No role or
    organization is normalized, elevated, or inferred by this boundary.
    """
    try:
        supplied = validate_identity(supplied)
    except ValueError:
        raise AccessDenied('A valid identity is required.') from None
    current = lookup_identity(identities, supplied['id'])
    if current is None or current != supplied:
        raise AccessDenied('Identity claims changed; reload before continuing.')
    return current
