from dash import register_page,html,dcc
from flask_login import current_user
from auth import protected
register_page(__name__, path = '/',order = 0)


@protected
def layout():
    if hasattr(current_user,'id') and current_user is not None:
        return html.Div(html.H3(f'Hello {current_user.id}. This is home page.'))
    return dcc.Location(id="logout-redirect", pathname="/login")
    # return html.Div([
    #     html.H3('You will redirect to login page.'),
    #     dcc.Interval(id={'index':'redirectLogin', 'type':'redirect'}, n_intervals=0, interval=500)
    # ])
    