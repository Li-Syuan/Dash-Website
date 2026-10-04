"""Offline maintenance CLI; app.py remains the sole product launcher."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reporting_workspace.deployment import (
    DeploymentError, backup_set, configuration_preflight, restore_set,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline configuration, SQLite backup and isolated restore checks.')
    commands = parser.add_subparsers(dest='command', required=True)
    preflight = commands.add_parser('preflight', help='Validate explicit JSON configuration, or current environment.')
    preflight.add_argument('--config-json', help='JSON mapping of REPORTING_* settings; values are never printed.')
    backup = commands.add_parser('backup', help='Capture a stopped/quiesced set in a NEW directory.')
    backup.add_argument('--state', required=True)
    backup.add_argument('--destination', required=True)
    backup.add_argument('--release', required=True)
    backup.add_argument('--profile', choices=('demo', 'production'), default='demo')
    backup.add_argument('--quiesced', action='store_true', help='Assert all application requests/workers/writers are stopped.')
    restore = commands.add_parser('restore', help='Validate and restore to a NEW offline-review directory.')
    restore.add_argument('--backup', required=True)
    restore.add_argument('--destination', required=True)
    restore.add_argument('--expected-release', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'preflight':
            environ = dict(os.environ)
            if args.config_json:
                path = Path(args.config_json)
                if not path.is_file() or path.stat().st_size > 1024 * 1024:
                    raise DeploymentError('configuration_file_invalid')
                environ = json.loads(path.read_text(encoding='utf-8'))
            result = configuration_preflight(environ)
            code = 0 if result['status'] == 'valid' else 2
        elif args.command == 'backup':
            manifest = backup_set(args.state, args.destination, args.release,
                                  profile=args.profile, quiesced=args.quiesced)
            result = dict(status='backup-complete', declared_release=manifest['declared_release'],
                          members=len(manifest['members']))
            code = 0
        else:
            result = restore_set(args.backup, args.destination, args.expected_release)
            code = 0
    except DeploymentError as error:
        result, code = dict(status='failed', code=error.code), 2
    except Exception:
        result, code = dict(status='failed', code='maintenance_check_failed'), 2
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == '__main__':
    sys.exit(main())
