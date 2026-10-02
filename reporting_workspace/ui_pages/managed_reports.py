"""Explicit managed-report route with ACL-enforced data and metadata actions.

Dash routing deliberately ignores search strings. This page owns a Location
input so navigating /reports?report=<id> also changes the selected report.
"""
from urllib.parse import parse_qs
import secrets

from dash import Input, Output, State, ctx, dash_table, dcc, html, no_update
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc

from ..crud import ValidationError
from ..notifications import notification_store_id
from ..registry import AccessPolicy, PageSpec
from .admin_console import service, notice, reference, clicked, _field, _panel
from .shared import heading, request_id


POLICY = AccessPolicy.require(roles=('admin', 'user'))


def report_from_search(search):
    if search in (None, '', '?'):
        return None
    if not isinstance(search, str) or len(search) > 256 or not search.startswith('?'):
        raise ValidationError()
    try:
        values = parse_qs(search[1:], keep_blank_values=True, strict_parsing=True)
    except (ValueError, TypeError):
        raise ValidationError() from None
    if set(values) != {'report'} or len(values['report']) != 1 or not values['report'][0]:
        raise ValidationError()
    return values['report'][0]


def _catalog(records):
    if not records:
        return html.Div('No reports match your access and search. An administrator can configure report access.',
                        className='managed-empty', role='status')
    return html.Div([dcc.Link([
        html.Span(record['category'] or 'Report', className='admin-status'),
        html.H2(record['name']), html.P(record['description'] or 'Open the latest authorized report data.'),
        html.Span('Open report →', className='admin-text-link'),
    ], href='/reports?report=' + record['id'], className='managed-report-card') for record in records],
        className='managed-catalog')


def _view(runtime, record=None, permissions=(), rows=None):
    can_view, can_export, can_maintain = (action in permissions for action in ('view', 'export', 'maintain'))
    data = record or {'name': '', 'category': '', 'description': ''}
    return [
        html.Div([
            html.Div([html.H2(record['name'] if record else 'Choose a report'),
                      html.P('{} · Version {}'.format(runtime.managed.source_label(record['source_key']), record['version'])
                             if record else 'Select an authorized report above to view its data.', className='admin-caption')]),
        ], className='managed-report-toolbar'),
        html.P(data['description'], className='managed-report-description'),
        dbc.Alert('This report is not currently available for viewing. Its permitted metadata can still be maintained.',
                  color='info', is_open=bool(record and not can_view)),
        dash_table.DataTable(id='managed-table',
            columns=[{'name': name.title(), 'id': name} for name in runtime.managed.columns],
            data=rows or [], editable=False, filter_action='native', sort_action='native',
            page_size=10, page_action='native',
            style_table={'overflowX': 'auto'},
            style_cell={'fontFamily': 'inherit', 'padding': '13px', 'textAlign': 'left'},
            style_header={'fontWeight': '600'}),
        html.P('Exports use server-owned data and do not include browser filters.', className='admin-field-hint'),
        html.Div(_panel('EDIT', 'Report metadata', 'Changes use the selected version and current server identity.', [
            html.Div([
                _field('Name', dbc.Input(id='managed-name', value=data['name'], maxLength=120, disabled=not can_maintain)),
                _field('Category', dbc.Input(id='managed-category', value=data['category'], maxLength=64, disabled=not can_maintain)),
            ], className='admin-form-grid'),
            _field('Description', dbc.Textarea(id='managed-description', value=data['description'], maxLength=240,
                                             rows=3, disabled=not can_maintain)),
            dbc.Button('Save metadata', id='managed-save', color='primary', disabled=not can_maintain),
            html.Small('Only name, category and description can be changed here. Refreshing or choosing a report '
                       'replaces unsaved changes. A version conflict keeps your form unchanged.', className='admin-field-hint'),
        ]), style={} if can_maintain else {'display': 'none'}),
    ]


def layout(runtime):
    available = runtime.managed.available
    return [html.Div([
        heading('Reports', 'Discover and open the reports your current identity is allowed to use.'),
        dbc.Alert('Managed reports require configured local SQLite storage. Ask the workspace operator to configure storage.',
                  color='warning', is_open=not available),
        _field('Find a report', dbc.Input(id='managed-search', type='search', value='', maxLength=120,
                                         debounce=True, placeholder='Search name, category or description', disabled=not available)),
        dcc.Location(id='managed-url', refresh=False),
        html.Div(id='managed-catalog'),
        html.Div([dbc.Button('Refresh report', id='managed-refresh', color='secondary', outline=True, disabled=True),
                  dbc.Button('Export CSV', id='managed-export', color='primary', disabled=True)], className='admin-actions'),
        html.Div(_view(runtime), id='managed-view'),
        html.P('', id='managed-status', role='status', **{'aria-live': 'polite'}, className='admin-action-status'),
        dcc.Store(id='managed-record'), dcc.Store(id='managed-change'), dcc.Download(id='managed-download'),
        *[dcc.Store(id=notification_store_id('managed.' + name)) for name in ('load', 'save', 'export')],
    ], className='managed-reports')]


def register_callbacks(callbacks, runtime):
    @callbacks.callback(Output('managed-catalog', 'children'), Output('managed-view', 'children'),
                        Output('managed-record', 'data'), Output('managed-refresh', 'disabled'), Output('managed-export', 'disabled'),
                        Output(notification_store_id('managed.load'), 'data'),
                        Input('managed-url', 'search'), Input('managed-search', 'value'),
                        Input('managed-refresh', 'n_clicks'), Input('managed-change', 'data'),
                        callback_id='managed.load', policy=POLICY, page_id='managed-reports')
    def load_report(search, query, refresh, change):
        try:
            managed, user = service(runtime), runtime.identity()
            if not isinstance(query, str) or len(query) > 120:
                raise ValidationError()
            records = managed.list_reports(user)
            query = query.casefold().strip()
            records = [record for record in records if query in ' '.join(
                (record['name'], record['category'], record['description'])).casefold()]
            report_id = report_from_search(search)
            if not report_id:
                return _catalog(records), _view(runtime), None, True, True, no_update
            permissions = managed.permissions(user, report_id)
            record = managed.get_report(user, report_id, for_maintenance='maintain' in permissions)
            rows = managed.rows(user, report_id) if 'view' in permissions else []
            return (_catalog(records), _view(runtime, record, permissions, rows),
                    {'id': record['id'], 'version': record['version']}, False, 'export' not in permissions, no_update)
        except Exception as error:
            # Navigation failures clear old data/references, avoiding a stale
            # authorized report being mistaken for the newly requested report.
            return _catalog([]), _view(runtime), None, True, True, notice(runtime, error)

    @callbacks.callback(Output(notification_store_id('managed.save'), 'data'), Output('managed-change', 'data'),
                        Output('managed-status', 'children'), Input('managed-save', 'n_clicks'),
                        State('managed-record', 'data'), State('managed-name', 'value'),
                        State('managed-category', 'value'), State('managed-description', 'value'),
                        callback_id='managed.save', policy=POLICY, page_id='managed-reports', prevent_initial_call=True)
    def save_metadata(clicks, current, name, category, description):
        if ctx.triggered_id != 'managed-save' or not clicked(clicks):
            raise PreventUpdate
        try:
            report_id, version = reference(current)
            record = service(runtime).update_report(runtime.identity(), report_id, version,
                     {'name': name, 'category': category, 'description': description}, request_id=request_id())
            return (runtime.notify.success('maintenance.updated', request_id=request_id()),
                    {'id': record['id'], 'version': record['version'], 'token': secrets.token_hex(16)}, 'Report metadata saved.')
        except Exception as error:
            return notice(runtime, error), no_update, 'Metadata was not saved. Review the notification; your form has been kept.'

    @callbacks.callback(Output('managed-download', 'data'), Output(notification_store_id('managed.export'), 'data'),
                        Input('managed-export', 'n_clicks'), State('managed-record', 'data'),
                        callback_id='managed.export', policy=POLICY, page_id='managed-reports', prevent_initial_call=True)
    def export_report(clicks, current):
        if ctx.triggered_id != 'managed-export' or not clicked(clicks):
            raise PreventUpdate
        try:
            report_id, version = reference(current)
            content = service(runtime).export(runtime.identity(), report_id)
            return ({'content': content, 'filename': 'managed-report.csv', 'type': 'text/csv'},
                    runtime.notify.success('report.export.ready', request_id=request_id()))
        except Exception:
            return no_update, runtime.notify.error('report.export.failed', request_id=request_id())


SPEC = PageSpec('managed-reports', '/reports', 'Managed reports', layout, POLICY,
                nav_order=20, register_callbacks=register_callbacks)
