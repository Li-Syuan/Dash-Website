from dash import html, dcc, register_page
from auth import protected,role_permission

register_page(__name__,path = '/page2',order = 2)

@protected
@role_permission(role = ['admin','user'])
@role_permission(org = 'A')
def layout():
    return html.Div(
        [
            html.H1("Page 2"),
        ]
    )


