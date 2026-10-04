"""Explicit app-owner shutdown for workers and long-lived local resources.

Call only after the host has stopped accepting requests, or after a test has
finished using its app. This is deliberately not a Flask request teardown.
"""


def dispose_app(server):
    """Release this app's resources without deleting configured durable state.

    Workers must finish before dependent connections can be closed. A failed
    stop or close propagates to the owner; temporary cleanup never hides it.
    Only the factory-owned TemporaryDirectory objects may remove directories.
    Repeated calls after a successful disposal are harmless.
    """
    extensions = server.extensions
    if extensions.get('_workspace_disposed'):
        return

    for name in ('etl_dispatch', 'job_monitor', 'qa_demo_jobs'):
        stop = getattr(extensions.get(name), 'stop', None)
        if callable(stop) and stop() is False:
            raise RuntimeError('Application worker did not stop.')

    closed = set()
    for name in ('qa_demo_builder', 'qa_demo_crud', 'qa_demo_jobs',
                 'maintenance_registry', 'operations_service',
                 'job_monitor', 'etl_dispatch'):
        resource = extensions.get(name)
        close = getattr(resource, 'close', None)
        if callable(close) and id(resource) not in closed:
            close()
            closed.add(id(resource))

    for name in ('qa_demo_temporary', 'operations_temporary',
                 'etl_dispatch_temporary'):
        temporary = extensions.get(name)
        if temporary is not None:
            temporary.cleanup()

    extensions['_workspace_disposed'] = True
