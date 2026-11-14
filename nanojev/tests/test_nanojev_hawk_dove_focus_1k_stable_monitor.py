from pathlib import Path
import json
import tempfile
import unittest
from tools import nanojev_hawk_dove_focus_1k_stable_monitor as monitor

class MonitorTests(unittest.TestCase):
    def test_saturation_and_error_quantiles_are_surfaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for step in (0,250):
                doc={'step':step,'focus_1k':{
                    'train_raw_coordinate_mae':5.,'new_dev_raw_coordinate_mae':6.,
                    'train_saturated_coordinate_fraction':.1,
                    'new_dev_saturated_coordinate_fraction':.2,
                    'train_by_band':{}, 'new_dev_by_band':{}},
                    'chunk_mean_train_losses_and_gradients':({'numeric_loss':3.} if step else None)}
                for band in monitor.BANDS:
                    doc['focus_1k']['train_by_band'][band]={'n':3,'accuracy_5pct_floor':0.,'mae':8.}
                    doc['focus_1k']['new_dev_by_band'][band]={'n':3,'accuracy_5pct_floor':0.,'mae':8.,
                        'median_abs_error':5.,'p90_abs_error':10.,'max_abs_error':11.}
                (root/f'probe-step-{step:07d}.json').write_text(json.dumps(doc))
            r=monitor.review(root)
            self.assertEqual(r['new_dev_saturation_fraction'],.2)
            self.assertEqual(r['bands']['1-10']['dev_p90_abs_error'],10.)
    def test_old_reports_show_missing_evidence_without_guessing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            doc={'step':0,'focus_1k':{'train_by_band':{},'new_dev_by_band':{}}}
            for name in monitor.BANDS:
                for k in ('train_by_band','new_dev_by_band'):
                    doc['focus_1k'][k][name]={'n':1,'accuracy_5pct_floor':0.,'mae':2.}
            (root/'probe-step-0000000.json').write_text(json.dumps(doc))
            out=monitor.review(root)
            self.assertIsNone(out['new_dev_saturation_fraction'])
            self.assertIsNone(out['bands']['1-10']['dev_p90_abs_error'])
if __name__=='__main__':unittest.main()
