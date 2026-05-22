from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import git_dirty


class SecretsFilterSavedPolicyBoundaryTests(unittest.TestCase):
    def test_no_policy_file_means_no_saved_rules_for_right_pane(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.assertFalse((root / ".git_dirty_rules.json").exists())

            payload = git_dirty.secrets_filter_payload(root, [{"path": "app.py"}])

        self.assertFalse(payload["policy"]["exists"])
        self.assertFalse(payload["saved_policy_exists"])
        self.assertEqual(len(payload["rules"]), 5)
        self.assertEqual(payload.get("saved_rules", []), [])
        self.assertEqual(payload.get("saved_summary", {}).get("enabled_rule_count", 0), 0)

    def test_policy_file_means_saved_rules_are_visible_for_right_pane(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git_dirty_rules.json").write_text(
                """{
  "policy_version": 1,
  "rules": {
    "windows_user_paths": true,
    "unix_user_paths": true,
    "user_names": true,
    "secrets": true,
    "detect_secrets": true
  }
}
""",
                encoding="utf-8",
            )

            payload = git_dirty.secrets_filter_payload(root, [{"path": "app.py"}])

        self.assertTrue(payload["policy"]["exists"])
        self.assertTrue(payload["saved_policy_exists"])
        self.assertEqual(len(payload.get("saved_rules", [])), 5)
        self.assertEqual(payload.get("saved_summary", {}).get("enabled_rule_count"), 5)


    def test_saved_false_rule_choices_remain_visible_but_unchecked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git_dirty_rules.json").write_text(
                """{
  "policy_version": 1,
  "rules": {
    "windows_user_paths": false,
    "unix_user_paths": true,
    "user_names": false,
    "secrets": true,
    "detect_secrets": false
  }
}
""",
                encoding="utf-8",
            )

            payload = git_dirty.secrets_filter_payload(root, [{"path": "app.py"}])

        self.assertTrue(payload["saved_policy_exists"])
        saved_rules = {rule["id"]: rule for rule in payload.get("saved_rules", [])}
        self.assertEqual(set(saved_rules), {
            "windows_user_paths",
            "unix_user_paths",
            "user_names",
            "secrets",
            "detect_secrets",
        })
        self.assertFalse(saved_rules["windows_user_paths"]["enabled"])
        self.assertFalse(saved_rules["user_names"]["enabled"])
        self.assertFalse(saved_rules["detect_secrets"]["enabled"])
        self.assertTrue(saved_rules["unix_user_paths"]["enabled"])
        self.assertTrue(saved_rules["secrets"]["enabled"])
        self.assertEqual(payload.get("saved_summary", {}).get("enabled_rule_count"), 2)


if __name__ == "__main__":
    unittest.main()
