import os
from pathlib import Path
import tempfile
import unittest

from reporting_workspace.launcher import configure_demo_storage


class DemoLauncherTests(unittest.TestCase):
    def test_direct_demo_uses_private_instance_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'instance'
            env = {}
            result = configure_demo_storage(env, root)
            self.assertEqual(result, str(root / 'workspace.sqlite'))
            self.assertEqual(env['REPORTING_STATE_PATH'], result)
            self.assertTrue(root.is_dir())
            self.assertFalse(Path(result).exists())  # StateStore owns migration/file creation.
            if os.name == 'posix':
                self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_existing_configuration_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'unused'
            for path in ['', str(Path(folder) / 'custom.sqlite')]:
                env = {'REPORTING_STATE_PATH': path}
                self.assertEqual(configure_demo_storage(env, root), path)
                self.assertEqual(env['REPORTING_STATE_PATH'], path)
                self.assertFalse(root.exists())

    def test_production_and_invalid_modes_create_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'unused'
            for mode in ['production', 'invalid']:
                env = {'REPORTING_MODE': mode}
                self.assertIsNone(configure_demo_storage(env, root))
                self.assertNotIn('REPORTING_STATE_PATH', env)
                self.assertFalse(root.exists())
