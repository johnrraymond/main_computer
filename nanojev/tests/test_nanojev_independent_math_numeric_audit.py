"""CPU-only tests: no checkpoint, frozen backbone, or network access."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_independent_math_numeric_audit as audit

class AuditTests(unittest.TestCase):
    def test_scale_rescue(self):
        d=audit.operand_diagnostics(13.5,135.)
        self.assertTrue(d['scale_oracle_rescues'])
        self.assertFalse(d['mantissa_oracle_rescues'])
    def test_sign_rescue(self):
        d=audit.operand_diagnostics(-135.,135.)
        self.assertTrue(d['sign_oracle_rescues'])
    def test_mantissa_rescue(self):
        d=audit.operand_diagnostics(1.35,1.99)
        self.assertTrue(d['mantissa_oracle_rescues'])
    def test_zero_handling(self):
        d=audit.operand_diagnostics(0.,135.)
        self.assertIsNone(d['scale_oracle_error'])
        self.assertFalse(d['scale_oracle_rescues'])
    def test_semantic_swap(self):
        import numpy as np
        rows=audit.annotated_rows([{'a':12,'b':37,'operation':'sym_add','family':'test'}],
            {'original':{'xy':np.array([[37.,12.]]),'result':np.array([49.]),'final':np.array([49.])}},'fork','validation')
        self.assertTrue(rows[0]['swap_joint_rescue'])
        self.assertFalse(rows[0]['joint_5pct'])
    def test_summary(self):
        import numpy as np
        rows=audit.annotated_rows([{'a':12,'b':37,'operation':'sym_add','family':'test'}],
            {'original':{'xy':np.array([[12.,37.]]),'result':np.array([49.]),'final':np.array([49.])}},'fork','validation')
        aggregate=[r for r in audit.summary(rows) if r['group']=='all'][0]
        self.assertEqual(aggregate['operand_5pct'],1.)
        self.assertEqual(aggregate['joint_5pct'],1.)

if __name__=='__main__':unittest.main()
