from dash import register_page,html,dcc
from auth import protected,role_permission
register_page(__name__, 
            #   path = '/admin',
              redirect_from='/admin',order = 1)


@protected
@role_permission(role = 'admin')
def layout():
    return html.Div()
    # return html.Div(html.H3(f'Hello {current_user.id}. This is home page.'))
    # return dcc.Location(id = 'admin-url',pathname= '/admin')

    