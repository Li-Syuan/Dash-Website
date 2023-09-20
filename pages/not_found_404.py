from dash import register_page,html
import dash_bootstrap_components as dbc
register_page(__name__)

layout = dbc.Container(
        [
            html.H1("404: Not found", className="text-danger"),
            html.Hr()
        ]
    )
