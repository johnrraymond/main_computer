import unittest
from email.message import EmailMessage
from unittest.mock import patch
from tools import email_engine_provider_acceptance as auth


def message(subject='marker', address='fixture@example.test', mid='<a@example.test>'):
    m=EmailMessage(); m['Subject']=subject; m['To']=address; m['Message-ID']=mid
    return m


class ProviderSmokeTests(unittest.TestCase):
    def test_modes(self):
        self.assertEqual(set(auth.MODES), {'outlook-device','microsoft365-oauth','gmail-oauth','gmail-app-password','yahoo-app-password','generic-tls'})

    def test_isolation(self):
        for candidate in ('gofer','mailflow'):
            spec,url=auth.spec_for(candidate,'x'*64,18896)
            self.assertEqual(url,'http://localhost:18896')
            self.assertEqual(spec['networks'],{'default':{'driver':'bridge'}})
            self.assertNotIn('fixture',spec['services']); self.assertNotIn('probe',spec['services'])
            ports=[port for svc in spec['services'].values() for port in svc.get('ports',[])]
            self.assertEqual(len(ports),1); self.assertTrue(ports[0].startswith('127.0.0.1:18896:'))
            self.assertTrue(all(not svc.get('container_name') for svc in spec['services'].values()))
            self.assertTrue(all(not vol.get('external') for vol in spec['volumes'].values()))
        spec,_=auth.spec_for('gofer','x'*64,18896)
        self.assertNotIn('GOFER_ALLOW_INSECURE_LAN',spec['services']['engine']['environment'])
        self.assertEqual(spec['services']['engine']['environment']['GOFER_AUTH_MODE'],'personal')

    def test_mailflow_frontend(self):
        spec,_=auth.spec_for('mailflow','x'*64,18896)
        self.assertIn('backend',spec['services']); self.assertIn('db',spec['services'])
        self.assertEqual(spec['services']['backend']['environment']['APP_URL'],'http://localhost:18896')
        self.assertEqual(spec['services']['frontend']['depends_on']['backend']['condition'],'service_healthy')

    def test_gofer_marker(self):
        with patch.object(auth.probe,'gofer_ids',return_value=[{'account':'a','id':1}]), patch.object(auth.probe,'gofer_mime',return_value=message()):
            self.assertEqual(auth.verify_marker('gofer',None,'fixture@example.test','marker'),('a','<a@example.test>'))
            self.assertIsNone(auth.verify_marker('gofer',None,'other@example.test','marker'))
            self.assertIsNone(auth.verify_marker('gofer',None,'fixture@example.test','wrong'))

    def test_mailflow_marker(self):
        with patch.object(auth.probe,'account_ids',return_value={'fixture@example.test':'a'}), patch.object(auth.probe,'search',return_value=[{'account_id':'b','subject':'marker','id':1},{'account_id':'a','subject':'marker','id':2}]), patch.object(auth.probe,'mailflow_mime',return_value=message()) as mime:
            self.assertEqual(auth.verify_marker('mailflow',None,'fixture@example.test','marker'),('a','<a@example.test>'))
            mime.assert_called_once()

    def test_duplicate_rejected(self):
        with patch.object(auth.probe,'gofer_ids',return_value=[{'account':'a','id':1},{'account':'a','id':2}]), patch.object(auth.probe,'gofer_mime',return_value=message()):
            with self.assertRaises(AssertionError): auth.verify_marker('gofer',None,'fixture@example.test','marker')

    def test_operator_attestation_not_enough(self):
        report={'checks':dict.fromkeys(auth.CHECKS,'NOT_RUN'),'cleanup':'PASS'}
        report['checks']['operator_enrollment']='PASS'
        self.assertEqual(auth.report_status(report),'INCOMPLETE')
        for key in auth.CHECKS[:9]: report['checks'][key]='PASS'
        self.assertEqual(auth.report_status(report),'AUTH_SMOKE_PASS_OPERATOR_ASSISTED')
        report['checks']['same_account']='FAIL'
        self.assertEqual(auth.report_status(report),'FAIL')
        report['checks']['same_account']='PASS'; report['cleanup']='FAIL'
        self.assertEqual(auth.report_status(report),'INCOMPLETE')

    def test_marker_timeout(self):
        now=[0]
        with patch.object(auth,'verify_marker',return_value=None), patch.object(auth,'emit'):
            with self.assertRaises(TimeoutError): auth.wait_marker('gofer',None,'fixture@example.test','marker',15,clock=lambda:now[0],sleep=lambda n:now.__setitem__(0,now[0]+n))
        self.assertEqual(now[0],15)

    def test_wait_for_new_message(self):
        now=[0]
        with patch.object(auth,'verify_marker',side_effect=[None,('a','id')]), patch.object(auth,'emit'):
            self.assertEqual(auth.wait_marker('gofer',None,'fixture@example.test','marker',15,clock=lambda:now[0],sleep=lambda n:now.__setitem__(0,now[0]+n)),('a','id'))

if __name__=='__main__': unittest.main()
