"""Patch 12: native knowledge + *existing* Application IR + v2 consumer contract."""
import copy
import json
from pathlib import Path
import subprocess
import unittest

from main_computer.mcel_application_ir import validate_application_ir
from main_computer.mcel_knowledge import SemanticCatalogError, load_catalog
from main_computer.mcel_knowledge.ir_bridge import (
    compile_knowledge_ir, resolve_knowledge, collection_projection,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tools/fixtures/mcel_computer_knowledge/expanded_computer_snapshot.v1.json'


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = load_catalog()
        cls.data = json.loads(FIXTURE.read_text(encoding='utf-8'))

    def compile(self, ob, **kwargs):
        return compile_knowledge_ir('test-app', observation_ids=[ob], **kwargs)

    def test_registered_multiapp_models_validate_under_existing_ir(self):
        ids = ['task-manager.processes', 'task-manager.connections', 'file-explorer.entries',
               'task-manager.schedules', 'worker.market-workers', 'worker.sessions']
        ir = compile_knowledge_ir('test-app', observation_ids=ids)['applicationIr']
        self.assertTrue(validate_application_ir(ir).valid)
        self.assertEqual(len(ir['models']), len(ids))
        self.assertEqual(len(ir['intents']), 0)
        self.assertEqual(len(ir['effects']), 0)
        self.assertEqual(len(ir['surfaces']), 0)
        self.assertEqual(ir['models'][0]['conceptRef'], self.cat['observations'][ir['models'][0]['observationRef']]['concept'])

    def test_remapped_producer_same_semantic_different_binding_fingerprint(self):
        old = self.compile('task-manager.processes')
        new = self.compile('task-manager.processes', bindings={'task-manager.processes': {
            'source': 'procItems',
            'properties': {'pid': 'process_identifier', 'created_at': 'started', 'name': 'executable'}}})
        self.assertEqual(old['semanticFingerprint'], new['semanticFingerprint'])
        self.assertNotEqual(old['sourceBindingFingerprint'], new['sourceBindingFingerprint'])
        raw = {'procItems': [{'process_identifier': 7, 'started': '2026-10-27', 'executable': 'python',
                              'memory_rss': 0, 'memory_availability': 'unavailable', 'status':'running', 'cmdline':'python'}]}
        original = copy.deepcopy(raw)
        rec = resolve_knowledge(raw, new)['sources']['task-manager.processes']['records'][0]
        self.assertEqual(rec['identity']['parts'], [7, '2026-10-27'])
        self.assertEqual(rec['properties']['resident_memory']['state'], 'unavailable')
        self.assertEqual(rec['properties']['resident_memory']['raw'], 0)
        self.assertEqual(raw, original)

    def test_snapshot_remains_untouched_multiapp(self):
        src = copy.deepcopy(self.data)
        bridge = compile_knowledge_ir('test-app', observation_ids=[
            'file-explorer.entries','worker.market-workers','worker.sessions','task-manager.schedules'])
        view = resolve_knowledge(src, bridge)
        self.assertEqual(src, self.data)
        self.assertEqual(view['sources']['file-explorer.entries']['records'][0]['identity']['parts'], ['workspace', 'tools/runs'])
        self.assertEqual(view['sources']['worker.sessions']['records'][0]['relationships'][0]['status'], 'candidate-only')
        self.assertEqual(view['sources']['task-manager.schedules']['records'][0]['properties']['run_at']['state'], 'present')
        self.assertFalse(any(x['executable'] or x['authorized'] for x in view['operations'].values()))

    def test_generic_browser_projection_exact_state_and_source(self):
        view = resolve_knowledge(self.data, self.compile('file-explorer.entries'))
        projected = collection_projection(view,'file-explorer.entries')
        self.assertEqual(projected['spec']['schema'], 'mcel.semantic-collection-spec.v2')
        self.assertEqual(len(projected['rows']), 2)
        self.assertEqual(projected['rows'][0]['size_bytesAvailability'], 'unavailable')
        self.assertNotIn('size_bytes', projected['rows'][0])
        self.assertEqual(projected['rows'][0]['mcelCanonicalRecord']['properties']['size_bytes']['raw'], 0)
        self.assertEqual(projected['rows'][0]['mcelCanonicalRecord']['properties']['size_bytes']['state'], 'unavailable')
        self.assertEqual(projected['rows'][1]['size_bytes'], 0)
        self.assertEqual(projected['rows'][1]['size_bytesAvailability'], 'present')
        self.assertNotIn('reconciliation', projected['spec'])  # path identity alone cannot prove same file lifetime
        self.assertTrue(projected['noOperations'])
        self.assertNotIn('operation', json.dumps(projected['spec']))

    def test_browser_v2_runtime_accepts_projection(self):
        view = resolve_knowledge(self.data, self.compile('file-explorer.entries'))
        projection = collection_projection(view,'file-explorer.entries')
        js = ROOT / 'main_computer/web/applications/scripts/mcel-semantic-value-contract.js'
        runner = '''const fs=require('fs'),vm=require('vm');
          const context={structuredClone};context.window=context;context.globalThis=context;
          vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
          vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
          const x=JSON.parse(fs.readFileSync(0,'utf8'));
          context.McelSemanticValueContract.validate(x.spec);
          const a=x.rows.map(row=>context.McelSemanticValueContract.rowValues(row,x.spec));
          if(a.length!==2 || a[0].fields.size_bytes.availability!=='unavailable' ||
             a[1].fields.size_bytes.rawValue!==0)throw Error('invalid semantic parity');
          const rendered=context.McelSemanticCollectionWorkspace.project(x.rows,x.spec);
          if(rendered.value_records[0].fields.size_bytes.availability!=='unavailable' ||
             rendered.value_records[1].fields.size_bytes.rawValue!==0 ||
             rendered.rawRecords.get(x.rows[0].mcelIdentity).mcelCanonicalRecord.properties.size_bytes.raw!==0)
             throw Error('lost exact source evidence in browser');
          console.log('MCEL_KNOWLEDGE_BROWSER_V2_PASS');'''
        workspace = ROOT / 'main_computer/web/applications/scripts/mcel-semantic-collection-workspace.js'
        completed = subprocess.run(['node','-e',runner,str(js),str(workspace)], input=json.dumps(projection),
                                   text=True, capture_output=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('MCEL_KNOWLEDGE_BROWSER_V2_PASS', completed.stdout)

    def test_identity_failure_refuses_collection(self):
        data = copy.deepcopy(self.data)
        del data['root_id']
        view = resolve_knowledge(data, self.compile('file-explorer.entries'))
        with self.assertRaisesRegex(SemanticCatalogError, 'Identity unsafe'):
            collection_projection(view,'file-explorer.entries')

    def test_duplicate_identity_refuses_collection(self):
        data = copy.deepcopy(self.data)
        data['entries'].append(copy.deepcopy(data['entries'][0]))
        view = resolve_knowledge(data, self.compile('file-explorer.entries'))
        with self.assertRaisesRegex(SemanticCatalogError, 'Identity unsafe'):
            collection_projection(view,'file-explorer.entries')

    def test_unknown_property_source_and_future_catalog_refuse(self):
        for bind in [ {'file-explorer.entries': {'properties': {'ghost':'name'}}},
                      {'file-explorer.entries': {'source': '_private'}}]:
            with self.assertRaises(SemanticCatalogError):
                self.compile('file-explorer.entries', bindings=bind)
        bridge = self.compile('file-explorer.entries')
        tampered = copy.deepcopy(bridge)
        tampered['catalogFingerprint'] = 'sha256:bad'
        with self.assertRaisesRegex(SemanticCatalogError, 'bridge'):
            resolve_knowledge(self.data, tampered)

    def test_ir_tampering_refused(self):
        bridge = self.compile('file-explorer.entries')
        broken = copy.deepcopy(bridge)
        broken['applicationIr']['models'][0]['fields'][0]['conceptRef']='mc.fake'
        with self.assertRaisesRegex(SemanticCatalogError, 'IR changed'):
            resolve_knowledge(self.data, broken)

    def test_no_domain_dispatch_producer_remap_worker(self):
        b = self.compile('worker.sessions', bindings={'worker.sessions': {
            'source': 'my_sessions', 'properties': {'session_id':'job_key'}}})
        item = copy.deepcopy(self.data['worker_sessions']['accepted_sessions']['s1'])
        item['job_key']=item.pop('session_id')
        view = resolve_knowledge({'my_sessions': [item]}, b)
        r=view['sources']['worker.sessions']['records'][0]
        self.assertEqual(r['properties']['session_id']['raw'], 's1')
        self.assertEqual(r['identity']['status'], 'complete')
        self.assertIn('worker.sessions', view['sources'])

    def test_explicit_catalog_generation_guarantee_controls_reconciliation(self):
        cat = copy.deepcopy(self.cat)
        cat['observations']['worker.sessions']['identity']['continuity'] = 'verified-generation'
        bridge = compile_knowledge_ir('test-app', observation_ids=['worker.sessions'], catalog=cat)
        view = resolve_knowledge(self.data, bridge, catalog=cat)
        spec = collection_projection(view,'worker.sessions',catalog=cat)['spec']
        self.assertEqual(spec['reconciliation']['generationFrom'], 'mcelIdentity')

    def test_future_application_catalog_fragment_requires_no_compiler_branch(self):
        cat = copy.deepcopy(self.cat)
        cat['observations']['other-app.jobs'] = {
            'id': 'other-app.jobs', 'concept': 'mc.process', 'source': 'other_jobs',
            'cardinality': 'many', 'identity': {'fields':['proc_key'], 'scope':'other-app-only'},
            'properties': [{'name':'id','source':'proc_key','type':'mc.identifier','required':True},
                           {'name':'name','source':'label','type':'mc.text'}]}
        bridge = compile_knowledge_ir('other-app', observation_ids=['other-app.jobs'], catalog=cat)
        graph = resolve_knowledge({'other_jobs':[{'proc_key':'j1','label':'Alpha'}]}, bridge, catalog=cat)
        self.assertEqual(graph['sources']['other-app.jobs']['records'][0]['identity']['parts'], ['j1'])
        proj = collection_projection(graph, 'other-app.jobs',catalog=cat)
        self.assertEqual(proj['rows'][0]['name'], 'Alpha')

    def test_unsupported_numeric_timestamp_preserved_not_called_failed(self):
        view = resolve_knowledge(self.data, self.compile('file-explorer.entries'))
        proj = collection_projection(view,'file-explorer.entries')
        self.assertIn({'property':'modified_at','reason':'numeric-timestamp-not-supported-by-v2'},proj['deferredFields'])
        self.assertEqual(proj['rows'][1]['mcelCanonicalRecord']['properties']['modified_at']['state'],'present')
        self.assertEqual(proj['rows'][1]['mcelCanonicalRecord']['properties']['modified_at']['raw'],1793131000.0)

    def test_readonly_ops_do_not_become_runtime_intents(self):
        b = self.compile('task-manager.schedules')
        v = resolve_knowledge(self.data,b)
        self.assertEqual(b['applicationIr']['intents'], [])
        self.assertEqual(b['applicationIr']['capabilities'], [])
        for op in v['operations'].values():
            self.assertFalse(op['authorized'])
            self.assertFalse(op['executable'])


if __name__ == '__main__':
    unittest.main()
