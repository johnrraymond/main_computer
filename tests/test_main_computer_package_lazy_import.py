from __future__ import annotations

import subprocess
import sys
import unittest


class MainComputerPackageLazyImportTests(unittest.TestCase):
    def test_package_exports_main_computer_lazily(self) -> None:
        script = (
            "import sys\n"
            "import main_computer\n"
            "assert 'main_computer.router' not in sys.modules\n"
            "assert main_computer.MainComputer.__name__ == 'MainComputer'\n"
            "assert 'main_computer.router' in sys.modules\n"
        )
        subprocess.run([sys.executable, "-c", script], check=True)

    def test_trust_contract_chat_module_executes_without_runpy_warning(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "main_computer.rag_trust_contract_chat", "--stdio"],
            input='{"prompt":"hello","deadline_ms":1000}\n',
            text=True,
            capture_output=True,
            timeout=20,
            check=True,
        )
        self.assertNotIn("RuntimeWarning", completed.stderr)
        self.assertNotIn("found in sys.modules after import of package", completed.stderr)


if __name__ == "__main__":
    unittest.main()
