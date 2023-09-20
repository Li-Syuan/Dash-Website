from dash import register_page,html
import dash_bootstrap_components as dbc
from flask_login import current_user

def check_role(role = 'admin'):
    if current_user is None:
        return True
    if hasattr(current_user,'id'):
        if current_user.role == 'admin':
            return False
        else:
            return True
    return True 


def forbidden():
    return dbc.Container(
        [
            html.H1("403: Forbbiden", className="text-danger"),
            html.Hr()
        ]
    )
