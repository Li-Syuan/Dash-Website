"""Report-definition values, validation and safe domain errors.

No HTTP context, identity lookup, SQL, storage, or side effects live here.
The compatibility exports in crud.py remain available to existing adapters.
"""
from collections.abc import Mapping
import re
import unicodedata
import uuid

from .state import _identifier


_MAX_VERSION = 9223372036854775807
_EDITABLE = frozenset({'name', 'description', 'cadence', 'enabled'})
_CADENCES = frozenset({'manual', 'daily', 'weekly', 'monthly'})
_RECORD_ID = re.compile(r'[0-9a-f]{32}\Z')


class CrudError(Exception):
    """A safe, fixed-message domain failure for the presentation layer."""


class ValidationError(CrudError):
    """Invalid editable fields, optimistic version or pagination (HTTP 400)."""


class AccessDenied(CrudError):
    """Unauthenticated identity or forbidden same-organization write (HTTP 403)."""


class NotFound(CrudError):
    """No record visible to this identity (HTTP 404)."""


class Conflict(CrudError):
    """Stale version or incompatible current lifecycle state (HTTP 409)."""


class StateUnavailable(CrudError):
    """Durable state cannot safely complete the operation (HTTP 503)."""


def _text(value, name, maximum, required=False, multiline=False):
    if not isinstance(value, str):
        raise ValidationError('{} must be text.'.format(name))
    value = value.strip()
    if len(value) > maximum or (required and not value):
        raise ValidationError('{} has an invalid length.'.format(name))
    if any(unicodedata.category(char) in ('Cc', 'Cs')
           and not (multiline and char in '\n\t') for char in value):
        raise ValidationError('{} contains unsupported characters.'.format(name))
    return value


def _payload(payload, create=False):
    if not isinstance(payload, Mapping):
        raise ValidationError('The payload must be an editable-field mapping.')
    if any(key not in _EDITABLE for key in payload):
        raise ValidationError('Unknown or server-owned fields are not editable.')
    if not payload or (create and 'name' not in payload):
        raise ValidationError('A name is required.' if create else 'At least one editable field is required.')
    result = {'description': '', 'cadence': 'manual', 'enabled': True} if create else {}
    for name, value in payload.items():
        if name == 'name':
            value = _text(value, 'name', 120, required=True)
        elif name == 'description':
            value = _text(value, 'description', 1000, multiline=True)
        elif name == 'cadence':
            value = _text(value, 'cadence', 7, required=True)
            if value not in _CADENCES:
                raise ValidationError('cadence must be manual, daily, weekly or monthly.')
        elif not isinstance(value, bool):
            raise ValidationError('enabled must be a boolean.')
        result[name] = value
    return result


def definition_field_errors(name, description, cadence):
    """Fixed UI feedback using the same rules as writes; never authorization.

    Mutations still independently validate the complete payload in the service.
    Exception messages and submitted values are deliberately not returned.
    """
    errors = {}
    for field, value, message in (
            ('name', name, 'Enter a name of 1–120 characters without control characters.'),
            ('description', description, 'Use at most 1000 characters; line breaks and tabs are allowed.'),
            ('cadence', cadence, 'Choose manual, daily, weekly or monthly.')):
        try:
            _payload({field: value})
        except ValidationError:
            errors[field] = message
    return errors


def _version(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_VERSION:
        raise ValidationError('expected_version must be a positive integer.')
    return value


def _record_id(value):
    # Malformed and absent/cross-organization identifiers have the same response.
    if not isinstance(value, str) or _RECORD_ID.fullmatch(value) is None:
        raise NotFound('Report definition not found.')
    return value


def _request_id(value):
    if value is not None:
        try:
            _identifier(value, 'request_id')
        except ValueError:
            raise ValidationError('request_id must be an opaque identifier.') from None
    return value


def _create_key(value):
    if value is None:
        return uuid.uuid4().hex
    if not isinstance(value, str) or _RECORD_ID.fullmatch(value) is None:
        raise ValidationError('request_key must be a 32-character lowercase hex identifier.')
    return value


def _record(row):
    result = dict(row)
    result.pop('create_key')
    result['enabled'] = bool(result['enabled'])
    return result
