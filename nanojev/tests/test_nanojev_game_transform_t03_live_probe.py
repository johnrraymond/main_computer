"""Offline checks for the one-command T03 live checkpoint probe."""
from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools import nanojev_game_transform_t03_live_probe as live
from tools import nanojev_game_transform_t03_tiered as tiered


class LiveProbeTest(unittest.TestCase):
    def test_default_one_command_interface(self):
        p = live.parser().parse_args([])
        self.assertIsNone(p.checkpoint)
        self.assertEqual(p.limit, 48)
        self.assertEqual(p.device, 'cuda')
        self.assertFalse(p.keep_snapshot)

    def test_immutable_snapshot_tracks_exact_saved_step(self):
        import torch
        with TemporaryDirectory() as temp:
            directory=Path(temp)
            latest=directory/'latest.pt'
            torch.save({'step':230000, 'decoder':{}, 'game_operator_head':{},
                        'source_sha256':'source', 'database_sha256':'database'}, latest)
            digest=live._sha256(latest)
            copied, step, copyhash=live.snapshot_checkpoint(latest,directory/'probes')
            self.assertEqual(step,230000)
            self.assertEqual(digest,copyhash)
            torch.save({'step':231000, 'decoder':{},'game_operator_head':{},
                        'source_sha256':'source','database_sha256':'database'}, latest)
            self.assertEqual(torch.load(copied,weights_only=True)['step'],230000)
            copied.unlink()

    def test_missing_checkpoint_does_not_create_fake_results(self):
        with TemporaryDirectory() as temp:
            with self.assertRaises(FileNotFoundError):
                live.snapshot_checkpoint(Path(temp)/'missing.pt',Path(temp)/'reports')

    def test_pooled_summary_and_transfer(self):
        def m(n, paired, x, y):
            return {'0.1':{'n':n,'paired_xy':paired,'x':x,'y':y}}
        scores={}
        for label in ('baseline','trained'):
            scores[f'{label}/dev/T03a']=m(2,0.5 if label=='baseline' else 1.0,1,1)
            scores[f'{label}/test/T03a']=m(3,0.0 if label=='baseline' else 2/3,1,1)
            scores[f'{label}/game_transfer/T03i']=m(4,0.0 if label=='baseline' else 0.25,1,0.25)
        report={'scores':scores,'steps':{'baseline':220000,'trained':230000}}
        out=live.format_summary(report)
        self.assertIn('230,000',out)
        self.assertIn('T03a',out)
        self.assertIn('80.0%',out)  # (2+2)/5 = 80%
        self.assertIn('T03i transfer',out)

    def test_one_command_invokes_unchanged_probe_and_retains_report(self):
        import torch
        with TemporaryDirectory() as temp:
            root=Path(temp);run=root/'run';run.mkdir()
            latest=run/'latest.pt'
            torch.save({'step':230000,'decoder':{},'game_operator_head':{},
                        'source_sha256':'s','database_sha256':'d'},latest)
            digest=live._sha256(latest)
            def evaluate(args):
                self.assertEqual(torch.load(args.experimental_checkpoint,weights_only=True)['step'],230000)
                self.assertNotEqual(args.experimental_checkpoint,latest)
                self.assertTrue(args.report.name.startswith('t03-step-000230000-'))
                args.report.write_text('{"mock":true}')
                return {'scores':{},'steps':{'baseline':220000,'trained':230000},
                        'checkpoint_sha256':digest}
            args=live.parser().parse_args(['--run',str(run),'--device','cpu'])
            with patch.object(tiered,'evaluate',side_effect=evaluate),redirect_stdout(io.StringIO()) as printed:
                _,path=live.run(args)
            self.assertIn('step 230,000',printed.getvalue())
            self.assertTrue(path.is_file())
            self.assertEqual(digest,live._sha256(latest))
            self.assertEqual(list((run/'live-probes').glob('*.pt')),[])

    def test_identity_mismatch_removes_temporary_snapshot(self):
        import torch
        with TemporaryDirectory() as temp:
            run=Path(temp)/'run';run.mkdir()
            torch.save({'step':20,'decoder':{},'game_operator_head':{},
                        'source_sha256':'s','database_sha256':'d'},run/'latest.pt')
            args=live.parser().parse_args(['--run',str(run),'--device','cpu'])
            with patch.object(tiered,'evaluate',return_value={
                'steps':{'trained':19,'baseline':10},'checkpoint_sha256':'bad','scores':{}}),\
                    redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError,'T03_LIVE_PROBE_SNAPSHOT_IDENTITY_MISMATCH'):
                    live.run(args)
            self.assertEqual(list((run/'live-probes').glob('*.pt')),[])


if __name__=='__main__':
    unittest.main()
