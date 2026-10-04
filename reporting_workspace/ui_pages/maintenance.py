"""Bounded, server-authorized report-definition maintenance.

Browser rows and Stores are only references, never authority. Every selection
loads the record again and every mutation delegates ownership, organization and
optimistic-version checks to the CRUD service. Cadence is descriptive metadata;
this page does not schedule or execute reports.
"""

import re
import secrets

from dash import Input, Output, State, ctx, dash_table, dcc, html, no_update
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc

from ..crud import (AccessDenied, Conflict, NotFound, StateUnavailable, ValidationError,
                    definition_field_errors)
from ..notifications import NotifyService, notification_store_id
from ..registry import PageSpec
from ..definition_policy import DEFINITION_ACCESS, DefinitionPrincipal
from .shared import heading, request_id


POLICY = DEFINITION_ACCESS
PAGE_SIZE = 20
MAX_PAGE = 5000
_CADENCES = ('manual', 'daily', 'weekly', 'monthly')
_EDITOR_OUTPUTS = 17
_DRAFT_KEY = re.compile(r'[0-9a-f]{32}\Z')


def _editable(record, user):
    """Presentation uses the exact owner/tenant rule enforced by the repository."""
    try:
        return DefinitionPrincipal.from_user(user).can_change(record)
    except AccessDenied:
        return False


def _notice(runtime, error, service=None):
    # A successfully bound callback uses the same immutable audience and request
    # ID for result data, permissions, audit and notification. Pre-bind failures
    # still use the application's current request identity.
    if service is not None:
        try:
            audience = service.identity()
        except (AccessDenied, StateUnavailable):
            # A revoked/unavailable scope cannot receive even a stale success
            # event. Keep the failed callback safe without querying that scope
            # again while constructing its error notification.
            return None
        notify = NotifyService(lambda: audience)
    else:
        notify = runtime.notify
    correlation = service.request_id if service is not None else request_id()
    # Never interpolate the exception, record, form data or IDs into a toast.
    if isinstance(error, ValidationError):
        return notify.warning('maintenance.invalid', request_id=correlation)
    if isinstance(error, Conflict):
        return notify.warning('maintenance.conflict', request_id=correlation)
    if isinstance(error, (AccessDenied, NotFound)):
        return notify.error('maintenance.denied', request_id=correlation)
    if isinstance(error, StateUnavailable):
        return notify.error('maintenance.unavailable', request_id=correlation)
    return notify.error('maintenance.failed', request_id=correlation)


def _service(runtime):
    if runtime.definitions is None:
        raise StateUnavailable()
    return runtime.definition_request()


def _reference(value):
    if (not isinstance(value, dict) or set(value) != {'id', 'version'}
            or not isinstance(value['id'], str) or type(value['version']) is not int
            or value['version'] < 1):
        raise ValidationError()
    return value['id'], value['version']


def _draft_key(value):
    if not isinstance(value, str) or _DRAFT_KEY.fullmatch(value) is None:
        raise ValidationError()
    return value


def _editor(record, user, available=True):
    if record is None:
        disabled = not available
        return (None, '', '', 'manual', True, 'New report definition',
                'Not saved yet',
                'Save creates a definition in your organization with you as its owner.',
                disabled, disabled, disabled, disabled, disabled, True, True, True, secrets.token_hex(16))
    can_edit = _editable(record, user)
    archived = record['deleted_at'] is not None
    disabled = not can_edit or archived
    hint = ('Restore this archived definition before editing.' if archived and can_edit
            else 'Read only. You can edit definitions you own; administrators can edit their organization.'
            if not can_edit else
            'Save checks the displayed version. If another editor saves first, reload before trying again.')
    return ({'id': record['id'], 'version': record['version']}, record['name'],
            record['description'], record['cadence'], record['enabled'],
            'Selected report definition',
            'Version {} · {} · {}'.format(record['version'], 'Archived' if archived else 'Active',
                                           'Editable by you' if can_edit else 'Read only'),
            hint, disabled, disabled, disabled, disabled, disabled,
            not can_edit or archived, not can_edit or not archived, False, no_update)


def layout(runtime):
    available = runtime.definitions is not None
    return [
        heading(SPEC.title, 'Maintain shared definitions with version-checked saves and reversible archiving.'),
        dbc.Alert('Legacy sample metadata. Admin-maintained report access and delivery settings are configured in Administration.', color='info'),
        dbc.Alert('Report definitions require configured local SQLite storage. Ask the workspace operator to configure storage.',
                  color='warning', is_open=not available),
        html.P('Cadence and enabled are metadata only. Saving a definition does not schedule or run a report.',
               className='subtitle'),
        dbc.Row([
            dbc.Col([
                dbc.Label('Search definitions', html_for='maintenance-search'),
                dbc.Input(id='maintenance-search', type='search', value='', maxLength=120,
                          debounce=True, placeholder='Name or description', disabled=not available),
            ], md=7),
            dbc.Col(dbc.Checklist(id='maintenance-show-archived',
                                 options=[{'label': 'Include archived', 'value': 'archived'}],
                                 value=[], switch=True, className='mt-4'), md=3),
            dbc.Col(dbc.Button('Refresh list', id='maintenance-refresh', outline=True,
                               color='primary', className='mt-4', disabled=not available), md=2),
        ], className='g-3 mb-3'),
        dash_table.DataTable(
            id='maintenance-table',
            columns=[{'name': title, 'id': key} for title, key in (
                ('Name', 'name'), ('Cadence', 'cadence'), ('Enabled', 'enabled'),
                ('Status', 'status'), ('Version', 'version'), ('Access', 'access'))],
            data=[], row_selectable='single', selected_rows=[], editable=False,
            page_action='none', sort_action='none', filter_action='none',
            style_table={'overflowX': 'auto'},
            style_cell={'fontFamily': 'inherit', 'padding': '12px', 'textAlign': 'left',
                        'whiteSpace': 'normal', 'height': 'auto', 'maxWidth': '280px'},
            style_header={'backgroundColor': '#edf2f7', 'fontWeight': '600'},
            style_data={'border': '1px solid #edf2f7'},
        ),
        html.Div([
            dbc.Button('Previous', id='maintenance-prev', outline=True, color='secondary', disabled=True),
            html.Span('Loading definitions…' if available else 'Storage unavailable',
                      id='maintenance-page-label', className='mx-3', role='status', **{'aria-live': 'polite'}),
            dbc.Button('Next', id='maintenance-next', outline=True, color='secondary', disabled=True),
        ], className='d-flex flex-wrap align-items-center gap-2 mt-3 mb-4'),
        dbc.Card(dbc.CardBody([
            html.Div([
                html.H2('New report definition', id='maintenance-editor-title'),
                dbc.Button('New definition', id='maintenance-new', outline=True,
                           color='primary', disabled=not available),
            ], className='d-flex flex-wrap justify-content-between align-items-center gap-2'),
            html.P('Not saved yet', id='maintenance-record-meta', className='subtitle',
                   role='status', **{'aria-live': 'polite'}),
            dbc.Label('Name', html_for='maintenance-name'),
            dbc.Input(id='maintenance-name', value='', maxLength=120, disabled=not available, invalid=False),
            dbc.FormFeedback(id='maintenance-name-feedback', type='invalid'),
            dbc.Label('Description', html_for='maintenance-description', className='mt-3'),
            dbc.Textarea(id='maintenance-description', value='', maxLength=1000,
                         rows=3, disabled=not available, invalid=False),
            dbc.FormFeedback(id='maintenance-description-feedback', type='invalid'),
            dbc.Row([
                dbc.Col([
                    dbc.Label('Cadence (metadata only)', html_for='maintenance-cadence', className='mt-3'),
                    dbc.Select(id='maintenance-cadence', value='manual', disabled=not available, invalid=False,
                               options=[{'label': value.title(), 'value': value} for value in _CADENCES]),
                    dbc.FormFeedback(id='maintenance-cadence-feedback', type='invalid'),
                ], md=6),
                dbc.Col(dbc.Switch(id='maintenance-enabled', label='Enabled (metadata only)',
                                   value=True, className='mt-5', disabled=not available), md=6),
            ], className='g-3'),
            html.P('Save creates a definition in your organization with you as its owner.',
                   id='maintenance-form-hint', className='subtitle mt-3'),
            html.Div([
                dbc.Button('Save definition', id='maintenance-save', color='primary', disabled=not available),
                dbc.Button('Archive', id='maintenance-archive', color='warning', outline=True, disabled=True),
                dbc.Button('Restore', id='maintenance-restore', color='success', outline=True, disabled=True),
                dbc.Button('Reload selected', id='maintenance-reload', color='secondary', outline=True, disabled=True),
            ], className='d-flex flex-wrap gap-2'),
            html.Small('Choosing a row, New definition or Reload selected replaces unsaved form changes. '
                       'Archiving is reversible; definitions are never permanently deleted here.',
                       className='d-block mt-3'),
        ])),
        dcc.Store(id='maintenance-page', data=0),
        dcc.Store(id='maintenance-record'),
        dcc.Store(id='maintenance-draft', data=secrets.token_hex(16), storage_type='memory'),
        dcc.Store(id='maintenance-mutation'),
        dcc.Store(id=notification_store_id('maintenance.list')),
        dcc.Store(id=notification_store_id('maintenance.select')),
        dcc.Store(id=notification_store_id('maintenance.mutate')),
    ]


def register_callbacks(callbacks, runtime):
    @callbacks.callback(
        Output('maintenance-name', 'invalid'), Output('maintenance-name-feedback', 'children'),
        Output('maintenance-description', 'invalid'), Output('maintenance-description-feedback', 'children'),
        Output('maintenance-cadence', 'invalid'), Output('maintenance-cadence-feedback', 'children'),
        Input('maintenance-save', 'n_clicks'), Input('maintenance-name', 'value'),
        Input('maintenance-description', 'value'), Input('maintenance-cadence', 'value'),
        Input('maintenance-draft', 'data'), Input('maintenance-record', 'data'),
        Input('maintenance-name', 'disabled'),
        callback_id='maintenance.feedback', policy=POLICY, page_id='maintenance', prevent_initial_call=True,
    )
    def field_feedback(save, name, description, cadence, draft, current, disabled):
        # A newly mounted/reset/selected editor starts clean. This callback only
        # decorates the form; it cannot approve or suppress a server mutation.
        triggers = set(ctx.triggered_prop_ids.values())
        reset = triggers.intersection(('maintenance-draft', 'maintenance-record'))
        errors = {} if disabled or reset else definition_field_errors(name, description, cadence)
        return tuple(value for field in ('name', 'description', 'cadence')
                     for value in (field in errors, errors.get(field, '')))

    @callbacks.callback(
        Output('maintenance-table', 'data'), Output('maintenance-table', 'selected_rows'),
        Output('maintenance-page', 'data'), Output('maintenance-page-label', 'children'),
        Output('maintenance-prev', 'disabled'), Output('maintenance-next', 'disabled'),
        Output(notification_store_id('maintenance.list'), 'data'),
        Input('maintenance-search', 'value'), Input('maintenance-show-archived', 'value'),
        Input('maintenance-prev', 'n_clicks'), Input('maintenance-next', 'n_clicks'),
        Input('maintenance-refresh', 'n_clicks'), Input('maintenance-mutation', 'data'),
        Input('maintenance-new', 'n_clicks'), State('maintenance-page', 'data'),
        callback_id='maintenance.list', policy=POLICY, page_id='maintenance',
    )
    def list_definitions(query, archived, previous, following, refresh, mutation, new, page):
        page = page if type(page) is int and 0 <= page <= MAX_PAGE else 0
        triggers = set(ctx.triggered_prop_ids.values())
        if not triggers or triggers.intersection(('maintenance-search', 'maintenance-show-archived')):
            page = 0
        elif 'maintenance-prev' in triggers:
            page = max(0, page - 1)
        elif 'maintenance-next' in triggers:
            page = min(MAX_PAGE, page + 1)
        service = None
        try:
            service = _service(runtime)
            if not isinstance(query, str) or archived not in ([], ['archived']):
                raise ValidationError()
            result = service.list(q=query, limit=PAGE_SIZE,
                                  offset=page * PAGE_SIZE, include_deleted=archived == ['archived'])
            last_page = max(0, (result['total'] - 1) // PAGE_SIZE)
            if page > last_page:
                page = last_page
                result = service.list(q=query, limit=PAGE_SIZE,
                                      offset=page * PAGE_SIZE, include_deleted=archived == ['archived'])
            user = service.principal
            rows = [{'id': record['id'], 'name': record['name'], 'cadence': record['cadence'],
                     'enabled': 'Yes' if record['enabled'] else 'No',
                     'status': 'Archived' if record['deleted_at'] is not None else 'Active',
                     'version': record['version'], 'access': 'Editable' if _editable(record, user) else 'Read only'}
                    for record in result['items']]
            label = ('No matching definitions' if not result['total'] else
                     '{}–{} of {} · Page {}'.format(page * PAGE_SIZE + 1, page * PAGE_SIZE + len(rows),
                                                   result['total'], page + 1))
            return rows, [], page, label, page == 0, page >= last_page, no_update
        except Exception as error:
            # Clear stale rows on failure rather than showing another filter's data.
            return [], [], 0, 'Definitions could not be loaded', True, True, _notice(runtime, error, service)

    @callbacks.callback(
        Output('maintenance-record', 'data'), Output('maintenance-name', 'value'),
        Output('maintenance-description', 'value'), Output('maintenance-cadence', 'value'),
        Output('maintenance-enabled', 'value'), Output('maintenance-editor-title', 'children'),
        Output('maintenance-record-meta', 'children'), Output('maintenance-form-hint', 'children'),
        Output('maintenance-name', 'disabled'), Output('maintenance-description', 'disabled'),
        Output('maintenance-cadence', 'disabled'), Output('maintenance-enabled', 'disabled'),
        Output('maintenance-save', 'disabled'), Output('maintenance-archive', 'disabled'),
        Output('maintenance-restore', 'disabled'), Output('maintenance-reload', 'disabled'),
        Output('maintenance-draft', 'data'), Output(notification_store_id('maintenance.select'), 'data'),
        Input('maintenance-table', 'selected_rows'), Input('maintenance-new', 'n_clicks'),
        Input('maintenance-reload', 'n_clicks'), Input('maintenance-mutation', 'data'),
        State('maintenance-table', 'data'), State('maintenance-record', 'data'),
        callback_id='maintenance.select', policy=POLICY, page_id='maintenance', prevent_initial_call=True,
    )
    def select_definition(selected, new, reload, mutation, rows, current):
        # Dash may batch row-selection clearing with New or a saved mutation.
        # Explicit actions take precedence over that dependent empty selection.
        triggers = set(ctx.triggered_prop_ids.values())
        if 'maintenance-new' in triggers:
            return _editor(None, runtime.identity(), runtime.definitions is not None) + (no_update,)
        service = None
        try:
            if 'maintenance-mutation' in triggers:
                if not isinstance(mutation, dict) or set(mutation) != {'id', 'token'}:
                    raise ValidationError()
                record_id = mutation['id']
            elif 'maintenance-reload' in triggers:
                record_id, version = _reference(current)
            elif 'maintenance-table' in triggers:
                # An empty selection is produced when the list changes. Retain the
                # explicit current form; never reinterpret an old row index.
                if selected == []:
                    raise PreventUpdate
                if (not isinstance(selected, list) or len(selected) != 1 or type(selected[0]) is not int
                        or not isinstance(rows, list) or not 0 <= selected[0] < len(rows)
                        or not isinstance(rows[selected[0]], dict)):
                    raise ValidationError()
                record_id = rows[selected[0]].get('id')
            else:
                raise PreventUpdate
            service = _service(runtime)
            record = service.get(record_id)
            return _editor(record, service.principal) + (no_update,)
        except PreventUpdate:
            raise
        except Exception as error:
            return (no_update,) * _EDITOR_OUTPUTS + (_notice(runtime, error, service),)

    @callbacks.callback(
        Output(notification_store_id('maintenance.mutate'), 'data'), Output('maintenance-mutation', 'data'),
        Input('maintenance-save', 'n_clicks'), Input('maintenance-archive', 'n_clicks'),
        Input('maintenance-restore', 'n_clicks'), State('maintenance-name', 'value'),
        State('maintenance-description', 'value'), State('maintenance-cadence', 'value'),
        State('maintenance-enabled', 'value'), State('maintenance-record', 'data'),
        State('maintenance-draft', 'data'),
        callback_id='maintenance.mutate', policy=POLICY, page_id='maintenance', prevent_initial_call=True,
    )
    def mutate_definition(save, archive, restore, name, description, cadence, enabled, current, draft):
        actions = set(ctx.triggered_prop_ids).intersection(
            ('maintenance-save.n_clicks', 'maintenance-archive.n_clicks',
             'maintenance-restore.n_clicks'))
        # A request must express one write intent. Never let a forged/batched
        # changedPropIds order choose between save, archive and restore.
        if len(actions) != 1:
            raise PreventUpdate
        trigger = next(iter(actions)).rsplit('.', 1)[0]
        service = None
        try:
            service = _service(runtime)
            if trigger == 'maintenance-save':
                payload = {'name': name, 'description': description, 'cadence': cadence, 'enabled': enabled}
                if current is None:
                    # One stable key per draft makes a rapid double-save or a
                    # retry after a lost response resolve to the same creation.
                    record = service.create(payload,
                                            request_key=_draft_key(draft))
                    code = 'maintenance.created'
                else:
                    record_id, version = _reference(current)
                    record = service.update(record_id, version, payload)
                    code = 'maintenance.updated'
            else:
                record_id, version = _reference(current)
                if trigger == 'maintenance-archive':
                    record = service.soft_delete(record_id, version)
                    code = 'maintenance.archived'
                else:
                    record = service.restore(record_id, version)
                    code = 'maintenance.restored'
            return NotifyService(service.identity).success(code, request_id=service.request_id), {
                'id': record['id'], 'token': secrets.token_hex(16)}
        except Exception as error:
            # Keep the form and expected version unchanged after failed writes.
            return _notice(runtime, error, service), no_update


SPEC = PageSpec(page_id='maintenance', path='/maintenance', title='Report definitions',
                layout=layout, policy=POLICY, nav_label='Maintenance', nav_order=50,
                register_callbacks=register_callbacks)
