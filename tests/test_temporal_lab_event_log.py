from __future__ import annotations

import pytest

from tools.temporal_lab.event_log import (
    TemporalLabEventLogError,
    append_jsonl_event,
    fake_token_text,
    read_jsonl_events,
)


def test_jsonl_event_log_writes_stable_events(tmp_path) -> None:
    path = tmp_path / "events.jsonl"

    append_jsonl_event(path, {"request_id": "req-001", "event": "start", "worker_id": "worker-1"})
    append_jsonl_event(path, {"request_id": "req-001", "event": "token", "seq": 1, "text": "tok-001"})

    assert path.read_text(encoding="utf-8").splitlines() == [
        '{"event":"start","request_id":"req-001","worker_id":"worker-1"}',
        '{"event":"token","request_id":"req-001","seq":1,"text":"tok-001"}',
    ]
    assert read_jsonl_events(path)[1]["text"] == "tok-001"


def test_fake_token_text_is_padded() -> None:
    assert fake_token_text(1) == "tok-001"
    assert fake_token_text(42) == "tok-042"


def test_event_log_rejects_missing_identity(tmp_path) -> None:
    with pytest.raises(TemporalLabEventLogError):
        append_jsonl_event(tmp_path / "events.jsonl", {"event": "start"})

    with pytest.raises(TemporalLabEventLogError):
        fake_token_text(0)
