from main_computer.mcel_slh_application import compile_slh_application


def test_surface_is_generated_from_semantics_only():
    ir, plan = compile_slh_application()
    assert not ir["layouts"]
    assert plan["policy"] == "mcel.semantic-structure.p1"
    assert any(region["recipe"] == "collection" for region in plan["regions"])
