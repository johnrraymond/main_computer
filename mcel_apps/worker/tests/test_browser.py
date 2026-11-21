from main_computer.mcel_worker_application import render_worker_page

def test_worker_candidate_has_real_runtime_mount():
    page = render_worker_page()
    assert 'McelSemanticOperationalRuntime.mount' in page
    assert 'data-mcel-worker-phase="candidate"' in page
