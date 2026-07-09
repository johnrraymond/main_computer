from __future__ import annotations

import zipfile
from pathlib import Path

from main_computer.bootstrap.install_root import (
    install_root_archive_path_allowed,
    install_tree_file_summary,
    iter_install_root_archive_files,
    write_install_root_archive,
)


def test_install_root_preservation_archive_skips_volatile_runtime_files(tmp_path: Path) -> None:
    install_root = tmp_path / "main-computer-debug"
    (install_root / "main_computer").mkdir(parents=True)
    (install_root / "main_computer" / "__init__.py").write_text("# app\n", encoding="utf-8")
    (install_root / "runtime" / "blockchain_service").mkdir(parents=True)
    (install_root / "runtime" / "blockchain_service" / "state.json").write_text("{\"height\":1}\n", encoding="utf-8")
    (install_root / "runtime" / "main_log").mkdir(parents=True)
    (install_root / "runtime" / "main_log" / "main.log.lex").write_text("live log\n", encoding="utf-8")

    assert not install_root_archive_path_allowed("runtime", is_dir=True)
    assert not install_root_archive_path_allowed("runtime/main_log/main.log.lex", is_dir=False)

    selected = {relative for _path, relative in iter_install_root_archive_files(install_root)}
    assert selected == {"main_computer/__init__.py"}

    file_count, total_bytes = install_tree_file_summary(install_root)
    assert file_count == 1
    assert total_bytes == len("# app\n".encode("utf-8"))

    archive_path = tmp_path / "preserved.zip"
    write_install_root_archive(install_root, archive_path)

    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == ["main_computer/__init__.py"]


def test_install_root_preservation_summary_ignores_live_runtime_growth(tmp_path: Path) -> None:
    install_root = tmp_path / "main-computer-debug"
    (install_root / "main_computer").mkdir(parents=True)
    (install_root / "main_computer" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    live_log = install_root / "runtime" / "service_supervisor" / "blockchain.stdout.log"
    live_log.parent.mkdir(parents=True)
    live_log.write_text("before\n", encoding="utf-8")

    before = install_tree_file_summary(install_root)
    live_log.write_text("before\nafter installer started\n", encoding="utf-8")
    after = install_tree_file_summary(install_root)

    assert after == before
