"""Default launcher: loopback-only offline demo with private local SQLite state."""
if __name__ == '__main__':
    from reporting_workspace.launcher import configure_demo_storage
    configure_demo_storage()
    from demo_app import run
    run()
else:
    # Keep the previous import contract without invoking launcher defaults.
    from demo_app import app, server, run
