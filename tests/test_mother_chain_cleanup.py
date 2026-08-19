from __future__ import annotations

from argparse import Namespace
import json

from tools import mother_chain_cleanup as cleanup


class FakeRpcClient:
    def __init__(
        self,
        *,
        validators: list[str],
        pending: dict[str, bool],
        block_numbers: list[str] | None = None,
        syncing: list[bool] | None = None,
        chain_id: str = "0x28757b0",
    ) -> None:
        self.validators = [item.lower() for item in validators]
        self.pending = dict(pending)
        self.block_numbers = list(block_numbers or ["0x1", "0x2"])
        self.syncing = list(syncing or [False, False])
        self.calls: list[tuple[str, list[object]]] = []
        self.discards: list[str] = []
        self._blocks_seen = 0
        self._syncing_seen = 0

    def rpc(self, method: str, params: list[object]) -> object:
        self.calls.append((method, list(params)))
        if method == "eth_chainId":
            return self.chain_id
        if method == "qbft_getValidatorsByBlockNumber":
            return list(self.validators)
        if method == "qbft_getPendingVotes":
            return dict(self.pending)
        if method == "eth_blockNumber":
            index = min(self._blocks_seen, len(self.block_numbers) - 1)
            self._blocks_seen += 1
            return self.block_numbers[index]
        if method == "eth_syncing":
            index = min(self._syncing_seen, len(self.syncing) - 1)
            self._syncing_seen += 1
            return self.syncing[index]
        if method == "qbft_discardValidatorVote":
            [address] = params
            self.discards.append(str(address))
            normalized = str(address).lower()
            for key in list(self.pending):
                if key.lower() == normalized or cleanup._validator_vote_address(key, "pending vote").lower() == normalized:
                    self.pending.pop(key)
            return True
        raise AssertionError(method)


class TickingClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.001, float(seconds))


def test_satisfied_pending_vote_targets_matches_v1_guardian_rules() -> None:
    live = [
        "0x9b809f05f8d68da17e697cd6ab040d4320494611",
        "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
    ]
    pending = {
        "0x9b809f05f8d68da17e697cd6ab040d4320494611": True,
        "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876": False,
        "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6": False,
    }

    assert cleanup.satisfied_pending_vote_targets(pending, live) == [
        {
            "address": "0x9b809f05f8d68da17e697cd6ab040d4320494611",
            "vote": True,
            "reason": "add_vote_already_in_validator_set",
        },
        {
            "address": "0xb612f95e8a2bdb3af3e7c9ddd2eeb19490508876",
            "vote": False,
            "reason": "remove_vote_already_absent_from_validator_set",
        },
    ]


def test_inspect_reports_would_clear_without_discarding() -> None:
    c1 = "0x9b809f05f8d68da17e697cd6ab040d4320494611"
    client = FakeRpcClient(validators=[c1], pending={c1: True})

    result = cleanup.inspect_satisfied_pending_votes(client, [c1], phase="inspect-test")

    assert result["status"] == "would_clear"
    assert result["cleanup_targets"][0]["reason"] == "add_vote_already_in_validator_set"
    assert client.discards == []
    assert "qbft_discardValidatorVote" not in [method for method, _ in client.calls]


def test_execute_clones_v1_quiet_window_then_discards_satisfied_vote() -> None:
    c1 = "0x9b809f05f8d68da17e697cd6ab040d4320494611"
    clock = TickingClock()
    client = FakeRpcClient(validators=[c1], pending={c1: True}, block_numbers=["0x10", "0x11"], syncing=[False, False])

    result = cleanup.cleanup_satisfied_pending_votes(
        client,
        [c1],
        phase="execute-test",
        vote_address_by_lower={c1: cleanup._validator_vote_address(c1, "committed validator address")},
        quiet_seconds=0.01,
        poll_seconds=0.01,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert result["status"] == "cleared"
    assert result["cleared"][0]["reason"] == "add_vote_already_in_validator_set"
    assert result["pending_votes_after"] == {}
    assert client.discards == [cleanup._validator_vote_address(c1, "committed validator address")]


def test_execute_defers_when_v1_quiet_window_is_not_satisfied() -> None:
    c1 = "0x9b809f05f8d68da17e697cd6ab040d4320494611"
    clock = TickingClock()
    client = FakeRpcClient(validators=[c1], pending={c1: True}, block_numbers=["0x10", "0x10"], syncing=[False, False])

    result = cleanup.cleanup_satisfied_pending_votes(
        client,
        [c1],
        phase="execute-test",
        vote_address_by_lower={c1: cleanup._validator_vote_address(c1, "committed validator address")},
        quiet_seconds=0.01,
        poll_seconds=0.01,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert result["status"] == "deferred_not_quiet"
    assert result["quiet_observation"]["reason"] == "quiet_window_without_block_progress"
    assert client.discards == []


def test_execute_requires_explicit_chain_only_acknowledgements() -> None:
    args = Namespace(
        mode="execute",
        rpc_url=["node=http://127.0.0.1:8545"],
        committed_validator_address=[],
        expected_chain_id=None,
        phase="test",
        quiet_seconds=0.01,
        poll_seconds=0.01,
        timeout=1.0,
        runtime_state_root=None,
        write_evidence=False,
        allow_discard_satisfied_pending_votes=False,
        acknowledge_chain_cleanup_only=True,
    )

    try:
        cleanup.run_chain_cleanup(args)
    except cleanup.MotherChainCleanupError as exc:
        assert "--allow-discard-satisfied-pending-votes" in str(exc)
    else:
        raise AssertionError("execute without discard acknowledgement should fail")


def test_script_is_chain_only_and_contains_no_infrastructure_mutation_imports() -> None:
    source = cleanup.Path(cleanup.__file__).read_text(encoding="utf-8")
    forbidden = [
        "resolve_coolify_controller",
        "docker compose",
        "docker rm",
        "exclude_from_status",
        "PATCH",
        "DELETE",
        "POST /api/v1/deploy",
        "execute_completed_mother_helper_cleanup",
    ]

    for token in forbidden:
        assert token not in source
