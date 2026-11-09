from main_computer.mcel_slh_application import render_slh_page


def test_native_deep_links_are_in_generated_host():
    page = render_slh_page("worker-bank")
    assert 'href="/applications/slh"' in page
    assert 'href="/applications/slh/workers"' in page
    assert 'data-mcel-slh-initial-view="worker-bank"' in page
