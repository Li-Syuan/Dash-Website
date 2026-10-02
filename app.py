"""Main launcher for login, report catalog, QSL maintenance and report wizard.

Run ``python -B app.py`` and open http://127.0.0.1:8050/login. Only direct
execution selects persistent local demo storage; imports retain factory defaults.
"""
from reporting_workspace.application import create_app


if __name__ == '__main__':
    import os
    from reporting_workspace.launcher import configure_demo_storage

    configure_demo_storage()
    # A 10 MiB QSL upload grows in the Dash JSON/base64 envelope. Preserve any
    # explicit deployment limit; the general factory default remains 1 MiB.
    os.environ.setdefault('REPORTING_MAX_CONTENT_LENGTH', str(15 * 1024 * 1024))


server = create_app()
runtime = server.extensions['workspace']
app = server.extensions['dash_app']
user_db = getattr(runtime.identities, 'user_db', {})


def run():
    """Start the single loopback-only application without debug/reloader mode."""
    monitor = server.extensions.get('job_monitor')
    if monitor is not None:
        monitor.start()
    try:
        app.run_server(host='127.0.0.1', port=8050, debug=False)
    finally:
        if monitor is not None:
            monitor.stop()


if __name__ == '__main__':
    run()
