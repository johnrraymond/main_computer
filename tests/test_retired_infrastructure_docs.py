"""Prevent retired deployment instructions from reentering active documentation."""

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "pretty_docs"


class RetiredInfrastructureDocsTests(unittest.TestCase):
    def test_old_hub_deployment_guides_are_absent(self) -> None:
        self.assertFalse((DOCS / "hub-coolify-deploy-runbook.md").exists())
        self.assertFalse((DOCS / "hub-chain-testnet-mainnet-architecture.md").exists())

    def test_current_guidance_uses_existing_authority_tools(self) -> None:
        content = (DOCS / "mother-network-operations.md").read_text(encoding="utf-8")
        self.assertIn("tools.mother_coolify controllers", content)
        self.assertIn("tools.mother_deploy plan --network testnet", content)
        self.assertIn("hub_mutate_harness.py", content)
        self.assertIn("fdb_mutate_harness.py", content)
        self.assertIn("mother.md", content)

    def test_mainnet_contract_runbook_never_instructs_deleted_deployer(self) -> None:
        content = (DOCS / "mainnet-chain-redeploy-runbook.md").read_text(encoding="utf-8")
        self.assertNotIn("coolify_qbft_network.py apply", content)
        self.assertNotIn("coolify_qbft_network.py observe-chain", content)
        self.assertIn("mainnet-operator.py deploy-contracts", content)


if __name__ == "__main__":
    unittest.main()
