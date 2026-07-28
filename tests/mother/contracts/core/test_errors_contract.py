from __future__ import annotations

import pytest

from tests.mother.support.implementation import require_mother_module


pytestmark = pytest.mark.mother_contract(
    requirements=["MOTHER-REQ-002"],
    operations=["MOTHER-OP-DIAGNOSE"],
    functionalities=["MOTHER-OF-OBS-001"],
    modules=["MOTHER-OFM-CORE-002"],
)


def _errors():
    return require_mother_module("common.errors", "MOTHER-OFM-CORE-002")


def _make_error(errors, **overrides):
    values = {
        "code": "MOTHER_STATE_HASH_MISMATCH",
        "message": "state hash mismatch",
        "operation_id": "operation-1",
        "module_id": "MOTHER-OFM-STATE-001",
        "retry_class": "after-reobserve",
        "authority_effect": "none",
        "durable_effect_refs": (),
        "evidence_refs": (),
        "allowed_next_actions": ("diagnose",),
        "cause_class": "ValidationFailure",
    }
    values.update(overrides)
    return errors.MotherError(**values)


def test_mother_error_exposes_the_complete_typed_envelope() -> None:
    errors = _errors()
    error = _make_error(errors)
    assert isinstance(error, Exception)
    assert error.code == "MOTHER_STATE_HASH_MISMATCH"
    assert error.retry_class == "after-reobserve"
    assert error.authority_effect == "none"
    assert error.durable_effect_refs == ()
    assert error.allowed_next_actions == ("diagnose",)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("retry_class", "sometimes"),
        ("authority_effect", "probably-changed"),
    ],
)
def test_error_enums_reject_unknown_values(field: str, value: str) -> None:
    errors = _errors()
    with pytest.raises((TypeError, ValueError)):
        _make_error(errors, **{field: value})


def test_vendor_error_is_wrapped_and_secret_redacted() -> None:
    errors = _errors()
    secret = "private-key-material"
    wrapped = errors.wrap_vendor_error(
        RuntimeError(f"vendor rejected password={secret}"),
        code="MOTHER_TRANSPORT_VENDOR_FAILURE",
        operation_id="operation-1",
        module_id="MOTHER-OFM-XPORT-002",
        retry_class="after-reobserve",
        authority_effect="live-state-maybe-changed",
        secret_values=(secret,),
    )
    rendered = " ".join(
        str(value)
        for value in (
            wrapped,
            wrapped.message,
            wrapped.cause_class,
            wrapped.durable_effect_refs,
            wrapped.evidence_refs,
        )
    )
    assert isinstance(wrapped, errors.MotherError)
    assert secret not in rendered
    assert wrapped.cause_class == "RuntimeError"


def test_exit_code_mapping_is_stable_and_nonzero_for_failure() -> None:
    errors = _errors()
    error = _make_error(errors)
    first = errors.exit_code_for(error)
    second = errors.exit_code_for(_make_error(errors))
    assert isinstance(first, int)
    assert first != 0
    assert second == first


def test_error_string_never_reveals_secret_bearing_values() -> None:
    errors = _errors()
    secret = "do-not-print"
    with pytest.raises((TypeError, ValueError)):
        _make_error(errors, message=f"credential={secret}", secret_values=(secret,))


def test_vendor_wrapper_redacts_supplied_secrets_from_allowed_actions() -> None:
    errors = _errors()
    secret = "operator-token-value"
    wrapped = errors.wrap_vendor_error(
        RuntimeError("vendor failure"),
        code="MOTHER_TRANSPORT_VENDOR_FAILURE",
        operation_id="operation-1",
        module_id="MOTHER-OFM-XPORT-002",
        retry_class="after-reobserve",
        authority_effect="live-state-maybe-changed",
        allowed_next_actions=(f"retry --token={secret}",),
        secret_values=(secret,),
    )
    assert secret not in wrapped.allowed_next_actions[0]
    assert "[REDACTED]" in wrapped.allowed_next_actions[0]


def test_vendor_wrapper_preserves_typed_durable_and_evidence_references() -> None:
    errors = _errors()
    from tools.mother.common import models

    durable_ref = models.ContentHash(algorithm="sha256", digest="a" * 64)
    evidence_ref = models.EvidenceRef(
        object_hash=models.ContentHash(algorithm="sha256", digest="b" * 64),
        schema="mother.evidence.v1",
        redaction_policy="public",
        source="contract-test",
        observation_time="2026-07-28T00:00:00Z",
    )
    wrapped = errors.wrap_vendor_error(
        RuntimeError("vendor failure"),
        code="MOTHER_TRANSPORT_VENDOR_FAILURE",
        operation_id="operation-1",
        module_id="MOTHER-OFM-XPORT-002",
        retry_class="after-reobserve",
        authority_effect="live-state-maybe-changed",
        durable_effect_refs=(durable_ref,),
        evidence_refs=(evidence_ref,),
    )
    assert wrapped.durable_effect_refs == (durable_ref,)
    assert wrapped.evidence_refs == (evidence_ref,)
    assert isinstance(wrapped.durable_effect_refs[0], models.ContentHash)
    assert isinstance(wrapped.evidence_refs[0], models.EvidenceRef)
