from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer


def post_json(url: str, payload: dict | None = None, *, timeout: float = 5.0, allow_error: bool = False) -> dict:
    request = Request(
        url,
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        if not allow_error:
            raise
        payload = json.loads(body) if body else {}
        payload["_http_status"] = exc.code
        return payload


def get_json(url: str, *, timeout: float = 5.0) -> dict:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class Phase10DeterministicWorkerRuntimeContractTests(unittest.TestCase):
    def _start_hub(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        config = MainComputerConfig(
            workspace=Path(tmp.name),
            model="mock-ai-model-phase10",
            hub_root=Path(tmp.name) / "hub-runtime",
            hub_credits_per_request=111,
        )
        hub = HubHttpServer(("127.0.0.1", 0), config, verbose=False)
        thread = threading.Thread(target=hub.serve_forever, daemon=True)
        thread.start()

        def cleanup() -> None:
            hub.shutdown()
            thread.join(timeout=5)
            hub.server_close()

        self.addCleanup(cleanup)
        return hub, f"http://127.0.0.1:{hub.server_port}"

    def _fund(self, hub: HubHttpServer, account_id: str) -> None:
        hub.credit_ledger.issue(
            account_id=account_id,
            credits=100_000_000,
            memo="phase10 deterministic runtime funded requester",
            owner_address="0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
        )

    def _register_worker(self, hub_base: str, *, worker_node_id: str, model: str, price: int) -> dict:
        return post_json(
            f"{hub_base}/api/hub/v1/workers/register",
            {
                "node_id": worker_node_id,
                "endpoint": "http://127.0.0.1:1",
                "model": model,
                "models": [model],
                "credits_per_request": price,
                "execution_mode": "worker_pull_v0",
                "pricing": {
                    "pricing_type": "fixed_per_call_v0",
                    "credits_per_request": price,
                    "unit": "compute_credit",
                },
                "capabilities": {"provider": "mock", "worker_pull_v0": True},
                "max_concurrency": 1,
            },
        )

    def _quote(self, hub_base: str, *, account_id: str, model: str, max_credits: int, key: str) -> dict:
        return post_json(
            f"{hub_base}/api/hub/v1/requests/quote",
            {
                "account_id": account_id,
                "model": model,
                "messages": [{"role": "user", "content": f"quote {key}"}],
                "max_credits": max_credits,
                "execution_mode": "worker_pull_v0",
                "pricing_mode": "market_offer_fixed_per_call_v0",
                "idempotency_key": key,
            },
        )["quote"]

    def _submit_request(
        self,
        hub_base: str,
        *,
        account_id: str,
        model: str,
        max_credits: int,
        quote_id: str,
        key: str,
    ) -> dict:
        return post_json(
            f"{hub_base}/api/hub/v1/requests",
            {
                "account_id": account_id,
                "client_node_id": account_id,
                "quote_id": quote_id,
                "model": model,
                "messages": [{"role": "user", "content": f"run {key}"}],
                "max_credits": max_credits,
                "execution_mode": "worker_pull_v0",
                "pricing_mode": "market_offer_fixed_per_call_v0",
                "metadata": {"worker_pull_v0": True},
                "idempotency_key": key,
            },
        )["request"]

    def _market_request(
        self,
        hub_base: str,
        *,
        account_id: str,
        model: str,
        price: int,
        label: str,
    ) -> dict:
        quote = self._quote(
            hub_base,
            account_id=account_id,
            model=model,
            max_credits=price,
            key=f"{label}-quote",
        )
        request = self._submit_request(
            hub_base,
            account_id=account_id,
            model=model,
            max_credits=price,
            quote_id=quote["quote_id"],
            key=f"{label}-request",
        )
        return {"quote": quote, "request": request}

    def test_success_path_completion_replay_is_idempotent_and_heartbeat_updates_status(self) -> None:
        hub, hub_base = self._start_hub()
        account_id = "phase10-requester-success"
        worker_node_id = "phase10-worker-success"
        model = "mock-ai-model-phase10"
        price = 5_500_123
        self._fund(hub, account_id)
        registered = self._register_worker(hub_base, worker_node_id=worker_node_id, model=model, price=price)
        self.assertEqual(registered["worker"]["offer"]["credits_per_request"], price)

        heartbeat = post_json(
            f"{hub_base}/api/hub/v1/workers/heartbeat",
            {
                "worker_node_id": worker_node_id,
                "status": "available",
                "queue_depth": 2,
                "models": [model],
            },
        )
        self.assertTrue(heartbeat["ok"])
        self.assertEqual(heartbeat["worker"]["queue_depth"], 2)
        self.assertEqual(heartbeat["worker"]["status"], "available")

        market = self._market_request(
            hub_base,
            account_id=account_id,
            model=model,
            price=price,
            label="phase10-success",
        )
        submitted = market["request"]
        duplicate_submitted = self._submit_request(
            hub_base,
            account_id=account_id,
            model=model,
            max_credits=price,
            quote_id=market["quote"]["quote_id"],
            key="phase10-success-request",
        )
        self.assertEqual(submitted["request_id"], duplicate_submitted["request_id"])

        holds = get_json(
            f"{hub_base}/api/hub/v1/credits/holds?"
            + urlencode({"account_id": account_id, "request_id": submitted["request_id"]})
        )
        self.assertEqual(holds["hold_count"], 1)
        self.assertEqual(holds["holds"][0]["status"], "held")
        self.assertEqual(holds["holds"][0]["credits"], price)

        lease = post_json(f"{hub_base}/api/hub/v1/workers/poll", {"worker_node_id": worker_node_id})["lease"]
        self.assertIsInstance(lease, dict)
        self.assertEqual(lease["pricing"]["worker_earning_credits"], price)

        completed = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": worker_node_id,
                "request_id": lease["request_id"],
                "lease_id": lease["lease_id"],
                "result": {
                    "status": "success",
                    "response": {"content": "Phase 10 success", "provider": "mock-worker", "model": model},
                },
            },
        )["request"]
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["charged_credits"], price)

        replay = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": worker_node_id,
                "request_id": lease["request_id"],
                "lease_id": lease["lease_id"],
                "result": {
                    "status": "success",
                    "response": {"content": "duplicate", "provider": "mock-worker", "model": model},
                },
            },
        )
        self.assertTrue(replay["ok"])
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["duplicate_completion_additional_charge"], 0)
        self.assertEqual(replay["request"]["request_id"], completed["request_id"])

        charges = get_json(f"{hub_base}/api/hub/v1/requests/{lease['request_id']}/charges")
        self.assertEqual(charges["charge_count"], 1)
        self.assertEqual(charges["charges"][0]["charged_credits"], price)

        earnings = get_json(
            f"{hub_base}/api/hub/v1/credits/worker-earnings?"
            + urlencode({"worker_node_id": worker_node_id, "request_id": lease["request_id"]})
        )
        self.assertEqual(earnings["worker_earning_count"], 1)
        self.assertEqual(earnings["worker_earnings"][0]["credits"], price)

    def test_worker_failure_and_leased_cancel_release_hold_without_charging(self) -> None:
        hub, hub_base = self._start_hub()
        account_id = "phase10-requester-failure-cancel"
        worker_node_id = "phase10-worker-failure-cancel"
        model = "mock-ai-model-phase10"
        price = 111_777
        self._fund(hub, account_id)
        self._register_worker(hub_base, worker_node_id=worker_node_id, model=model, price=price)

        failed_market = self._market_request(
            hub_base,
            account_id=account_id,
            model=model,
            price=price,
            label="phase10-failure",
        )
        failed_request = failed_market["request"]
        failed_lease = post_json(f"{hub_base}/api/hub/v1/workers/poll", {"worker_node_id": worker_node_id})["lease"]
        failed = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": worker_node_id,
                "request_id": failed_lease["request_id"],
                "lease_id": failed_lease["lease_id"],
                "result": {"status": "failed", "error": "deterministic mock worker failure"},
            },
        )["request"]
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["terminal_reason"], "worker_result_failed")
        self.assertEqual(failed["charged_credits"], 0)

        failure_holds = get_json(
            f"{hub_base}/api/hub/v1/credits/holds?"
            + urlencode({"account_id": account_id, "request_id": failed_request["request_id"]})
        )
        self.assertEqual(failure_holds["hold_count"], 1)
        self.assertEqual(failure_holds["holds"][0]["status"], "released")
        self.assertEqual(failure_holds["holds"][0]["credits"], price)
        self.assertEqual(get_json(f"{hub_base}/api/hub/v1/requests/{failed_request['request_id']}/charges")["charge_count"], 0)

        # Failure intentionally marks the worker offline. Re-register it for an
        # unrelated client cancellation; cancellation should not punish the worker.
        self._register_worker(hub_base, worker_node_id=worker_node_id, model=model, price=price)
        cancel_market = self._market_request(
            hub_base,
            account_id=account_id,
            model=model,
            price=price,
            label="phase10-cancel",
        )
        cancel_request = cancel_market["request"]
        cancel_lease = post_json(f"{hub_base}/api/hub/v1/workers/poll", {"worker_node_id": worker_node_id})["lease"]
        self.assertIsInstance(cancel_lease, dict)

        cancelled = post_json(f"{hub_base}/api/hub/v1/requests/{cancel_request['request_id']}/cancel", {})["request"]
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(cancelled["terminal_reason"], "client_cancelled")
        self.assertEqual(cancelled["charged_credits"], 0)

        cancel_holds = get_json(
            f"{hub_base}/api/hub/v1/credits/holds?"
            + urlencode({"account_id": account_id, "request_id": cancel_request["request_id"]})
        )
        self.assertEqual(cancel_holds["hold_count"], 1)
        self.assertEqual(cancel_holds["holds"][0]["status"], "released")
        self.assertEqual(get_json(f"{hub_base}/api/hub/v1/requests/{cancel_request['request_id']}/charges")["charge_count"], 0)

        worker = get_json(f"{hub_base}/api/hub/v1/workers/{worker_node_id}")["worker"]
        self.assertEqual(worker["status"], "available")
        self.assertEqual(worker["active_requests"], 0)

    def test_expired_lease_requeues_request_and_rejects_stale_completion_without_charge(self) -> None:
        hub, hub_base = self._start_hub()
        account_id = "phase10-requester-timeout"
        worker_node_id = "phase10-worker-timeout"
        model = "mock-ai-model-phase10"
        price = 222_333
        self._fund(hub, account_id)
        self._register_worker(hub_base, worker_node_id=worker_node_id, model=model, price=price)

        market = self._market_request(
            hub_base,
            account_id=account_id,
            model=model,
            price=price,
            label="phase10-timeout",
        )
        submitted = market["request"]
        first_lease = post_json(
            f"{hub_base}/api/hub/v1/workers/poll",
            {"worker_node_id": worker_node_id, "lease_seconds": 1},
        )["lease"]
        self.assertIsInstance(first_lease, dict)
        expired_at = (datetime.now(tz=timezone.utc) - timedelta(seconds=5)).isoformat()
        hub.dispatcher.plex_service.request_store.update(submitted["request_id"], lease_expires_at=expired_at)

        stale_completion = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": worker_node_id,
                "request_id": first_lease["request_id"],
                "lease_id": first_lease["lease_id"],
                "result": {
                    "status": "success",
                    "response": {"content": "too late", "provider": "mock-worker", "model": model},
                },
            },
            allow_error=True,
        )
        self.assertEqual(stale_completion["_http_status"], 400)

        requeued = get_json(f"{hub_base}/api/hub/v1/requests/{submitted['request_id']}")["request"]
        self.assertEqual(requeued["state"], "queued")
        self.assertEqual(requeued["charged_credits"], 0)
        self.assertEqual(get_json(f"{hub_base}/api/hub/v1/requests/{submitted['request_id']}/charges")["charge_count"], 0)

        second_lease = post_json(f"{hub_base}/api/hub/v1/workers/poll", {"worker_node_id": worker_node_id})["lease"]
        self.assertIsInstance(second_lease, dict)
        self.assertNotEqual(second_lease["lease_id"], first_lease["lease_id"])

        completed = post_json(
            f"{hub_base}/api/hub/v1/workers/results",
            {
                "worker_node_id": worker_node_id,
                "request_id": second_lease["request_id"],
                "lease_id": second_lease["lease_id"],
                "result": {
                    "status": "success",
                    "response": {"content": "after retry", "provider": "mock-worker", "model": model},
                },
            },
        )["request"]
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["charged_credits"], price)
        charges = get_json(f"{hub_base}/api/hub/v1/requests/{submitted['request_id']}/charges")
        self.assertEqual(charges["charge_count"], 1)


if __name__ == "__main__":
    unittest.main()
