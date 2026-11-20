from pathlib import Path
import json
import tempfile
import unittest
from tools import nanojev_contract_v3_live_eval as ev
from tools import nanojev_contract_v3_live_head as head

BASE=Path(__file__).resolve().parents[1]/'tools'/'fixtures'/'nanojev_v3_live_head'

class EvalTest(unittest.TestCase):
    def test_diverse_positive_ir_is_valid_and_grounded(self):
        ir,missing=head.interpret((BASE/'alternate_request.txt').read_text(),
                                  (BASE/'alternate_model_response.json').read_text())
        self.assertEqual(missing,[])
        self.assertEqual(ir['amount_wei'],9*10**18)
        self.assertEqual(ir['milestones'][1]['max_extensions'],2)
        self.assertEqual(ir['policy']['on_appointment_failure'],'pay_worker')

    def test_correct_refusal_on_conflicting_request(self):
        ir,missing=head.interpret((BASE/'conflicted_request.txt').read_text(),
                                  (BASE/'conflicted_model_response.json').read_text())
        self.assertIsNone(ir)
        self.assertTrue(missing)

    def test_replay_evaluation_does_not_claim_ai_accuracy(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'evaluation.json'
            result=ev.evaluate(manifest=BASE/'manifest.json',output=p,mode='replay')
            self.assertEqual(result['status'],'PASS_OFFLINE_EVAL_PIPELINE_NOT_MODEL_VALIDATION',result)
            self.assertEqual(result['score'],{'passed':4,'total':4,'evaluated':4,
                                             'unavailable':0,'no_final_model_answer':0,'skipped':0,
                                             'positive_exact':2,'positive_total':2,
                                             'correct_refusals':2,'refusal_total':2})
            self.assertFalse(result['model_inference_proven'])
            self.assertTrue(all(not c['model_invoked'] for c in result['cases']))

    def test_live_timeouts_not_counted_as_semantic_failure(self):
        def timed_out(*_):
            raise TimeoutError('timed out')
        with tempfile.TemporaryDirectory() as tmp:
            result=ev.evaluate(manifest=BASE/'manifest.json',
                               output=Path(tmp)/'model_timeout.json',
                               mode='live',timeout_seconds=7,post=timed_out)
            self.assertEqual(result['status'],'INCOMPLETE_LIVE_EVAL_INFERENCE_UNAVAILABLE')
            self.assertIsNone(result['semantic_accuracy'])
            self.assertEqual(result['score']['evaluated'],0)
            self.assertEqual(result['score']['unavailable'],1)
            self.assertEqual(result['score']['skipped'],3)
            self.assertTrue(result['cases'][0]['model_request_attempted'])
            self.assertFalse(result['cases'][0]['model_invoked'])
            self.assertEqual(result['cases'][0]['blockers'][0]['stage'],'model_timeout')
            self.assertTrue(all(c['result']=='SKIPPED_AFTER_INFERENCE_UNAVAILABLE'
                                for c in result['cases'][1:]))

    def test_model_no_final_answer_not_counted_as_semantic_error(self):
        output={'done':True,'done_reason':'stop','eval_count':49,
                'message':{'content':''}}
        def no_final(*_):
            return json.dumps(output).encode()
        with tempfile.TemporaryDirectory() as tmp:
            result=ev.evaluate(manifest=BASE/'manifest.json',
                               output=Path(tmp)/'no_final.json',mode='live',post=no_final)
            self.assertEqual(result['status'],'INCOMPLETE_LIVE_EVAL_NO_FINAL_MODEL_ANSWER')
            self.assertIsNone(result['semantic_accuracy'])
            self.assertEqual(result['score']['no_final_model_answer'],1)
            self.assertEqual(result['score']['evaluated'],0)
            self.assertEqual(result['score']['skipped'],3)
            self.assertEqual(result['cases'][0]['ollama_diagnostics']['eval_count'],49)
            self.assertEqual(result['cases'][0]['blockers'][0]['stage'],'model_empty_answer')

    def test_keep_going_option_tries_all_cases_after_timeout(self):
        attempts=[]
        def timed_out(*args):
            attempts.append(args)
            raise TimeoutError('timed out')
        with tempfile.TemporaryDirectory() as tmp:
            result=ev.evaluate(manifest=BASE/'manifest.json',
                               output=Path(tmp)/'all_timeout.json',
                               mode='live',timeout_seconds=9,post=timed_out,
                               continue_after_unavailable=True)
            self.assertEqual(len(attempts),4)
            self.assertEqual(result['score']['unavailable'],4)
            self.assertEqual(result['score']['skipped'],0)
            self.assertEqual(result['score']['evaluated'],0)
            self.assertIsNone(result['semantic_accuracy'])

    def test_wrong_gold_cannot_count_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'wrong.json'
            p.write_text(json.dumps({'schema':'nanojev.contract.v3.live_head_eval_manifest.v1',
                 'cases':[{'id':'bad_case','expected':'contract',
                           'request':'heldout_request.txt','replay':'heldout_model_response.json',
                           'gold':'alternate_expected_ir.json'}]}))
            # Local copied corpus paths in test folder
            for f in ('heldout_request.txt','heldout_model_response.json','alternate_expected_ir.json'):
                (Path(tmp)/f).write_bytes((BASE/f).read_bytes())
            report=ev.evaluate(manifest=p,output=Path(tmp)/'result.json',mode='replay')
            self.assertEqual(report['status'],'FAIL_HELDOUT_EVALUATION')
            self.assertEqual(report['score']['passed'],0)

if __name__=='__main__':unittest.main()
