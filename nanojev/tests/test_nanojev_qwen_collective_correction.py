import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
from tools.nanojev_qwen_collective_correction import POPS, fold, local_teacher, partitions

class CollectiveTests(unittest.TestCase):
    def test_disjoint_partitions(self):
        for pop in POPS:
            parts=partitions(240,47,pop,20)
            sets=[set(x.tolist()) for x in parts.values()]
            self.assertEqual(sum(map(len,sets)),240)
            for i in range(4):
                for j in range(i+1,4):self.assertFalse(sets[i]&sets[j])

    def test_local_teacher_does_not_use_query_label(self):
        ref=np.arange(1,120,dtype=float).reshape(-1,1)
        q=np.array([[12.5],[45.5]])
        p=local_teacher(q,ref,3*ref[:,0],k=8,penalty=.1)
        self.assertEqual(p.shape,(2,))
        self.assertTrue(np.all(np.isfinite(p)))
        self.assertAlmostEqual(p[0],37.5,delta=2)

    def test_end_to_end_no_qwen(self):
        rng=np.random.default_rng(21)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for j,p in enumerate(POPS):
                folder=root/p;folder.mkdir()
                vals=rng.normal(size=240)
                X=np.stack((vals,vals**2,rng.normal(size=240)*.15,
                           rng.normal(size=240)*.1),axis=1).astype('float32')
                y=vals*(3+j*.05)+2
                np.save(folder/'activations.npy',X)
                with (folder/'probes.jsonl').open('w') as out:
                    for yi in y:out.write(json.dumps({'answer':float(yi)})+'\n')
            report=fold(root,root/'out','C4_T00',holdout=20,rank=3,adapter_rank=2,
                        k=8,epochs=4,max_train=15,max_val=30)
            self.assertEqual(report['held_population'],'C4_T00')
            self.assertEqual(report['holdout_count'],240)
            self.assertEqual(len(report['trained_populations']),4)
            self.assertTrue((root/'out/C4_T00/adapter.npz').is_file())
            self.assertTrue((root/'out/C4_T00/holdout_predictions.csv').is_file())

if __name__=='__main__':unittest.main()

class WithinPopulationTests(unittest.TestCase):
    def test_five_populations_one_adapter_strict_holdouts(self):
        rng=np.random.default_rng(23)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for j,pop in enumerate(POPS):
                folder=root/pop;folder.mkdir()
                vals=rng.normal(size=240)
                X=np.stack((vals, vals**2, rng.normal(size=240)*.12,
                            rng.normal(size=240)*.12),axis=1).astype('float32')
                y=3*vals+0.1*j
                np.save(folder/'activations.npy', X)
                with (folder/'probes.jsonl').open('w') as fp:
                    for yi in y:fp.write(json.dumps({'answer':float(yi)})+'\n')
            from tools.nanojev_qwen_collective_correction import fold
            report=fold(root,root/'out',None,holdout=20,rank=3,adapter_rank=2,
                        k=8,epochs=4,max_train=15,max_val=30)
            self.assertEqual(report['split'],'within-population')
            self.assertEqual(set(report['trained_populations']),set(POPS))
            self.assertEqual(set(report['evaluations']),set(POPS))
            self.assertTrue((root/'out/within-population/adapter.npz').is_file())
            for pop in POPS:
                self.assertEqual(report['evaluations'][pop]['holdout_count'],20)
                path=root/'out/within-population'/f'{pop}_holdout_predictions.csv'
                self.assertEqual(len(path.read_text().splitlines()),21)
