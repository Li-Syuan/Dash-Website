from dash import dash_table,register_page
import pandas as pd
from auth import protected,role_permission

register_page(__name__,path = '/page3',order = 3)

df = pd.read_csv('https://raw.githubusercontent.com/plotly/datasets/master/solar.csv')

@protected
@role_permission(role = ['admin'])
def layout():
    return dash_table.DataTable(
        id='table',
        columns=[{"name": i, "id": i} for i in df.columns],
        data=df.to_dict('records'),
        editable=True,
        filter_action="native",
        sort_action="native",
        sort_mode="multi",
        column_selectable="single",
        row_selectable="multi",
        row_deletable=True,
        selected_columns=[],
        selected_rows=[],
        page_action="native",
        page_current= 0,
        page_size= 10,
    )
