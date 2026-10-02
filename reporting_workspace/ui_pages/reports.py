"""Server-authorized report layout and explicit report/notification callbacks."""
from flask import current_app
from dash import html, dcc, Input, Output, no_update, dash_table
import dash_bootstrap_components as dbc

from ..errors import ProviderUnavailable
from ..notifications import notification_store_id
from ..registry import AccessPolicy, PageSpec
from .shared import heading, request_id


POLICY = AccessPolicy.require(roles=('admin',))


def _record_view(runtime):
    operations = current_app.extensions.get('operations_service')
    if operations is not None:
        try:
            operations.record_usage(runtime.identity(), 'monthly-performance', 'report_view')
        except Exception:
            current_app.logger.warning('operations_usage_write_failed')


def layout(runtime):
    try:
        rows = runtime.reports.rows(runtime.identity())
        _record_view(runtime)
        event = runtime.notify.success('report.loaded', request_id=request_id())
    except ProviderUnavailable:
        rows = []
        event = runtime.notify.error('report.failed', request_id=request_id())
    totals = {key: sum(row[key] for row in rows) for key in ['revenue', 'cost', 'profit']}
    months = sorted(set(row['period'] for row in rows))
    figure = {
        'data': [dict(type='bar', x=months,
                      y=[sum(row[key] for row in rows if row['period'] == month) for month in months],
                      name=key.title(), marker={'color': color})
                 for key, color in [('revenue', '#315fe7'), ('cost', '#b8c8f3')]],
        'layout': dict(
            paper_bgcolor='white', plot_bgcolor='white',
            font={'family': 'Arial, sans-serif', 'color': '#65758b'},
            margin=dict(l=50, r=20, t=20, b=40), height=270, barmode='group',
            legend=dict(orientation='h', y=1.15),
            yaxis=dict(gridcolor='#edf2f7', title='Demo units' if runtime.is_demo else 'Report units'),
            xaxis=dict(type='category', showgrid=False),
        ),
    }
    return [
        heading(SPEC.title, 'Synthetic data, ready to explore.' if runtime.is_demo
                else 'Authorized data from the configured report adapter.'),
        dbc.Row([
            dbc.Col(dbc.Card(dbc.CardBody([
                html.P(key.upper(), className='eyebrow'),
                html.H2('{:,.0f}'.format(totals[key])),
                html.Small(('Demo units' if runtime.is_demo else 'Report units')
                           + ' · {} periods'.format(len(months))),
            ])), md=4) for key in ['revenue', 'cost', 'profit']
        ], className='g-3 metric-row'),
        dbc.Card(dbc.CardBody([
            html.Div([html.H3('Revenue and cost'),
                      html.Small('{} departments'.format(len(set(row['department'] for row in rows))))],
                     className='chart-heading'),
            dcc.Graph(id='performance-chart', figure=figure,
                      config={'displayModeBar': False, 'responsive': True}),
        ]), className='mt-4 chart-card'),
        dbc.Row([
            dbc.Col(html.H3('Report details'), width=8),
            dbc.Col([dbc.Button('Refresh', id='report-refresh', outline=True, color='primary', className='me-2'),
                     dbc.Button('Export CSV', id='report-export', color='primary')],
                    width=4, className='text-end report-actions'),
        ], className='mt-4 mb-3 report-toolbar'),
        dash_table.DataTable(
            id='table', columns=[{'name': c.title(), 'id': c} for c in runtime.reports.columns], data=rows,
            editable=True, filter_action='native', sort_action='native', sort_mode='multi',
            column_selectable='single', row_selectable='multi', row_deletable=True,
            selected_columns=[], selected_rows=[], page_action='native', page_current=0, page_size=10,
            style_table={'overflowX': 'auto'},
            style_cell={'fontFamily': 'inherit', 'padding': '14px', 'textAlign': 'left'},
            style_header={'backgroundColor': '#edf2f7', 'fontWeight': '600'},
            style_data={'border': '1px solid #edf2f7'},
        ),
        html.P('CSV exports the original fixture, not unsaved edits or the current filter.' if runtime.is_demo
               else 'CSV exports server-owned report data, not unsaved table edits or the current filter.',
               className='subtitle mt-3'),
        dcc.Download(id='report-download'),
        dcc.Store(id=notification_store_id('reports.load'), data=event),
        dcc.Store(id=notification_store_id('reports.refresh')),
        dcc.Store(id=notification_store_id('reports.export')),
    ]


def register_callbacks(callbacks, runtime):
    @callbacks.callback(Output('table', 'data'), Output(notification_store_id('reports.refresh'), 'data'),
                        Input('report-refresh', 'n_clicks'), callback_id='reports.refresh',
                        policy=POLICY, page_id='reports', prevent_initial_call=True)
    def refresh_report(n):
        try:
            rows = runtime.reports.rows(runtime.identity())
            _record_view(runtime)
        except ProviderUnavailable:
            return no_update, runtime.notify.error('report.failed', request_id=request_id())
        return rows, runtime.notify.success('report.loaded', request_id=request_id())

    @callbacks.callback(Output('report-download', 'data'), Output(notification_store_id('reports.export'), 'data'),
                        Input('report-export', 'n_clicks'), callback_id='reports.export',
                        policy=POLICY, page_id='reports', prevent_initial_call=True)
    def download_report(n):
        try:
            content = runtime.reports.export(runtime.identity())
        except ProviderUnavailable:
            return no_update, runtime.notify.error('report.export.failed', request_id=request_id())
        download = {'content': content, 'filename': 'demo-report.csv' if runtime.is_demo else 'report.csv', 'type': 'text/csv'}
        return download, runtime.notify.success('report.export.ready', request_id=request_id())


SPEC = PageSpec('reports', '/page3', 'Monthly performance', layout, POLICY,
                nav_order=30, register_callbacks=register_callbacks,
                catalog_category='Performance',
                catalog_description='Explore synthetic revenue, cost and profit with a chart, editable table and CSV export.',
                catalog_tags=('demo', 'finance', 'monthly'))
