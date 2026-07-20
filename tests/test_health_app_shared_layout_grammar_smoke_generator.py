from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "scripts" / "health_app_shared_layout_grammar_smoke_generator.py"


def test_health_app_shared_layout_generator_builds_html_svg_and_precision_smoke(tmp_path: Path) -> None:
    target = tmp_path / "health_app_shared_layout_grammar_smoke"

    result = subprocess.run(
        [sys.executable, str(GENERATOR), str(target)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Shared Layout Grammar Smoke" in result.stdout
    assert (target / "contracts" / "health_app.semantic.mcel").is_file()
    assert (target / "surface" / "standard_health_app.html").is_file()
    assert (target / "surface" / "standard_health_app.svg").is_file()
    assert (target / "surface" / "precision_spectacle_health_app.svg").is_file()
    assert (target / "tests" / "test_shared_layout_grammar_smoke.py").is_file()

    generated_test = target / "tests" / "test_shared_layout_grammar_smoke.py"
    generated_source = generated_test.read_text(encoding="utf-8")
    assert "test_renderers_share_exact_layout_grammar" in generated_source
    assert "test_html_standard_svg_and_precision_svg_compile_to_same_graph" in generated_source
    assert "test_nodes_and_controls_are_inside_viewport" in generated_source
    assert "test_node_boxes_do_not_collide" in generated_source

    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

    smoke = subprocess.run(
        [sys.executable, "-m", "pytest", str(generated_test), "-q"],
        cwd=target,
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    assert "11 passed" in smoke.stdout
