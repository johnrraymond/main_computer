from pathlib import Path
from main_computer.mcel_onlyoffice_application import compile_onlyoffice
from main_computer.mcel_scaffolding.package_validator import validate_package_path

ROOT = Path(__file__).resolve().parents[3]


def test_onlyoffice_paired_package_valid():
    result = validate_package_path(ROOT/'mcel_apps/onlyoffice')
    assert result.ok, result.errors
    ir = compile_onlyoffice(ROOT)
    assert ir['application']['appId'] == 'onlyoffice'
    assert {x['id'] for x in ir['intents']} == {'intent:'+x for x in ('refresh-workbooks','refresh-server-status','select-workbook','open-workbook','reload-editor','create-workbook','upload-workbook','save-now','attach-code')}
