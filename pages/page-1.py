from dash import html, dcc, register_page
from .forbidden_403 import check_role,forbidden

register_page(__name__,path = '/page1')

def layout():
    if check_role('admin'):
        return forbidden()
    return html.Div(
        [
            html.H1("Page 1"),
            dcc.Dropdown(
                id="page-1-dropdown",
                options=[{"label": i, "value": i} for i in ["LA", "NYC", "MTL"]],
                value="LA",
            )
        ]
    )


