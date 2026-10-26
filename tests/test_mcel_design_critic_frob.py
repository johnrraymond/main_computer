"""MCEL reviewer packet acceptance: independent browser and simulated local critic."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import mcel_design_critic_frob as critic


class DesignCriticContractTests(unittest.TestCase):
    def fixture(self):
        return {'schema':critic.PACKET_SCHEMA,'screenshots':[
            {'id':f'{c}-{w}-default','candidate':c,'viewport':w,'state':'default',
             'path':f'{c}-{w}-default.png','sha256':critic.sha(b'fake png')} for c in 'ABC' for w in (390,1440)],
            'questions':critic.review_questions()}

    def review(self,packet):
        return {'schema':critic.REVIEW_SCHEMA,'reviewer':'test','packet_sha256':critic.review_hash(packet),
                'preferred_candidate':'B','candidate_scores':{c:{k:4 for k in critic.CRITERIA} for c in 'ABC'},
                'findings':[{'candidate':'B','severity':'minor','screenshot_ids':['B-390-default'],
                             'observation':'Example observation','why':'Example rationale'}],
                'policy_hypotheses':[{'id':'P1','action':'prefer_composition','scope':'general',
                                      'rationale':'Example','verification':['Test on holdout'],
                                      'human_confirmation_required':True}],
                'images_not_reviewed':[],'limitations':['Example only'],'human_approval':'PENDING'}

    def test_critic_schema_rejects_fake_photo_or_approval(self):
        packet=self.fixture()
        valid=self.review(packet)
        self.assertEqual(critic.parse_review(valid,packet)['status'],'CRITIQUE_ONLY')
        bad=copy.deepcopy(valid);bad['human_approval']='APPROVED'
        with self.assertRaisesRegex(ValueError,'human approval'):
            critic.parse_review(bad,packet)
        bad=copy.deepcopy(valid);bad['findings'][0]['screenshot_ids']=['invented.png']
        with self.assertRaisesRegex(ValueError,'screenshot IDs'):
            critic.parse_review(bad,packet)
        bad=copy.deepcopy(valid);bad['policy_hypotheses'][0]['action']='execute_code'
        with self.assertRaisesRegex(ValueError,'general'):
            critic.parse_review(bad,packet)

    def test_local_only_endpoint_enforced(self):
        self.assertEqual(critic.safe_local_ollama('http://127.0.0.1:11434'),'http://127.0.0.1:11434')
        for url in ('https://127.0.0.1:11434','http://evil.example:11434','http://username:pass@localhost:11434'):
            with self.assertRaises(ValueError):critic.safe_local_ollama(url)

    def test_local_ollama_payload_contains_images_and_same_questions(self):
        packet=self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            critic.jd(path/'packet.json',packet)
            for x in packet['screenshots']:(path/x['path']).write_bytes(b'fake png')
            calls=[]
            def fake_post(url,body,timeout):
                calls.append((url,body))
                if url.endswith('/api/show'):return {'capabilities':['completion','vision']}
                self.assertEqual(len(body['messages'][1]['images']),3)
                self.assertIn('Q1',body['messages'][1]['content'])
                self.assertFalse(body['think'])
                return {'message':{'content':json.dumps(self.review(packet))}}
            with patch.object(critic,'http_json',fake_post),patch.object(critic,'images_for_local',return_value=['BASE64_FAKE']*3):
                result=critic.local_review(path,path/'results')
            self.assertEqual(result['groups'],2)
            self.assertEqual(result['votes'],{'B':2})
            self.assertEqual(len([x for x in calls if x[0].endswith('/api/chat')]),2)
            summary=json.loads((path/'results/local-review.json').read_text())
            self.assertEqual(summary['human_approval'],'PENDING')

    def test_local_refuses_models_without_vision(self):
        packet=self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);critic.jd(path/'packet.json',packet)
            for x in packet['screenshots']:(path/x['path']).write_bytes(b'fake png')
            with patch.object(critic,'http_json',return_value={'capabilities':['completion']}):
                with self.assertRaisesRegex(RuntimeError,'vision'):
                    critic.local_review(path,path/'out')

    def test_run_packet_generates_distinct_canonical_browser_outputs(self):
        executable=os.environ.get('MCEL_CHROMIUM_EXECUTABLE') or shutil.which('chromium') or shutil.which('chromium-browser')
        if not executable:self.skipTest('Chromium unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            result=critic.build(critic.generator.DEFAULT_DESIGN,Path(tmp))
            self.assertEqual(result['screenshots'],12)
            pkt,load=critic.open_packet(Path(result['packet']))
            self.assertEqual(len(pkt['screenshots']),12)
            self.assertEqual(pkt['component_count'],153)
            self.assertEqual(pkt['human_approval'],'PENDING')
            self.assertEqual(len({(e['candidate'],e['viewport'],e['state']) for e in pkt['screenshots']}),12)
            dry=critic.local_review(Path(result['packet']),Path(tmp)/'local',dry_run=True)
            self.assertEqual(dry['status'],'READY_NO_INFERENCE')
            self.assertEqual(len(dry['groups']),4)
            template=json.loads((Path(tmp)/'review-template.json').read_text())
            self.assertEqual(critic.parse_review(template,pkt)['status'],'CRITIQUE_ONLY')
            summary=json.loads((Path(tmp)/'browser-report.json').read_text())
            self.assertEqual(summary['distinct_wide_compositions'],3)
            self.assertTrue((Path(tmp)/'gallery.html').is_file())

if __name__=='__main__':unittest.main()
