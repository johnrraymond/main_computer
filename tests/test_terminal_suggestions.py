from __future__ import annotations

import unittest

from main_computer.terminal_suggestions import (
    normalize_terminal_risk,
    parse_terminal_suggestion,
    validate_terminal_command,
)


class TerminalSuggestionTests(unittest.TestCase):
    def test_parse_terminal_suggestion_requires_json_object(self) -> None:
        self.assertEqual(parse_terminal_suggestion('{"command":"git status"}')["command"], "git status")
        with self.assertRaisesRegex(ValueError, "valid JSON"):
            parse_terminal_suggestion("git status")
        with self.assertRaisesRegex(ValueError, "JSON object"):
            parse_terminal_suggestion('["git status"]')

    def test_validate_terminal_command_rejects_empty_multiline_and_long(self) -> None:
        self.assertEqual(validate_terminal_command("  git status  "), "git status")
        with self.assertRaisesRegex(ValueError, "required"):
            validate_terminal_command("")
        with self.assertRaisesRegex(ValueError, "single line"):
            validate_terminal_command("git status\nGet-ChildItem")
        with self.assertRaisesRegex(ValueError, "4000"):
            validate_terminal_command("x" * 4001)

    def test_normalize_terminal_risk(self) -> None:
        self.assertEqual(normalize_terminal_risk("READ-ONLY"), "read-only")
        self.assertEqual(normalize_terminal_risk("surprising"), "unknown")


if __name__ == "__main__":
    unittest.main()
