import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import torch

from tools import nanojev_hawk_dove_internal_ab_best_watch as watch


def fake_report(step, *, bank=19, continuous=21, gate=40):
    arms={}
    for name, count in [('bank', bank),('continuous',continuous)]:
        scores={t:{'cases':48,'correct':count,'accuracy_5pct':count/48,
                   'passes_gate':count/48 >= gate/100} for t in watch.TIERS}
        arms[name]={'scores':scores,'all_nine_pass':count/48>=gate/100,
                    'macro_accuracy_5pct':count/48}
    return {'step':step,'gate_percent':gate,'arms':arms}


def staged(root,step,report):
    ident={'gate_percent':40.,'donor_snapshot_sha256':'a'*64}
    (root/'config.json').write_text(json.dumps(ident),encoding='utf8')
    raw=io.BytesIO()
    torch.save({'schema':watch.SCHEMA,'step':step,'identity':ident,
                'heads':{'bank':{'w':torch.tensor([step])},
                         'continuous':{'w':torch.tensor([step])}}},raw)
    watch.atomic_write(root/'latest.pt',raw.getvalue())
    if report is not None:
        watch.atomic_write(root/f'probe-step-{step:07d}.json',json.dumps(report).encode('utf8'))


class WatchTests(unittest.TestCase):
    def test_saves_matching_best_for_both_arms(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            staged(root,300,fake_report(300))
            step,saved=watch.poll_once(root)
            self.assertEqual(step,300)
            self.assertEqual(len(saved),3)  # bank macro; continuous macro+gate
            folder=root/'best_checkpoints'
            self.assertFalse((folder/'best_all_nine_bank.pt').exists())
            self.assertTrue((folder/'best_all_nine_continuous.pt').exists())
            self.assertEqual(torch.load(folder/'best_macro_bank.pt',weights_only=True)['step'],300)
            self.assertEqual(watch.poll_once(root,300)[1],[])
            staged(root,600,fake_report(600,bank=20,continuous=20))
            step,saved=watch.poll_once(root,300)
            self.assertEqual(len(saved),2) # bank macro+gate, continuous not improved
            self.assertEqual(torch.load(folder/'best_all_nine_bank.pt',weights_only=True)['step'],600)
            self.assertEqual(torch.load(folder/'best_all_nine_continuous.pt',weights_only=True)['step'],300)
            staged(root,900,fake_report(900,bank=18,continuous=18))
            self.assertEqual(watch.poll_once(root,600)[1],[])
            self.assertEqual(torch.load(folder/'best_macro_bank.pt',weights_only=True)['step'],600)

    def test_waits_for_matching_probe_and_never_copies_wrong_step(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            staged(root,900,None)
            self.assertEqual(watch.poll_once(root),(None,[]))
            (root/'probe-step-0000900.json').write_text(json.dumps(fake_report(600)))
            with self.assertRaisesRegex(ValueError,'REPORT_MISMATCH'):
                watch.poll_once(root)
            self.assertFalse((root/'best_checkpoints').exists())

    def test_rejects_score_and_donor_integrity_errors(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            report=fake_report(300)
            report['arms']['bank']['scores']['T00']['correct']+=1
            staged(root,300,report)
            with self.assertRaisesRegex(ValueError,'INVALID_SCORE'):
                watch.poll_once(root)
            staged(root,300,fake_report(300))
            watch.poll_once(root)
            path=root/'best_checkpoints/best_macro_bank.pt'
            path.write_bytes(b'bad')
            staged(root,600,fake_report(600,bank=22))
            with self.assertRaisesRegex(ValueError,'CORRUPT'):
                watch.poll_once(root)


if __name__=='__main__':
    unittest.main()
