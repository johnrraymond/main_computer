from main_computer.mcel_worker_application import compile_worker_application

def test_worker_generated_regions():
    ir, plan = compile_worker_application()
    assert not ir['layouts']
    assert any(x['recipe'] == 'navigation' for x in plan['regions'])
    assert any(x['recipe'] == 'task' for x in plan['regions'])  # Worker ring form remains
