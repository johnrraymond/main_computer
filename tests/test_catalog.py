from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path

from main_computer.catalog import ProjectCatalog


def make_workspace(root: Path) -> None:
    main = root / "main_computer"
    test = root / "main_computer_test"
    production = root / "main_copmputer_production"
    plate = root / "holographic_plate_bundle"

    for project in (main, test, production, plate):
        project.mkdir(parents=True)
        (project / "README.md").write_text(f"# {project.name}\n", encoding="utf-8")

    (test / "pyproject.toml").write_text("[project]\nname='main-computer-test'\n", encoding="utf-8")
    (test / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (test / "TODO.md").write_text("# TODO\nRelease work.\n", encoding="utf-8")
    (test / "missing.txt").write_text(
        "Companion gap inventory for the Main Computer User Requirements Document.\n",
        encoding="utf-8",
    )
    (test / "main_computer").mkdir()
    (test / "main_computer" / "viewport.py").write_text("# viewport\n", encoding="utf-8")
    (test / "revision_control").mkdir()
    (test / "revision_control" / "snapshots").mkdir()
    (test / "debug_assets").mkdir()
    (test / "debug_assets" / "scan-todo.txt").write_text("generated\n", encoding="utf-8")

    (plate / "generate_holographic_plate.py").write_text("def main():\n    return 0\n", encoding="utf-8")


class CatalogTests(unittest.TestCase):
    def test_catalog_lists_projects(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_workspace(root)

            projects = ProjectCatalog(root).list_projects()

        names = [project.name for project in projects]
        self.assertIn("main_computer_test", names)
        main = next(project for project in projects if project.name == "main_computer_test")
        self.assertIn("pyproject.toml", main.markers)

    def test_catalog_context_summary_includes_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_workspace(root)

            summary = ProjectCatalog(root).context_summary()

        self.assertIn("Visible project folders:", summary)
        self.assertIn("Projects:", summary)

    def test_catalog_context_summary_pins_main_computer_projects(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_workspace(root)

            summary = ProjectCatalog(root).context_summary(limit=5)

        self.assertIn("- main_computer ", summary)
        self.assertIn("- main_computer_test ", summary)
        self.assertIn("- main_copmputer_production ", summary)
        self.assertIn("- holographic_plate_bundle ", summary)

    def test_catalog_context_summary_includes_main_computer_file_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_workspace(root)

            summary = ProjectCatalog(root).context_summary(limit=5)

        self.assertIn("Main computer file manifest:", summary)
        self.assertIn("- TODO.md", summary)
        self.assertIn("- missing.txt", summary)
        self.assertIn("- main_computer/viewport.py", summary)
        self.assertIn("- generate_holographic_plate.py", summary)
        self.assertNotIn("revision_control/snapshots", summary)
        self.assertNotIn("debug_assets/scan-todo.txt", summary)

    def test_catalog_context_pack_includes_holographic_plate_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_workspace(root)

            pack = ProjectCatalog(root).build_context_pack("load holographic plate bundle")

        self.assertIn("holographic_plate_bundle", pack.text)
        self.assertIn("generate_holographic_plate.py", pack.text)
        evidence_paths = {item.path for item in pack.evidence}
        self.assertIn("holographic_plate_bundle", evidence_paths)

    def test_catalog_builds_query_context_pack_with_matching_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_workspace(root)

            pack = ProjectCatalog(root).build_context_pack("load the todo and find viewport.py")

        self.assertIn("Deterministic workspace context pack:", pack.text)
        self.assertIn("main_computer_test/main_computer/viewport.py", pack.text)
        self.assertIn("TODO.md", pack.text)
        evidence_paths = {item.path for item in pack.evidence}
        self.assertIn("main_computer", evidence_paths)
        self.assertTrue(any(path.endswith("TODO.md") for path in evidence_paths))
        self.assertTrue(any(path.endswith("viewport.py") for path in evidence_paths))

    def test_catalog_context_pack_does_not_treat_control_words_as_file_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_workspace(root)
            (root / "main_computer" / "activity.py").write_text("# activity\n", encoding="utf-8")
            (root / "main_computer" / "terminal_suggestions.py").write_text("# terminal\n", encoding="utf-8")

            pack = ProjectCatalog(root).build_context_pack(
                "Use a computer mount and request Terminal to list the files in main_computer. "
                "Do not just describe the files."
            )

        self.assertIn("main_computer", pack.text)
        self.assertNotIn("Matched file excerpts:", pack.text)
        self.assertFalse(any(item.kind == "excerpt" for item in pack.evidence))

    def test_catalog_context_pack_auto_includes_missing_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_workspace(root)

            with contextlib.chdir(root / "main_computer_test"):
                pack = ProjectCatalog(root).build_context_pack("hello")

        self.assertIn("Pinned project guidance:", pack.text)
        self.assertIn("main_computer_test/missing.txt", pack.text)
        self.assertIn("Companion gap inventory for the Main Computer User Requirements Document.", pack.text)
        evidence_paths = {item.path for item in pack.evidence}
        self.assertIn("main_computer_test/missing.txt", evidence_paths)


if __name__ == "__main__":
    unittest.main()
