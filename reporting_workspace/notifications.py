"""Small, request-local notification adapter for dash-mantine-components 0.12.0.

Page callbacks emit a single event into their own memory ``dcc.Store``. One
authenticated application callback validates those untrusted browser values and
renders them. There is no server queue, history, worker, polling, or transport.
An audience digest prevents accidental cross-user replay; it is not a signature
or an authorization boundary. Page/service/callback authorization stays server
side, and no report data or free-form exception text belongs in an event.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
import secrets
from types import MappingProxyType

import dash_mantine_components as dmc

from .providers import validate_identity


MAX_VISIBLE = 3
MAX_SEEN = 64
MAX_EVENTS = 64
STORE_TYPE = 'workspace-notify'
_ACTION = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
_EVENT_ID = re.compile(r'[0-9a-f]{32}\Z')
_AUDIENCE = re.compile(r'[0-9a-f]{64}\Z')
_REQUEST_ID = re.compile(r'[0-9a-fA-F]{8,64}\Z')
_REQUIRED_FIELDS = frozenset(('event_id', 'code', 'kind', 'audience'))
_EVENT_FIELDS = _REQUIRED_FIELDS | {'request_id'}


@dataclass(frozen=True)
class NotificationSpec:
    kind: str
    title: str
    message: str


CATALOG = MappingProxyType({
    'report.loaded': NotificationSpec(
        'success', 'Report refreshed', 'The latest available report is ready.'),
    'report.export.ready': NotificationSpec(
        'success', 'Export ready', 'Your report download has been prepared.'),
    'report.failed': NotificationSpec(
        'error', 'Report unavailable', 'The report could not be loaded. Please try again.'),
    'report.export.failed': NotificationSpec(
        'error', 'Export unavailable', 'The report export could not be prepared. Please try again.'),
    'simulation.done': NotificationSpec(
        'success', 'Simulation complete', 'The offline simulation finished successfully.'),
    'simulation.skipped': NotificationSpec(
        'info', 'Simulation skipped', 'This simulation run was already claimed; it was not repeated.'),
    'simulation.busy': NotificationSpec(
        'warning', 'Simulation busy', 'Another simulation holds the lease. Please try again later.'),
    'simulation.failed': NotificationSpec(
        'error', 'Simulation unavailable', 'The simulation could not complete. Please try again.'),
    'maintenance.created': NotificationSpec(
        'success', 'Record created', 'The maintenance record was created successfully.'),
    'maintenance.updated': NotificationSpec(
        'success', 'Record updated', 'The maintenance record was updated successfully.'),
    'maintenance.archived': NotificationSpec(
        'success', 'Record archived', 'The maintenance record was archived successfully.'),
    'maintenance.restored': NotificationSpec(
        'success', 'Record restored', 'The maintenance record was restored successfully.'),
    'maintenance.invalid': NotificationSpec(
        'warning', 'Check the record', 'Review the required fields and their allowed values before saving.'),
    'maintenance.conflict': NotificationSpec(
        'warning', 'Record changed', 'Reload the selected record before editing; it changed since you loaded it.'),
    'maintenance.denied': NotificationSpec(
        'error', 'Action unavailable', 'You do not have permission to perform this maintenance action.'),
    'maintenance.failed': NotificationSpec(
        'error', 'Maintenance action failed', 'The maintenance action could not complete. Please try again.'),
    'maintenance.unavailable': NotificationSpec(
        'error', 'Maintenance unavailable', 'Maintenance records are temporarily unavailable. Please try again later.'),
})
_COLORS = {'success': 'teal', 'error': 'red', 'warning': 'yellow', 'info': 'blue'}
_TIMEOUTS = {'success': 5000, 'error': 8000, 'warning': 7000, 'info': 5000}


def _matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _audience(identity):
    try:
        user = validate_identity(identity)
    except ValueError:
        return None
    # Include organization and current role so changed claims invalidate old
    # events. JSON avoids ambiguous concatenations; provider extras are omitted.
    scope = json.dumps([user['id'], user['org'], user['role']], ensure_ascii=True,
                       separators=(',', ':'))
    return hashlib.sha256(('workspace-notify-v1:' + scope).encode('utf-8')).hexdigest()


def notification_store_id(action):
    """Return a fresh, unique-per-callback pattern ID for a memory dcc.Store."""
    if not _matches(_ACTION, action):
        raise ValueError('notification action must be a bounded callback identifier')
    return {'type': STORE_TYPE, 'action': action}


class NotifyService:
    """Stateless event factory; callers can select only a fixed catalog entry.

    Each call reloads the current identity. Missing/malformed identity emits
    nothing. A wrong code/kind/request ID is a programming error, never a reason
    to fall back to free-form text. Do not pass raw exception messages here.
    """

    def __init__(self, identity_getter):
        if not callable(identity_getter):
            raise ValueError('identity_getter must be callable')
        self._identity_getter = identity_getter

    def _event(self, kind, code, request_id):
        if not isinstance(code, str) or code not in CATALOG or CATALOG[code].kind != kind:
            raise ValueError('notification code must match its catalog severity')
        if request_id is not None and not _matches(_REQUEST_ID, request_id):
            raise ValueError('request_id must be 8 to 64 hexadecimal characters')
        audience = _audience(self._identity_getter())
        if audience is None:
            return None
        return {'event_id': secrets.token_hex(16), 'code': code, 'kind': kind,
                'audience': audience,
                'request_id': request_id.lower() if request_id is not None else None}

    def success(self, code, request_id=None):
        return self._event('success', code, request_id)

    def error(self, code, request_id=None):
        return self._event('error', code, request_id)

    def warning(self, code, request_id=None):
        return self._event('warning', code, request_id)

    def info(self, code, request_id=None):
        return self._event('info', code, request_id)


def _valid_event(event, audience):
    if not isinstance(event, Mapping):
        return False
    keys = set(event)
    if not _REQUIRED_FIELDS.issubset(keys) or not keys.issubset(_EVENT_FIELDS):
        return False
    code = event.get('code')
    if not isinstance(code, str) or code not in CATALOG:
        return False
    if (event.get('kind') != CATALOG[code].kind
            or not _matches(_EVENT_ID, event.get('event_id'))
            or not _matches(_AUDIENCE, event.get('audience'))
            or event['audience'] != audience):
        return False
    request_id = event.get('request_id')
    return request_id is None or _matches(_REQUEST_ID, request_id)


def _seen_ids(seen):
    if not isinstance(seen, (list, tuple)):
        return []
    # Read only a bounded tail even if the browser tampers with this Store.
    result = []
    for event_id in seen[-MAX_SEEN:]:
        if _matches(_EVENT_ID, event_id) and event_id not in result:
            result.append(event_id)
    return result


def _notification(event):
    spec = CATALOG[event['code']]
    message = spec.message
    if spec.kind == 'error' and event.get('request_id') is not None:
        message += ' Request ID: {}.'.format(event['request_id'].lower())
    # A stable browser-local toast ID coalesces repeated clicks while that toast
    # is open. The random event_id still acknowledges each individual action.
    return dmc.Notification(
        id='workspace-notification-{}-{}'.format(event['audience'], event['code']),
        action='show', title=spec.title, message=message,
        className='workspace-notification',
        color=_COLORS[spec.kind], autoClose=_TIMEOUTS[spec.kind],
        disallowClose=False, radius='md',
        styles={
            'root': {'backgroundColor': 'var(--surface, #ffffff)',
                     'borderColor': 'var(--line, #dce5f1)',
                     'boxShadow': 'var(--shadow-lg, 0 18px 42px rgba(25, 50, 90, .10))',
                     'maxWidth': '100%'},
            'title': {'color': 'var(--ink, #172b48)', 'fontWeight': 650},
            'description': {'color': 'var(--muted, #596d87)',
                            'overflowWrap': 'anywhere', 'whiteSpace': 'normal'},
            'closeButton': {'minWidth': 32, 'minHeight': 32,
                            '&:focus-visible': {'outline': '2px solid var(--focus, #2559da)',
                                                'outlineOffset': 2}},
        },
    )


def render_events(events, seen, identity):
    """Validate an ALL-Store batch and return (DMC notifications, seen IDs).

    Browser Stores are untrusted. Reject unknown fields (including message,
    title, HTML, styles), unknown codes, mismatched kinds, invalid IDs, and other
    identities. Acknowledge valid but coalesced/capped events to avoid a delayed
    backlog on the next callback. Nothing is retained by this module.
    """
    audience = _audience(identity)
    if audience is None:
        return [], []
    acknowledged = _seen_ids(seen)
    if not isinstance(events, (list, tuple)):
        return [], acknowledged
    known = set(acknowledged)
    batch_codes = set()
    rendered = []
    for event in events[:MAX_EVENTS]:
        if not _valid_event(event, audience) or event['event_id'] in known:
            continue
        known.add(event['event_id'])
        acknowledged.append(event['event_id'])
        if event['code'] in batch_codes:
            continue
        batch_codes.add(event['code'])
        if len(rendered) < MAX_VISIBLE:
            rendered.append(_notification(event))
    return rendered, acknowledged[-MAX_SEEN:]


def wrap_notifications(children):
    """Wrap the stable app root using only DMC 0.12.0-supported properties.

    Mantine's built-in notification has role=alert and a native close button.
    DMC 0.12.0 does not expose closeButtonProps on Notification; the bundled
    Mantine component supports it through theme defaultProps. The provider's
    own width is calc(100% - 2 * spacing.md), capped by containerWidth, so it
    fits narrow viewports. No normalize/global CSS is enabled over Bootstrap.
    """
    return dmc.MantineProvider(
        id='workspace-mantine-provider',
        theme={
            'colorScheme': 'light', 'primaryColor': 'blue',
            'fontFamily': '-apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif',
            'respectReducedMotion': True,
            'components': {'Notification': {'defaultProps': {
                'closeButtonProps': {'aria-label': 'Dismiss notification', 'title': 'Dismiss notification'},
            }}},
        },
        withNormalizeCSS=False, withGlobalStyles=False, withCSSVariables=False,
        children=dmc.NotificationsProvider(
            children=children, position='top-right', containerWidth=420,
            limit=MAX_VISIBLE, autoClose=6000, transitionDuration=150,
            notificationMaxHeight=300, zIndex=1200,
        ),
    )
