"""Regression for the production application shell missing L18 runtime modules."""
from tools.mcel_lifecycle_shell_runtime_frob import shell_includes,REQUIRED,verify


def test_live_app_includes_all_required_l18_modules_in_dependency_order():
    includes=shell_includes()
    assert all(includes.count(module)==1 for module in REQUIRED)
    assert [includes.index(module) for module in REQUIRED] == sorted(includes.index(module) for module in REQUIRED)


def test_l18_mounts_using_production_app_include_list(tmp_path):
    report=verify(tmp_path/'runtime')
    assert report['status']=='PASS',report
    assert len(report['cases'])==2
    assert all(len(case['cases'])==18 for case in report['cases'])
