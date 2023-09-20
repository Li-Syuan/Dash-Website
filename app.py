from dash import Dash
from app_config import server_layout
from app_server import server
import dash_bootstrap_components as dbc

app = Dash(
    __name__, 
    server = server, 
    use_pages = True,
    suppress_callback_exceptions=True,
    external_stylesheets = [dbc.themes.SPACELAB,dbc.icons.FONT_AWESOME]
)

app.layout = server_layout

if __name__ == '__main__':
    app.run_server(debug=True)