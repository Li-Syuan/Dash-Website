"""Explicit development-launcher defaults; never used by the WSGI factory."""
import os
from pathlib import Path


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
