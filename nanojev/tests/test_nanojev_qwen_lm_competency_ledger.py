from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import tempfile

from tools import nanojev_qwen_lm_competency_ledger as c


class CompetencyLedgerTests(TestCase):
    def test_paired_transitions_are_counted_but_new_cases_are_not(self):
        old={'a':True,'b':False,'c':True}
        new={'a':False,'b':True,'c':True,'new':True}
        self.assertEqual(c._paired(old,new),{'paired_tested':3,'gained':1,'lost':1,'net':0})
        self.assertTrue(c._rolling([{'paired':{'paired_tested':1,'gained':0,'lost':0}}]*4)['flat_or_negative'])
        self.assertFalse(c._rolling([{'paired':{'paired_tested':1,'gained':1,'lost':0}}]*4)['flat_or_negative'])

    def test_probe_band_order_and_native_class_truth(self):
        state={'policy':{'source_levels':2,'operand_boundary':1024,'seed':22,
                         'start_rung_override':0},'seed_state':{'rung':0}}
        a=[c._fresh_cases(state,'samples',i,count=18) for i in range(5)]
        bands=[next(x['band'] for x in rows if x['task']=='arithmetic_operators' and x['level']==0)
               for rows in a]
        self.assertEqual(set(bands),set(x[0] for x in c.BANDS))
        self.assertEqual(len(set(bands)),5)
        for rows in a:
            self.assertTrue(all(x['band']=='native' for x in rows if x['task']=='algebra'))
        self.assertEqual(len(c._fixed_cases(state,'samples')),18)

    def test_store_true_hard_and_paired_gain_loss_then_restart(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'mixed';out.mkdir()
            source=Path(d)/'sys';source.mkdir()
            state={'step':100,'phase_step':100,'parent_sha256':'sha123',
                   'policy':{'source_levels':2,'seed':123,'operand_boundary':1024,
                             'start_rung_override':0},'seed_state':{'rung':0}}
            args=SimpleNamespace(device='cpu',max_tokens=256,max_new_tokens=64)
            progress={'iteration':0,'calls':0}
            def greedy(bb,tok,row,a):
                progress['calls']+=1
                # Use the gold answer except for fixed first anchor (correct->wrong)
                # and second fixed anchor (wrong->correct).
                anchors=c._fixed_cases(state,'samples')
                if row['source']==anchors[0]['source']:
                    return row['target'] if progress['iteration']==0 else 'WRONG'
                if row['source']==anchors[1]['source']:
                    return 'WRONG' if progress['iteration']==0 else row['target']
                return row['target']
            def truth(source,target,pred):return pred==target,pred
            with patch.object(c.fr,'_greedy_one',side_effect=greedy),\
                 patch.object(c.ladder,'correctness',side_effect=truth):
                r1=c.observe(bb=None,tok=None,state=state,args=args,out=out,
                    phase='samples',previous=source,new_per_check=3,replay_per_check=2)
                self.assertEqual(r1['paired']['net'],0)
                self.assertTrue(r1['nonretroactive'])
                self.assertGreater(r1['hard_archive_size'],0)
                prior_calls=progress['calls']
                again=c.observe(bb=None,tok=None,state=state,args=args,out=out,
                    phase='samples',previous=source,new_per_check=3,replay_per_check=2)
                self.assertEqual(again,r1)
                self.assertEqual(progress['calls'],prior_calls)
                progress['iteration']=1
                state['step']=350;state['phase_step']=350
                r2=c.observe(bb=None,tok=None,state=state,args=args,out=out,
                    phase='samples',previous=source,new_per_check=3,replay_per_check=2)
                self.assertGreaterEqual(r2['paired']['gained'],1)
                self.assertGreaterEqual(r2['paired']['lost'],1)
                self.assertEqual(r2['fixed_anchor_paired']['net'],0)
                cases=c._read(out/'glass_hard_candidates.jsonl')
                self.assertTrue(all(x['source'] and x['target'] and x['case_key'] for x in cases))
                self.assertEqual(len(c._read(out/'competency_checks.jsonl')),2)
                # Rewind a checkpoint: future sidecar must not contaminate an
                # earlier observation. The old check is simply replayed.
                state['step']=100;state['phase_step']=100
                old=c.observe(bb=None,tok=None,state=state,args=args,out=out,
                    phase='samples',previous=source,new_per_check=3,replay_per_check=2)
                self.assertEqual(old,r1)

    def test_historical_failures_imported_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as d:
            a,b=Path(d)/'sys',Path(d)/'mixed';a.mkdir();b.mkdir()
            item={'case_id':'id1','source':'PROBLEM: 1+1','target':'2',
                  'task':'arithmetic_operators','level':0,'step':100}
            c.fr._append(a/'discovered_errors.jsonl',item)
            c.fr._append(b/'mixed_discovered_errors.jsonl',item)
            self.assertEqual(c._archive_import(b,a,'samples','sha'),1)
            self.assertEqual(c._archive_import(b,a,'samples','sha'),0)
            saved=c._read(b/'glass_hard_candidates.jsonl')
            self.assertEqual(saved[0]['kind'],'historical_unrechecked')
            self.assertEqual(saved[0]['source'],'PROBLEM: 1+1')

    def test_torn_tail_repaired_for_archive_append(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'candidates.jsonl'
            f.write_bytes(b'{"case_key":"1"}\n{"case_key":')
            self.assertTrue(c._append_once(f,{'case_key':'2'},lambda r:r['case_key']))
            self.assertEqual([v['case_key'] for v in c._read(f)],['1','2'])

    def test_export_historical_and_diagnostic_without_probing_or_modifying_weights(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);sys=root/'systematic';sam=root/'samples';diag=root/'diag'
            for p in (sys,sam,diag):p.mkdir()
            (sam/'glass_ready.pt').write_bytes(b'untouched')
            raw={'source':'PROBLEM: 10','target':'10','task':'arithmetic_operators',
                 'level':0,'step':11}
            c.fr._append(sys/'discovered_errors.jsonl',raw)
            e={'id':'diag1','source':'PROBLEM: 11','target':'11','task':'arithmetic_operators',
               'level':0,'band':'inner_1','baseline_correct':False,'baseline_loss':.5}
            c.fr._append(diag/'hard_candidates.jsonl',e)
            one=c.export_existing(systematic_output=sys,samples_output=sam,diagnostic_report=diag)
            two=c.export_existing(systematic_output=sys,samples_output=sam,diagnostic_report=diag)
            self.assertEqual((one['total_hard_candidates'],two['total_hard_candidates']),(2,2))
            self.assertEqual((one['diagnostic_errors_imported'],two['diagnostic_errors_imported']),(1,0))
            self.assertEqual((sam/'glass_ready.pt').read_bytes(),b'untouched')
