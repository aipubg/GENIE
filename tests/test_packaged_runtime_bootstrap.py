import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PYTHON = ROOT / "backend-dist" / "backend-runtime" / "python" / "python.exe"
RUNTIME_APP = ROOT / "backend-dist" / "backend-runtime" / "app"


@unittest.skipUnless(os.name == "nt", "the packaged backend runtime is Windows-only")
class PackagedRuntimeBootstrapTests(unittest.TestCase):
    def test_embedded_python_processes_pywin32_paths_before_backend_imports(self):
        if not RUNTIME_PYTHON.is_file() or not RUNTIME_APP.is_dir():
            self.skipTest("packaged backend runtime is not present")
        code = (
            "import sys; "
            f"sys.path.insert(0, {str(RUNTIME_APP)!r}); "
            "import backend_entry; backend_entry.bootstrap_paths(); "
            "import win32api, pywinauto; "
            "from pywinauto import Desktop; "
            "assert Desktop is not None; "
            "print('READY', win32api.__file__)"
        )
        result = subprocess.run(
            [str(RUNTIME_PYTHON), "-c", code], capture_output=True, text=True,
            timeout=10, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("READY", result.stdout)


if __name__ == "__main__":
    unittest.main()
