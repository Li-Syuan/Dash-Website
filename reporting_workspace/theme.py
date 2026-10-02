"""Browser-only color preference and presentation callbacks.

The memory Store contains only a color scheme and click bookkeeping. JavaScript
persists an explicit light/dark choice under ``workspace.theme``; nothing here
reads identity, changes a report, or creates a server-side preference record.
"""

from dash import ClientsideFunction, Input, Output, State, dcc, html

from .registry import AccessPolicy


PROVIDER_ID = 'workspace-mantine-provider'
STORE_ID = 'workspace-theme-state'
TOGGLE_ID = 'workspace-theme-toggle'


def theme_store():
    """Return a fresh, shell-scoped memory Store, never a persisted user Store."""
    return dcc.Store(id=STORE_ID, storage_type='memory')


def theme_toggle():
    """Native keyboard-operable icon button; JavaScript supplies the live state."""
    return html.Button(
        '\u263e', id=TOGGLE_ID, type='button', n_clicks=0,
        className='workspace-theme-toggle', title='Switch to dark mode',
        **{'aria-label': 'Switch to dark mode', 'aria-pressed': 'false'},
    )


def register_theme(callbacks):
    """Register with the app-owned registry before it freezes.

    The client-only callbacks are explicitly public, presentation-only, and
    cannot be invoked through the server callback endpoint. Graph ID is an Input
    so a newly routed report receives the current theme without fetching data.
    """
    callbacks.clientside_callback(
        ClientsideFunction(namespace='workspace_theme', function_name='sync'),
        Output(PROVIDER_ID, 'theme'), Output(STORE_ID, 'data'),
        Output(TOGGLE_ID, 'children'), Output(TOGGLE_ID, 'aria-label'),
        Output(TOGGLE_ID, 'aria-pressed'), Output(TOGGLE_ID, 'title'),
        Input(TOGGLE_ID, 'n_clicks'), State(STORE_ID, 'data'),
        State(PROVIDER_ID, 'theme'),
        callback_id='shell.theme', policy=AccessPolicy.public(),
    )
    callbacks.clientside_callback(
        ClientsideFunction(namespace='workspace_theme', function_name='figure'),
        Output('performance-chart', 'figure'),
        Input(STORE_ID, 'data'), Input('performance-chart', 'id'),
        State('performance-chart', 'figure'),
        callback_id='shell.theme_chart', policy=AccessPolicy.public(),
    )
