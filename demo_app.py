"""Legacy app entrypoint backed by an independently configurable application factory."""
from demo_server import server, runtime

app = server.extensions['dash_app']


def run():
    app.run_server(host='127.0.0.1', port=8050, debug=False)
