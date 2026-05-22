from __future__ import annotations

import json
from pathlib import Path

import pytest

from main_computer import rag_new_patch_candidate_functional_smoke as smoke


def make_output_dir(tmp_path: Path) -> Path:
    output_dir = tmp_path / "debug_assets" / "rag_new_patch_recreation_tester" / "rag_new_patch_018" / "20260506_223946"
    output_dir.mkdir(parents=True)
    (output_dir / "master_results.json").write_text('{"ok": true}\n', encoding="utf-8")
    (output_dir / "proposed_new_patch.py").write_text("print('candidate')\n", encoding="utf-8")
    return output_dir


def test_build_context_requires_explicit_output_dir_and_creates_child_run(tmp_path: Path) -> None:
    repo = tmp_path
    (repo / "new_patch.py").write_text("# repo marker\n", encoding="utf-8")
    (repo / "debug_assets").mkdir()
    output_dir = make_output_dir(repo)

    ctx = smoke.build_context(
        repo,
        str(output_dir.relative_to(repo)),
        candidate_arg=None,
        run_id="functional_test",
    )

    assert ctx.output_dir == output_dir.resolve()
    assert ctx.candidate == (output_dir / "proposed_new_patch.py").resolve()
    assert ctx.run_dir == output_dir / "candidate_functional_smoke_runs" / "functional_test"
    assert ctx.provider_raw.name == "provider.raw"
    assert ctx.render_raw.name == "render.raw"


def test_resolve_candidate_rejects_file_outside_first_output_dir(tmp_path: Path) -> None:
    output_dir = make_output_dir(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        smoke.resolve_candidate(output_dir, str(outside))


def test_make_run_dir_does_not_overwrite_existing_attempt(tmp_path: Path) -> None:
    output_dir = make_output_dir(tmp_path)
    candidate = output_dir / "proposed_new_patch.py"
    first = smoke.make_run_dir(output_dir, candidate, "same")
    second = smoke.make_run_dir(output_dir, candidate, "same")

    assert first.name == "same"
    assert second.name == "same_2"
    assert first != second


def test_create_fixture_zip_and_target_are_expected(tmp_path: Path) -> None:
    repo = tmp_path
    output_dir = make_output_dir(repo)
    ctx = smoke.build_context(repo, str(output_dir), candidate_arg=None, run_id="fixture_test")

    fixture = smoke.create_fixture(ctx)

    assert Path(fixture["target"]).read_text(encoding="utf-8") == "old line\n"
    assert fixture["target_before"] == "old line\n"
    assert Path(fixture["artifact"]).exists()


def test_dry_run_output_has_diff_requires_old_and_new_lines() -> None:
    good = smoke.ProcessResult(
        argv=["python"],
        returncode=0,
        stdout="--- sample.txt\n+++ sample.txt\n-old line\n+new line\n",
        stderr="",
        elapsed_s=0.1,
    )
    missing_diff = smoke.ProcessResult(
        argv=["python"],
        returncode=0,
        stdout="no changes here\n",
        stderr="",
        elapsed_s=0.1,
    )

    assert smoke.dry_run_output_has_diff(good)
    assert not smoke.dry_run_output_has_diff(missing_diff)


def test_main_writes_pass_file_when_compile_and_dry_run_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path
    (repo / "new_patch.py").write_text("# repo marker\n", encoding="utf-8")
    (repo / "debug_assets").mkdir(exist_ok=True)
    output_dir = make_output_dir(repo)

    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        smoke,
        "docker_compile",
        lambda ctx, docker_image, timeout_s: smoke.ProcessResult(
            argv=["compile"],
            returncode=0,
            stdout="PY_COMPILE_OK\n",
            stderr="",
            elapsed_s=0.1,
        ),
    )
    def passing_dry_run(ctx, docker_image, timeout_s):
        fixture = smoke.create_fixture(ctx)
        smoke.write_json(ctx.run_dir / "fixture.json", fixture)
        return smoke.ProcessResult(
            argv=["dry-run"],
            returncode=0,
            stdout="--- sample.txt\n+++ sample.txt\n-old line\n+new line\n",
            stderr="",
            elapsed_s=0.1,
        )

    monkeypatch.setattr(smoke, "docker_dry_run_fixture", passing_dry_run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "smoke",
            str(output_dir.relative_to(repo)),
            "--run-id",
            "passes",
        ],
    )

    assert smoke.main() == 0
    run_dir = output_dir / "candidate_functional_smoke_runs" / "passes"
    status = json.loads((run_dir / "functional_smoke_status.json").read_text(encoding="utf-8"))

    assert status["ok"] is True
    assert (run_dir / "test_passed.txt").exists()
    assert (run_dir / "provider.raw").exists()
    assert (run_dir / "render.raw").exists()


def test_main_fails_before_dry_run_when_compile_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path
    (repo / "new_patch.py").write_text("# repo marker\n", encoding="utf-8")
    (repo / "debug_assets").mkdir(exist_ok=True)
    output_dir = make_output_dir(repo)

    dry_run_called = False

    def fail_compile(ctx, docker_image, timeout_s):
        return smoke.ProcessResult(
            argv=["compile"],
            returncode=1,
            stdout="",
            stderr="SyntaxError",
            elapsed_s=0.1,
        )

    def unexpected_dry_run(ctx, docker_image, timeout_s):
        nonlocal dry_run_called
        dry_run_called = True
        return smoke.ProcessResult(argv=["dry-run"], returncode=0, stdout="", stderr="", elapsed_s=0.1)

    monkeypatch.chdir(repo)
    monkeypatch.setattr(smoke, "docker_compile", fail_compile)
    monkeypatch.setattr(smoke, "docker_dry_run_fixture", unexpected_dry_run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "smoke",
            str(output_dir.relative_to(repo)),
            "--run-id",
            "compile_fails",
        ],
    )

    assert smoke.main() == 1
    assert dry_run_called is False
    run_dir = output_dir / "candidate_functional_smoke_runs" / "compile_fails"
    assert not (run_dir / "test_passed.txt").exists()
    status = json.loads((run_dir / "functional_smoke_status.json").read_text(encoding="utf-8"))
    assert status["compile_ok"] is False
    assert (run_dir / "functional_failure_context.json").exists()
    assert (run_dir / "functional_ai_repair_prompt.txt").exists()
    assert (run_dir / "run_ai_repair_from_functional_failure.ps1").exists()
    assert status["functional_ai_repair_prompt"].endswith("functional_ai_repair_prompt.txt")


def test_compile_failure_context_extracts_line_and_excerpt(tmp_path: Path) -> None:
    output_dir = make_output_dir(tmp_path)
    broken = output_dir / "proposed_new_patch.py"
    broken.write_text("one\nbad = f'{:'\nthree\n", encoding="utf-8")
    ctx = smoke.build_context(tmp_path, str(output_dir), candidate_arg=None, run_id="context")
    result = smoke.ProcessResult(
        argv=["compile"],
        returncode=1,
        stdout="",
        stderr='  File "x.py", line 2\n    bad = f"{:"\n              ^\nSyntaxError: f-string: valid expression required before ":"\n',
        elapsed_s=0.1,
    )

    context = smoke.process_failure_context(ctx=ctx, stage="compile", result=result)

    assert context["line_number"] == 2
    assert ">>    2:" in context["source_excerpt"]
    assert "bad = f" in context["source_excerpt"]


def test_write_ai_repair_handoff_creates_prompt_context_and_command(tmp_path: Path) -> None:
    output_dir = make_output_dir(tmp_path)
    ctx = smoke.build_context(tmp_path, str(output_dir), candidate_arg=None, run_id="handoff")
    context = {
        "stage": "compile",
        "candidate": str(ctx.candidate),
        "line_number": 1,
        "stderr_preview": "SyntaxError",
        "source_excerpt": ">>    1: bad",
    }

    paths = smoke.write_ai_repair_handoff(ctx, context, base_url="http://127.0.0.1:8765")

    assert Path(paths["functional_failure_context"]).exists()
    prompt = Path(paths["functional_ai_repair_prompt"]).read_text(encoding="utf-8")
    command = Path(paths["functional_ai_repair_command"]).read_text(encoding="utf-8")
    assert "Return a complete replacement implementation" in prompt
    assert "--candidate" in command
    assert "http://127.0.0.1:8765" in command

