"""One policy for report-definition transport, services and editor affordances.

Only trusted server identities may construct a principal. A principal is an
immutable snapshot, not a browser credential or a substitute for authentication.
No current user, result cache or request state is retained at module scope.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib

from .definition_domain import AccessDenied, Conflict, NotFound, _MAX_VERSION
from .registry import AccessPolicy


DEFINITION_ACCESS = AccessPolicy.require(roles=('admin', 'user'))


@dataclass(frozen=True)
class DefinitionPrincipal:
    id: str
    org: str
    role: str
    actor_id: str = field(init=False)

    def __post_init__(self):
        if not DEFINITION_ACCESS.allows({'id': self.id, 'org': self.org, 'role': self.role}):
            raise AccessDenied('A valid identity with a supported role is required.')
        object.__setattr__(self, 'actor_id', hashlib.sha256(self.id.encode('utf-8')).hexdigest())

    @classmethod
    def from_user(cls, user):
        if isinstance(user, cls):
            return user
        if not isinstance(user, Mapping):
            raise AccessDenied('A valid identity is required.')
        # Copy each authoritative claim once. Do not trim identity strings: that
        # would conflate distinct organizations or owners.
        return cls(id=user.get('id'), org=user.get('org'), role=user.get('role'))

    def can_change(self, record):
        """Owner/admin rule, including organization; lifecycle is separate."""
        return bool(record is not None and record['org'] == self.org and (
            self.role == 'admin' or record['owner_id'] == self.actor_id))

    def require_change(self, record, expected_version, deleted):
        # Check tenant before ownership/version to keep invisible IDs opaque.
        if record['org'] != self.org:
            raise NotFound('Report definition not found.')
        if not self.can_change(record):
            raise AccessDenied('Only the owner or an organization administrator may change this definition.')
        if record['version'] != expected_version:
            raise Conflict('The definition changed. Reload it and try again.')
        if (record['deleted_at'] is not None) != deleted:
            raise Conflict('The definition has a different deletion state. Reload it and try again.')
        if record['version'] == _MAX_VERSION:
            raise Conflict('The definition version is exhausted.')
