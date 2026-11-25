import base64
import hashlib
import hmac
import json
import pytest
from tools import mcel_secure_functional_smoke as smoke


def test_exact_https_boundary():
    assert smoke.allowed(smoke.ORIGIN + smoke.VERIFY, 'POST')
    assert smoke.allowed(smoke.ORIGIN + smoke.WORKER, 'GET')
    assert not smoke.allowed(smoke.ORIGIN + smoke.WORKER, 'POST')
    assert not smoke.allowed(smoke.ORIGIN + '/api/other', 'GET')
    assert not smoke.allowed('http://127.0.0.1:8765' + smoke.VERIFY, 'POST')
    assert not smoke.allowed(smoke.ORIGIN + '.evil.test' + smoke.VERIFY, 'POST')
    assert not smoke.allowed(smoke.ORIGIN + smoke.VERIFY + '?x=1', 'POST')
    assert not smoke.allowed('https://elsewhere.test' + smoke.VERIFY, 'POST')


def test_proof_signature_matches_protocol(tmp_path):
    key = bytes(range(32))
    path = tmp_path / 'proof.json'
    path.write_text(json.dumps({'type': 'mc-proof-v2', 'key_b64': base64.urlsafe_b64encode(key).decode().rstrip('=')}))
    challenge = {'algorithm': 'HMAC-SHA256', 'challenge_id': 'abc', 'nonce': 'xyz'}
    signed = smoke.proof_signature(path, challenge)
    expected = hmac.digest(key, b'MC-AUTH-BOOTSTRAP-V1\nabc\nxyz\n', hashlib.sha256)
    assert signed == {'challenge_id': 'abc', 'signature': base64.urlsafe_b64encode(expected).decode().rstrip('=')}
    with pytest.raises(smoke.SmokeFailure):
        smoke.proof_signature(path, {**challenge, 'nonce': 'bad\nnonce'})
    with pytest.raises(smoke.SmokeFailure):
        smoke.proof_signature(path, {**challenge, 'algorithm': 'other'})
