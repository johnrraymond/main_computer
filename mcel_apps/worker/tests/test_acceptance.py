from main_computer.mcel_worker_application import compile_worker_application, render_worker_page

def test_package_acceptance_candidate():
    ir, plan = compile_worker_application()
    assert ir['application']['appId'] == 'worker'
    assert plan['policy'] == 'mcel.semantic-structure.p3'
    assert ir['layouts'] == [] and not ir['surfaces'][0].get('htmlBindings')
    assert 'McelSemanticWorkerPorts' in render_worker_page()
