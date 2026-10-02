"""Run from the repository root: python -B -m examples.registry_fixture."""
from dash import html, Input, Output
import dash_bootstrap_components as dbc

from demo_services import AccessDenied
from reporting_workspace.application import create_app
from reporting_workspace.registry import AccessPolicy, PageSpec
from reporting_workspace.ui_pages.shared import heading


POLICY = AccessPolicy.require(roles=('admin', 'user'), org='A')


def fixture_summary(user):
    """Keep data/service permission independent of navigation and callback UI."""
    if not POLICY.allows(user):
        raise AccessDenied('Access denied')
    return 'Synthetic fixture: 3 checks, 0 failures. No company data was queried.'


def layout(runtime):
    return [heading(SPEC.title, 'An opt-in example of one page declaration.'),
            dbc.Button('Refresh fixture', id='example-quality-refresh', color='primary'),
            html.P(fixture_summary(runtime.identity()), id='example-quality-result', className='mt-3')]


def register_callbacks(callbacks, runtime):
    @callbacks.callback(Output('example-quality-result', 'children'), Input('example-quality-refresh', 'n_clicks'),
                        callback_id='example.quality.refresh', policy=POLICY, page_id='example.quality',
                        prevent_initial_call=True)
    def refresh(n):
        return fixture_summary(runtime.identity())


SPEC = PageSpec('example.quality', '/examples/quality', 'Quality fixture', layout, POLICY,
                nav_label='Quality fixture', nav_order=60, register_callbacks=register_callbacks)


if __name__ == '__main__':
    server = create_app(extra_pages=(SPEC,))
    server.extensions['dash_app'].run_server(host='127.0.0.1', port=8050, debug=False)
