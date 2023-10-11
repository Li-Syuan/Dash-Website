from dash import html,Dash,dcc,callback, Output, Input, State
from dash.exceptions import PreventUpdate
from datetime import datetime
import pandas as pd
import dash_bootstrap_components as dbc
import dash_mantine_components as dmc
from dash_mantine_react_table import DashMantineReactTable
from typing import List

app = Dash(__name__, 
        suppress_callback_exceptions=True,
        prevent_initial_callbacks=True,
        external_stylesheets=[dbc.themes.CERULEAN]
    )

column_name = ['First Name','Email','Test','Time']
column_type = ['Text','Text','Number','Time']

class data_block:
    def __init__(self, suffix: str = '', column: List[str] = []):
        self.suffix = suffix
        self.column = column

        self.modal_id = 'modal' + suffix
        self.modal_open_id = 'modal-open' + suffix
        self.modal_close_id = 'modal-close' + suffix
        self.modal_submit_id = 'modal-submit' + suffix


    def get_callback_block(self):
        return [
            Output(self.modal_id, 'is_open')
        ],[
            Input(self.modal_open_id,'n_clicks'),
            Input(self.modal_close_id,'n_clicks')
        ],[
            State(self.modal_id,'is_open')
        ]

    def _get_keyin_ModalBody(self):
        block = []
        for col in self.column:
            block.append(
                dmc.TextInput(
                    label = col,
                    id = col.lower().replace(' ','_') + self.suffix,
                    placeholder='Type ' + col.lower()
                )
            )
        return dbc.ModalBody(block)
    
    def _get_ModalFooter(self):
        return dbc.ModalFooter([
            dmc.Button("submit",id = self.modal_submit_id, variant="gradient",n_clicks = 0),
            dmc.Button('close',id = self.modal_close_id, color = 'red',n_clicks = 0)
        ])

    def get_ModalBlock(self,title = 'Add new data'):
        return (
            dbc.Button(title, id = self.modal_open_id,n_clicks = 0),
            dbc.Modal(
                children = [
                    dbc.ModalHeader(dbc.ModalTitle(title)),
                    self._get_keyin_ModalBody(),
                    self._get_ModalFooter()
                ],
                id = self.modal_id,
                is_open = False,
            )
        )
    
block = data_block(suffix = '-test',column= column_name) 

def serve_layout():   
    return html.Div([

        dmc.Container([
            dmc.Title('CRUD App',order = 1),
            *block.get_ModalBlock()
        ])
    ])

app.layout = serve_layout

@callback(
    *block.get_callback_block()
)
def toggle_modal(n1,n2,is_open):
    if n1 or n2:
        return not is_open,
    return is_open,

if __name__ == '__main__':
    app.run_server(debug=True)
