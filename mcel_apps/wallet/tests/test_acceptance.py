from main_computer.mcel_wallet_application import compile_wallet_application, render_wallet_page

def test_package_acceptance_candidate():
    ir, plan = compile_wallet_application()
    assert ir['application']['appId'] == 'wallet'
    assert plan['policy'] == 'mcel.semantic-structure.p3'
    assert not ir['layouts'] and not ir['surfaces'][0].get('htmlBindings')
    assert 'McelSemanticWalletPorts' in render_wallet_page()
