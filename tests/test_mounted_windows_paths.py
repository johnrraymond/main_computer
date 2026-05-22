from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from main_computer.config import MainComputerConfig
from main_computer.mounted_windows_paths import (
    MountedWindowsPathResolver,
    build_mounted_windows_path_resolver,
    discover_host_drive_mounts,
    host_drive_fallback_candidates,
    host_path_to_windows_path,
    parse_windows_drive_mounts,
    parse_windows_drive_mounts_file,
    resolve_existing_host_path,
    windows_path_to_host_path,
)


FIXTURE_WINDOWS_USER = "fixture-user"
FIXTURE_DESKTOP_RELATIVE = f"Users/{FIXTURE_WINDOWS_USER}/Desktop"
FIXTURE_DESKTOP_WINDOWS = rf"C:\Users\{FIXTURE_WINDOWS_USER}\Desktop"


class MountedWindowsPathResolverTests(unittest.TestCase):
    def test_parse_env_mounts(self) -> None:
        mounts = parse_windows_drive_mounts("C=/host/c;D=/host/d")

        self.assertEqual(sorted(mounts), ["C", "D"])
        self.assertEqual(mounts["C"].root_id, "drive-c")
        self.assertEqual(mounts["D"].display_root, "D:\\")

    def test_parse_json_mounts_file(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "mounts.json"
            path.write_text(json.dumps({"host_os": "windows", "drives": {"Z": tempdir}}), encoding="utf-8")

            mounts = parse_windows_drive_mounts_file(path)

        self.assertEqual(sorted(mounts), ["Z"])

    def test_resolve_display_and_status_for_synthetic_mount(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            nested = root / "Users" / FIXTURE_WINDOWS_USER
            nested.mkdir(parents=True)
            target = nested / "notes.txt"
            target.write_text("hello\n", encoding="utf-8")
            resolver = MountedWindowsPathResolver(parse_windows_drive_mounts(f"Z={root}"), path_mode="mounted-windows", host_os="windows")

            resolved = resolver.resolve("drive-z", f"Users/{FIXTURE_WINDOWS_USER}/notes.txt")

            self.assertEqual(resolved, target.resolve())
            self.assertEqual(resolver.relative_path("drive-z", target), f"Users/{FIXTURE_WINDOWS_USER}/notes.txt")
            self.assertEqual(resolver.display_path("drive-z", f"Users/{FIXTURE_WINDOWS_USER}/notes.txt"), rf"Z:\Users\{FIXTURE_WINDOWS_USER}\notes.txt")
            self.assertTrue(resolver.status()["enabled"])
            self.assertEqual(resolver.status()["count"], 1)

    def test_rejects_absolute_paths_and_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            resolver = MountedWindowsPathResolver(parse_windows_drive_mounts(f"Z={tempdir}"), path_mode="mounted-windows", host_os="windows")
            for relative_path in ["../escape.txt", "nested/../../escape.txt", "/absolute/path.txt", "C:/absolute/path.txt"]:
                with self.subTest(relative_path=relative_path):
                    with self.assertRaises(ValueError):
                        resolver.resolve("drive-z", relative_path, must_exist=False)

    def test_build_resolver_from_config_defaults_to_disabled_local_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            resolver = build_mounted_windows_path_resolver(MainComputerConfig(workspace=Path(tempdir)))

        self.assertFalse(resolver.enabled)
        self.assertEqual(resolver.status()["path_mode"], "local")
        self.assertEqual(resolver.root_candidates(), {})

    def test_windows_path_candidates_keep_literal_first_then_host_alias(self) -> None:
        candidates = host_drive_fallback_candidates(rf"{FIXTURE_DESKTOP_WINDOWS}\notes.txt")

        self.assertEqual(str(candidates[0]), rf"{FIXTURE_DESKTOP_WINDOWS}\notes.txt")
        self.assertEqual(candidates[1].as_posix(), f"/host/c/Users/{FIXTURE_WINDOWS_USER}/Desktop/notes.txt")

    def test_host_path_candidates_keep_literal_first_then_windows_alias(self) -> None:
        candidates = host_drive_fallback_candidates("/host/d/Projects/game/project.json")

        self.assertEqual(candidates[0].as_posix(), "/host/d/Projects/game/project.json")
        self.assertEqual(str(candidates[1]), r"D:\Projects\game\project.json")

    def test_host_alias_conversion_rejects_ambiguous_paths(self) -> None:
        for path in ["/host/cc/file.txt", "/host/1/file.txt", "/host/c/../secret.txt", r"C:relative\path.txt"]:
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    host_drive_fallback_candidates(path)

    def test_host_alias_conversion_helpers(self) -> None:
        self.assertEqual(windows_path_to_host_path(r"E:\USB\note.md").as_posix(), "/host/e/USB/note.md")
        self.assertEqual(str(host_path_to_windows_path("/host/z/share/file.csv")), r"Z:\share\file.csv")

    def test_resolve_existing_host_path_uses_literal_before_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            host_root = root / "host"
            literal = root / "literal.txt"
            literal.write_text("literal\n", encoding="utf-8")

            resolved = resolve_existing_host_path(literal, host_root=host_root)

            self.assertEqual(resolved, literal.resolve())

    def test_resolve_existing_host_path_uses_host_fallback_when_literal_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            host_root = root / "host"
            fallback = host_root / "c" / "Users" / FIXTURE_WINDOWS_USER / "notes.txt"
            fallback.parent.mkdir(parents=True)
            fallback.write_text("fallback\n", encoding="utf-8")

            resolved = resolve_existing_host_path(rf"C:\Users\{FIXTURE_WINDOWS_USER}\notes.txt", host_root=host_root)

            self.assertEqual(resolved, fallback.resolve())

    def test_resolve_existing_host_path_fails_when_no_candidate_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            with self.assertRaises(ValueError):
                resolve_existing_host_path(rf"C:\Users\{FIXTURE_WINDOWS_USER}\missing.txt", host_root=Path(tempdir) / "host")


    def test_discover_host_drive_mounts_for_windows_host(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            host_root = Path(tempdir) / "host"
            (host_root / "c").mkdir(parents=True)
            (host_root / "d").mkdir()
            (host_root / "cc").mkdir()
            (host_root / "1").mkdir()

            mounts = discover_host_drive_mounts(host_root, host_os="windows")

        self.assertEqual(sorted(mounts), ["C", "D"])
        self.assertEqual(mounts["C"].root_id, "drive-c")
        self.assertEqual(mounts["D"].display_root, "D:\\")

    def test_discover_host_drive_mounts_ignores_non_windows_host(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            host_root = Path(tempdir) / "host"
            (host_root / "c").mkdir(parents=True)

            self.assertEqual(discover_host_drive_mounts(host_root, host_os="linux"), {})
            self.assertEqual(discover_host_drive_mounts(host_root, host_os="auto"), {})

    def test_build_resolver_auto_discovers_windows_host_drive_root(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            host_root = root / "host"
            (host_root / "c" / "Users" / FIXTURE_WINDOWS_USER).mkdir(parents=True)
            config = MainComputerConfig(
                workspace=root,
                path_mode="mounted-windows",
                host_os="windows",
                host_drive_root=host_root,
            )

            resolver = build_mounted_windows_path_resolver(config)

            self.assertTrue(resolver.enabled)
            self.assertIn("drive-c", resolver.root_candidates())
            self.assertEqual(resolver.display_path("drive-c", f"Users/{FIXTURE_WINDOWS_USER}"), rf"C:\Users\{FIXTURE_WINDOWS_USER}")



if __name__ == "__main__":
    unittest.main()
