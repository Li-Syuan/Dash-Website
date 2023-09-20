from dash import register_page,html,Output,Input,callback,dcc,ctx,no_update
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc
from app_server import User,login_user,user_db

register_page(__name__, path = '/login',order = 101)

def username_password():
    return  dbc.Card(
        dbc.CardBody([
        html.H4("Welcome to my app.", className="display-6"),
        html.Hr(),
        dbc.Label('Username'),
        dbc.Input(type = 'text', id = 'username-box',name = 'username', placeholder = 'Enter username.'),
        dbc.FormText('Please enter your username.',color = 'secondary'),
        html.Br(),
        dbc.Label('Password'),
        dbc.Input(type = 'password', id = 'password-box',name = 'password', placeholder = 'Enter password.'),
        dbc.FormText('Please enter your password.',color = 'secondary'),
        html.Br(),
        dbc.Button('Login',type="submit", id = 'login-box', outline=False, color="secondary"),
        dbc.Alert('Invalid username and password.',id = 'login-alert',is_open = False, color = 'danger'),
        dcc.Location(id = 'redirectHome')
    ]))

def layout():
    return dbc.Container(
        fluid = True,
        children = [
            dbc.Col(username_password(),width = 6)
        ],
    )

@callback(
    Output('redirectHome','pathname'),
    Output('login-alert','is_open'),
    Input('username-box','value'),
    Input('password-box','value'),
    Input('login-box','n_clicks'),
)
def login_button_click(username,password,n):
    if ctx.triggered_id != 'login-box':
        raise PreventUpdate
    else:
        if username not in user_db.keys():
            return no_update,True
        elif user_db.get(username).password == password:
            login_user(User(username))
            return '/',False 
        else:
            return no_update,True
