"""Performance changes must retain real ACL verification and revocation."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from main_computer import auth_bootstrap as auth
from main_computer import auth_windows_acl as acl


class PrivateACLContract(unittest.TestCase):
    owner = 'S-1-5-21-1-2-3-1001'

    def rules(self, flags=0):
        return [(0, flags, 0x1F01FF, sid) for sid in
                (self.owner, 'S-1-5-18', 'S-1-5-32-544')]

    def test_only_exact_protected_acl_is_fast(self):
        for directory in (False, True):
            rules = self.rules(3 if directory else 0)
            self.assertTrue(acl._exact_private_rules(0x1000, rules, self.owner, directory=directory))
            self.assertFalse(acl._exact_private_rules(0, rules, self.owner, directory=directory))
            self.assertFalse(acl._exact_private_rules(0x1000, rules[:-1], self.owner, directory=directory))
            for replacement in ((1, rules[0][1], rules[0][2], self.owner),
                                (0, 16, rules[0][2], self.owner),
                                (0, rules[0][1], 1, self.owner),
                                (0, rules[0][1], rules[0][2], 'S-1-1-0')):
                self.assertFalse(acl._exact_private_rules(0x1000, [replacement, *rules[1:]], self.owner, directory=directory))
            self.assertFalse(acl._exact_private_rules(0x1000, rules + [rules[0]], self.owner, directory=directory))

    @unittest.skipUnless(os.name == 'nt', 'real Windows DACL required')
    def test_actual_private_acl_avoids_subprocess_and_rechecks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'private'
            auth._private_folder(root)
            file = root / 'key'
            auth._write_new_private(file, b'disposable')
            owner = auth._sid()
            # Windows must prove the native fast path really matches the ACLs
            # produced by the existing repair mechanism, not just skip it.
            self.assertTrue(acl.already_private(root, owner, directory=True))
            self.assertTrue(acl.already_private(file, owner, directory=False))
            with patch.object(auth, '_command', side_effect=AssertionError('unexpected ACL rewrite')), \
                 patch.object(auth.subprocess, 'run', side_effect=AssertionError('unexpected ACL subprocess')):
                auth._protect(root, directory=True)
                auth._protect(file, directory=False)
            # Deliberately change only a disposable file. The next inspection
            # must see the new DACL and the normal repair must remove the ACE.
            result = auth._command(['icacls.exe', str(file), '/grant', '*S-1-1-0:R'])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(acl.already_private(file, owner, directory=False))
            auth._protect(file, directory=False)
            self.assertTrue(acl.already_private(file, owner, directory=False))

    def test_revocation_visible_to_already_running_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'state'
            auth.provision(root)
            first = auth.BootstrapAuthority(root)
            second = auth.BootstrapAuthority(root)
            token = first.sessions.issue()
            self.assertTrue(second.sessions.valid(token))
            first.sessions.revoke(token)
            self.assertFalse(second.sessions.valid(token))
