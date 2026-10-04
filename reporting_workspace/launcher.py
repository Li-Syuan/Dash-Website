"""Explicit development-launcher defaults; never used by the WSGI factory."""
import os
from pathlib import Path


def start_background_services(server):
    """Start only the explicit launcher-owned synthetic workers.

    Application construction and WSGI imports never call this function. If a
    later worker cannot start, stop already-started workers before propagating
    the error so the launcher cannot leave a partial background lifecycle.
    """
    started = []
    try:
        for key in ('job_monitor', 'etl_dispatch'):
            worker = server.extensions.get(key)
            if worker is not None:
                worker.start()
                started.append(worker)
    except Exception:
        for worker in reversed(started):
            worker.stop()
        raise


def stop_background_services(server):
    """Stop both workers, even if one worker fails during shutdown."""
    first_error = None
    for key in ('etl_dispatch', 'job_monitor'):
        worker = server.extensions.get(key)
        if worker is not None:
            try:
                worker.stop()
            except Exception as error:
                if first_error is None:
                    first_error = error
    if first_error is not None:
        raise first_error


def configure_demo_storage(environ=None, instance_directory=None):
    """Give direct demo runs a private persistent database without changing production.

    A supplied state-path value, even an invalid blank, is never silently replaced.
    Tests and WSGI callers use explicit Settings and do not execute this helper.
    """
    source = os.environ if environ is None else environ
    if source.get('REPORTING_MODE', 'demo') != 'demo' or 'REPORTING_STATE_PATH' in source:
        return source.get('REPORTING_STATE_PATH')
    directory = Path(instance_directory) if instance_directory is not None else Path(__file__).resolve().parent.parent / 'instance'
    directory = directory.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = str(directory / 'workspace.sqlite')
    source['REPORTING_STATE_PATH'] = path
    return path
