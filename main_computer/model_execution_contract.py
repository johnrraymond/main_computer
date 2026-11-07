"""Fail-closed model identity contract shared by Hub, SLH and Worker clients.

A model name is a capability, not a suggestion or a provider default. A Hub
may only select a live Worker that explicitly advertises the model/operation;
execution may succeed only with evidence of the requested model. No aliases.
"""
from __future__ import annotations

import re
from typing import Any

_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_OPERATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class ModelContractError(ValueError):
    pass


def model_id(value: Any) -> str:
    if not isinstance(value, str) or not _MODEL.fullmatch(value):
        raise ModelContractError("explicit canonical model is required")
    return value


def operation_id(value: Any) -> str:
    if not isinstance(value, str) or not _OPERATION.fullmatch(value):
        raise ModelContractError("explicit supported operation is required")
    return value


def advertised_models(market: Any) -> list[str]:
    if not isinstance(market, dict):
        raise ModelContractError("worker market profile is required")
    raw = market.get("models")
    if not isinstance(raw, list) or not raw or len(raw) > 32:
        raise ModelContractError("worker must advertise a nonempty models list")
    models = [model_id(item) for item in raw]
    if len(set(models)) != len(models):
        raise ModelContractError("worker models must be unique")
    if market.get("model") not in (None, "", models[0]):
        raise ModelContractError("worker model conflicts with models list")
    return models


def advertised_operations(market: Any) -> list[str]:
    if not isinstance(market, dict):
        raise ModelContractError("worker market profile is required")
    raw = market.get("capabilities")
    if not isinstance(raw, list) or not raw:
        raise ModelContractError("worker must advertise explicit capabilities")
    operations = [operation_id(item) for item in raw]
    if len(set(operations)) != len(operations):
        raise ModelContractError("worker capabilities must be unique")
    return operations


def request_contract(work: Any) -> tuple[str, str, tuple[str, ...]]:
    if not isinstance(work, dict):
        raise ModelContractError("work object is required")
    # A public marketplace Worker only self-asserts its execution identity.
    # Treat an explicit demand for verified/confidential execution as an
    # admission constraint, not as a label the Worker can declare true.
    if (work.get("requires_verified_execution") is True
            or work.get("confidential") is True
            or work.get("trust_tier") in ("trusted", "verified", "attested")
            or work.get("execution_assurance") in ("verified", "attested")):
        raise ModelContractError("independent execution attestation unavailable for public Worker jobs")
    model = model_id(work.get("model"))
    if ("capabilities" in work and "required_capabilities" in work
            and work["capabilities"] != work["required_capabilities"]):
        raise ModelContractError("request capabilities disagree with required_capabilities")
    raw_caps = work.get("capabilities", work.get("required_capabilities"))
    if raw_caps is None:
        raw_caps = []
    if not isinstance(raw_caps, list):
        raise ModelContractError("request capabilities must be a list")
    caps = tuple(operation_id(item) for item in raw_caps)
    explicit = work.get("operation")
    if explicit is not None:
        operation = operation_id(explicit)
        if caps and operation not in caps:
            raise ModelContractError("request operation disagrees with capabilities")
    elif caps:
        # The first capability is the operation; additional capabilities
        # further constrain selection. This preserves isolation constraints.
        operation = caps[0]
    else:
        source = work.get("input")
        kind = str(source.get("kind") or "") if isinstance(source, dict) else ""
        if kind in ("chat", "chat.completions"):
            operation = "chat.completions"
        else:
            raise ModelContractError("request must specify operation or capabilities")
    required = tuple(dict.fromkeys((operation, *caps)))
    return model, operation, required


def market_supports(market: Any, work: Any) -> bool:
    try:
        model, _op, caps = request_contract(work)
        return (model in advertised_models(market)
                and set(caps).issubset(advertised_operations(market)))
    except ModelContractError:
        return False


def verify_execution_result(work: Any, result: Any) -> str:
    expected, _op, _caps = request_contract(work)
    if not isinstance(result, dict) or result.get("status") != "success":
        raise ModelContractError("worker did not return a successful execution")
    response = result.get("response")
    if not isinstance(response, dict):
        raise ModelContractError("worker result has no execution model evidence")
    actual = model_id(response.get("model"))
    if actual != expected:
        raise ModelContractError("executed model differs from requested model")
    solution = result.get("solution")
    if solution is not None:
        family = solution.get("model", {}).get("family") if isinstance(solution, dict) else None
        if family != expected:
            raise ModelContractError("native solution model differs from requested model")
    return actual
