from main_computer.mcel_wallet_application import compile_wallet_application

def test_wallet_transfer_is_consequential():
    ir, _ = compile_wallet_application()
    intents = {x['id']: x for x in ir['intents']}
    assert intents['intent:send-credits']['risk'] == 'consequential'
    assert intents['intent:retry-transfer']['risk'] == 'consequential'
    assert intents['intent:inspect-wallet']['risk'] == 'external-read'
