from dash import html, dcc, register_page
# from .forbidden_403 import check_role,forbidden
from auth import protected

register_page(__name__,path = '/page1')

@protected
def layout():
    # if check_role('admin'):
        # return forbidden()
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


