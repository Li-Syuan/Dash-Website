from dash import register_page,html,dcc,callback,Output,Input,ALL,no_update,page_registry
import dash_bootstrap_components as dbc
from flask_login import logout_user, current_user

register_page(__name__, path = '/logout',order = 102)

def layout():
    if current_user.is_authenticated:
        logout_user()
    return html.Div(
        [
            html.Div(html.H3(f"You have been logged out")),
            dcc.Interval(id={'index':'redirectLogin', 'type':'redirect'}, n_intervals=0, interval=500)
        ]
    )

@callback(
    Output('url', 'pathname'),
    Input({'index': ALL, 'type': 'redirect'}, 'n_intervals')
)
def refresh(n):
    ### logout redirect
    for page in page_registry.values():
        if page['path'] == '/login':
            path = page['relative_path']
    if n:
        if not n[0]:
            return no_update
        else:
            return path