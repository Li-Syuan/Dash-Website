from dash import register_page,html,dcc
from flask_login import current_user
from auth import protected
register_page(__name__, path = '/',order = 0)


@protected
def layout():
    return html.Div(html.H3(f'Hello {current_user.id}. This is home page.'))

    