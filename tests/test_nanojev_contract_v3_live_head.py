"""Regression tests for the opt-in V3 live language bridge; NO actual LLM here."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import unittest.mock

from tools import nanojev_contract_v3_live_head as bridge
from tools import nanojev_contract_v3_governance_language as language

FIX=Path(__file__).resolve().parents[1]/'tools'/'fixtures'/'nanojev_v3_live_head'
REQUEST=(FIX/'heldout_request.txt').read_text(encoding='utf-8')
RESPONSE=(FIX/'heldout_model_response.json').read_text(encoding='utf-8')
IR=json.loads((FIX/'heldout_expected_ir.json').read_text(encoding='utf-8'))


class LiveHeadTest(unittest.TestCase):
    def test_positive_source_grounded_v3_spec(self):
        actual,missing=bridge.interpret(REQUEST,RESPONSE)
        self.assertEqual(missing,[])
        self.assertEqual(actual,language.validate(IR))

    def test_literal_unscoped_extension_quote_realigns_to_unique_named_line(self):
        data=json.loads(RESPONSE)
        path='milestones.prototype.extension_seconds'
        data['source_spans'][path]='extension adds 7 days.'
        audit=[]
        spec,missing=bridge.interpret(REQUEST,json.dumps(data),evidence_alignment=audit)
        self.assertEqual(missing,[])
        self.assertEqual(spec,language.validate(IR))
        self.assertEqual([entry['field'] for entry in audit],[path])
        self.assertEqual(audit[0]['status'],'UNSCOPED_LITERAL_QUOTE_ALIGNED')
        self.assertEqual(set(audit[0]),{'field','status','original_quote_sha256','aligned_quote_sha256'})

    def test_wrong_milestone_citation_cannot_be_reassigned(self):
        data=json.loads(RESPONSE)
        path='milestones.prototype.extension_seconds'
        data['source_spans'][path]='The beta extension adds 7 days.'
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.interpret(REQUEST,json.dumps(data))
        self.assertEqual(caught.exception.stage,'evidence')

    def test_partial_unscoped_quote_not_repaired_if_source_ambiguous(self):
        data=json.loads(RESPONSE)
        path='milestones.prototype.extension_seconds'
        data['source_spans'][path]='extension adds 7 days.'
        source=REQUEST+'\nThe prototype extension adds 7 days.\n'
        with self.assertRaises(bridge.BridgeError):
            bridge.interpret(source,json.dumps(data))

    def test_partial_citation_cannot_overrule_wrong_model_value(self):
        data=json.loads(RESPONSE)
        data['contract']['milestones'][0]['extension_seconds']=8*86400
        data['source_spans']['milestones.prototype.extension_seconds']='extension adds 7 days.'
        with self.assertRaises(bridge.BridgeError):
            bridge.interpret(REQUEST,json.dumps(data))

    def test_nonexistent_quote_cannot_be_repaired(self):
        data=json.loads(RESPONSE)
        data['source_spans']['milestones.prototype.extension_seconds']='extension adds 12 days.'
        with self.assertRaises(bridge.BridgeError):
            bridge.interpret(REQUEST,json.dumps(data))

    def test_literal_unscoped_quote_repaired_during_offline_execution(self):
        data=json.loads(RESPONSE)
        data['source_spans']['milestones.prototype.extension_seconds']='extension adds 7 days.'
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            response=p/'candidate.json'; response.write_text(json.dumps(data))
            report=bridge.execute(request=FIX/'heldout_request.txt',
                output=p/'check.json',response_file=response,
                gold=FIX/'heldout_expected_ir.json')
            self.assertEqual(report['status'],'PASS_OFFLINE_REPLAY_V3_IR_NOT_LIVE_NOT_CERTIFIED')
            self.assertEqual(len(report['evidence_alignments']),1)
            self.assertEqual(report['semantic_comparison'],'MATCH_AUTHOR_SUPPLIED_GOLD')
            self.assertIn('UNAMBIGUOUS_LITERAL_ALIGNMENT',report['evidence_status'])

    def test_incomplete_original_request_refused(self):
        actual,missing=bridge.interpret((FIX/'underspecified_request.txt').read_text(),
                     (FIX/'underspecified_model_response.json').read_text())
        self.assertIsNone(actual)
        self.assertTrue(missing)

    def test_model_must_not_supply_missing_terms(self):
        source='The payer escrows 12 ETH for worker with arbitrator. Not all terms specified.'
        with self.assertRaises(bridge.BridgeError):
            bridge.interpret(source,RESPONSE)

    def test_numeric_hallucination_rejected(self):
        data=json.loads(RESPONSE)
        data['contract']['milestones'][0]['deadline_seconds']=15*86400
        with self.assertRaises(bridge.BridgeError):bridge.interpret(REQUEST,json.dumps(data))

    def test_bad_silence_beneficiary_rejected(self):
        data=json.loads(RESPONSE)
        data['contract']['policy']['on_review_silence']='refund_payer'
        with self.assertRaises(bridge.BridgeError):bridge.interpret(REQUEST,json.dumps(data))

    def test_forged_source_quote_rejected(self):
        data=json.loads(RESPONSE)
        data['source_spans']['milestones.beta.amount_wei']='The beta is worth 15 ETH.'
        with self.assertRaises(bridge.BridgeError):bridge.interpret(REQUEST,json.dumps(data))

    def test_field_omission_refused(self):
        data=json.loads(RESPONSE)
        del data['source_spans']['policy.on_arbitrator_silence']
        with self.assertRaises(bridge.BridgeError):bridge.interpret(REQUEST,json.dumps(data))

    def test_no_extra_contract_fields(self):
        data=json.loads(RESPONSE)
        data['contract']['deployment_address']='0xdeadbeef'
        with self.assertRaises(bridge.BridgeError):bridge.interpret(REQUEST,json.dumps(data))

    def test_no_untrusted_status(self):
        data=json.loads(RESPONSE)
        data['status']='deploy'
        with self.assertRaises(bridge.BridgeError):bridge.interpret(REQUEST,json.dumps(data))

    def test_strict_loopback_endpoint(self):
        for url in ('https://127.0.0.1:11434','http://localhost:11434','http://8.8.8.8:11434',
                    'http://127.0.0.1:11434/other','http://a:b@127.0.0.1:11434'):
            with self.subTest(url=url),self.assertRaises(bridge.BridgeError):bridge._url(url)
        self.assertEqual(bridge._url('http://127.0.0.1:11434'),'http://127.0.0.1:11434/api/chat')

    def test_timeout_is_inference_unavailable_not_bad_semantics(self):
        def no_completion(*_):
            raise TimeoutError('timed out')
        with tempfile.TemporaryDirectory() as tmp:
            result=bridge.execute(request=FIX/'heldout_request.txt',
                                  output=Path(tmp)/'timeout.json',live_ollama=True,
                                  timeout=12,post=no_completion)
            self.assertEqual(result['status'],'INFERENCE_UNAVAILABLE_NOT_SCORED')
            self.assertTrue(result['model_request_attempted'])
            self.assertFalse(result['llm_inference_performed'])
            self.assertEqual(result['model_timeout_seconds'],12)
            self.assertEqual(result['blockers'][0]['stage'],'model_timeout')
            self.assertFalse((Path(tmp)/'timeout.bridge.workspace').exists())

    def test_transport_connection_refusal_not_semantic_failure(self):
        def refused(*_):
            raise ConnectionRefusedError('connection refused')
        with tempfile.TemporaryDirectory() as tmp:
            result=bridge.execute(request=FIX/'heldout_request.txt',
                                  output=Path(tmp)/'refused.json',live_ollama=True,
                                  post=refused)
            self.assertEqual(result['status'],'INFERENCE_UNAVAILABLE_NOT_SCORED')
            self.assertEqual(result['blockers'][0]['stage'],'model_transport')

    def test_compact_system_and_full_separate_protocol(self):
        data=json.loads(bridge._request_payload(REQUEST,bridge.DEFAULT_MODEL))
        self.assertFalse(data['think'])
        self.assertEqual(data['format'],'json')
        self.assertLess(len(data['messages'][0]['content']),500)
        self.assertIn('source_spans',data['messages'][1]['content'])
        self.assertIn(REQUEST,data['messages'][1]['content'])
        self.assertNotIn(REQUEST,data['messages'][0]['content'])
        self.assertEqual(len(data['messages']),2)

    def test_empty_content_with_thinking_is_not_semantic_failure(self):
        payload={'done':True,'done_reason':'length','eval_count':1400,
                 'prompt_eval_count':2100,
                 'message':{'content':'','thinking':'private reasoning'}}
        diag={}
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.ollama(REQUEST,post=lambda *_:json.dumps(payload).encode(),diagnostics=diag)
        self.assertEqual(caught.exception.stage,'model_empty_answer')
        self.assertEqual(diag['done_reason'],'length')
        self.assertEqual(diag['eval_count'],1400)
        self.assertEqual(diag['thinking_characters'],len('private reasoning'))
        self.assertNotIn('private reasoning',json.dumps(diag))

    def test_empty_stop_content_preserves_response_metadata(self):
        payload={'done':True,'done_reason':'stop','eval_count':49,
                 'message':{'content':''}}
        with tempfile.TemporaryDirectory() as tmp:
            result=bridge.execute(request=FIX/'heldout_request.txt',
                output=Path(tmp)/'empty.json',live_ollama=True,
                post=lambda *_:json.dumps(payload).encode())
            self.assertEqual(result['status'],'MODEL_NO_FINAL_ANSWER_NOT_SCORED')
            self.assertEqual(result['ollama_diagnostics']['done_reason'],'stop')
            self.assertFalse(result['llm_inference_performed'])
            self.assertEqual(result['semantic_comparison'],'NOT_CHECKED')
            self.assertFalse((Path(tmp)/'empty.bridge.workspace').exists())

    def test_nonempty_model_content_is_used_not_thinking(self):
        diag={}
        body={'done':True,'eval_count':100,
              'message':{'content':RESPONSE,'thinking':'private reasoning'}}
        self.assertEqual(bridge.ollama(REQUEST,post=lambda *_:json.dumps(body).encode(),diagnostics=diag),RESPONSE)
        self.assertEqual(diag['content_characters'],len(RESPONSE))
        self.assertTrue(diag['thinking_present'])

    def test_live_transport_wiring_not_falsely_attested(self):
        calls=[]
        def post(url,body,timeout):
            calls.append((url,json.loads(body),timeout))
            return json.dumps({'message':{'role':'assistant','content':RESPONSE}}).encode()
        self.assertEqual(bridge.ollama(REQUEST,post=post),RESPONSE)
        self.assertEqual(calls[0][1]['model'],bridge.DEFAULT_MODEL)
        self.assertFalse(calls[0][1]['stream'])
        self.assertFalse(calls[0][1]['think'])
        self.assertEqual(calls[0][1]['options']['temperature'],0)

    def test_offline_pipeline_to_unmodified_v3(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            result=bridge.execute(request=FIX/'heldout_request.txt',
                 output=path/'live_result.json',response_file=FIX/'heldout_model_response.json',
                 gold=FIX/'heldout_expected_ir.json')
            self.assertEqual(result['status'],'PASS_OFFLINE_REPLAY_V3_IR_NOT_LIVE_NOT_CERTIFIED',result)
            self.assertFalse(result['llm_inference_performed'])
            self.assertEqual(result['semantic_comparison'],'MATCH_AUTHOR_SUPPLIED_GOLD')
            self.assertEqual(result['graph_status'],'PASS_CONDITIONAL')
            child=Path(result['downstream']['smoke_report'])
            self.assertTrue(child.is_file())
            self.assertTrue((path/'live_result.bridge.workspace'/'normalized-governance-ir.json').is_file())
            self.assertTrue((path/'live_result.bridge.workspace'/'v3-governance-smoke.workspace'/'src'/'NanoJevGoverned01.sol').is_file())

    def test_incomplete_refusal_generates_no_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            result=bridge.execute(request=FIX/'underspecified_request.txt',
                 output=path/'missing.json',response_file=FIX/'underspecified_model_response.json')
            self.assertEqual(result['status'],'REFUSED_MISSING_EXPLICIT_TERMS')
            self.assertFalse((path/'missing.bridge.workspace').exists())

    def test_mismatch_gold_fails_before_compile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            mutated=copy.deepcopy(IR)
            mutated['milestones'][0]['amount_wei']+=1
            mutated['milestones'][1]['amount_wei']-=1
            gold=path/'wrong_gold.json';gold.write_text(json.dumps(mutated))
            result=bridge.execute(request=FIX/'heldout_request.txt',output=path/'out.json',
                  response_file=FIX/'heldout_model_response.json',gold=gold)
            self.assertEqual(result['semantic_comparison'],'MISMATCH_AUTHOR_SUPPLIED_GOLD')
            self.assertEqual(result['status'],'FAIL_BRIDGE_NOT_VERIFIED')
            self.assertFalse((path/'out.bridge.workspace').exists())

    def test_preexisting_output_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'out.json';p.write_text('marker')
            result=bridge.execute(request=FIX/'heldout_request.txt',output=p,
                        response_file=FIX/'heldout_model_response.json')
            self.assertEqual(result['status'],'FAIL_BRIDGE_NOT_VERIFIED')
            self.assertEqual(p.read_text(),'marker')

    def test_live_injected_model_claims_no_real_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            result=bridge.execute(request=FIX/'heldout_request.txt',output=path/'test.json',
                    live_ollama=True,post=lambda u,b,t: json.dumps({'message':{'content':RESPONSE}}).encode())
            self.assertTrue(result['status'].startswith('PASS_'))
            self.assertFalse(result['llm_inference_performed'])
            self.assertEqual(result['mode'],'INJECTED_TRANSPORT_NOT_LIVE_PROOF')

    def test_contradictory_beneficiary_quote_rejected(self):
        data=json.loads(RESPONSE)
        source=REQUEST.replace('On review silence, pay worker.',
                               'On review silence, pay worker and refund payer.')
        data['source_spans']['policy.on_review_silence']='On review silence, pay worker and refund payer.'
        with self.assertRaises(bridge.BridgeError):bridge.interpret(source,json.dumps(data))

    def test_frozen_governance_kernel_guard(self):
        self.assertEqual(len(bridge.check_kernel()),13)
        with unittest.mock.patch.dict(bridge.PINNED_GOVERNANCE_SHA256,
                                     {'nanojev_contract_v3_governance_language.py':'0'*64}):
            with self.assertRaises(bridge.BridgeError):bridge.check_kernel()

    def test_kernel_digest_unchanged(self):
        from tools import nanojev_contract_v3_governance_smoke as frozen
        for name,sha in frozen.KERNEL_SHA256.items():
            p=Path(frozen.DIR)/name
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),sha)

    def test_explicit_extension_clarifications_are_model_false_refusals(self):
        missing=['extension_seconds for prototype milestone',
                 'extension_seconds for beta milestone',
                 'extension_seconds for final application milestone']
        evidence=bridge.audit_false_clarification(REQUEST,missing)
        self.assertEqual(len(evidence),3)
        self.assertEqual([x['duration_seconds'] for x in evidence],[604800]*3)
        self.assertEqual([x['line'] for x in evidence],[4,9,14])
        self.assertEqual({x['field'] for x in evidence},
            {'milestones.prototype.extension_seconds',
             'milestones.beta.extension_seconds',
             'milestones.final_application.extension_seconds'})

    def test_false_refusal_offline_not_counted_as_safe_refusal(self):
        answer={'status':'needs_clarification',
                'missing':['extension_seconds for prototype milestone',
                           'extension_seconds for beta milestone',
                           'extension_seconds for final application milestone']}
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            answerfile=p/'answer.json';answerfile.write_text(json.dumps(answer))
            result=bridge.execute(request=FIX/'heldout_request.txt',output=p/'result.json',
                                  response_file=answerfile,gold=FIX/'heldout_expected_ir.json')
            self.assertEqual(result['status'],'MODEL_FALSE_REFUSAL_EXPLICIT_SOURCE_TERMS')
            self.assertEqual(result['semantic_comparison'],'NOT_CHECKED')
            self.assertEqual(len(result['false_refusal_evidence']),3)
            self.assertFalse((p/'result.bridge.workspace').exists())

    def test_genuine_omission_is_not_marked_false_refusal(self):
        short=REQUEST.replace('The prototype extension adds 7 days.\n','')
        self.assertEqual(bridge.audit_false_clarification(short,
                          ['extension_seconds for prototype milestone']),[])

    def test_contradictory_extension_quotes_are_not_marked_false_refusal(self):
        conflicting=REQUEST+'\nThe prototype extension adds 10 days.\n'
        self.assertEqual(bridge.audit_false_clarification(conflicting,
                         ['extension_seconds for prototype milestone']),[])

    def test_unknown_missing_field_not_automatically_discredited(self):
        self.assertEqual(bridge.audit_false_clarification(REQUEST,
                         ['unknown arbitration obligation']),[])
        self.assertEqual(bridge.audit_false_clarification(REQUEST,
                         ['extension_seconds for prototype milestone',
                          'unknown policy']),[])

    def test_assisted_retry_is_labeled_and_keeps_first_failure(self):
        wrong=json.dumps({'status':'needs_clarification',
                          'missing':['extension_seconds for prototype milestone',
                                     'extension_seconds for beta milestone',
                                     'extension_seconds for final application milestone']})
        answers=[wrong,RESPONSE];prompts=[]
        def post(_url,body,_timeout):
            prompts.append(json.loads(body)['messages'][-1]['content'])
            return json.dumps({'done':True,'eval_count':33,'message':{'content':answers.pop(0)}}).encode()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            report=bridge.execute(request=FIX/'heldout_request.txt',
                 output=path/'out.json',gold=FIX/'heldout_expected_ir.json',
                 live_ollama=True,post=post,retry_false_clarification=True)
            self.assertEqual(report['status'],'ASSISTED_PASS_INJECTED_V3_IR_NOT_LIVE',report)
            self.assertEqual(report['first_pass_status'],'MODEL_FALSE_REFUSAL_EXPLICIT_SOURCE_TERMS')
            self.assertEqual(report['assisted_retry']['outcome'],'CANDIDATE_PRODUCED')
            self.assertEqual(report['semantic_comparison'],'MATCH_AUTHOR_SUPPLIED_GOLD')
            self.assertEqual(report['mode'],'INJECTED_TRANSPORT_NOT_LIVE_PROOF')
            self.assertFalse(report['llm_inference_performed'])
            self.assertEqual(len(prompts),2)
            self.assertNotIn('Re-evaluation notice',prompts[0])
            self.assertIn('Re-evaluation notice',prompts[1])
            self.assertIn('The prototype extension adds 7 days.',prompts[1])
            self.assertIn('first_pass_head_response_sha256',report)
            self.assertFalse(report['first_pass_head_response_sha256']==report['head_response_sha256'])

    def test_retry_still_refuses_and_never_generates_code(self):
        wrong=json.dumps({'status':'needs_clarification',
                          'missing':['extension_seconds for prototype milestone']})
        calls=[]
        def post(_url,_body,_timeout):
            calls.append(1)
            return json.dumps({'done':True,'message':{'content':wrong}}).encode()
        with tempfile.TemporaryDirectory() as tmp:
            result=bridge.execute(request=FIX/'heldout_request.txt',
               output=Path(tmp)/'result.json',live_ollama=True,post=post,
               retry_false_clarification=True)
            self.assertEqual(result['status'],'MODEL_FALSE_REFUSAL_AFTER_ASSISTED_RETRY')
            self.assertEqual(result['semantic_comparison'],'NOT_CHECKED')
            self.assertEqual(len(calls),2)
            self.assertFalse((Path(tmp)/'result.bridge.workspace').exists())

if __name__=='__main__':unittest.main()
