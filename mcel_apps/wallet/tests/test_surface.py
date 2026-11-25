from main_computer.mcel_wallet_application import compile_wallet_application

def test_wallet_generated_navigation():
    ir, plan = compile_wallet_application()
    assert not ir['layouts']
    assert any(x['recipe'] == 'navigation' for x in plan['regions'])
