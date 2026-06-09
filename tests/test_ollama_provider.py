from __future__ import annotations

import unittest
from unittest.mock import patch

from main_computer.models import ChatMessage
from main_computer.providers.ollama import OllamaProvider, OllamaStreamTerminalError


class OllamaProviderTests(unittest.TestCase):
    def test_rejects_one_token_context_length_exhaustion(self) -> None:
        class FakeStreamResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def __iter__(self):
                return iter(
                    [
                        b'{"message":{"content":"```"},"done":false}\n',
                        (
                            b'{"message":{"content":""},"done":true,"done_reason":"length",'
                            b'"prompt_eval_count":4095,"eval_count":1}\n'
                        ),
                    ]
                )

        def fake_urlopen(request, timeout):
            return FakeStreamResponse()

        with patch("main_computer.providers.ollama.urlopen", fake_urlopen):
            with self.assertRaises(OllamaStreamTerminalError) as caught:
                OllamaProvider(model="fake").chat([ChatMessage(role="user", content="hello")])

        self.assertEqual(caught.exception.terminal_fault_type, "context_length_exhausted_before_useful_output")
        self.assertIn("prompt_eval_count=4095", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
