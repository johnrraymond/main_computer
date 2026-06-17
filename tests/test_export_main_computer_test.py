import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ExportMainComputerTestTests(unittest.TestCase):
    def test_export_script_includes_pretty_docs_and_excludes_generated_files(self) -> None:
        script = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")

        self.assertIn('"pretty_docs"', script)
        self.assertIn('"game_projects"', script)
        self.assertIn('"__pycache__"', script)
        self.assertIn('".pyc"', script)
        self.assertIn('".tmp"', script)
        self.assertIn('".bak"', script)
        self.assertIn('"main_computer"', script)
        self.assertIn('"tests"', script)
        self.assertIn('"new_patch.py"', script)
        self.assertIn('"export-main-computer-test.ps1"', script)
        self.assertIn('"prod-command.py"', script)
        self.assertIn('"docker-compose.onlyoffice.yml"', script)
        self.assertIn('"docker-compose.applications.yml"', script)
        self.assertIn('"docker-compose.gitea.yml"', script)
        self.assertIn('"deploy/coolify/local-docker"', script)
        self.assertIn('"proto-dev"', script)
        self.assertIn('"tools"', script)
        self.assertTrue((ROOT / "tools" / "project_diagnosis.py").exists())
        self.assertTrue((ROOT / "tools" / "release_diagnosis.py").exists())
        self.assertTrue((ROOT / "tools" / "executor_diagnosis.py").exists())
        self.assertTrue((ROOT / "tools" / "ollama_prompt_space_tester.py").exists())
        self.assertIn('"dev-chain-wallet-smoke-guide.py"', script)
        self.assertIn('"release_reports"', script)



    def test_export_script_includes_versioned_runtime_websites_without_unblocking_all_runtime(self) -> None:
        script = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")

        self.assertIn('"runtime/websites/hub-site"', script)
        self.assertIn('"runtime/websites/hub-site/"', script)
        self.assertIn('"runtime/websites/johnrraymond"', script)
        self.assertIn('"runtime/websites/johnrraymond/"', script)
        self.assertIn('"runtime/"', script)
        self.assertIn('$allowedGeneratedPrefixes', script)
        self.assertIn('$isAllowedGeneratedPrefixPath', script)
        self.assertIn('-and -not $isAllowedGeneratedPrefixPath', script)


    def test_export_script_blocks_generated_local_platform_compose_outputs(self) -> None:
        script = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")

        self.assertIn('"deploy/local-platform/generated/docker-compose.websites.yml"', script)
        self.assertIn('"deploy/local-platform/generated/"', script)
        self.assertIn('"/.main-computer/local-platform/docker-compose.yml"', script)
        self.assertIn('$blockedGeneratedExactPaths', script)
        self.assertIn('$blockedGeneratedPrefixes', script)
        self.assertIn('$blockedGeneratedSuffixes', script)


    def test_gitignore_preserves_versioned_runtime_websites_but_ignores_generated_compose_outputs(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("!runtime/websites/hub-site/**", gitignore)
        self.assertIn("!runtime/websites/johnrraymond/**", gitignore)
        self.assertIn("deploy/local-platform/generated/", gitignore)
        self.assertIn("runtime/websites/*/.main-computer/local-platform/docker-compose.yml", gitignore)


    def test_export_script_includes_network_deployment_latest_manifests_without_unblocking_all_runtime(self) -> None:
        script = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")

        self.assertIn('"runtime/deployments/dev/latest.json"', script)
        self.assertIn('"runtime/deployments/dev/latest.json"', script)
        self.assertIn('"runtime/deployments/test/latest.json"', script)
        self.assertIn('"runtime/deployments/testnet/latest.json"', script)
        self.assertIn('"runtime/deployments/mainnet/latest.json"', script)
        self.assertIn('"runtime/deployments"', script)
        self.assertIn('"runtime/deployments/mainnet"', script)
        self.assertIn('"runtime/"', script)
        self.assertIn('$allowedGeneratedExactPaths', script)
        self.assertIn('$allowedGeneratedParentDirs', script)
        self.assertIn('-and -not $isAllowedGeneratedPrefixPath', script)

    def test_export_script_prunes_generated_documentation_work_history(self) -> None:
        script = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")

        self.assertIn('"generated_component_docs/work"', script)
        self.assertIn('"generated_component_docs/archive"', script)
        self.assertIn('"generated_component_docs/work/"', script)
        self.assertIn('"generated_component_docs/archive/"', script)
        self.assertIn('"generated_component_docs/doc-build.json"', script)
        self.assertIn('"generated_component_docs/doc-health.json"', script)
        self.assertIn('"generated_component_docs/graph.json"', script)
        self.assertIn('"tools/documentation/plan-"', script)
        self.assertIn('"contracts/out"', script)
        self.assertIn('"contracts/out/"', script)


    def test_export_script_has_installer_rehome_mode_without_changing_default_archive(self) -> None:
        script = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")

        self.assertIn("[switch]$InstallerReHome", script)
        self.assertIn('"mcrh"', script)
        self.assertIn('$installerReHomeRunRoot = Join-Path $installerReHomeBaseRoot $nonce', script)
        self.assertIn('$ArchiveRoot = Join-Path $installerReHomeRunRoot "z"', script)
        self.assertIn('$installerReHomeExtractRoot = Join-Path $installerReHomeRunRoot "x"', script)
        self.assertIn('$tempRoot = Join-Path $installerReHomeRunRoot "s"', script)
        self.assertIn('[System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $installerReHomeExtractRoot)', script)
        self.assertIn('Write-Output $installerReHomeRepoRoot', script)
        self.assertIn('$ArchiveRoot = Join-Path (Split-Path -Parent $SourceRoot) "archive"', script)


    def test_export_script_installer_rehome_parameter_has_no_alias_collision(self) -> None:
        script = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")

        match = __import__("re").search(
            r"\[Alias\(([^\)]*)\)\]\s*\[switch\]\$InstallerReHome",
            script,
            __import__("re").S,
        )
        self.assertIsNotNone(match)
        aliases = [value.strip().strip('"\'') for value in match.group(1).split(",")]
        lower_aliases = [value.casefold() for value in aliases]
        self.assertEqual(len(lower_aliases), len(set(lower_aliases)))
        self.assertNotIn("installerrehome", lower_aliases)

    def test_pretty_docs_seed_files_exist(self) -> None:
        index_path = ROOT / "pretty_docs" / "index.json"
        guide_path = ROOT / "pretty_docs" / "main-computer-user-guide.md"

        self.assertTrue(index_path.exists())
        self.assertTrue(guide_path.exists())
        self.assertIn("Main Computer User Guide", guide_path.read_text(encoding="utf-8"))

    def test_game_editor_seed_project_exists(self) -> None:
        project_root = ROOT / "game_projects" / "starter-game"
        webgl_root = ROOT / "game_projects" / "webgl-demo"

        self.assertTrue((project_root / "project.json").exists())
        self.assertTrue((project_root / "assets").exists())
        self.assertTrue((project_root / "scripts").exists())
        self.assertTrue((project_root / "data").exists())
        self.assertTrue((webgl_root / "project.json").exists())
        self.assertTrue((webgl_root / "assets").exists())
        self.assertTrue((webgl_root / "scripts").exists())
        self.assertTrue((webgl_root / "data").exists())
        webgl_project = (webgl_root / "project.json").read_text(encoding="utf-8")
        self.assertIn("WebGL Demo", webgl_project)
        self.assertIn("hero-sprite", webgl_project)
        self.assertIn("sprite-actor", webgl_project)
        self.assertIn("particle-emitter", webgl_project)
        self.assertIn('"particleCount": 97', webgl_project)
        self.assertIn('"particleMultiplier": 2', webgl_project)
        self.assertIn('"effectMultiplier": 2', webgl_project)
        self.assertIn('"moveSpeed": 3.15', webgl_project)
        self.assertIn("left-click", webgl_project)
        self.assertIn("movementBounds", webgl_project)
        self.assertIn("spriteRig", webgl_project)
        self.assertIn("parentId", webgl_project)
        self.assertIn("spell-swirl", webgl_project)
        self.assertIn("spell-bolt", webgl_project)
        self.assertIn("impact-burst", webgl_project)
        self.assertIn("nova-ring", webgl_project)
        self.assertIn("starfall", webgl_project)
        self.assertIn("Arcstorm Finale", webgl_project)
        self.assertIn("phase-4-finale-showcase", webgl_project)
        self.assertNotIn("mesh-actor", webgl_project)
        self.assertNotIn("player-capsule", webgl_project)
        self.assertNotIn("hero-mesh", webgl_project)


if __name__ == "__main__":
    unittest.main()
