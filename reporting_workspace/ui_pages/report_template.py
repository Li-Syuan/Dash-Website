"""Opt-in quality report PageSpec; launched through the existing app.py only."""

from dash import Input, Output, State, ctx, dash_table, dcc, html, no_update
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc

from ..errors import ProviderUnavailable
from ..notifications import notification_store_id
from ..registry import PageSpec
from ..report_templates import (COLUMNS, POLICY, InspectionReport, Query, QueryError,
                                SyntheticInspectionRepository)
from .shared import heading, request_id


PAGE_ID = 'report_template'
PREFIX = 'template-quality'


def make_spec(repository=None):
    """Explicit trusted adapter injection for isolated tests, never browser input.

    The shipped template is synthetic-only. A private adapter is a separately
    reviewed integration, not a switch that relabels this example as production.
    """
    repository = SyntheticInspectionRepository() if repository is None else repository

    def service(runtime):
        if not runtime.is_demo:
            raise ValueError('The synthetic report template requires demo mode.')
        return InspectionReport(runtime.identities, repository)

    def layout(runtime):
        report = service(runtime)
        result = report.query(runtime.identity(), {})
        return [
            heading('Quality inspection template',
                    'Synthetic read-only inspections shared within your organization.'),
            dbc.Row([
                dbc.Col([dbc.Label('Search sample, product or date', html_for=PREFIX + '-search'),
                         dbc.Input(id=PREFIX + '-search', value='', maxLength=80)], md=4),
                dbc.Col([dbc.Label('Department', html_for=PREFIX + '-department'),
                         dcc.Dropdown(id=PREFIX + '-department', value='', clearable=False,
                                      options=[{'label': 'All departments', 'value': ''},
                                               {'label': 'Assembly', 'value': 'Assembly'},
                                               {'label': 'Laboratory', 'value': 'Laboratory'}])], md=3),
            ], className='g-3'),
            dbc.Row([
                dbc.Col([dbc.Label('Order by', html_for=PREFIX + '-order'),
                         dcc.Dropdown(id=PREFIX + '-order', value='inspection_date', clearable=False,
                                      options=[{'label': label, 'value': value} for label, value in
                                               (('Inspection date', 'inspection_date'),
                                                ('Sample ID', 'sample_id'), ('Rejected count', 'rejected'))])], md=4),
                dbc.Col([dbc.Label('Direction', html_for=PREFIX + '-direction'),
                         dcc.Dropdown(id=PREFIX + '-direction', value='asc', clearable=False,
                                      options=[{'label': 'Ascending', 'value': 'asc'},
                                               {'label': 'Descending', 'value': 'desc'}])], md=3),
                dbc.Col([dbc.Label('Rows per page', html_for=PREFIX + '-limit'),
                         dcc.Dropdown(id=PREFIX + '-limit', value=20, clearable=False,
                                      options=[{'label': str(value), 'value': value}
                                               for value in (10, 20, 50, 100)])], md=2),
            ], className='g-3 mt-2'),
            html.Div([
                dbc.Button('Apply filters', id=PREFIX + '-refresh', n_clicks=0, color='primary'),
                dbc.Button('Export filtered CSV', id=PREFIX + '-export', n_clicks=0,
                           outline=True, color='primary', className='ms-2'),
            ], className='mt-3 mb-3'),
            dash_table.DataTable(id=PREFIX + '-table', data=result['rows'],
                                 columns=[{'id': name, 'name': name.replace('_', ' ').title(),
                                           'type': 'numeric' if name in ('inspected', 'rejected') else 'text'}
                                          for name in COLUMNS],
                                 editable=False, page_action='none', sort_action='none',
                                 filter_action='none', style_table={'overflowX': 'auto'}),
            html.P(_status(result), id=PREFIX + '-status', role='status', className='mt-3'),
            dbc.Button('Previous', id=PREFIX + '-prev', n_clicks=0, disabled=True),
            dbc.Button('Next', id=PREFIX + '-next', n_clicks=0,
                       disabled=result['total'] <= result['limit'], className='ms-2'),
            html.P('CSV uses the current controls and includes every matching server row '
                   '(up to 1000), not only this page. Apply filters to preview them. '
                   'Text that could be a spreadsheet formula is exported as literal text.', className='subtitle mt-3'),
            dcc.Store(id=PREFIX + '-offset', data=0, storage_type='memory'),
            dcc.Download(id=PREFIX + '-download'),
            dcc.Store(id=notification_store_id('report_template.query')),
            dcc.Store(id=notification_store_id('report_template.export')),
        ]

    def register(callbacks, runtime):
        report = service(runtime)
        filter_states = (State(PREFIX + '-search', 'value'), State(PREFIX + '-department', 'value'),
                         State(PREFIX + '-order', 'value'), State(PREFIX + '-direction', 'value'),
                         State(PREFIX + '-limit', 'value'))

        @callbacks.callback(
            Output(PREFIX + '-table', 'data'), Output(PREFIX + '-offset', 'data'),
            Output(PREFIX + '-status', 'children'), Output(PREFIX + '-prev', 'disabled'),
            Output(PREFIX + '-next', 'disabled'),
            Output(notification_store_id('report_template.query'), 'data'),
            Input(PREFIX + '-refresh', 'n_clicks'), Input(PREFIX + '-prev', 'n_clicks'),
            Input(PREFIX + '-next', 'n_clicks'), *filter_states, State(PREFIX + '-offset', 'data'),
            callback_id='report_template.query', policy=POLICY, page_id=PAGE_ID,
            prevent_initial_call=True)
        def query_rows(refresh, previous, following, search, department, order, direction, limit, offset):
            action = ctx.triggered_id
            if action not in (PREFIX + '-refresh', PREFIX + '-prev', PREFIX + '-next'):
                raise PreventUpdate
            values = _filters(search, department, order, direction, limit, offset)
            try:
                query = Query.parse(values)
                offset = (0 if action == PREFIX + '-refresh' else
                          max(0, query.offset - query.limit) if action == PREFIX + '-prev' else
                          min(10000, query.offset + query.limit))
                values['offset'] = offset
                result = report.query(runtime.identity(), values)
            except QueryError:
                return [], 0, 'Check the report filters and try again.', True, True, no_update
            except ProviderUnavailable:
                return [], 0, 'The report is temporarily unavailable.', True, True, no_update
            return (result['rows'], result['offset'], _status(result), result['offset'] == 0,
                    result['offset'] + result['limit'] >= result['total'],
                    runtime.notify.success('report.loaded', request_id=request_id()))

        @callbacks.callback(
            Output(PREFIX + '-download', 'data'),
            Output(notification_store_id('report_template.export'), 'data'),
            Input(PREFIX + '-export', 'n_clicks'), *filter_states,
            callback_id='report_template.export', policy=POLICY, page_id=PAGE_ID,
            prevent_initial_call=True)
        def export_rows(clicks, search, department, order, direction, limit):
            if type(clicks) is not int or clicks < 1:
                raise PreventUpdate
            try:
                content = report.export_csv(runtime.identity(),
                                            _filters(search, department, order, direction, limit, 0))
            except (QueryError, ProviderUnavailable):
                return no_update, runtime.notify.error('report.export.failed', request_id=request_id())
            return (dict(content=content, filename='synthetic-quality-inspections.csv', type='text/csv'),
                    runtime.notify.success('report.export.ready', request_id=request_id()))

    return PageSpec(PAGE_ID, '/QA_portal/report-template', 'Quality inspection template', layout, POLICY,
                    register_callbacks=register, nav_order=80, catalog_category='Quality',
                    catalog_description='Opt-in synthetic inspections with tenant-scoped filters and CSV export.',
                    catalog_tags=('synthetic', 'quality', 'template'))


def _filters(search, department, order, direction, limit, offset):
    return dict(search=search, department=department, order_by=order, direction=direction,
                limit=limit, offset=offset)


def _status(result):
    first = result['offset'] + 1 if result['rows'] else 0
    last = result['offset'] + len(result['rows']) if result['rows'] else 0
    return '{}-{} of {} matching rows'.format(first, last, result['total'])


SPEC = make_spec()
