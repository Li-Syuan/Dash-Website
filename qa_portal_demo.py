"""Dedicated offline integration demonstration; never imports the production app."""
import argparse
from reporting_workspace.legacy_demo_ui import create_demo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8051)
    parser.add_argument('--data-dir', default='instance/qa_portal_demo',
                        help='Local synthetic database directory; contains history and saved reports.')
    args = parser.parse_args()
    app = create_demo(data_directory=args.data_dir)
    print('Synthetic QA Portal: http://127.0.0.1:{}/QA_portal/'.format(args.port))
    try:
        app.run_server(host='127.0.0.1', port=args.port, debug=False)
    finally:
        app.server.extensions['qa_demo_builder'].close()
        app.server.extensions['qa_demo_crud'].close()
        app.server.extensions['qa_demo_temporary'].cleanup()


if __name__ == '__main__':
    main()
