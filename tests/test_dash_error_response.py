"""Exercise the pinned renderer workaround using native Fetch Response objects."""

from pathlib import Path
import shutil
import subprocess
import unittest


class DashErrorResponseTests(unittest.TestCase):
    def test_native_response_compatibility_boundaries(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node is required for native Fetch Response contracts')
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [node, str(root / 'tests' / 'js' / 'dash_error_response.cjs'),
             str(root / 'assets' / '00_dash_error_response.js')],
            capture_output=True, text=True, encoding='utf-8', timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('dash error response contracts passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
