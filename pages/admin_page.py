from dash import register_page,html,dcc
from auth import protected,role_permission
from flask_login import current_user

register_page(__name__, path = '/admin',order = 1)

@protected
@role_permission(role = 'admin')
def layout():
    return html.Div(html.H3(f'Hello {current_user.id}. This is home page.'))





