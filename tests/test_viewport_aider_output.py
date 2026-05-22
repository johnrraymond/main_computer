from __future__ import annotations

import unittest

from main_computer.viewport import APPLICATIONS_INDEX_HTML


class ViewportAiderOutputTests(unittest.TestCase):
    def test_applications_index_strips_trailing_aider_token_counts_from_message_panel(self) -> None:
        self.assertIn("function stripTrailingAiderTokenLine", APPLICATIONS_INDEX_HTML)
        self.assertIn("/^Tokens:\\s+/i.test", APPLICATIONS_INDEX_HTML)
        self.assertIn(
            'if (fenceIndex >= 0) return stripTrailingAiderTokenLine(text.slice(fenceIndex).trim());',
            APPLICATIONS_INDEX_HTML,
        )
        self.assertIn(
            'if (diffIndex >= 0) return stripTrailingAiderTokenLine(text.slice(diffIndex).trim());',
            APPLICATIONS_INDEX_HTML,
        )
        self.assertIn(
            'return stripTrailingAiderTokenLine(stripInitialAiderPreamble(text));',
            APPLICATIONS_INDEX_HTML,
        )
        self.assertIn("<summary>Activity console</summary>", APPLICATIONS_INDEX_HTML)

    def test_applications_index_exposes_editable_aider_timeout_in_code_editor(self) -> None:
        self.assertIn('id="aider-timeout-seconds"', APPLICATIONS_INDEX_HTML)
        self.assertIn(
            'const aiderTimeoutSeconds = document.querySelector("#aider-timeout-seconds");',
            APPLICATIONS_INDEX_HTML,
        )
        self.assertIn("function normalizedAiderTimeoutSeconds()", APPLICATIONS_INDEX_HTML)
        self.assertIn(
            'aiderTimeoutSeconds.addEventListener("change", normalizedAiderTimeoutSeconds);',
            APPLICATIONS_INDEX_HTML,
        )
        self.assertIn("timeout_seconds: normalizedAiderTimeoutSeconds()", APPLICATIONS_INDEX_HTML)
        self.assertIn("timeout: ${data.timeout_seconds}s", APPLICATIONS_INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
