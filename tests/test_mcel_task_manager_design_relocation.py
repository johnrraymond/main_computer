"""Prevent the old root-level Task Manager design directory from becoming a dependency again."""
from pathlib import Path
import yaml

from main_computer.mcel_application_packages import build_application_package_catalog

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tools/fixtures/mcel/task-manager"


def test_relocated_design_pair_is_complete_and_cross_referenced():
    design = yaml.safe_load((FIXTURE / "design.yaml").read_text(encoding="utf-8"))
    contract = yaml.safe_load((FIXTURE / "frob_crosscheck.yaml").read_text(encoding="utf-8"))
    assert design["schema"] == "main-computer.mcel.application-design.v1"
    assert design["application"]["id"] == "task-manager"
    assert contract["schema"] == "main-computer.mcel.layout-frob-crosscheck.v1"
    assert contract["design_source"] == "tools/fixtures/mcel/task-manager/design.yaml"


def test_catalog_stays_valid_even_with_design_fixture_present():
    catalog = build_application_package_catalog(ROOT)
    assert catalog.ok
    assert "task-manager" not in {record.directory_name for record in catalog.packages}


def test_export_includes_new_fixture_tree_without_old_root_dependency():
    export = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")
    assert '"tools",' in export  # recursively copied through Copy-ExportDirectoryPruned
    assert '"tools/patching/"' in export  # patch history intentionally excluded
