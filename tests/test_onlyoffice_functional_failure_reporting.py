import pytest

from tools.mcel_onlyoffice_full_functional_acceptance import failure_details, reported_cleanup


def broken_cleanup():
    raise RuntimeError('disposable-session-revocation-unverified')


def test_cleanup_cannot_mask_ai_failure():
    result = {'failed_stage': 'real-ai-notebook-response'}
    with pytest.raises(AssertionError, match='AI failure'):
        with reported_cleanup(result, 'browser-close', lambda: None):
            with reported_cleanup(result, 'session-revocation', broken_cleanup):
                raise AssertionError('AI failure')
    assert result['failed_stage'] == 'real-ai-notebook-response'
    assert result['cleanup']['session-revocation']['status'] == 'FAIL'
    assert result['cleanup']['session-revocation']['failure_reason'] == 'disposable-session-revocation-unverified'
    assert result['cleanup']['browser-close']['status'] == 'PASS'


def test_cleanup_alone_fails_gate():
    result = {'failed_stage': None}
    with pytest.raises(RuntimeError, match='disposable-session-revocation-unverified'):
        with reported_cleanup(result, 'session-revocation', broken_cleanup):
            pass
    assert result['failed_stage'] == 'session-revocation'
    assert result['cleanup']['session-revocation']['status'] == 'FAIL'


def test_successful_cleanup_preserves_primary_failure():
    result = {'failed_stage': 'real-ai-notebook-response'}
    with pytest.raises(ValueError, match='primary'):
        with reported_cleanup(result, 'session-revocation', lambda: None):
            raise ValueError('primary')
    assert result['failed_stage'] == 'real-ai-notebook-response'
    assert result['cleanup']['session-revocation']['status'] == 'PASS'


def test_second_cleanup_failure_does_not_mask_first():
    result = {'failed_stage': None}
    with pytest.raises(RuntimeError, match='disposable-session-revocation-unverified'):
        with reported_cleanup(result, 'browser-close', lambda: (_ for _ in ()).throw(ValueError('close failed'))):
            with reported_cleanup(result, 'session-revocation', broken_cleanup):
                pass
    assert result['failed_stage'] == 'session-revocation'
    assert all(v['status'] == 'FAIL' for v in result['cleanup'].values())


def test_error_classification_omits_document_grants():
    details = failure_details(RuntimeError('Page.evaluate: Execution context was destroyed at https://example.invalid/?cap=PRIVATE'))
    assert details == {'failure_class': 'RuntimeError', 'failure_reason': 'execution-context-destroyed'}
