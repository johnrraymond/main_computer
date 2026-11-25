from main_computer.mcel_wallet_application import render_wallet_page

def test_wallet_real_runtime_mount_source():
    page = render_wallet_page()
    assert 'McelSemanticOperationalRuntime.mount' in page
    assert 'McelSemanticWalletPorts.create' in page
