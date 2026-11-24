import unittest
from pathlib import Path
from tools.mcel_application_shell_acceptance import classify, inventory, gateway_error, diagnose_child_502
from unittest.mock import Mock

class ShellSweep(unittest.TestCase):
    def test_registry_includes_runtime_launcher(self):
        apps=inventory(Path(__file__).resolve().parents[1])
        ids={a['id'] for a in apps}
        self.assertEqual(len(apps),len(ids))
        self.assertTrue({'desktop','browser','onlyoffice','slh','code-editor','webgl'}<=ids)
        self.assertEqual(next(a['selector'] for a in apps if a['id']=='webgl'),'#webgl-demo')
    def test_semantic_workspace_witnesses(self):
        root=Path(__file__).resolve().parents[1]
        apps={app['id']:app for app in inventory(root)}
        self.assertEqual(apps['calculator']['selector'], '#mcel-expanded-root[data-mcel-semantic-operational] > *')
        for name in ('file-explorer','task-manager','code-editor','worker','slh'):
            self.assertEqual(apps[name]['selector'], '#mcel-workbench[data-mcel-semantic-operational] > *')
            self.assertIn('data-error',apps[name]['mount_error_selector'])
        hosts={'calculator':'mcel_calculator_expanded_host.py','file-explorer':'mcel_semantic_other_apps_host.py',
               'task-manager':'mcel_semantic_generated_host.py','code-editor':'mcel_semantic_other_apps_host.py',
               'worker':'mcel_worker_application.py','slh':'mcel_slh_application.py'}
        for name,filename in hosts.items():
            self.assertIn('id="'+apps[name]['selector'].split('[')[0][1:]+'"',
                (root/'main_computer'/filename).read_text(encoding='utf-8'))
        runtime=(root/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js').read_text(encoding='utf-8')
        self.assertIn('root.setAttribute("data-mcel-semantic-operational",ir.application.appId)', runtime)
    def test_gateway_error_redacted_and_reproduced(self):
        self.assertIsNone(gateway_error({'error':'secret arbitrary text'}))
        self.assertIsNone(gateway_error({'error':{'secret':'value'}}))
        self.assertIsNone(gateway_error([]))
        context=Mock(); response=context.request.get.return_value
        response.status=502; response.json.return_value={'error':'BACKEND_RESPONSE_TOO_LARGE','secret':'never log'}
        self.assertEqual(diagnose_child_502(context,'https://dev.main-computer.localhost:8875',{'route':'/applications/document'},60000),
                         {'diagnostic_http':502,'diagnostic_error':'BACKEND_RESPONSE_TOO_LARGE'})
        context.request.get.assert_called_once_with('https://dev.main-computer.localhost:8875/applications/document?mcel-shell-child=1',headers={'Accept':'application/json,text/html'},max_redirects=0,timeout=60000)
        response.dispose.assert_called_once()
    def test_error_priorities(self):
        self.assertEqual(classify(502,None,False,False),'PARENT_HTTP_502')
        self.assertEqual(classify(200,None,False,False),'CHILD_NAVIGATION_NOT_OBSERVED')
        self.assertEqual(classify(200,{'status':502,'error':'BACKEND_RESPONSE_TOO_LARGE','frame':'DENY'},False,False),'BACKEND_RESPONSE_TOO_LARGE')
        self.assertEqual(classify(200,{'status':401},False,False),'CHILD_HTTP_401')
        self.assertEqual(classify(200,{'status':200,'frame':'DENY'},False,False),'CHILD_FRAME_DENIED')
        self.assertEqual(classify(200,{'status':200,'csp':"frame-ancestors 'none'"},False,False),'CHILD_FRAME_DENIED')
        self.assertEqual(classify(200,{'status':200},True,True),'CHILD_RECURSIVE_SHELL')
        self.assertEqual(classify(200,{'status':200},False,False),'CHILD_ROOT_NOT_VISIBLE')
        self.assertEqual(classify(200,{'status':200,'frame':'SAMEORIGIN'},True,False),'PASS')
        self.assertEqual(classify(200,{'status':200},True,False,'CHILD_SEMANTIC_MOUNT_REFUSED'),'CHILD_SEMANTIC_MOUNT_REFUSED')
if __name__=='__main__': unittest.main()
