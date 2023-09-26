from dash import Input,Output,callback,html,dcc,State,page_container,page_registry
import dash_bootstrap_components as dbc
from flask_login import current_user

SIDEBAR_STYLE = {
    "position": "fixed",
    # "top": 80.5,
    # "bottom": 0,
    "left": 0,
    "width": "16rem",
    "height": "100%",
    "z-index": 1,
    "overflow-x": "hidden",
    "transition": "all 0.5s",
    "padding": "0.5rem 1rem",
    "background-color": "rgb(240,240,240)",
}

SIDEBAR_HIDEN = {
    "position": "fixed",
    # "top": 80.5,
    # "bottom": 0,
    "left": "-16rem",
    "width": "16rem",
    "height": "100%",
    "z-index": 1,
    "overflow-x": "hidden",
    "transition": "all 0.5s",
    "padding": "0rem 0rem",
    "background-color": "rgb(240,240,240)",
}

CONTENT_STYLE = {
    "transition": "margin-left .5s",
    "margin-left": "18rem",
    "margin-right": "2rem",
    "padding": "2rem 1rem",
    "background-color": "rgb(245,245,245)",
}

CONTENT_STYLE1 = {
    "transition": "margin-left .5s",
    "margin-left": "2rem",
    "margin-right": "2rem",
    "padding": "2rem 1rem",
    "background-color": "rgb(245,245,245)",
}

def get_navbar():
    normal_style = {'color' : 'white','backgroundColor':'inherit',  'fontStyle': 'normal'}
    
    path = {}
    for page in page_registry.values():
        if page['path'] == '/':
            path['home'] = page['relative_path']
        if page['path'] == '/login':
            path['login'] = page['relative_path']
        if page['path'] == '/logout':
            path['logout'] = page['relative_path']

    btn = lambda x: dbc.Button(x.capitalize(), outline=False, color="light", href = path[x.lower()])
    current_btn = btn('login')
    if  current_user.is_authenticated:
        current_btn = btn('logout')
    return dbc.NavbarSimple(
        children=[                    
            dbc.Button('Remark', id = 'popover-target', outline=False, color="light"),
            dbc.Popover([
                dbc.PopoverHeader('Designer'),
                    dbc.PopoverBody([
                        html.H6('Author: Leo_Hong'),
                    ])
                ],id = 'popover',is_open = False,trigger = 'hover',target = 'popover-target'),
            current_btn
        ],
        brand = dbc.Row([
            dbc.Col([html.I(className= 'fa fa-bars',id="btn_sidebar")],width=3),
            dbc.Col([html.H3('My App',style =  normal_style )],width=9)
        ]),
        brand_href = '#',
        brand_style = normal_style,
        color = 'rgb(70,160,80)',
        sticky = 'top',
        light = True,
        fluid = True
    )

def get_sidebar():
    for_all_link = []
    page1 = [
            dbc.NavLink(f"{page['name']} - {page['path']}", href=page["relative_path"])
            for page in page_registry.values() if page['order'] == 1
        ]
    page2 = [
            dbc.NavLink(f"{page['name']} - {page['path']}", href=page["relative_path"])
            for page in page_registry.values() if page['order'] == 2
        ]
    if current_user.is_authenticated:
        if current_user.role == 'admin':
            for_all_link = page1
        if current_user.role != 'admin':
            for_all_link = page2
    
    return html.Div([
            html.H4("Outline", className="display-6"),
            html.Hr(),
            html.P("ALL", className="lead"),
            dbc.Nav(for_all_link,vertical=True,pills=True),            
            html.Hr()
        ],
        id="sidebar",
        style=SIDEBAR_STYLE,
    )

def get_content():
    return html.Div(page_container,id="page-content", style=CONTENT_STYLE)

def get_footer():
    return html.Footer(
                "COPYRIGHT © 2023 Leo Hong",
                style={
                    "backgroundColor": 'rgb(146,208,80)',
                    "position": "fixed",
                    "bottom": 0,
                    "width": "100%",
                    "textAlign": "center",
                    "padding": "10px 0px",
                },
            )

def server_layout():
    return html.Div(
        [
            dcc.Store(id='side_click'),
            dcc.Location(id = 'url'),
            get_navbar(),
            get_sidebar(),
            get_content(),
            get_footer()
        ]
    )

@callback(
    Output('popover','is_open'),
    Input('popover-target','n_clicks'),
    State('popover', 'is_open')
)
def toggle_popover(n,is_open):
    if n:
        return not is_open
    return is_open

@callback(
    [
        Output("sidebar", "style"),
        Output("page-content", "style"),
        Output("side_click", "data"),
    ],
    [Input("btn_sidebar", "n_clicks")],
    [State("side_click", "data")]
)
def toggle_sidebar(n, nclick):
    if n:
        if nclick == "SHOW":
            sidebar_style = SIDEBAR_HIDEN
            content_style = CONTENT_STYLE1
            cur_nclick = "HIDDEN"
        else:
            sidebar_style = SIDEBAR_STYLE
            content_style = CONTENT_STYLE
            cur_nclick = "SHOW"
    else:
        sidebar_style = SIDEBAR_STYLE
        content_style = CONTENT_STYLE
        cur_nclick = 'SHOW'

    return sidebar_style, content_style, cur_nclick



