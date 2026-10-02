"""Admin-only diagnostics and offline simulation controls."""
from dash import html, dcc, Input, Output, no_update
import dash_bootstrap_components as dbc
from demo_services import AccessDenied

from ..notifications import notification_store_id
from ..registry import AccessPolicy, PageSpec
from .shared import heading, request_id


POLICY = AccessPolicy.require(roles=('admin',))


def layout(runtime):
    if not runtime.is_demo:
        return [heading(SPEC.title, 'Runtime boundaries and operational readiness.'),
                dbc.Card(dbc.CardBody([
                    html.H3('Explicit execution only'),
                    html.P('No scheduler or outbound mail is started by a web worker.'),
                    html.P('SQLite state is configured for this host. Real scheduling and company connectors require a reviewed adapter and separate owner process.'),
                ]))]
    return [
        heading(SPEC.title, 'Manual simulations only. No company services are connected.'),
        dbc.Card(dbc.CardBody([
            html.H3('Run a safe simulation'),
            html.P('Test a lease, a fixed single-claim job and an in-memory mail sink.'),
            dbc.Button('Run simulation', id='adapter-run', color='primary'),
            html.Pre(id='adapter-result', className='mt-3'),
            html.Small('Optional SQLite state persists claims across local processes. No real scheduler or external delivery is started.'),
            dcc.Store(id=notification_store_id('admin.simulate')),
        ])),
    ]


def register_callbacks(callbacks, runtime):
    @callbacks.callback(Output('adapter-result', 'children'), Output(notification_store_id('admin.simulate'), 'data'),
                        Input('adapter-run', 'n_clicks'), callback_id='admin.simulate',
                        policy=POLICY, page_id='administration', prevent_initial_call=True)
    def run_simulation(n):
        if not runtime.is_demo:
            raise AccessDenied('Simulation disabled outside demo mode')
        try:
            result = runtime.run_simulation_result(runtime.identity())
        except AccessDenied:
            raise
        except Exception:
            return no_update, runtime.notify.error('simulation.failed', request_id=request_id())
        if not result['acquired']:
            event = runtime.notify.warning('simulation.busy', request_id=request_id())
        elif not result['ran']:
            event = runtime.notify.info('simulation.skipped', request_id=request_id())
        else:
            event = runtime.notify.success('simulation.done', request_id=request_id())
        return runtime.simulation_text(result), event


SPEC = PageSpec('administration', '/admin', 'Adapter laboratory', layout, POLICY,
                nav_label='Admin', nav_order=40, register_callbacks=register_callbacks)
