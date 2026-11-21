"""Smoke acceptance for the current deterministic named-code argument resolver."""
from tools.mcel_semantic_argument_resolver_smoke import run


def test_real_code_editor_argument_matching_smoke():
    report = run()
    assert report['status'] == 'PASS_POLICY_FREE_GRAPH_MATCH'
    assert report['argumentCount'] == 11
    assert report['groups'] == {'execution': 2, 'preview': 2, 'session': 7}
    assert len(report['cases']) == 13
    assert all(case['outcome'] == 'PASS' for case in report['cases'])
