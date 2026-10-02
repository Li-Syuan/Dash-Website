"""App-scoped shell; page and callback declarations are explicit and centralized."""
from pathlib import Path

from dash import Dash, Input, Output, State, ALL, ctx, dcc, html
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc

from .registry import AccessPolicy, CallbackRegistry, PageRegistry
from .notifications import render_events, wrap_notifications
from .ui_pages import default_pages
from .theme import register_theme, theme_store, theme_toggle
from .governance import CombinedCatalog


PUBLIC = AccessPolicy.public()
_INFRASTRUCTURE_PATHS = ('/healthz', '/readyz', '/assets', '/api', '/demo-api', '/_dash', '/_reload-hash')


def create_dash_app(server, runtime, extra_pages=()):
    app = Dash(__name__, server=server, suppress_callback_exceptions=True,
               assets_folder=str(Path(__file__).resolve().parent.parent / 'assets'),
               title='Report workspace', update_title='Loading…')
    pages = PageRegistry()
    for spec in tuple(default_pages()) + tuple(extra_pages):
        pages.register(spec)
        if any(spec.path == root or spec.path.startswith(root + '/') or
               (root == '/_dash' and spec.path.startswith('/_dash-')) for root in _INFRASTRUCTURE_PATHS):
            raise ValueError('Page path conflicts with an application infrastructure endpoint')
    pages.freeze()
    callbacks = CallbackRegistry(app, pages, runtime.identity)
    server.extensions['page_registry'] = pages
    runtime.page_registry = pages
    runtime.catalog_registry = CombinedCatalog(pages, runtime.managed)
    server.extensions['callback_registry'] = callbacks

    @callbacks.callback(Output('_pages_content', 'children'), Output('_pages_store', 'data'),
                        Input('_pages_location', 'pathname'), Input('_pages_location', 'search'),
                        callback_id='shell.route', policy=PUBLIC)
    def route_page(pathname, search):
        # Routing is public so login/logout work. Page permission is checked
        # before layout construction. Queries cannot choose adapters or policies.
        spec = pages.get(pathname or '/')
        if spec is None:
            return dbc.Alert('404: Not found', color='warning'), {'path': pathname or '/', 'title': 'Not found'}
        user = runtime.identity() if spec.policy.authenticated else None
        if not spec.policy.allows(user):
            content = (dcc.Location(id='redirect-unauthenticated-user-to-login', pathname='/login')
                       if user is None else dbc.Alert('403: Forbidden — role or organization not permitted.', color='danger'))
        else:
            content = spec.layout(runtime)
        return content, {'path': spec.path, 'title': spec.title}

    def server_layout():
        user = runtime.identity()
        links = pages.navigation(user)
        return wrap_notifications(html.Div([
            html.Div(id='workspace-notifications'),
            dcc.Store(id='workspace-notification-seen', storage_type='memory'),
            dcc.Store(id='side_click', data='HIDDEN'), dcc.Location(id='url'), theme_store(),
            html.Header([
                html.Div([html.Strong('Report workspace')], className='brand'),
                html.Nav([dbc.NavLink(spec.nav_label, href=spec.path, active='exact') for spec in links],
                         className='top-navigation', **{'aria-label': 'Workspace sections'}),
                html.Div([
                    dbc.Button('Demo info' if runtime.is_demo else 'Workspace info', id='popover-target', outline=True, size='sm'),
                    dbc.Popover('Offline synthetic demo. No LDAP, Oracle, SMTP, live scheduler or DVC integration.'
                                if runtime.is_demo else 'Access is enforced by the configured identity and report adapters. Background execution is separate.',
                                id='popover', target='popover-target', is_open=False),
                    theme_toggle(),
                    html.Button('Assistant', id='btn_sidebar', className='btn btn-outline-primary btn-sm ms-2',
                                **{'aria-label': 'Open assistant panel', 'aria-controls': 'sidebar', 'aria-expanded': 'false'}),
                    dbc.Button('Sign out' if user else 'Sign in', href='/logout' if user else '/login', color='primary', size='sm', className='ms-2'),
                ]),
            ], className='topbar'),
            html.Aside([
                html.Div([html.H2('Assistant'), html.Button('Close', id='assistant-close', className='btn btn-sm',
                          **{'aria-label': 'Close assistant panel'})], className='assistant-heading'),
                html.Span('Not connected', className='assistant-status'),
                html.P('An approved assistant adapter can be connected here later.', className='subtitle mt-3'),
                html.Div('This panel does not send messages or read report data. No company data is transmitted.',
                         className='empty-state mt-3'),
                dbc.Label('Message', html_for='assistant-message', className='mt-4'),
                dbc.Textarea(id='assistant-message', placeholder='Connect an approved assistant adapter first.', disabled=True),
                dbc.Button('Send', color='primary', disabled=True, className='mt-3'),
            ], id='sidebar', className='assistant-panel', style={'display': 'none'},
               **{'aria-label': 'Assistant panel', 'aria-hidden': 'true'}),
            html.Main([
                dbc.Alert('TEST ENVIRONMENT · Synthetic data and simulated services only.' if runtime.is_demo
                          else 'CONFIGURED WORKSPACE · Company integration must be independently validated.',
                          color='info', className='demo-banner'),
                html.Div([dcc.Location(id='_pages_location', refresh=False), html.Div(id='_pages_content'), dcc.Store(id='_pages_store')]),
            ], id='page-content'),
            html.Footer('Reporting foundation · Company integration and browser validation pending'),
        ]))

    app.layout = server_layout

    @callbacks.callback(Output('popover', 'is_open'), Input('popover-target', 'n_clicks'), State('popover', 'is_open'),
                        callback_id='shell.info', policy=PUBLIC)
    def toggle_popover(n, opened):
        return not opened if n else opened

    @callbacks.callback(Output('sidebar', 'style'), Output('page-content', 'style'), Output('side_click', 'data'),
                        Input('btn_sidebar', 'n_clicks'), Input('assistant-close', 'n_clicks'), State('side_click', 'data'),
                        callback_id='shell.sidebar', policy=PUBLIC)
    def toggle_sidebar(n, close, state):
        hidden = ctx.triggered_id == 'assistant-close' or not n or state == 'SHOW'
        return ({'display': 'none'} if hidden else {'display': 'block'}, {}, 'HIDDEN' if hidden else 'SHOW')

    @callbacks.callback(Output('btn_sidebar', 'aria-expanded'), Output('sidebar', 'aria-hidden'),
                        Output('btn_sidebar', 'aria-label'), Input('side_click', 'data'),
                        callback_id='shell.sidebar_accessibility', policy=PUBLIC)
    def sidebar_accessibility(state):
        opened = state == 'SHOW'
        return ('true' if opened else 'false', 'false' if opened else 'true',
                'Close assistant panel' if opened else 'Open assistant panel')

    @callbacks.callback(Output('workspace-notifications', 'children'), Output('workspace-notification-seen', 'data'),
                        Input({'type': 'workspace-notify', 'action': ALL}, 'data'),
                        State('workspace-notification-seen', 'data'), callback_id='shell.notifications',
                        policy=AccessPolicy.require(), prevent_initial_call=True)
    def show_notifications(events, seen):
        messages, next_seen = render_events(events, seen, runtime.identity())
        if not messages:
            raise PreventUpdate
        return messages, next_seen

    for spec in pages.pages:
        if spec.register_callbacks is not None:
            spec.register_callbacks(callbacks.for_page(spec.page_id), runtime)
    register_theme(callbacks)
    callbacks.freeze()
    return app
