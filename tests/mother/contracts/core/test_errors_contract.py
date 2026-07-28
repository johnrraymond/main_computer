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
