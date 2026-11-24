"""Offline contract tests for the narrow ONLYOFFICE failure recheck."""
import unittest
from tools.mcel_onlyoffice_failed_acceptance import (
    select_stages, stages, check_stage,
)


class FocusedAcceptanceContract(unittest.TestCase):
    def test_default_only_includes_the_three_interruption_suspects(self):
        selected = select_stages('restart-sensitive', 'latest')
        self.assertEqual([x.name for x in selected], [
            'offline-final-close-journal',
            'docker-real-documentserver-editor',
            'docker-shadow-revision-editor',
        ])
        self.assertNotIn('docker-two-live-editor-tabs', [x.name for x in selected])
        self.assertNotIn('docker-final-close-callback', [x.name for x in selected])

    def test_original_full_run_commands_are_preserved(self):
        chosen = stages('latest')
        self.assertEqual(chosen[1].command[-3:], ('run', '--onlyoffice-tag', 'latest'))
        self.assertEqual(chosen[2].command[-1], '--shadow-revision')

    def test_two_editor_save_failure_has_specific_reason(self):
        stage = select_stages('restart-sensitive', 'latest')[1]
        status, reason = check_stage(stage, {'returncode': 1,
            'summary': {'status': 'FAIL'}, 'timeout': False, 'launch_error': None},
            'MC_ONLYOFFICE_REAL genuine-editor-callback-saved-changed-cell FAIL detail=A1=original')
        self.assertEqual((status, reason), ('FAIL', 'EDITOR_SAVE_UNCHANGED'))

    def test_abort_is_not_mislabeled_as_security_code_failure(self):
        stage = select_stages('restart-sensitive', 'latest')[0]
        status, reason = check_stage(stage, {'returncode': 1,
            'summary': {'status': 'FAIL'}, 'timeout': False, 'launch_error': None},
            'ConnectionAbortedError: [WinError 10053]')
        self.assertEqual((status, reason), ('FAIL', 'HOST_CONNECTION_ABORT'))

    def test_journal_requires_all_16_and_specific_positive_control(self):
        stage = select_stages('restart-sensitive', 'latest')[0]
        process = {'returncode': 0, 'summary': {'status': 'PASS', 'tests': 16,
            'errors': 0, 'failures': 0, 'production_modified': False}}
        self.assertEqual(check_stage(stage, process, 'test_source_backed_http_status2_admission_and_replay ... ok'),
                         ('PASS', 'JOURNAL_16_OF_16'))
        self.assertEqual(check_stage(stage, process, 'some other test ... ok'),
                         ('UNVERIFIED', 'INCOMPLETE_JOURNAL_PROOF'))

    def test_real_editor_requires_saved_workbook_not_merely_accepted_command(self):
        stage = select_stages('restart-sensitive', 'latest')[1]
        process = {'returncode': 0, 'summary': {'status': 'PASS',
            'real_documentserver_edit_save_reopen': True, 'production_modified': False}}
        required = '\n'.join([
            'genuine-editor-callback-saved-changed-cell PASS',
            'reopened-workbook-cell-persisted PASS',
            'editor-origin-never-received-mc-session-cookie PASS',
            'credentialless-bob-container-attacks-blocked PASS',
        ])
        self.assertEqual(check_stage(stage, process, required),
                         ('PASS', 'REAL_EDIT_SAVE_REOPEN_VERIFIED'))
        self.assertEqual(check_stage(stage, process, 'real-documentserver-forcesave-command-accepted PASS'),
                         ('UNVERIFIED', 'INCOMPLETE_EDITOR_PROOF'))

    def test_shadow_revision_requires_its_two_proofs(self):
        stage = select_stages('restart-sensitive', 'latest')[2]
        process = {'returncode': 0, 'summary': {'status': 'PASS',
            'real_documentserver_edit_save_reopen': True, 'production_modified': False,
            'shadow_revision_fence': True}}
        required = '\n'.join([
            'genuine-editor-callback-saved-changed-cell PASS',
            'reopened-workbook-cell-persisted PASS',
            'editor-origin-never-received-mc-session-cookie PASS',
            'credentialless-bob-container-attacks-blocked PASS',
        ])
        self.assertEqual(check_stage(stage, process, required),
                         ('UNVERIFIED', 'INCOMPLETE_EDITOR_PROOF'))
        self.assertEqual(check_stage(stage, process, required + '\n' + '\n'.join([
            'shadow-real-editor-callback-committed-through-revision-fence PASS',
            'shadow-replayed-old-grant-rejected-after-real-editor-save PASS',
        ])), ('PASS', 'REAL_EDIT_SAVE_REOPEN_VERIFIED'))

    def test_source_blockers_kept_separate(self):
        chosen = select_stages('source-blockers', 'latest')
        self.assertEqual(len(chosen), 3)
        self.assertEqual(len(select_stages('combined', 'latest')), 6)
        self.assertTrue(all(s.category == 'source-blocker' for s in chosen))

    def test_missing_docker_cannot_be_called_pass(self):
        stage = select_stages('restart-sensitive', 'latest')[1]
        self.assertEqual(check_stage(stage, {'launch_error': 'Docker unavailable'}, ''),
                         ('UNVERIFIED', 'LAUNCH_ERROR'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
