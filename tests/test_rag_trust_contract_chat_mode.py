from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Sequence

from main_computer.chat_ai_subprocess import _run_rag_trust_contract_chat_child
from main_computer.models import ChatMessage, ChatResponse
from main_computer.rag_trust_contract_chat import (
    TRUST_CONTRACT_SYSTEM_PROMPT,
    run_stdio,
    run_trust_contract_chat_request,
)


class CaptureStdout:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    def emit(self, message: dict[str, object]) -> None:
        self.messages.append(message)


class ContractProvider:
    name = "contract-provider"
    model = "contract-model"

    def __init__(self, content: str) -> None:
        self.content = content
        self.messages: list[ChatMessage] = []

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        self.messages = list(messages)
        return ChatResponse(content=self.content, provider=self.name, model=self.model)


class TrustContractChatModeTests(unittest.TestCase):
    def test_deterministic_mode_emits_oob_control_and_chat_frames(self) -> None:
        frames: list[dict[str, object]] = []
        result = run_trust_contract_chat_request(
            run_id="race-1",
            prompt="How should Git track a folder with no .git inside a parent repo?",
            evidence=[
                {
                    "evidence_id": "repo-boundary",
                    "source": "task-manager.js",
                    "text": "A folder with no .git inside a parent repo must ask whether to start Git here or use the parent repository.",
                }
            ],
            provider=None,
            emit=frames.append,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.final_mode, "answer")
        self.assertIn("start Git here", result.answer)
        self.assertTrue(any(frame.get("type") == "control" and frame.get("channel") == "oob" for frame in frames))
        self.assertTrue(any(frame.get("type") == "activity" for frame in frames))
        self.assertTrue(any(frame.get("type") == "content" and frame.get("channel") == "chat" for frame in frames))

    def test_provider_candidate_is_verified_against_cited_evidence(self) -> None:
        provider = ContractProvider(json.dumps({
            "mode": "answer",
            "answer": "The selected folder should ask whether to start Git here or use the parent repository.",
            "claims": [
                {
                    "text": "selected folder should ask whether to start Git here or use the parent repository",
                    "evidence_ids": ["repo-boundary"],
                }
            ],
        }))

        result = run_trust_contract_chat_request(
            run_id="race-2",
            prompt="What should happen to the selected folder?",
            evidence=[
                {
                    "evidence_id": "repo-boundary",
                    "source": "task-manager.js",
                    "text": "The selected folder should ask whether to start Git here or use the parent repository.",
                }
            ],
            provider=provider,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "contract-provider")
        self.assertIn(TRUST_CONTRACT_SYSTEM_PROMPT.splitlines()[0], provider.messages[0].content)

    def test_provider_cheating_claim_is_rejected(self) -> None:
        provider = ContractProvider(json.dumps({
            "mode": "answer",
            "answer": "The first commit definitely happened.",
            "claims": [
                {"text": "the first commit definitely happened", "evidence_ids": ["commit-log"]}
            ],
        }))

        result = run_trust_contract_chat_request(
            run_id="race-3",
            prompt="Did the first commit happen?",
            evidence=[
                {
                    "evidence_id": "commit-log",
                    "source": "terminal",
                    "text": "fatal: your current branch main does not have any commits yet",
                }
            ],
            provider=provider,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.final_mode, "reject")
        self.assertTrue(any("retriever found no live evidence" in failure or "not anchored" in failure for failure in result.failures))

    def test_stdio_protocol_reads_request_and_writes_pipe_frames(self) -> None:
        request = {
            "mode": "rag_trust_contract_chat",
            "run_id": "stdio-1",
            "prompt": "What should the modal do?",
            "evidence": [
                {
                    "evidence_id": "modal",
                    "source": "ui",
                    "text": "The modal should let the user choose Start Git here or Use parent repository.",
                }
            ],
        }
        stdout = io.StringIO()

        code = run_stdio(io.StringIO(json.dumps(request) + "\n"), stdout)

        self.assertEqual(code, 0)
        frames = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
        self.assertTrue(any(frame.get("type") == "control" for frame in frames))
        self.assertTrue(any(frame.get("type") == "result" and frame.get("ok") for frame in frames))

    def test_chat_ai_subprocess_dispatch_function_supports_trust_contract_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = CaptureStdout()
            payload = _run_rag_trust_contract_chat_child(
                {
                    "mode": "rag_trust_contract_chat",
                    "run_id": "worker-1",
                    "prompt": "What should the selected folder do?",
                    "use_provider": False,
                    "evidence": [
                        {
                            "evidence_id": "repo-boundary",
                            "source": "task-manager.js",
                            "text": "The selected folder should ask whether to start Git here or use the parent repository.",
                        }
                    ],
                },
                stdout,
                log_file=str(Path(tmp) / "session.log"),
            )

        self.assertTrue(payload["result"]["ok"])
        self.assertEqual(payload["response"]["metadata"]["mode"], "rag_trust_contract_chat")
        self.assertTrue(any(message.get("type") == "control" for message in stdout.messages))
        self.assertTrue(any(message.get("type") == "result" for message in stdout.messages))


if __name__ == "__main__":
    unittest.main()
