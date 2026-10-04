"""Report-definition application service and backwards-compatible public API.

Callbacks bind a trusted current identity once for their request. Domain rules
and safe errors live in definition_domain; the single authorization policy is
in definition_policy; only definition_repository issues SQL. No identity, row
cache or connection is retained on the shared service instance.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import time
import uuid

from demo_services import AccessDenied as IdentityAccessDenied
from .authorization import current_identity
from .errors import ProviderUnavailable

# Compatibility exports: existing administration/operations integrations import
# these symbols. They continue to denote the same exception and value contracts.
from .definition_domain import (
    AccessDenied, Conflict, CrudError, NotFound, StateUnavailable, ValidationError,
    _CADENCES, _EDITABLE, _MAX_VERSION, _RECORD_ID, _create_key, _payload,
    _record, _record_id, _request_id, _text, _version, definition_field_errors,
)
from .definition_policy import DefinitionPrincipal
from .definition_repository import ReportDefinitionRepository


def _identity(user):
    """Compatibility helper returning the historically documented scope tuple."""
    principal = DefinitionPrincipal.from_user(user)
    return principal.org, principal.actor_id, principal.role


class ReportDefinitions:
    """Stateless metadata application service over local transactional storage.

    Public mapping-based methods remain available for trusted server callers.
    Web callbacks use bind(), which fixes the actor and correlation ID and never
    accepts either in a browser payload. Same-organization users share reads;
    only the owner and that organization's administrators can change a record.
    Runtime supplies identities to recheck current claims on every operation.
    Omitting identities retains the trusted offline-caller contract; mappings
    and principals are not authentication credentials.
    """

    def __init__(self, store, identities=None):
        if identities is not None and not callable(getattr(identities, 'get_user', None)):
            raise TypeError('identities must provide get_user or be None')
        self.repository = ReportDefinitionRepository(store)
        self.store = store
        self.identities = identities

    def _principal(self, user):
        principal = DefinitionPrincipal.from_user(user)
        if self.identities is not None:
            try:
                current_identity(self.identities, dict(id=principal.id, org=principal.org,
                                                       role=principal.role))
            except IdentityAccessDenied:
                raise AccessDenied('A current valid identity is required.') from None
            except ProviderUnavailable:
                raise StateUnavailable('Current identity is unavailable.') from None
        return principal

    @contextmanager
    def _transaction(self, principal, write=False):
        with self.repository.transaction(principal, write=write) as repository:
            # A writer may have waited for SQLite. Recheck after the wait, and
            # again before the transaction releases data or commits mutations.
            self._principal(principal)
            yield repository
            self._principal(principal)

    def bind(self, user, request_id=None, is_active=None):
        return DefinitionRequest(self, self._principal(user),
                                 _request_id(request_id), is_active)

    def list(self, user, q='', limit=20, offset=0, include_deleted=False):
        principal = self._principal(user)
        q = _text(q, 'q', 120)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValidationError('limit must be an integer between 1 and 100.')
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 100000:
            raise ValidationError('offset must be an integer between 0 and 100000.')
        if not isinstance(include_deleted, bool):
            raise ValidationError('include_deleted must be a boolean.')
        with self._transaction(principal) as repository:
            return repository.list(q, limit, offset, include_deleted)

    def get(self, user, identifier):
        principal = self._principal(user)
        identifier = _record_id(identifier)
        with self._transaction(principal) as repository:
            return repository.get(identifier)

    def create(self, user, payload, *, request_id=None, request_key=None):
        """Create once per owner/org/key; omit the key for an independent create."""
        principal = self._principal(user)
        fields, request_id = _payload(payload, create=True), _request_id(request_id)
        create_key = _create_key(request_key)
        with self._transaction(principal, write=True) as repository:
            previous = repository.created_with(create_key)
            if previous is not None:
                if (previous['version'] != 1 or previous['deleted_at'] is not None
                        or any(previous[name] != fields[name] for name in _EDITABLE)):
                    raise Conflict('This create request was already used. Reload the definition or start a new draft.')
                return previous
            return repository.insert(uuid.uuid4().hex, create_key, fields, time.time(), request_id)

    def update(self, user, identifier, expected_version, payload, *, request_id=None):
        principal = self._principal(user)
        identifier, expected_version = _record_id(identifier), _version(expected_version)
        fields, request_id = _payload(payload), _request_id(request_id)
        with self._transaction(principal, write=True) as repository:
            return repository.update(identifier, expected_version, fields, time.time(), request_id)

    def _set_deleted(self, user, identifier, expected_version, deleted, request_id):
        principal = self._principal(user)
        identifier, expected_version = _record_id(identifier), _version(expected_version)
        request_id = _request_id(request_id)
        with self._transaction(principal, write=True) as repository:
            return repository.set_deleted(identifier, expected_version, deleted, time.time(), request_id)

    def soft_delete(self, user, identifier, expected_version, *, request_id=None):
        return self._set_deleted(user, identifier, expected_version, True, request_id)

    def restore(self, user, identifier, expected_version, *, request_id=None):
        return self._set_deleted(user, identifier, expected_version, False, request_id)


@dataclass(frozen=True)
class DefinitionRequest:
    """One operation/request scope, never an application-level current user.

    is_active is a trusted lifecycle guard supplied by Runtime for HTTP calls.
    Direct/background callers may bind an immutable identity without a Flask
    dependency, but must re-resolve it before each new unit of work. These objects
    are not credentials; do not cache them or construct them from browser claims.
    """
    _service: ReportDefinitions = field(repr=False)
    principal: DefinitionPrincipal
    request_id: str = None
    _is_active: object = field(default=None, repr=False)

    def __post_init__(self):
        if not isinstance(self.principal, DefinitionPrincipal):
            raise TypeError('A validated definition principal is required')
        _request_id(self.request_id)
        if self._is_active is not None and not callable(self._is_active):
            raise TypeError('is_active must be callable or None')

    def _check(self):
        if self._is_active is not None:
            try:
                active = self._is_active() is True
            except Exception:
                active = False
            if not active:
                raise AccessDenied('This definition request is no longer active.')
        self._service._principal(self.principal)

    def identity(self):
        """Fresh presentation copy of this scope, never a mutable shared claim."""
        self._check()
        return {'id': self.principal.id, 'org': self.principal.org, 'role': self.principal.role}

    def can_change(self, record):
        self._check()
        return self.principal.can_change(record)

    def list(self, q='', limit=20, offset=0, include_deleted=False):
        self._check()
        return self._service.list(self.principal, q=q, limit=limit, offset=offset,
                                  include_deleted=include_deleted)

    def get(self, identifier):
        self._check()
        return self._service.get(self.principal, identifier)

    def create(self, payload, *, request_key=None):
        self._check()
        return self._service.create(self.principal, payload, request_id=self.request_id,
                                    request_key=request_key)

    def update(self, identifier, expected_version, payload):
        self._check()
        return self._service.update(self.principal, identifier, expected_version, payload,
                                    request_id=self.request_id)

    def soft_delete(self, identifier, expected_version):
        self._check()
        return self._service.soft_delete(self.principal, identifier, expected_version,
                                         request_id=self.request_id)

    def restore(self, identifier, expected_version):
        self._check()
        return self._service.restore(self.principal, identifier, expected_version,
                                     request_id=self.request_id)
