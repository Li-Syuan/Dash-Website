"""Public login/logout pages with explicit callback policy."""
from dash import html, dcc, Input, Output, ctx, no_update, dash_table
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc
from demo_services import AccessDenied, require
from ..registry import AccessPolicy, PageSpec
from .shared import heading

PUBLIC = AccessPolicy.public()


def login_layout(runtime):
    identity = runtime.identity
    if identity():
        return dcc.Location(id='redirect-authenticated-user-to-path', pathname='/')
    return [heading(LOGIN.title, 'Sign in with an isolated demo identity.' if runtime.is_demo else 'Sign in through the configured identity provider.'), dbc.Card(dbc.CardBody([dbc.Label('Username', html_for='username-box'), dbc.Input(id='username-box', autoComplete='username'), dbc.Label('Password', html_for='password-box', className='mt-3'), dbc.Input(id='password-box', type='password', autoComplete='current-password'), dbc.Button('Sign in', id='login-box', color='primary', className='mt-4'), dbc.Alert('Invalid username or password.', id='login-alert', is_open=False, color='danger', className='mt-3'), html.Hr(), html.Small('Users: demo-admin, demo-user-a, demo-user-b. Password: demo-only.' if runtime.is_demo else 'Use your approved organization identity. Access is checked on the server.'), dcc.Location(id='redirectHome')]), className='login-card')]


def logout_layout(runtime):
    runtime.logout()
    return dcc.Location(id='logout-redirect', pathname='/login')


def register_login_callbacks(callbacks, runtime):
    @callbacks.callback(Output('redirectHome', 'pathname'), Output('login-alert', 'is_open'),
                        Input('username-box', 'value'), Input('password-box', 'value'),
                        Input('login-box', 'n_clicks'), callback_id='login.submit',
                        policy=PUBLIC, page_id='login')
    def login_button_click(username, password, n):
        if ctx.triggered_id != 'login-box':
            raise PreventUpdate
        return ('/', False) if runtime.authenticate(username, password) else (no_update, True)


LOGIN = PageSpec('login', '/login', 'Welcome back', login_layout, PUBLIC,
                 register_callbacks=register_login_callbacks)
LOGOUT = PageSpec('logout', '/logout', 'Sign out', logout_layout, PUBLIC)
