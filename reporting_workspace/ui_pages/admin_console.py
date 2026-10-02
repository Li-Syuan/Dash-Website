"""Organization-scoped managed report console; every write is service-authorized.

Client Stores carry references, not records or identity claims. Selection always
reloads the server record. Only the service decides access, versions and replay.
No scheduler or external delivery is started by these controls.
"""
import re
import secrets
from datetime import datetime, timezone

from dash import Input, Output, State, ctx, dash_table, dcc, html, no_update
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc
from demo_services import AccessDenied

from ..crud import Conflict, NotFound, StateUnavailable, ValidationError
from ..notifications import notification_store_id
from ..registry import AccessPolicy
from .shared import heading, request_id


POLICY = AccessPolicy.require(roles=('admin',))
_KEY = re.compile(r'[0-9a-f]{32}\Z')
_ACTIONS = ('view', 'export', 'maintain')
_WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
_MUTATION_BUTTONS = ('admin-report-save', 'admin-report-archive', 'admin-report-restore',
                     'admin-grant-save', 'admin-schedule-save', 'admin-schedule-simulate')


def clicked(value):
    """Dynamic component mounting is not an intentional button click."""
    return type(value) is int and value > 0


def service(runtime):
    managed = runtime.managed
    if not managed.available:
        raise StateUnavailable()
    return managed


def notice(runtime, error):
    if isinstance(error, ValidationError):
        kind, code = 'warning', 'maintenance.invalid'
    elif isinstance(error, Conflict):
        kind, code = 'warning', 'maintenance.conflict'
    elif isinstance(error, (AccessDenied, NotFound)):
        kind, code = 'error', 'maintenance.denied'
    elif isinstance(error, StateUnavailable):
        kind, code = 'error', 'maintenance.unavailable'
    else:
        kind, code = 'error', 'maintenance.failed'
    return getattr(runtime.notify, kind)(code, request_id=request_id())


def reference(value):
    if (not isinstance(value, dict) or set(value) != {'id', 'version'}
            or not isinstance(value['id'], str) or not value['id']
            or type(value['version']) is not int or value['version'] < 1):
        raise ValidationError()
    return value['id'], value['version']


def draft_key(value):
    if not isinstance(value, str) or _KEY.fullmatch(value) is None:
        raise ValidationError()
    return value


def _ref(record):
    return {'id': record['id'], 'version': record['version']} if record else None


def _panel(number, title, subtitle, children, class_name=''):
    return html.Section([
        html.Div([html.Span(number, className='admin-step'), html.Div([
            html.H2(title), html.P(subtitle, className='admin-caption'),
        ])], className='admin-panel-heading'),
        html.Div(children, className='admin-panel-body'),
    ], className='admin-panel ' + class_name)


def _field(label, component, hint=None):
    return html.Div([dbc.Label(label, html_for=component.id), component,
                     html.Small(hint, className='admin-field-hint') if hint else None],
                    className='admin-field')


def _timestamp(value):
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    return value or '—'


def _table(table_id, columns, rows):
    return dash_table.DataTable(
        id=table_id, columns=[{'name': label, 'id': key} for key, label in columns],
        data=rows, editable=False, page_size=8, page_action='native',
        sort_action='native', style_table={'overflowX': 'auto'},
        style_cell={'fontFamily': 'inherit', 'padding': '12px', 'textAlign': 'left',
                    'whiteSpace': 'normal', 'height': 'auto', 'minWidth': '100px', 'maxWidth': '280px'},
        style_header={'fontWeight': '600'},
    )


def _schedule_editor(runtime, record=None, report=None):
    enabled = bool(runtime.managed.available and report and not report['archived_at'])
    data = record or {'recipients': [], 'cadence': 'daily', 'time': '09:00',
                      'timezone': 'UTC', 'weekday': 0, 'enabled': False}
    return [
        html.P('Version {}'.format(record['version']) if record else 'New schedule',
               className='admin-record-meta', role='status'),
        _field('Recipients', dbc.Textarea(id='admin-schedule-recipients',
               value='\n'.join(data['recipients']), rows=2, maxLength=2000, disabled=not enabled),
               'One address per line. The offline mail sink accepts only @example.invalid.'),
        html.Div([
            _field('Cadence', dbc.Select(id='admin-schedule-cadence', value=data['cadence'],
                   options=[{'label': 'Daily', 'value': 'daily'}, {'label': 'Weekly', 'value': 'weekly'}],
                   disabled=not enabled)),
            _field('Local time', dbc.Input(id='admin-schedule-time', type='time', value=data['time'],
                                          disabled=not enabled)),
            _field('Time zone', dbc.Select(id='admin-schedule-timezone', value=data['timezone'],
                   options=[{'label': 'UTC', 'value': 'UTC'}, {'label': 'Asia / Taipei', 'value': 'Asia/Taipei'}],
                   disabled=not enabled)),
            _field('Weekday', dbc.Select(id='admin-schedule-weekday', value=str(data['weekday']),
                   options=[{'label': label, 'value': str(index)} for index, label in enumerate(_WEEKDAYS)],
                   disabled=not enabled), 'Used for weekly schedules only.'),
        ], className='admin-form-grid'),
        dbc.Switch(id='admin-schedule-enabled', label='Schedule configuration enabled',
                   value=data['enabled'], disabled=not enabled),
        html.Div([
            html.Span('ENGINE STOPPED', className='admin-status admin-status-stopped'),
            html.Span('Saving does not start a scheduler or send mail.', className='admin-caption'),
        ], className='admin-engine-note'),
        html.P('Next planned occurrence (UTC): {}'.format(record.get('next_run_at_utc') or 'Not scheduled')
               if record else 'The next planned occurrence appears after saving.',
               className='admin-field-hint'),
        html.Div([
            dbc.Button('Save schedule', id='admin-schedule-save', color='primary', disabled=not enabled),
            dbc.Button('Simulate once', id='admin-schedule-simulate', outline=True, color='secondary',
                       disabled=not (enabled and record and record['enabled'] and report['enabled'] and runtime.managed.is_demo)),
        ], className='admin-actions'),
        html.Small('Simulation is manual and offline, once per saved configuration version. '
                   'A running or uncertain result is not retried automatically. Editing creates a new version.',
                   className='admin-field-hint'),
    ]


def _details(runtime, record=None, schedule_id=None, blocked=False):
    available = runtime.managed.available
    selected = bool(record)
    active = bool(selected and not record['archived_at'])
    disabled = blocked or not available or (selected and not active)
    data = record or dict(name='', category='General', description='', source_key=None, enabled=True)
    user = runtime.identity()
    grants, schedules, runs, audit = [], [], [], []
    if available and selected:
        managed = service(runtime)
        audit = managed.list_audit(user, record['id'])
        grants = managed.list_grants(user, record['id'])
        schedules = managed.list_schedules(user, record['id'])
        runs = managed.list_runs(user, record['id'])
    grant_rows = [{'kind': item['kind'].title(), 'subject': item['subject'],
                   'actions': ', '.join(item['actions'])} for item in grants]
    run_rows = [{'status': item.get('status', ''), 'error': item.get('error_code') or 'None',
                 'version': item.get('schedule_version', item.get('version', '')),
                 'started': _timestamp(item.get('started_at', item.get('created_at'))),
                 'finished': _timestamp(item.get('finished_at'))} for item in runs]
    audit_rows = [{'action': item['action'], 'actor': item['actor_id'], 'resource': item['resource_id'],
                   'version': '{} → {}'.format(item.get('before_version') or '—', item.get('after_version') or '—'),
                   'fields': ', '.join(item.get('changed_fields') or []),
                   'at': _timestamp(item['occurred_at'])} for item in audit]
    schedule_options = [{'label': '{} · {} {} · {}'.format(
        item['cadence'].title(), item['time'], item['timezone'],
        'Enabled' if item['enabled'] else 'Disabled'), 'value': item['id']} for item in schedules]
    chosen_schedule = next((item for item in schedules if item['id'] == schedule_id), None)
    return [
        html.Div([
            _panel('01', 'Report details', 'Name the report and choose a registered data source.', [
                html.Div([html.Span('Version {}'.format(record['version']) if selected else 'No report selected' if blocked else 'Unsaved draft',
                                    className='admin-record-meta'),
                          html.Span('Archived' if selected and not active else 'Active' if selected else 'New',
                                    className='admin-status')], className='admin-meta-line'),
                _field('Report name', dbc.Input(id='admin-report-name', value=data['name'], maxLength=120,
                                               disabled=disabled)),
                html.Div([
                    _field('Category', dbc.Input(id='admin-report-category', value=data['category'],
                                                maxLength=64, disabled=disabled)),
                    _field('Data source', dbc.Select(id='admin-report-source', value=data['source_key'],
                           options=runtime.managed.sources(), disabled=disabled or selected),
                           'Only reviewed, server-registered sources can be selected.'),
                ], className='admin-form-grid'),
                _field('Description', dbc.Textarea(id='admin-report-description', value=data['description'],
                       maxLength=240, rows=3, disabled=disabled)),
                dbc.Switch(id='admin-report-enabled', label='Visible to authorized viewers',
                           value=data['enabled'], disabled=disabled),
                html.Div([
                    dbc.Button('Save report', id='admin-report-save', color='primary', disabled=disabled),
                    dbc.Button('Archive', id='admin-report-archive', outline=True, color='warning', disabled=not active),
                    dbc.Button('Restore', id='admin-report-restore', outline=True, color='success',
                               disabled=not selected or active),
                    html.A('Open report ↗', href='/reports?report=' + record['id'], className='admin-text-link')
                    if selected else None,
                ], className='admin-actions'),
                html.Small('Saves check the displayed version. Archive is reversible; there is no permanent delete.',
                           className='admin-field-hint'),
            ]),
            _panel('02', 'Access rules', 'Grant view, export or metadata-maintenance access.', [
                _table('admin-grants-table', [('kind', 'Kind'), ('subject', 'Subject'), ('actions', 'Access')], grant_rows),
                html.P('No explicit grants yet. Administrators retain access within their organization.'
                       if not grants else '{} explicit access rule(s).'.format(len(grants)), className='admin-caption'),
                html.Div([
                    _field('Subject type', dbc.Select(id='admin-grant-kind', value='role',
                           options=[{'label': label, 'value': value} for label, value in
                                    [('Role', 'role'), ('User ID', 'user'), ('Organization', 'org')]], disabled=not active)),
                    _field('Subject', dbc.Input(id='admin-grant-subject', value='', maxLength=120,
                           placeholder='user, admin, user ID or your organization', disabled=not active)),
                ], className='admin-form-grid'),
                dbc.Checklist(id='admin-grant-actions', options=[{'label': name.title(), 'value': name,
                              'disabled': not active} for name in _ACTIONS], value=['view'], inline=True),
                html.Div([dbc.Button('Save access rule', id='admin-grant-save', color='primary',
                                     outline=True, disabled=not active)], className='admin-actions'),
                html.Small('Export and maintain also require view in the same rule. Leave all actions unchecked '
                           'to remove that exact rule. Rules add together: removing a user rule does not remove '
                           'access granted through a role or organization. Maintainers can edit name, category '
                           'and description only.', className='admin-field-hint'),
            ]),
        ], className='admin-two-column'),
        _panel('03', 'Delivery schedule', 'Configure an offline schedule and inspect a manual simulation.', [
            html.Div([
                _field('Saved schedule', dbc.Select(id='admin-schedule-picker', value=chosen_schedule['id'] if chosen_schedule else '',
                       options=[{'label': 'New schedule', 'value': ''}] + schedule_options, disabled=not active)),
                dbc.Button('New schedule', id='admin-schedule-new', outline=True, color='secondary', disabled=not active),
            ], className='admin-schedule-toolbar'),
            html.Div(_schedule_editor(runtime, chosen_schedule, record), id='admin-schedule-editor'),
        ]),
        html.Div([
            _panel('04', 'Run history', 'The latest 50 runs; errors use fixed, sanitized codes.', [
                _table('admin-runs-table', [('status', 'Status'), ('version', 'Config version'),
                       ('started', 'Started'), ('finished', 'Finished'), ('error', 'Error code')], run_rows),
                html.P('No runs for this report yet.' if not runs else 'Running can mean an uncertain outcome. '
                       'Inspect the run before taking further action.', className='admin-caption'),
            ]),
            _panel('05', 'Audit trail', 'Recent configuration changes in your organization.', [
                _table('admin-audit-table', [('at', 'Time (UTC)'), ('action', 'Action'),
                       ('actor', 'Actor reference'), ('resource', 'Resource reference'),
                       ('version', 'Version'), ('fields', 'Changed fields')], audit_rows),
                html.P('No configuration changes recorded yet.' if not audit else
                       'Field names are recorded; report contents and recipient payloads are not shown in the audit trail.',
                       className='admin-caption'),
            ]),
        ], className='admin-two-column admin-history'),
    ]


def layout(runtime):
    available = runtime.managed.available
    return [html.Div([
        heading('Administration', 'Manage reports, access and offline delivery settings in one place.'),
        html.Div([
            html.Div([html.Span('WORKSPACE', className='eyebrow'), html.Strong('Organization scoped'),
                      html.Small('Your current identity is checked on every action')], className='admin-summary-card'),
            html.Div([html.Span('STORAGE', className='eyebrow'), html.Strong('Local SQLite' if available else 'Not configured'),
                      html.Small('Versioned saves and reversible archive')], className='admin-summary-card'),
            html.Div([html.Span('DELIVERY ENGINE', className='eyebrow'), html.Strong('Stopped'),
                      html.Small('No background jobs or external mail')], className='admin-summary-card'),
        ], className='admin-summary'),
        dbc.Alert('Managed reports require configured local SQLite storage. Ask the workspace operator to configure storage.',
                  color='warning', is_open=not available),
        html.Div([
            _field('Select a report', dbc.Select(id='admin-report-picker', value='', options=[], disabled=not available)),
            dbc.Checklist(id='admin-show-archived', options=[{'label': 'Include archived', 'value': 'archived',
                          'disabled': not available}], value=[], switch=True),
            dbc.Button('Refresh', id='admin-refresh', outline=True, color='secondary', disabled=not available),
            dbc.Button('New report', id='admin-report-new', color='primary', disabled=not available),
            dbc.Button('Reload selected', id='admin-report-reload', outline=True, color='secondary', disabled=not available),
        ], className='admin-toolbar'),
        html.P('Choose a report or create a new one.', id='admin-list-status', className='admin-caption',
               role='status', **{'aria-live': 'polite'}),
        html.P('Choosing a report, reloading, or completing an action replaces unsaved form changes.',
               className='admin-field-hint'),
        html.P('', id='admin-action-status', className='admin-action-status', role='status', **{'aria-live': 'polite'}),
        html.Div(_details(runtime), id='admin-details'),
        dcc.Store(id='admin-record'), dcc.Store(id='admin-draft', data=secrets.token_hex(16)),
        dcc.Store(id='admin-schedule-record'), dcc.Store(id='admin-schedule-draft', data=secrets.token_hex(16)),
        dcc.Store(id='admin-change'),
        *[dcc.Store(id=notification_store_id('admin.' + name)) for name in ('list', 'select', 'schedule', 'mutate')],
    ], className='admin-console')]


def register_callbacks(callbacks, runtime):
    @callbacks.callback(Output('admin-report-picker', 'options'), Output('admin-list-status', 'children'),
                        Output(notification_store_id('admin.list'), 'data'),
                        Input('admin-show-archived', 'value'), Input('admin-refresh', 'n_clicks'),
                        Input('admin-change', 'data'), callback_id='admin.list', policy=POLICY, page_id='administration')
    def list_reports(archived, refresh, change):
        try:
            if archived not in ([], ['archived']):
                raise ValidationError()
            records = service(runtime).list_reports(runtime.identity(), include_archived=archived == ['archived'], admin=True)
            options = [{'label': '{}{}'.format(record['name'], ' · Archived' if record['archived_at'] else ''),
                        'value': record['id']} for record in records]
            return options, '{} report(s) in your organization. Select one to manage its configuration.'.format(len(records)), no_update
        except Exception as error:
            return [], 'Reports could not be loaded.', notice(runtime, error)

    @callbacks.callback(Output('admin-details', 'children'), Output('admin-record', 'data'), Output('admin-draft', 'data'),
                        Output('admin-report-picker', 'value'), Output(notification_store_id('admin.select'), 'data'),
                        Input('admin-report-picker', 'value'), Input('admin-report-new', 'n_clicks'),
                        Input('admin-report-reload', 'n_clicks'), Input('admin-change', 'data'),
                        State('admin-record', 'data'), callback_id='admin.select', policy=POLICY,
                        page_id='administration', prevent_initial_call=True)
    def select_report(selected, new, reload, change, current):
        triggers = set(ctx.triggered_prop_ids.values())
        try:
            if 'admin-report-new' in triggers:
                if not clicked(new):
                    raise PreventUpdate
                return _details(runtime), None, secrets.token_hex(16), '', no_update
            schedule_id = None
            if 'admin-change' in triggers:
                if (not isinstance(change, dict) or set(change) != {'id', 'schedule_id', 'token'}
                        or not isinstance(change['id'], str)):
                    raise ValidationError()
                report_id, schedule_id = change['id'], change['schedule_id']
            elif 'admin-report-reload' in triggers:
                if not clicked(reload):
                    raise PreventUpdate
                report_id, version = reference(current)
            elif 'admin-report-picker' in triggers:
                if selected in ('', None):
                    raise PreventUpdate
                report_id = selected
            else:
                raise PreventUpdate
            record = service(runtime).get_report(runtime.identity(), report_id, for_maintenance=True)
            return (_details(runtime, record, schedule_id), _ref(record), secrets.token_hex(16),
                    record['id'] if 'admin-change' in triggers else no_update, no_update)
        except PreventUpdate:
            raise
        except Exception as error:
            # A failed selection must not leave a different report as the Save
            # target. Mutation conflicts never call this callback, so their
            # unsaved fields and expected version remain untouched.
            return _details(runtime, blocked=True), None, secrets.token_hex(16), '', notice(runtime, error)

    @callbacks.callback(Output('admin-schedule-editor', 'children'), Output('admin-schedule-record', 'data'),
                        Output('admin-schedule-draft', 'data'), Output(notification_store_id('admin.schedule'), 'data'),
                        Input('admin-schedule-picker', 'value'), Input('admin-schedule-new', 'n_clicks'),
                        Input('admin-record', 'data'), State('admin-change', 'data'),
                        callback_id='admin.schedule', policy=POLICY, page_id='administration', prevent_initial_call=True)
    def select_schedule(selected, new, current, change):
        triggers = set(ctx.triggered_prop_ids.values())
        if 'admin-schedule-new' in triggers and not clicked(new):
            triggers.discard('admin-schedule-new')
            if not triggers:
                raise PreventUpdate
        try:
            if current is None:
                return _schedule_editor(runtime), None, secrets.token_hex(16), no_update
            managed, user = service(runtime), runtime.identity()
            report_id, version = reference(current)
            report = managed.get_report(user, report_id, for_maintenance=True)
            if 'admin-schedule-new' in triggers:
                selected = None
            elif 'admin-record' in triggers:
                selected = change.get('schedule_id') if isinstance(change, dict) and change.get('id') == report_id else None
            record = managed.get_schedule(user, selected) if selected else None
            if record and record['report_id'] != report_id:
                raise ValidationError()
            return _schedule_editor(runtime, record, report), _ref(record), secrets.token_hex(16), no_update
        except Exception as error:
            # Clear the schedule reference rather than leaving another report's
            # selected schedule as the target of a subsequent click.
            return _schedule_editor(runtime), None, secrets.token_hex(16), notice(runtime, error)

    @callbacks.callback(Output(notification_store_id('admin.mutate'), 'data'), Output('admin-change', 'data'),
                        Output('admin-action-status', 'children'),
                        *[Input('admin-' + name, 'n_clicks') for name in
                          ('report-save', 'report-archive', 'report-restore', 'grant-save', 'schedule-save', 'schedule-simulate')],
                        *[State('admin-' + name, 'value') for name in
                          ('report-name', 'report-category', 'report-description', 'report-source', 'report-enabled',
                           'grant-kind', 'grant-subject', 'grant-actions', 'schedule-recipients', 'schedule-cadence',
                           'schedule-time', 'schedule-timezone', 'schedule-weekday', 'schedule-enabled')],
                        State('admin-record', 'data'), State('admin-draft', 'data'),
                        State('admin-schedule-record', 'data'), State('admin-schedule-draft', 'data'),
                        callback_id='admin.mutate', policy=POLICY, page_id='administration', prevent_initial_call=True)
    def mutate(*values):
        trigger = ctx.triggered_id
        if trigger not in _MUTATION_BUTTONS or not clicked(values[_MUTATION_BUTTONS.index(trigger)]):
            raise PreventUpdate
        (name, category, description, source, enabled, kind, subject, actions, recipients,
         cadence, local_time, timezone, weekday, schedule_enabled, current, draft, schedule_ref, schedule_draft) = values[6:]
        try:
            managed, user = service(runtime), runtime.identity()
            schedule_id = None
            if trigger == 'admin-report-save':
                payload = dict(name=name, category=category, description=description, source_key=source, enabled=enabled)
                if current is None:
                    result = managed.create_report(user, payload, request_key=draft_key(draft), request_id=request_id())
                    code, message = 'maintenance.created', 'Report created.'
                else:
                    report_id, version = reference(current)
                    payload.pop('source_key')
                    result = managed.update_report(user, report_id, version, payload, request_id=request_id())
                    code, message = 'maintenance.updated', 'Report details saved.'
                report_id = result['id']
            else:
                report_id, version = reference(current)
                if trigger in ('admin-report-archive', 'admin-report-restore'):
                    archive = trigger == 'admin-report-archive'
                    method = managed.archive_report if archive else managed.restore_report
                    method(user, report_id, version, request_id=request_id())
                    code = 'maintenance.archived' if archive else 'maintenance.restored'
                    message = 'Report archived. You can restore it from Include archived.' if archive else 'Report restored.'
                elif trigger == 'admin-grant-save':
                    managed.set_grant(user, report_id, version, kind, subject, actions, request_id=request_id())
                    code, message = 'maintenance.updated', 'Access rule saved. Other matching rules still apply.'
                elif trigger == 'admin-schedule-save':
                    if not isinstance(recipients, str) or len(recipients) > 2000 or str(weekday) not in tuple(str(i) for i in range(7)):
                        raise ValidationError()
                    payload = dict(recipients=[address.strip() for address in recipients.replace(',', '\n').splitlines() if address.strip()],
                                   cadence=cadence, time=local_time, timezone=timezone, weekday=int(weekday), enabled=schedule_enabled)
                    if schedule_ref is None:
                        result = managed.create_schedule(user, report_id, version, payload,
                                  request_key=draft_key(schedule_draft), request_id=request_id())
                    else:
                        schedule_id, schedule_version = reference(schedule_ref)
                        saved = managed.get_schedule(user, schedule_id)
                        if saved['report_id'] != report_id:
                            raise ValidationError()
                        result = managed.update_schedule(user, schedule_id, schedule_version, payload, request_id=request_id())
                    schedule_id = result['id']
                    code, message = 'maintenance.updated', 'Schedule configuration saved. The delivery engine is still stopped.'
                else:
                    schedule_id, schedule_version = reference(schedule_ref)
                    saved = managed.get_schedule(user, schedule_id)
                    if saved['report_id'] != report_id:
                        raise ValidationError()
                    result = managed.simulate_schedule(user, schedule_id, schedule_version, request_id=request_id())
                    if result['status'] == 'failed':
                        event = runtime.notify.error('simulation.failed', request_id=request_id())
                        message = 'Simulation failed. Inspect its fixed error code in run history.'
                    elif result.get('duplicate'):
                        event = runtime.notify.info('simulation.skipped', request_id=request_id())
                        message = 'This configuration was already claimed. No simulation was repeated.'
                    elif result['status'] == 'running':
                        event = runtime.notify.warning('simulation.busy', request_id=request_id())
                        message = 'The run is running or uncertain. It will not retry automatically.'
                    else:
                        event = runtime.notify.success('simulation.done', request_id=request_id())
                        message = 'Offline simulation completed. No external mail was sent.'
                    return event, dict(id=report_id, schedule_id=schedule_id, token=secrets.token_hex(16)), message
            return (runtime.notify.success(code, request_id=request_id()),
                    dict(id=report_id, schedule_id=schedule_id, token=secrets.token_hex(16)), message)
        except Exception as error:
            message = ('The record changed. Reload the selected record before retrying; your form has been kept.'
                       if isinstance(error, Conflict) else 'The action could not be completed. Review the fields and access; your form has been kept.')
            return notice(runtime, error), no_update, message
