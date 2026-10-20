"""FDB observer diagnostics must be accurate and strictly read-only."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import fdb_mutate_harness as harness
from tools.fdb_control.common.coolify import CoolifyResponse
from tools.fdb_control.common.models import FdbContext
from tools.fdb_control.observer_diagnostics import diagnose_observer


class FakeClient:
    def __init__(self, *, log_status: int = 200, observer_name: str = "testnetb-fdb1-observer"):
        self.log_status = log_status
        self.observer_name = observer_name
        self.methods: list[str] = []

    def request(self, method: str, path: str, payload=None) -> CoolifyResponse:
        self.methods.append(method)
        assert method == "GET", "diagnostics must never mutate Coolify"
        uuid = "fcyzqystqzud5tdev7gx5wxx"
        if path == "/api/v1/services":
            return CoolifyResponse(True, 200, method, path, [
                {"name": "main-computer-testnetb-fdb1", "uuid": uuid}])
        if path == f"/api/v1/services/{uuid}":
            return CoolifyResponse(True, 200, method, path, {
                "name": "main-computer-testnetb-fdb1", "uuid": uuid,
                "status": "running", "applications": [
                    {"name": "testnetb-fdb1", "status": "running:healthy"},
                    {"name": self.observer_name, "status": "exited"},
                ],
            })
        if path.startswith(f"/api/v1/services/{uuid}/logs?"):
            subservice = parse_qs(urlparse(path).query)["sub_service_name"][0]
            if self.log_status != 200:
                return CoolifyResponse(False, self.log_status, method, path, {"message": "Unknown subresource"})
            return CoolifyResponse(True, 200, method, path,
                {"logs": "python3: not found\n" if subservice.endswith("-observer") else "fdbserver starting\n"})
        raise AssertionError(f"unexpected API path {path}")


def _ctx(tmp_path: Path) -> FdbContext:
    doc = tmp_path / "runtime/state/mother/identity.private.yaml"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("""networks:
  testnet:
    coolify:
      controllers:
        coolify-b:
          url: http://coolify.invalid:8000
          api_token: test-token
""", encoding="utf-8")
    return FdbContext.from_repo(tmp_path)


@pytest.mark.parametrize("http_status", [200, 400, 404])
def test_diagnosis_preserves_log_http_failure_and_resource_state(tmp_path, http_status):
    client = FakeClient(log_status=http_status)
    report = diagnose_observer(_ctx(tmp_path), "testnet", "testnetb-fdb1", client_factory=lambda _: client)
    assert report["coolify_service_uuid"] == "fcyzqystqzud5tdev7gx5wxx"
    assert report["read_only"] is True
    assert report["network_mutation_performed"] is False
    assert report["observer_listed"] is True
    assert report["observer_logs"]["http_status"] == http_status
    assert report["observer_logs"]["retrieved"] is (http_status == 200)
    assert report["observer_logs"]["log_tail"] == (["python3: not found"] if http_status == 200 else [])
    assert client.methods and set(client.methods) == {"GET"}


def test_diagnosis_exposes_wrong_assumed_observer_name(tmp_path):
    client = FakeClient(observer_name="server-generated-observer")
    report = diagnose_observer(_ctx(tmp_path), "testnet", "testnetb-fdb1", client_factory=lambda _: client)
    assert report["observer_listed"] is False
    assert "server-generated-observer" in [i["name"] for i in report["subresources"]]
    assert report["observer_expected_name"] == "testnetb-fdb1-observer"


def test_harness_diagnose_is_read_only_and_requires_service(tmp_path):
    args = argparse.Namespace(operation="diagnose-observer", network="testnet", service="testnetb-fdb1",
        repo_root=tmp_path, allow_full_deletion=False, execute_mutations=False, yes_i_know_this_mutates_fdb=False)
    instance = harness.Harness(args)
    assert instance.run_dir is None
    assert not (tmp_path / "runtime/state/fdb/harness-runs").exists()
    assert instance.control_cmd("diagnose-observer", "testnet", "--service", instance.service)[-4:] == [
        "diagnose-observer", "testnet", "--service", "testnetb-fdb1"]
    args.execute_mutations = True
    args.yes_i_know_this_mutates_fdb = True
    with pytest.raises(harness.HarnessError, match="read-only"):
        harness.Harness(args).run()
