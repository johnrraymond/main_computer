from pathlib import Path
from main_computer.mcel_scaffolding.package_validator import validate_package_path

def test_package_catalog_is_valid():
    root = Path(__file__).resolve().parents[1]
    result = validate_package_path(root, expected_app_id='worker')
    assert result.ok, result.errors
