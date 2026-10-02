"""App-scoped Dash UI; fixed routes and callback IDs, no global page registry."""
from pathlib import Path
from dash import Dash, Input, Output, State, ctx, dcc, html, no_update, dash_table
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc
from demo_services import allowed, require, AccessDenied


def create_dash_app(server, runtime):
    app = Dash(__name__, server=server, suppress_callback_exceptions=True,
               assets_folder=str(Path(__file__).resolve().parent.parent / 'assets'),
               title='Report workspace', update_title='Loading…')
    identity, authenticate, reports = runtime.identity, runtime.authenticate, runtime.reports

    def protect(content, roles=None, org=None):
        user = identity()
        if not user:
            return dcc.Location(id='redirect-unauthenticated-user-to-login', pathname='/login')
        if not allowed(user, roles, org):
            return dbc.Alert('403: Forbidden — role or organization not permitted.', color='danger')
        return content()


    def heading(title, subtitle):
        return html.Div([html.P('REPORT WORKSPACE', className='eyebrow'), html.H1(title), html.P(subtitle, className='subtitle')], className='page-heading')


    def home(**query):
        return protect(lambda: [heading('A clear view of your reports', 'Offline fixtures for safe testing.' if runtime.is_demo else 'Reports supplied through the configured adapter.'),
            dbc.Row([dbc.Col(dbc.Card(dbc.CardBody([html.P(label, className='eyebrow'), html.H2(value), html.Small(note)])), md=4)
                for label, value, note in [('MODE', 'Offline' if runtime.is_demo else 'Configured', 'No company services connected' if runtime.is_demo else 'Explicit adapter configuration'), ('ACCESS', identity()['role'], 'Organization '+identity()['org']), ('DATA', 'Synthetic' if runtime.is_demo else 'Adapter', 'Safe fixtures for testing' if runtime.is_demo else 'Server-authorized reports')]], className='g-3'),
            dbc.Card(dbc.CardBody([html.H3('Start testing'), html.P('Review permissions, reports and simulated adapters using the navigation.'), dbc.Button('Open report' if identity()['role'] == 'admin' else 'Check access', href='/page3' if identity()['role'] == 'admin' else '/page2', color='primary')]), className='mt-4')])


    def login_layout(**query):
        if identity():
            return dcc.Location(id='redirect-authenticated-user-to-path', pathname='/')
        return [heading('Welcome back', 'Sign in with an isolated demo identity.' if runtime.is_demo else 'Sign in through the configured identity provider.'), dbc.Card(dbc.CardBody([
            dbc.Label('Username', html_for='username-box'), dbc.Input(id='username-box', autoComplete='username'),
            dbc.Label('Password', html_for='password-box', className='mt-3'), dbc.Input(id='password-box', type='password', autoComplete='current-password'),
            dbc.Button('Sign in', id='login-box', color='primary', className='mt-4'),
            dbc.Alert('Invalid username or password.', id='login-alert', is_open=False, color='danger', className='mt-3'),
            html.Hr(), html.Small('Users: demo-admin, demo-user-a, demo-user-b. Password: demo-only.' if runtime.is_demo else 'Use your approved organization identity. Access is checked on the server.'), dcc.Location(id='redirectHome')]), className='login-card')]


    def logout_layout(**query):
        runtime.logout()
        return dcc.Location(id='logout-redirect', pathname='/login')


    def admin_layout(**query):
        if not runtime.is_demo:
            return protect(lambda: [heading('Adapter laboratory', 'Runtime boundaries and operational readiness.'), dbc.Card(dbc.CardBody([html.H3('Explicit execution only'), html.P('No scheduler or outbound mail is started by a web worker.'), html.P('SQLite state is configured for this host. Real scheduling and company connectors require a reviewed adapter and separate owner process.')]))], ['admin'])
        return protect(lambda: [heading('Adapter laboratory', 'Manual simulations only. No company services are connected.'),
            dbc.Card(dbc.CardBody([html.H3('Run a safe simulation'), html.P('Test a lease, a fixed once-only job and an in-memory mail sink.'),
                dbc.Button('Run simulation', id='adapter-run', color='primary'), html.Pre(id='adapter-result', className='mt-3'),
                html.Small('Optional SQLite state persists claims across local processes. No real scheduler or external delivery is started.')]))], ['admin'])


    def report_layout(**query):
        def content():
            rows = reports.rows(identity())
            totals = {key: sum(row[key] for row in rows) for key in ['revenue', 'cost', 'profit']}
            months = sorted(set(row['period'] for row in rows))
            figure = {'data': [dict(type='bar', x=months, y=[sum(row[key] for row in rows if row['period'] == month) for month in months], name=key.title(), marker={'color': color}) for key, color in [('revenue', '#315fe7'), ('cost', '#b8c8f3')]],
                      'layout': dict(paper_bgcolor='white', plot_bgcolor='white', font={'family': 'Arial, sans-serif', 'color': '#65758b'}, margin=dict(l=50, r=20, t=20, b=40), height=270, barmode='group', legend=dict(orientation='h', y=1.15), yaxis=dict(gridcolor='#edf2f7', title='Demo units' if runtime.is_demo else 'Report units'), xaxis=dict(type='category', showgrid=False))}
            return [heading('Monthly performance', 'Synthetic data, ready to explore.' if runtime.is_demo else 'Authorized data from the configured report adapter.'),
                dbc.Row([dbc.Col(dbc.Card(dbc.CardBody([html.P(key.upper(), className='eyebrow'), html.H2('{:,.0f}'.format(totals[key])), html.Small(('Demo units' if runtime.is_demo else 'Report units') + ' · {} periods'.format(len(months)))])), md=4) for key in ['revenue', 'cost', 'profit']], className='g-3 metric-row'),
                dbc.Card(dbc.CardBody([html.Div([html.H3('Revenue and cost'), html.Small('{} departments'.format(len(set(row['department'] for row in rows))))], className='chart-heading'), dcc.Graph(id='performance-chart', figure=figure, config={'displayModeBar': False, 'responsive': True})]), className='mt-4 chart-card'),
                dbc.Row([dbc.Col(html.H3('Report details'), width=8), dbc.Col([
                    dbc.Button('Refresh', id='report-refresh', outline=True, color='primary', className='me-2'),
                    dbc.Button('Export CSV', id='report-export', color='primary')], width=4, className='text-end report-actions')], className='mt-4 mb-3 report-toolbar'),
                dash_table.DataTable(id='table', columns=[{'name': c.title(), 'id': c} for c in reports.columns], data=rows,
                    editable=True, filter_action='native', sort_action='native', sort_mode='multi', column_selectable='single',
                    row_selectable='multi', row_deletable=True, selected_columns=[], selected_rows=[], page_action='native', page_current=0, page_size=10,
                    style_table={'overflowX': 'auto'}, style_cell={'fontFamily': 'inherit', 'padding': '14px', 'textAlign': 'left'},
                    style_header={'backgroundColor': '#edf2f7', 'fontWeight': '600'}, style_data={'border': '1px solid #edf2f7'}),
                html.P('CSV exports the original fixture, not unsaved edits or the current filter.' if runtime.is_demo else 'CSV exports server-owned report data, not unsaved table edits or the current filter.', className='subtitle mt-3'), dcc.Download(id='report-download')]
        return protect(content, ['admin'])


    routes = {
        '/': home, '/login': login_layout, '/logout': logout_layout,
        '/admin': admin_layout,
        '/page1': lambda: protect(lambda: heading('Page 1', 'Admin-only compatibility route.'), ['admin']),
        '/page2': lambda: protect(lambda: heading('Page 2', 'Role admin/user AND organization A.'), ['admin', 'user'], 'A'),
        '/page3': report_layout,
    }

    @app.callback(Output('_pages_content', 'children'), Output('_pages_store', 'data'),
                  Input('_pages_location', 'pathname'), Input('_pages_location', 'search'))
    def route_page(pathname, search):
        # Query arguments never select adapters or override policy. Known routes are fixed.
        view = routes.get(pathname or '/')
        content = view() if view else dbc.Alert('404: Not found', color='warning')
        return content, {'path': pathname or '/'}


    def server_layout():
        user = identity()
        links = []
        if user:
            links = [('Overview', '/')]
            links += [('Page 1', '/page1'), ('Reports', '/page3'), ('Adapter lab', '/admin')] if user['role'] == 'admin' else [('Page 2', '/page2')]
        return html.Div([dcc.Store(id='side_click'), dcc.Location(id='url'),
            html.Header([html.Div([html.Button('☰', id='btn_sidebar', className='menu-button', **{'aria-label': 'Toggle navigation', 'aria-controls': 'sidebar', 'aria-expanded': 'true'}), html.Strong('Report workspace')], className='brand'),
                html.Div([dbc.Button('Demo info' if runtime.is_demo else 'Workspace info', id='popover-target', outline=True, size='sm'),
                    dbc.Popover('Offline synthetic demo. No LDAP, Oracle, SMTP, live scheduler or DVC integration.' if runtime.is_demo else 'Access is enforced by the configured identity and report adapters. Background execution is separate.', id='popover', target='popover-target', is_open=False),
                    dbc.Button('Sign out' if user else 'Sign in', href='/logout' if user else '/login', color='primary', size='sm', className='ms-2')])], className='topbar'),
            html.Aside([html.P('WORKSPACE', className='eyebrow'), dbc.Nav([dbc.NavLink(label, href=url, active='exact') for label,url in links], vertical=True, pills=True),
                html.Div('OFFLINE DEMO' if runtime.is_demo else 'CONFIGURED WORKSPACE', className='demo-tag')], id='sidebar'),
            html.Main([dbc.Alert('TEST ENVIRONMENT · Synthetic data and simulated services only.' if runtime.is_demo else 'CONFIGURED WORKSPACE · Company integration must be independently validated.', color='info', className='demo-banner'), html.Div([dcc.Location(id='_pages_location', refresh=False), html.Div(id='_pages_content'), dcc.Store(id='_pages_store')])], id='page-content'),
            html.Footer('Reporting foundation · Company integration and browser validation pending')])


    app.layout = server_layout


    @app.callback(Output('redirectHome','pathname'), Output('login-alert','is_open'), Input('username-box','value'), Input('password-box','value'), Input('login-box','n_clicks'))
    def login_button_click(username, password, n):
        if ctx.triggered_id != 'login-box':
            raise PreventUpdate
        return ('/', False) if authenticate(username, password) else (no_update, True)


    @app.callback(Output('popover','is_open'), Input('popover-target','n_clicks'), State('popover','is_open'))
    def toggle_popover(n, opened):
        return not opened if n else opened


    @app.callback(Output('sidebar','style'), Output('page-content','style'), Output('side_click','data'), Input('btn_sidebar','n_clicks'), State('side_click','data'))
    def toggle_sidebar(n, state):
        hidden = bool(n and state == 'SHOW')
        return ({'display':'none'} if hidden else {}, {'marginLeft':'0'} if hidden else {}, 'HIDDEN' if hidden else 'SHOW')


    @app.callback(Output('table','data'), Input('report-refresh','n_clicks'), prevent_initial_call=True)
    def refresh_report(n):
        return reports.rows(identity())


    @app.callback(Output('report-download','data'), Input('report-export','n_clicks'), prevent_initial_call=True)
    def download_report(n):
        return {'content': reports.export(identity()), 'filename':'demo-report.csv' if runtime.is_demo else 'report.csv', 'type':'text/csv'}


    @app.callback(Output('adapter-result','children'), Input('adapter-run','n_clicks'), prevent_initial_call=True)
    def run_simulation(n):
        if not runtime.is_demo:
            raise AccessDenied('Simulation disabled outside demo mode')
        user = identity()
        require(user, ['admin'])
        return runtime.run_simulation(user)

    @app.callback(Output('btn_sidebar', 'aria-expanded'), Input('side_click', 'data'))
    def sidebar_accessibility(state):
        return 'false' if state == 'HIDDEN' else 'true'

    return app
