from main_computer.mcel_worker_application import compile_worker_application

def test_worker_identity_and_slh_enrollment_not_fabricated():
    ir, _ = compile_worker_application()
    assert 'enrollWorker' not in str(ir)
    assert 'walletSign' not in str(ir)
