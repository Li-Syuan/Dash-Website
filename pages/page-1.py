from dash import html, dcc, register_page
from auth import protected,role_permission

register_page(__name__,path = '/page1',order = 1)

@protected
@role_permission(role = 'admin')
def layout():
    return html.Div(
        [
            html.H1("Page 1"),
        ]
    )


