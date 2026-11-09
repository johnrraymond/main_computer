from pathlib import Path
from main_computer.mcel_slh_application import compile_slh_application, render_slh_page


def test_package_acceptance_first_slice():
    ir, plan = compile_slh_application()
    assert ir["application"]["appId"] == "slh" and plan["schema"] == "mcel.generated-composition.v1"
    assert not ir["layouts"] and not ir["surfaces"][0].get("htmlBindings")
    assert all(e["risk"] == "external-read" for e in ir["effects"])
    page = render_slh_page()
    assert 'href="/applications/slh"' in page and 'href="/applications/slh/workers"' in page
