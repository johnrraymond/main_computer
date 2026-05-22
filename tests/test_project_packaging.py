import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


ROOT = Path(__file__).resolve().parents[1]


class ProjectPackagingTests(unittest.TestCase):
    def test_setuptools_discovery_is_limited_to_main_computer(self) -> None:
        pyproject_path = ROOT / "pyproject.toml"
        text = pyproject_path.read_text(encoding="utf-8")

        if tomllib is not None:
            data = tomllib.loads(text)
            find_config = data["tool"]["setuptools"]["packages"]["find"]
            include = find_config["include"]
            exclude = find_config["exclude"]
            harness_extra = data["project"]["optional-dependencies"]["harness"]
        else:
            include = text
            exclude = text
            harness_extra = text

        self.assertIn("main_computer*", include)
        self.assertIn("pretty_docs*", exclude)
        self.assertTrue("harness_output*" in exclude or "harness_output_*" in exclude)
        self.assertTrue("diagnostics_output*" in exclude or "diagnostics_output_*" in exclude)
        self.assertTrue(any("playwright" in item for item in harness_extra))


if __name__ == "__main__":
    unittest.main()
