from __future__ import annotations

from pathlib import Path


SMOKE = Path(__file__).resolve().parents[1] / "main_computer" / "rag_debug_site_only_golden_path_smoke.py"


def test_debug_site_only_smoke_is_new_e2e_gate() -> None:
    source = SMOKE.read_text(encoding="utf-8")

    assert "Debug-site-only golden website path smoke" in source
    assert "rag_golden_website_path_smoke.py" in source
    assert "debug_site_only_contract" in source
    assert "website_builder_app_ready_gate" in source
    assert "runtime/websites/debug-golden-path-*" in source
    assert "selected site id is debug-golden-path-*" in source
    assert "Git top-level is the selected site" in source


def test_debug_site_only_contract_accepts_only_debug_site_report() -> None:
    from main_computer import rag_debug_site_only_golden_path_smoke as smoke

    report = {
        "site_id": "debug-golden-path-safe",
        "site_wsl_path": "/mnt/c/repo/runtime/websites/debug-golden-path-safe",
        "checks": {
            "site_git_top_level_is_selected_site": True,
            "new_patch_target_roots_are_selected_site": True,
            "committed_debug_site_true": True,
            "committed_install_or_hub_repo_false": True,
        },
    }

    assert all(smoke.debug_site_only_contract(report).values())


def test_debug_site_only_contract_rejects_hub_or_parent_targets() -> None:
    from main_computer import rag_debug_site_only_golden_path_smoke as smoke

    hub_report = {
        "site_id": "hub-site",
        "site_wsl_path": "/mnt/c/repo/runtime/websites/hub-site",
        "checks": {
            "site_git_top_level_is_selected_site": True,
            "new_patch_target_roots_are_selected_site": True,
            "committed_debug_site_true": True,
            "committed_install_or_hub_repo_false": True,
        },
    }
    contract = smoke.debug_site_only_contract(hub_report)

    assert not contract["debug_site_id_only"]
    assert not contract["target_not_hub_or_install"]


def test_debug_site_only_evaluate_rejects_non_debug_explicit_site() -> None:
    import argparse

    from main_computer import rag_debug_site_only_golden_path_smoke as smoke

    args = argparse.Namespace(site="hub-site")
    try:
        smoke.evaluate(args)
    except ValueError as exc:
        assert "debug-golden-path" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("hub-site must not reach the golden website path")
