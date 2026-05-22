from __future__ import annotations

import unittest

from main_computer.output_snippets import parse_fenced_code_snippets


class OutputSnippetTests(unittest.TestCase):
    def test_detects_mathics_and_powershell_blocks_without_auto_promotion(self) -> None:
        snippets = parse_fenced_code_snippets(
            """Try these:

```mathics
D[Sin[x]^2, x]
```

```powershell
Get-ChildItem
```
"""
        )
        self.assertEqual(len(snippets), 2)
        self.assertEqual(snippets[0]["kind"], "mathics")
        self.assertIn("mathics", snippets[0]["suggested_target_cell_types"])
        self.assertEqual(snippets[1]["kind"], "terminal")
        self.assertIn("terminal", snippets[1]["suggested_target_cell_types"])
        self.assertFalse(snippets[0]["metadata"]["auto_promote"])
        self.assertFalse(snippets[1]["metadata"]["auto_promote"])


if __name__ == "__main__":
    unittest.main()
