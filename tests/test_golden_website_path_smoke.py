from __future__ import annotations

import os
from pathlib import Path


SMOKE = Path(__file__).resolve().parents[1] / "main_computer" / "rag_golden_website_path_smoke.py"


def write_minimal_debug_site(root: Path, site_id: str) -> Path:
    site = root / "runtime" / "websites" / site_id
    site.mkdir(parents=True)
    (site / "site.json").write_text(
        '{"id":"' + site_id + '","kind":"debug-site","source":{"path":"runtime/websites/' + site_id + '"}}\n',
        encoding="utf-8",
    )
    (site / "index.html").write_text("<!doctype html><title>" + site_id + "</title>\n", encoding="utf-8")
    (site / "style.css").write_text("body {}\n", encoding="utf-8")
    (site / "script.js").write_text("console.log('ready')\n", encoding="utf-8")
    (site / "builder.json").write_text('{"version":2}\n', encoding="utf-8")
    return site


def test_golden_website_path_smoke_is_a_new_site_scoped_smoke() -> None:
    source = SMOKE.read_text(encoding="utf-8")

    assert "Golden website path smoke" in source
    assert "rag_debug_website_golden_path_smoke.py" in source
    assert "runtime/websites/<debug-golden-path-*>" in source
    assert "select_site_id" in source
    assert "most_recent_debug_golden_path_site" in source
    assert "ensure_builder_debug_site" in source
    assert "tools/local-platform/debug-website.py" in source
    assert "run_blessed_generated_editor_patch_artifact" in source
    assert "new_patch_command_for_site" in source
    assert "commit_command_for_site" in source
    assert "site_scoped_builder_path" in source
    assert "GIT_CEILING_DIRECTORIES" in source
    assert "git rev-parse --show-toplevel" in source
    assert "site_git_top_level_is_selected_site" in source

    forbidden = [
        "TemporaryDirectory(prefix=\"mc_debug_golden_install_",
        "write_wsl_seed_script(",
        "rm -rf \"$fixture\"",
        "find /home",
        "debug_site_id()",
        "host_mount_rejected",
    ]
    for needle in forbidden:
        assert needle not in source


def test_select_site_id_defaults_to_most_recent_debug_golden_path_site(tmp_path: Path) -> None:
    from main_computer import rag_golden_website_path_smoke as smoke

    older = write_minimal_debug_site(tmp_path, "debug-golden-path-older")
    newer = write_minimal_debug_site(tmp_path, "debug-golden-path-newer")
    ignored = write_minimal_debug_site(tmp_path, "debug-other-site")

    old_time = 1_700_000_000
    new_time = 1_800_000_000
    ignored_time = 1_900_000_000
    for path in (older, older / "site.json", older / "index.html"):
        os.utime(path, (old_time, old_time))
    for path in (newer, newer / "site.json", newer / "index.html"):
        os.utime(path, (new_time, new_time))
    for path in (ignored, ignored / "site.json", ignored / "index.html"):
        os.utime(path, (ignored_time, ignored_time))

    site_id, selection = smoke.select_site_id(builder_root=tmp_path, explicit_site=None)

    assert site_id == "debug-golden-path-newer"
    assert selection["source"] == "most_recent_debug_golden_path_site"
    assert selection["candidate_count"] == 2


def test_select_site_id_allows_explicit_debug_golden_path_site(tmp_path: Path) -> None:
    from main_computer import rag_golden_website_path_smoke as smoke

    site_id, selection = smoke.select_site_id(
        builder_root=tmp_path,
        explicit_site="debug-golden-path-explicit",
    )

    assert site_id == "debug-golden-path-explicit"
    assert selection["source"] == "explicit"


def test_select_site_id_rejects_non_golden_debug_site(tmp_path: Path) -> None:
    from main_computer import rag_golden_website_path_smoke as smoke

    try:
        smoke.select_site_id(builder_root=tmp_path, explicit_site="debug-bootstrap")
    except ValueError as exc:
        assert "debug-golden-path" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("debug-bootstrap should not be accepted as a golden website path target")


def test_resolve_builder_site_target_allows_only_selected_runtime_debug_golden_site(tmp_path: Path) -> None:
    from main_computer import rag_golden_website_path_smoke as smoke

    site = write_minimal_debug_site(tmp_path, "debug-golden-path-target")
    target = smoke.resolve_builder_site_target(
        builder_root=tmp_path,
        site_id="debug-golden-path-target",
        site_path=site,
    )

    assert target.ok
    assert target.site_wsl_path
    assert target.site_wsl_path.endswith("/runtime/websites/debug-golden-path-target")
    assert target.websites_root
    assert target.websites_root.endswith("/runtime/websites")

    outside = tmp_path / "debug-golden-path-target"
    outside.mkdir()
    rejected_outside = smoke.resolve_builder_site_target(
        builder_root=tmp_path,
        site_id="debug-golden-path-target",
        site_path=outside,
    )
    assert not rejected_outside.ok
    assert rejected_outside.reason == "outside_builder_websites_root"

    non_golden = write_minimal_debug_site(tmp_path, "debug-bootstrap")
    rejected_non_golden = smoke.resolve_builder_site_target(
        builder_root=tmp_path,
        site_id="debug-bootstrap",
        site_path=non_golden,
    )
    assert not rejected_non_golden.ok
    assert rejected_non_golden.reason == "not_debug_golden_path_site"


def test_new_patch_command_targets_selected_builder_site(tmp_path: Path) -> None:
    from main_computer import rag_golden_website_path_smoke as smoke

    site = write_minimal_debug_site(tmp_path, "debug-golden-path-patch-target")
    target = smoke.resolve_builder_site_target(
        builder_root=tmp_path,
        site_id="debug-golden-path-patch-target",
        site_path=site,
    )

    command = smoke.new_patch_command_for_site(
        root=tmp_path,
        zip_path=tmp_path / "patch.zip",
        target=target,
        wsl_command="wsl.exe",
        distribution="MainComputerExecutorTest",
        dry_run=True,
    )

    assert "--target-root" in command
    assert command[command.index("--target-root") + 1] == target.site_wsl_path
    assert command[-1] == "--dry-run"
    assert smoke.target_root_ok(command, target=target)


def test_site_git_commands_use_ceiling_and_do_not_climb_to_builder_repo(tmp_path: Path) -> None:
    from main_computer import rag_golden_website_path_smoke as smoke

    site = write_minimal_debug_site(tmp_path, "debug-golden-path-git-boundary")
    target = smoke.resolve_builder_site_target(
        builder_root=tmp_path,
        site_id="debug-golden-path-git-boundary",
        site_path=site,
    )

    inside = smoke.wsl_site_git(
        target=target,
        git_args=["rev-parse", "--show-toplevel"],
        wsl_command="wsl.exe",
        distribution="MainComputerExecutorTest",
    )
    assert "--exec" in inside
    exec_argv = inside[inside.index("--exec") + 1 :]
    assert exec_argv[:3] == [
        "env",
        "GIT_CEILING_DIRECTORIES=" + target.websites_root,
        "git",
    ]
    assert smoke.git_command_is_site_scoped(
        inside,
        target=target,
        wsl_command="wsl.exe",
        distribution="MainComputerExecutorTest",
    )

    script = smoke.ensure_site_git_script(target=target, ai_branch="ai/debug-website-golden-path")
    assert "[ ! -d .git ]" in script
    assert "git rev-parse --is-inside-work-tree" not in script
    assert "git rev-parse --show-toplevel" in script
    assert 'if [ "$top" != "$PWD" ]; then' in script
