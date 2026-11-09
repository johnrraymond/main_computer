import json
from pathlib import Path
from main_computer.mcel_application_packages import build_application_package_catalog


def test_package_catalog_is_valid():
    repo = Path(__file__).resolve().parents[3]
    catalog = build_application_package_catalog(repo)
    assert catalog.ok, [(r.app_id, r.errors) for r in catalog.packages if not r.valid]
    record = next(r for r in catalog.packages if r.app_id == "slh")
    assert record.valid and record.title == "Secure Local Hub"
    manifest = json.loads((repo / "mcel_apps/slh/mcel.app.json").read_text())
    assert manifest["conformance"]["currentMode"] == "structural-only"
