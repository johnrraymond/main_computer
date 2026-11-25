from types import SimpleNamespace
import pytest
from tools.mcel_worker_identity_live_acceptance import (
    PARENT, CHILD, ProbeFailure, verify_canonical_surface, url_class, allow_read,
)


class Surface:
    def __init__(self, status=200, url=PARENT, frame_missing=False, marker_missing=False):
        self.status = status
        self.url = url
        self.frame_missing = frame_missing
        self.marker_missing = marker_missing

    def goto(self, *_args, **_kwargs):
        return None if self.status is None else SimpleNamespace(status=self.status)

    def locator(self, selector):
        if selector.startswith('body'):
            def wait_for(**_kwargs):
                if self.marker_missing:
                    raise TimeoutError('not attached')
            return SimpleNamespace(wait_for=wait_for)
        frame = None if self.frame_missing else self
        return SimpleNamespace(element_handle=lambda: SimpleNamespace(content_frame=lambda: frame))

    def wait_for_url(self, url):
        assert url == CHILD


@pytest.mark.parametrize('status,reason', [(401,'canonical-access-denied'), (403,'canonical-access-denied'), (503,'canonical-http-not-200'), (None,'canonical-no-http-response')])
def test_http_failure_is_distinguished(status, reason):
    result = {'checks': {}}
    with pytest.raises(ProbeFailure, match=reason):
        verify_canonical_surface(Surface(status), result)
    assert result['canonicalHttpStatus'] == status
    assert result['stage'] == 'canonical-http'
    assert not result['checks']


def test_redirect_is_classified_without_disclosing_secret():
    result = {'checks': {}}
    with pytest.raises(ProbeFailure, match='canonical-unexpected-url'):
        verify_canonical_surface(Surface(url='http://127.0.0.1:8765/login?secret=private'), result)
    assert result['canonicalUrlClass'] == 'other-local-route'
    assert 'private' not in str(result)


def test_frame_failure_preserves_completed_checks():
    result = {'checks': {}}
    with pytest.raises(ProbeFailure, match='canonical-child-missing'):
        verify_canonical_surface(Surface(frame_missing=True), result)
    assert result['stage'] == 'canonical-frame-attachment'
    assert result['checks'] == {'canonicalHttp200': True, 'canonicalParentMarker': True, 'canonicalFrameElement': True}


def test_missing_marker_not_confused_with_http_failure():
    result = {'checks': {}}
    with pytest.raises(TimeoutError):
        verify_canonical_surface(Surface(marker_missing=True), result)
    assert result['stage'] == 'canonical-parent-marker'
    assert result['checks'] == {'canonicalHttp200': True}


def test_success_requires_all_canonical_checks():
    result = {'checks': {}}
    surface = Surface()
    assert verify_canonical_surface(surface, result) is surface
    assert result['checks']['canonicalShellAndChild'] is True
    assert len(result['checks']) == 6


def test_allowlist_not_relaxed():
    assert allow_read(PARENT, 'GET') and allow_read(CHILD, 'GET')
    assert not allow_read(PARENT, 'POST')
    assert not allow_read('https://main-computer.localhost/applications/worker','GET')
    assert not allow_read('http://127.0.0.1:8765/login','GET')
    assert url_class('https://main-computer.localhost/login?secret=private') == 'local-https-route'
