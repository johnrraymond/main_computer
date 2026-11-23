"""Shadow ONLYOFFICE storage gate regression tests (no production changes)."""
from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from tools import mcel_onlyoffice_shadow_revision_smoke as smoke
from tools.mcel_onlyoffice_shadow_revision import ShadowRevisionFence, RevisionConflict, RevisionInvalid
from tools import mcel_onlyoffice_real_editor_lab as lab


class ShadowRevisionContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='mc-oo-shadow-regression-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.file=self.root/'Book.xlsx'
        self.file.write_bytes(smoke.create_xlsx('ORIGINAL'))
        self.fence=ShadowRevisionFence(self.root)

    def test_issued_base_cannot_be_replayed_after_commit(self):
        base=self.fence.snapshot(self.file)
        self.fence.commit(self.file,base,smoke.create_xlsx('FIRST'))
        with self.assertRaises(RevisionConflict):
            self.fence.commit(self.file,base,smoke.create_xlsx('OLDER'))
        self.assertEqual(smoke.read_a1(self.file.read_bytes()),'FIRST')

    def test_atomic_replacement_failure_does_not_leak_temp(self):
        base=self.fence.snapshot(self.file)
        with patch('tools.mcel_onlyoffice_shadow_revision.os.replace',side_effect=OSError('injected')):
            with self.assertRaises(OSError):
                self.fence.commit(self.file,base,smoke.create_xlsx('FAILED'))
        self.assertEqual(smoke.read_a1(self.file.read_bytes()),'ORIGINAL')
        self.assertEqual(list(self.root.glob('.Book.xlsx.*.tmp')),[])

    def test_invalid_and_external_paths_rejected(self):
        outside=self.root.parent/(self.root.name+'-external.xlsx')
        outside.write_bytes(smoke.create_xlsx('OUTSIDE'))
        self.addCleanup(outside.unlink,missing_ok=True)
        with self.assertRaises(RevisionInvalid):
            self.fence.commit(outside,self.fence.snapshot(self.file),smoke.create_xlsx('ATTACK'))
        with self.assertRaises(RevisionInvalid):
            self.fence.commit(self.file,'x'*64,smoke.create_xlsx('ATTACK'))

    def test_testonly_compose_mounts_shadow_readonly(self):
        base=lab.manifest(self.root,54321,'a'*43,'b'*45,'latest',shadow_revision=False)
        shadow=lab.manifest(self.root,54321,'a'*43,'b'*45,'latest',shadow_revision=True)
        fixture=shadow['services']['fixture']
        mounts=[m for m in fixture['volumes'] if m['target']=='/lab/mcel_onlyoffice_shadow_revision.py']
        self.assertEqual(len(mounts),1)
        self.assertTrue(mounts[0]['read_only'])
        self.assertEqual(fixture['environment']['LAB_SHADOW_REVISION_FENCE'],'1')
        self.assertFalse(any(m['target']=='/lab/mcel_onlyoffice_shadow_revision.py'
                             for m in base['services']['fixture']['volumes']))
        self.assertEqual(shadow['services']['onlyoffice']['networks'],['sandbox'])
        self.assertEqual(shadow['services']['fixture']['networks'],['sandbox'])
        self.assertEqual(shadow['services']['proxy']['ports'],[{'target':443,'published':54321,
                           'host_ip':'127.0.0.1','protocol':'tcp'}])

    def test_certification_requires_genuine_and_replay_evidence(self):
        source=(lab.ROOT/'tools'/'mcel_onlyoffice_pre_cutover_certification.py').read_text(encoding='utf8')
        self.assertIn('shadow-real-editor-callback-committed-through-revision-fence PASS',source)
        self.assertIn('shadow-replayed-old-grant-rejected-after-real-editor-save PASS',source)
        self.assertIn("'shadow-revision-real-editor-candidate'",source)
        self.assertIn('source-unchanged-through-experiments',source)

    def test_fixture_requires_jwt_before_revision_guard(self):
        fixture=(lab.ROOT/'tools'/'mcel_onlyoffice_real_editor_fixture.py').read_text(encoding='utf8')
        self.assertLess(fixture.index("if not jwt:"),fixture.index('issued_base = ACTIVE_KEYS'))
        self.assertIn('obj._onlyoffice_atomic_write = guarded_write',fixture)
        self.assertIn('REVISION_FENCE.commit(TARGET,old_bases[0],before)',fixture)

    def test_shadow_smoke_is_independently_executable(self):
        summary=smoke.run()
        self.assertEqual(summary['status'],'PASS')
        self.assertGreaterEqual(summary['checks'],17)
        self.assertTrue(summary['real_source_callback_executed'])
        self.assertFalse(summary['production_modified'])


if __name__=='__main__':unittest.main(verbosity=2)
