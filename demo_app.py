"""Python 3.8 / Dash 2.9.1 / Bootstrap 1.4.1 compatible offline demo."""
from dash import Dash, Input, Output, State, callback, ctx, dcc, html, no_update
from dash import page_container, register_page, dash_table
from dash.exceptions import PreventUpdate
from flask_login import logout_user
import dash_bootstrap_components as dbc
from demo_server import server, identity, authenticate, reports
from demo_services import allowed, require, DemoLocks, DemoScheduler, MailSink

app = Dash(__name__, server=server, use_pages=True, pages_folder='', suppress_callback_exceptions=True)
locks, scheduler, mail = DemoLocks(), DemoScheduler(), MailSink()


def protect(content, roles=None, org=None):
    user = identity()
    if not user:
        return dcc.Location(id='redirect-unauthenticated-user-to-login', pathname='/login')
    if not allowed(user, roles, org):
        return dbc.Alert('403: Forbidden — role or organization not permitted.', color='danger')
    return content()


def heading(title, subtitle):
    return html.Div([html.P('REPORT WORKSPACE', className='eyebrow'), html.H1(title), html.P(subtitle, className='subtitle')], className='page-heading')


def home():
    return protect(lambda: [heading('A clear view of your reports', 'A compatible starting point, built with offline demo data.'),
        dbc.Row([dbc.Col(dbc.Card(dbc.CardBody([html.P(label, className='eyebrow'), html.H2(value), html.Small(note)])), md=4)
            for label, value, note in [('MODE', 'Offline', 'No company services connected'), ('ACCESS', identity()['role'], 'Organization '+identity()['org']), ('DATA', 'Synthetic', 'Safe fixtures for testing')]], className='g-3'),
        dbc.Card(dbc.CardBody([html.H3('Start testing'), html.P('Review permissions, reports and simulated adapters using the navigation.'), dbc.Button('Open report', href='/page3', color='primary')]), className='mt-4')])


def login_layout():
    if identity():
        return dcc.Location(id='redirect-authenticated-user-to-path', pathname='/')
    return [heading('Welcome back', 'Sign in with an isolated demo identity.'), dbc.Card(dbc.CardBody([
        dbc.Label('Username'), dbc.Input(id='username-box', autoComplete='username'),
        dbc.Label('Password', className='mt-3'), dbc.Input(id='password-box', type='password', autoComplete='current-password'),
        dbc.Button('Sign in', id='login-box', color='primary', className='mt-4'),
        dbc.Alert('Invalid username or password.', id='login-alert', is_open=False, color='danger', className='mt-3'),
        html.Hr(), html.Small('Users: demo-admin, demo-user-a, demo-user-b. Password: demo-only.'), dcc.Location(id='redirectHome')]), className='login-card')]


def logout_layout():
    logout_user()
    return dcc.Location(id='logout-redirect', pathname='/login')


def admin_layout():
    return protect(lambda: [heading('Adapter laboratory', 'Manual simulations only. No company services are connected.'),
        dbc.Card(dbc.CardBody([html.H3('Run a safe simulation'), html.P('Test a lease, a fixed once-only job and an in-memory mail sink.'),
            dbc.Button('Run simulation', id='adapter-run', color='primary'), html.Pre(id='adapter-result', className='mt-3'),
            html.Small('Process-local demo state. Production file locks and multi-worker scheduling require company adapters.')]))], ['admin'])


def report_layout():
    def content():
        return [heading('Monthly performance', 'Synthetic fixture • table edits and deleted rows are temporary.'),
            dbc.Row([dbc.Col(html.H3('Revenue and cost'), width=8), dbc.Col([
                dbc.Button('Refresh', id='report-refresh', outline=True, color='primary', className='me-2'),
                dbc.Button('Export CSV', id='report-export', color='primary')], width=4, className='text-end')], className='mb-3'),
            dash_table.DataTable(id='table', columns=[{'name': c.title(), 'id': c} for c in reports.columns], data=reports.rows(identity()),
                editable=True, filter_action='native', sort_action='native', sort_mode='multi', column_selectable='single',
                row_selectable='multi', row_deletable=True, selected_columns=[], selected_rows=[], page_action='native', page_current=0, page_size=10,
                style_table={'overflowX': 'auto'}, style_cell={'fontFamily': 'inherit', 'padding': '14px', 'textAlign': 'left'},
                style_header={'backgroundColor': '#edf2f7', 'fontWeight': '600'}, style_data={'border': '1px solid #edf2f7'}),
            html.P('CSV exports the original fixture, not unsaved edits or the current filter.', className='subtitle mt-3'), dcc.Download(id='report-download')]
    return protect(content, ['admin'])


register_page('demo.home', path='/', order=0, layout=home)
register_page('demo.login', path='/login', order=101, layout=login_layout)
register_page('demo.logout', path='/logout', order=102, layout=logout_layout)
register_page('demo.admin', path='/admin', order=1, layout=admin_layout)
register_page('demo.page1', path='/page1', order=1, layout=lambda: protect(lambda: heading('Page 1', 'Admin-only compatibility route.'), ['admin']))
register_page('demo.page2', path='/page2', order=2, layout=lambda: protect(lambda: heading('Page 2', 'Role admin/user AND organization A.'), ['admin', 'user'], 'A'))
register_page('demo.page3', path='/page3', order=3, layout=report_layout)
register_page('demo.not_found_404', layout=lambda: dbc.Alert('404: Not found', color='warning'))


def server_layout():
    user = identity()
    links = []
    if user:
        links = [('Overview', '/')]
        links += [('Page 1', '/page1'), ('Reports', '/page3'), ('Adapter lab', '/admin')] if user['role'] == 'admin' else [('Page 2', '/page2')]
    return html.Div([dcc.Store(id='side_click'), dcc.Location(id='url'),
        html.Header([html.Div([html.Button('☰', id='btn_sidebar', className='menu-button'), html.Strong('Report workspace')], className='brand'),
            html.Div([dbc.Button('Demo info', id='popover-target', outline=True, size='sm'),
                dbc.Popover('Offline synthetic demo. No LDAP, Oracle, SMTP, live scheduler or DVC integration.', id='popover', target='popover-target', is_open=False),
                dbc.Button('Sign out' if user else 'Sign in', href='/logout' if user else '/login', color='primary', size='sm', className='ms-2')])], className='topbar'),
        html.Aside([html.P('WORKSPACE', className='eyebrow'), dbc.Nav([dbc.NavLink(label, href=url, active='exact') for label,url in links], vertical=True, pills=True),
            html.Div('OFFLINE DEMO', className='demo-tag')], id='sidebar'),
        html.Main([dbc.Alert('TEST ENVIRONMENT · Synthetic data and simulated services only.', color='info', className='demo-banner'), page_container], id='page-content'),
        html.Footer('Compatible demo v1 · Company integration pending')])


app.layout = server_layout


@callback(Output('redirectHome','pathname'), Output('login-alert','is_open'), Input('username-box','value'), Input('password-box','value'), Input('login-box','n_clicks'))
def login_button_click(username, password, n):
    if ctx.triggered_id != 'login-box':
        raise PreventUpdate
    return ('/', False) if authenticate(username, password) else (no_update, True)


@callback(Output('popover','is_open'), Input('popover-target','n_clicks'), State('popover','is_open'))
def toggle_popover(n, opened):
    return not opened if n else opened


@callback(Output('sidebar','style'), Output('page-content','style'), Output('side_click','data'), Input('btn_sidebar','n_clicks'), State('side_click','data'))
def toggle_sidebar(n, state):
    hidden = bool(n and state == 'SHOW')
    return ({'display':'none'} if hidden else {}, {'marginLeft':'0'} if hidden else {}, 'HIDDEN' if hidden else 'SHOW')


@callback(Output('table','data'), Input('report-refresh','n_clicks'), prevent_initial_call=True)
def refresh_report(n):
    return reports.rows(identity())


@callback(Output('report-download','data'), Input('report-export','n_clicks'), prevent_initial_call=True)
def download_report(n):
    return {'content': reports.export(identity()), 'filename':'demo-report.csv', 'type':'text/csv'}


@callback(Output('adapter-result','children'), Input('adapter-run','n_clicks'), prevent_initial_call=True)
def run_simulation(n):
    user = identity()
    require(user, ['admin'])
    acquired = locks.acquire('demo-report', user['id'])
    released = locks.release('demo-report', user['id']) if acquired else False
    ran = scheduler.run_once('demo-mail', 'fixture-1', lambda: mail.send('Demo report ready', 'Synthetic fixture only', ['tester@example.invalid']))
    return 'Lease acquired: {}\nLease released: {}\nFixed job executed: {}\nMessages captured: {}\nNo external delivery.'.format(acquired, released, ran, len(mail.messages))


def run():
    app.run_server(host='127.0.0.1', port=8050, debug=False)
