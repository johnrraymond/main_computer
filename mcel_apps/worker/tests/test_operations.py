from main_computer.mcel_worker_application import compile_worker_application

def test_worker_consequential_ops_are_explicit():
    ir, _ = compile_worker_application()
    intents = {x['id']: x for x in ir['intents']}
    for name in ('select-destination','disconnect','save-policy'):
        assert intents['intent:' + name]['risk'] == 'consequential'
