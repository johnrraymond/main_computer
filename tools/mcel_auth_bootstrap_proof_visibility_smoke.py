"""Exercise the neutral MC proof name and reauthentication on format upgrade.

Everything runs in disposable directories. No real account or proof is accessed.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from main_computer.auth_bootstrap import (
    BootstrapAuthority, PROOF_NAME, PROOF_TYPE, PROOF_LINK_VERSION,
    proof_file_path, provision, recover_deleted_proof_locally,
)


def main() -> int:
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix='mc-auth-proof-picker-') as temporary:
        base = Path(temporary)
        root = base / 'auth-state'
        proof = provision(root)
        original = proof.read_bytes()
        authority_hash = hashlib.sha256((root / 'authority.json').read_bytes()).hexdigest()
        auth = BootstrapAuthority(root)
        session = auth.sessions.issue()
        checks['neutral_AAA_filename'] = proof.name == 'AAA_AUTH_MAIN_COMPUTER.json'
        checks['neutral_proof_type'] = json.loads(original)['type'] == PROOF_TYPE
        checks['private_state_separate'] = (root / 'authority.json').is_file()
        checks['test_proof_is_disposable'] = proof.parent == root
        checks['profile_target_uses_neutral_name'] = (
            proof_file_path(root, profile_root=base / 'Windows User')
            == base / 'Windows User' / PROOF_NAME)
        os.utime(proof, (1000000000, 1000000000))
        checks['idempotent_provision'] = provision(root) == proof and proof.read_bytes() == original
        checks['date_sorted_picker_finds_recent_proof'] = proof.stat().st_mtime > 1000000000
        checks['session_persists_when_proof_unchanged'] = auth.sessions.valid(session)
        obsolete = root / 'AAA_AUTH_UNUSED_MAIN_COMPUTER.json'
        obsolete.write_text(json.dumps({
            'type': 'obsolete-proof-format', 'key_b64': json.loads(original)['key_b64']
        }))
        unrelated = root / 'AAA_AUTH_OTHER_MAIN_COMPUTER.json'
        unrelated.write_text(json.dumps({'type': 'other', 'key_b64': 'B' * 43}))
        provision(root)
        checks['obsolete_same_authority_proof_discarded'] = not obsolete.exists()
        checks['unrelated_proof_not_removed'] = unrelated.is_file()
        unrelated.unlink()

        proof.unlink()
        checks['automatic_reissue_on_provision'] = provision(root) == proof and proof.is_file()
        checks['missing_proof_revokes_old_session'] = not auth.sessions.valid(session)
        checks['reissued_key_is_unchanged'] = (json.loads(proof.read_text())['key_b64']
                                                == json.loads(original)['key_b64'])
        recovered, did_recover = recover_deleted_proof_locally(root)
        checks['recovery_is_idempotent'] = recovered == proof and not did_recover
        checks['no_key_rotation_on_reissue'] = (
            hashlib.sha256((root / 'authority.json').read_bytes()).hexdigest() == authority_hash)
        checks['new_session_can_be_issued'] = auth.sessions.valid(auth.sessions.issue())

        # Simulate pre-upgrade proof-link/session metadata without importing old cookies.
        upgrade = base / 'format-upgrade'
        upgrade_proof = provision(upgrade)
        upgraded_key = json.loads(upgrade_proof.read_text())['key_b64']
        old_digest = hashlib.sha256(b'old-persistent-session').hexdigest()
        (upgrade / 'proof-link.json').write_text(json.dumps({
            'version': 1, 'generation': 'old-generation', 'status': 'active',
            'fingerprint': 'obsolete-format',
        }))
        (upgrade / 'sessions.json').write_text(json.dumps({
            old_digest: {'until': 9999999999, 'generation': 'old-generation'},
        }))
        upgrade_proof.unlink()
        checks['new_proof_reissued_after_format_upgrade'] = provision(upgrade).is_file()
        new_link = json.loads((upgrade / 'proof-link.json').read_text())
        checks['proof_link_format_upgraded'] = new_link['version'] == PROOF_LINK_VERSION
        checks['old_sessions_revoked_on_format_upgrade'] = (
            json.loads((upgrade / 'sessions.json').read_text()) == {}
            and not BootstrapAuthority(upgrade).sessions.valid('old-persistent-session'))
        checks['permanent_key_preserved'] = (
            json.loads(upgrade_proof.read_text())['key_b64'] == upgraded_key)
        checks['new_login_required'] = not BootstrapAuthority(upgrade).sessions.valid('old-persistent-session')

        # An existing corrupted file must never be silently overwritten.
        record = json.loads(proof.read_text())
        record['key_b64'] = 'A' * 43
        proof.write_text(json.dumps(record))
        refused = False
        try:
            provision(root)
        except RuntimeError as exc:
            refused = 'MC_PROOF_AUTHORITY_MISMATCH' in str(exc)
        checks['corrupted_existing_proof_refused'] = refused
        checks['authority_unchanged_after_refusal'] = (
            hashlib.sha256((root / 'authority.json').read_bytes()).hexdigest() == authority_hash)
        page = (Path(__file__).resolve().parents[1] / 'main_computer' / 'web' / 'auth_login.html').read_text()
        checks['login_page_has_default_picker_path'] = (
            r'%USERPROFILE%\AAA_AUTH_MAIN_COMPUTER.json' in page)
        checks['login_page_neutral'] = ('Sign in to Main Computer' in page
                                       and 'Select MC Proof File' in page)

    for name, ok in checks.items():
        print('MC_AUTH_PROOF_VISIBILITY ' + name + ' ' + ('PASS' if ok else 'FAIL'), flush=True)
    failed = [name for name, ok in checks.items() if not ok]
    print('MC_AUTH_PROOF_VISIBILITY SUMMARY ' + json.dumps({
        'status': 'PASS' if not failed else 'FAIL', 'tests': len(checks),
        'failed': failed, 'production_modified': False,
    }), flush=True)
    return int(bool(failed))


if __name__ == '__main__':
    raise SystemExit(main())
