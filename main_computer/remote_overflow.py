from __future__ import annotations

"""Remote overflow assessment and mock hub AI execution contracts.

This module is intentionally backend-only.  It decides whether a local AI
request may offer remote overflow, and it can return a clearly simulated hub AI
result for end-to-end flow tests.  It does not submit real remote work, mint
credits, hold credits, spend credits, or expose private worker prices.
"""

import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r\n", "\n").replace("\r", "\n").split())


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled", "enable"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "disable"}:
        return False
    return default


def _as_int(value: Any, default: int = 0, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    if minimum is not None:
        number = max(int(minimum), number)
    if maximum is not None:
        number = min(int(maximum), number)
    return number


def _card(key: str, title: str, status: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "status": status,
        "message": message,
        "details": dict(details or {}),
    }


@dataclass(frozen=True)
class RemoteOverflowAssessment:
    status: str
    action: str
    reason_code: str
    user_message: str
    authorization_required: bool
    offer_remote: bool
    cards: list[dict[str, Any]]
    authorization_payload: dict[str, Any] | None = None
    updated_at: str = field(default_factory=_utc_now)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RemoteRequestEstimate:
    ok: bool
    reason_code: str
    estimated_input_tokens: int
    max_output_tokens: int
    credits_per_token: int
    minimum_useful_credits: int
    estimated_max_credits: int
    message: str
    card: dict[str, Any]


@dataclass(frozen=True)
class CreditReadiness:
    ok: bool
    reason_code: str
    bridged_credits: int
    spendable_credits: int
    pending_holds: int
    daily_remote_limit: int
    daily_remote_used: int
    minimum_useful_credits: int
    estimated_max_credits: int
    message: str
    card: dict[str, Any]


@dataclass(frozen=True)
class HubAvailability:
    ok: bool
    reason_code: str
    willing_worker_count: int
    preflight_id: str
    message: str
    card: dict[str, Any]


def _messages_from_request(request: dict[str, Any]) -> list[dict[str, Any]]:
    messages = request.get("messages")
    if isinstance(messages, list):
        return [item for item in messages if isinstance(item, dict)]
    cell = request.get("cell") if isinstance(request.get("cell"), dict) else {}
    source = str(request.get("source") or cell.get("source") or request.get("prompt") or "")
    return [{"role": "user", "content": source}] if source else []


def estimate_remote_request(request: dict[str, Any]) -> RemoteRequestEstimate:
    if _as_bool(request.get("force_estimate_unavailable"), False):
        card = _card(
            "remote_request_estimate",
            "Remote request estimate",
            "blocked",
            "This request could not be estimated safely, so remote workers were not offered.",
            {"reason_code": "cost_estimate_unavailable"},
        )
        return RemoteRequestEstimate(False, "cost_estimate_unavailable", 0, 0, 0, 0, 0, card["message"], card)

    messages = _messages_from_request(request)
    content_chars = sum(len(str(item.get("content") or "")) for item in messages)
    attachment_count = 0
    for item in messages:
        attachments = item.get("attachments")
        if isinstance(attachments, list):
            attachment_count += len(attachments)
    if content_chars <= 0 and attachment_count <= 0:
        card = _card(
            "remote_request_estimate",
            "Remote request estimate",
            "blocked",
            "This request has no estimable prompt content, so remote workers were not offered.",
            {"reason_code": "cost_estimate_unavailable", "message_count": len(messages)},
        )
        return RemoteRequestEstimate(False, "cost_estimate_unavailable", 0, 0, 0, 0, 0, card["message"], card)

    estimated_input_tokens = max(1, int(math.ceil(content_chars / 4.0)) + attachment_count * 256)
    max_output_tokens = _as_int(request.get("max_output_tokens"), 1024, minimum=1, maximum=128_000)
    credits_per_token = _as_int(request.get("credits_per_token"), 1, minimum=1, maximum=1_000_000)
    estimated_max_credits = (estimated_input_tokens + max_output_tokens) * credits_per_token
    default_minimum = min(estimated_max_credits, max(128 * credits_per_token, max_output_tokens // 4 * credits_per_token))
    minimum_useful_credits = _as_int(
        request.get("minimum_useful_credits"),
        default_minimum,
        minimum=1,
        maximum=max(1, estimated_max_credits),
    )
    message = f"Estimated maximum remote authorization is {estimated_max_credits:,} credits."
    card = _card(
        "remote_request_estimate",
        "Remote request estimate",
        "pass",
        message,
        {
            "reason_code": "cost_estimate_ready",
            "message_count": len(messages),
            "content_chars": content_chars,
            "attachment_count": attachment_count,
            "estimated_input_tokens": estimated_input_tokens,
            "max_output_tokens": max_output_tokens,
            "credits_per_token": credits_per_token,
            "minimum_useful_credits": minimum_useful_credits,
            "estimated_max_credits": estimated_max_credits,
        },
    )
    return RemoteRequestEstimate(
        True,
        "cost_estimate_ready",
        estimated_input_tokens,
        max_output_tokens,
        credits_per_token,
        minimum_useful_credits,
        estimated_max_credits,
        message,
        card,
    )


def assess_credit_readiness(request: dict[str, Any], estimate: RemoteRequestEstimate) -> CreditReadiness:
    credit = request.get("credit")
    credit_payload = credit if isinstance(credit, dict) else request

    if _as_bool(credit_payload.get("credit_ready"), False):
        default_balance = max(estimate.estimated_max_credits, estimate.minimum_useful_credits)
        bridged_credits = _as_int(credit_payload.get("bridged_credits"), default_balance, minimum=0)
        spendable_credits = _as_int(credit_payload.get("spendable_credits"), default_balance, minimum=0)
    elif any(key in credit_payload for key in ("bridged_credits", "spendable_credits", "pending_holds", "daily_remote_limit", "daily_remote_used")):
        bridged_credits = _as_int(credit_payload.get("bridged_credits"), 0, minimum=0)
        spendable_credits = _as_int(credit_payload.get("spendable_credits"), 0, minimum=0)
    else:
        card = _card(
            "credit_readiness",
            "Credit readiness",
            "blocked",
            "Remote workers were not offered because bridged credit state is not known to this assessment.",
            {
                "reason_code": "credit_state_unknown",
                "minimum_useful_credits": estimate.minimum_useful_credits,
                "estimated_max_credits": estimate.estimated_max_credits,
                "no_credit_hold_created": True,
            },
        )
        return CreditReadiness(
            False,
            "credit_state_unknown",
            0,
            0,
            0,
            0,
            0,
            estimate.minimum_useful_credits,
            estimate.estimated_max_credits,
            card["message"],
            card,
        )

    pending_holds = _as_int(credit_payload.get("pending_holds"), 0, minimum=0)
    daily_remote_limit = _as_int(credit_payload.get("daily_remote_limit"), 0, minimum=0)
    daily_remote_used = _as_int(credit_payload.get("daily_remote_used"), 0, minimum=0)

    ok = True
    reason_code = "credit_ready"
    message = "Spendable bridged credits are sufficient for this remote request."
    if bridged_credits < estimate.minimum_useful_credits:
        ok = False
        reason_code = "insufficient_bridged_credits"
        message = "Remote workers were not offered because bridged credits are below the minimum useful request budget."
    elif spendable_credits < estimate.minimum_useful_credits:
        ok = False
        reason_code = "insufficient_spendable_credits"
        message = "Remote workers were not offered because spendable credits are below the minimum useful request budget."
    elif max(0, spendable_credits - pending_holds) < estimate.minimum_useful_credits:
        ok = False
        reason_code = "pending_holds_exhaust_budget"
        message = "Remote workers were not offered because pending holds exhaust the useful remote request budget."
    elif daily_remote_limit and (daily_remote_used + estimate.minimum_useful_credits) > daily_remote_limit:
        ok = False
        reason_code = "daily_remote_limit_exceeded"
        message = "Remote workers were not offered because the daily remote spend limit would be exceeded."

    card = _card(
        "credit_readiness",
        "Credit readiness",
        "pass" if ok else "blocked",
        message,
        {
            "reason_code": reason_code,
            "bridged_credits": bridged_credits,
            "spendable_credits": spendable_credits,
            "pending_holds": pending_holds,
            "daily_remote_limit": daily_remote_limit,
            "daily_remote_used": daily_remote_used,
            "minimum_useful_credits": estimate.minimum_useful_credits,
            "estimated_max_credits": estimate.estimated_max_credits,
            "no_credit_minted": True,
            "no_credit_hold_created": True,
        },
    )
    return CreditReadiness(
        ok,
        reason_code,
        bridged_credits,
        spendable_credits,
        pending_holds,
        daily_remote_limit,
        daily_remote_used,
        estimate.minimum_useful_credits,
        estimate.estimated_max_credits,
        message,
        card,
    )


def probe_hub_availability(request: dict[str, Any], estimate: RemoteRequestEstimate, credit: CreditReadiness) -> HubAvailability:
    hub = request.get("hub")
    hub_payload = hub if isinstance(hub, dict) else request
    willing_worker_count = _as_int(hub_payload.get("willing_worker_count"), 0, minimum=0, maximum=1_000_000)
    preflight_id = str(hub_payload.get("preflight_id") or "").strip()
    if not preflight_id and willing_worker_count:
        preflight_id = f"mock-preflight-{int(time.time() * 1000)}"

    if willing_worker_count <= 0:
        message = "No compatible remote workers are currently willing under your payment policy."
        card = _card(
            "hub_availability",
            "Hub availability",
            "blocked",
            message,
            {
                "reason_code": "no_willing_workers",
                "willing_worker_count": 0,
                "capability": str(request.get("capability") or "chat.completions"),
                "model": str(request.get("model") or ""),
                "private_worker_prices_exposed": False,
            },
        )
        return HubAvailability(False, "no_willing_workers", 0, "", message, card)

    message = f"{willing_worker_count} compatible worker{'s are' if willing_worker_count != 1 else ' is'} currently willing under your payment policy."
    card = _card(
        "hub_availability",
        "Hub availability",
        "available",
        message,
        {
            "reason_code": "willing_workers_available",
            "willing_worker_count": willing_worker_count,
            "capability": str(request.get("capability") or "chat.completions"),
            "model": str(request.get("model") or ""),
            "preflight_id": preflight_id,
            "private_worker_prices_exposed": False,
        },
    )
    return HubAvailability(True, "willing_workers_available", willing_worker_count, preflight_id, message, card)


class RemoteOverflowDecisionEngine:
    """Backend decision service for local-first remote-overflow routing."""

    def __init__(self, local_capacity_provider: Callable[..., dict[str, Any]] | None = None) -> None:
        self.local_capacity_provider = local_capacity_provider or self._default_local_capacity_provider

    @staticmethod
    def _default_local_capacity_provider(*, thread_id: str = "", max_local_concurrency: int = 1) -> dict[str, Any]:
        return {
            "ok": True,
            "available_now": True,
            "busy": False,
            "reason_code": "local_ai_available",
            "user_message": "This chat can use local AI now.",
            "thread_id": thread_id,
            "active_run_count": 0,
            "max_local_concurrency": max_local_concurrency,
            "active_thread_ids": [],
            "active_runs": [],
            "cards": [
                _card(
                    "local_capacity",
                    "Local AI capacity",
                    "pass",
                    "This chat can use local AI now.",
                    {
                        "checked_thread_id": thread_id,
                        "active_run_count": 0,
                        "max_local_concurrency": max_local_concurrency,
                        "reason_code": "local_ai_available",
                    },
                )
            ],
        }

    def assess(self, request: dict[str, Any] | None = None) -> RemoteOverflowAssessment:
        request = dict(request or {})
        cards: list[dict[str, Any]] = []

        cards.append(
            _card(
                "overflow_conduct",
                "Remote overflow conduct",
                "pass",
                "Remote overflow is a local-first routing decision; assessment does not mint credits, hold credits, spend credits, or hide worker-price spread.",
                {
                    "captain_responsibility": "local_machine",
                    "no_credit_minted": True,
                    "no_credit_hold_created": True,
                    "no_credit_spent": True,
                    "private_worker_prices_exposed": False,
                    "mock_execution_only": True,
                },
            )
        )

        trust_payload = request.get("trust_calibration") if isinstance(request.get("trust_calibration"), dict) else {}
        cards.append(
            _card(
                "trust_calibration",
                "Three-computer trust calibration",
                "visible",
                "The local computer decides, the hub coordinates, remote workers may execute, and the fourth trusted source is treated as telemetry rather than spend authority.",
                {
                    "computer_1": "local_captain",
                    "computer_2": "remote_worker",
                    "computer_3": "hub_coordinator",
                    "fourth_trusted_source": "telemetry_evidence",
                    "command_count": _as_int(trust_payload.get("command_count"), _as_int(request.get("command_count"), 0, minimum=0), minimum=0),
                    "fourth_source_can_authorize_spend": False,
                },
            )
        )

        remote_enabled = _as_bool(request.get("remote_overflow_enabled"), False)
        local_only = _as_bool(request.get("local_only"), False)
        if local_only:
            policy_message = "Remote overflow is blocked because this request is marked local-only."
            policy_reason = "local_only"
        elif not remote_enabled:
            policy_message = "Paid remote worker overflow is disabled in Worker settings."
            policy_reason = "remote_overflow_disabled"
        else:
            policy_message = "Paid remote worker overflow is enabled."
            policy_reason = "remote_overflow_enabled"

        policy_status = "pass" if remote_enabled and not local_only else "blocked"
        cards.append(
            _card(
                "remote_overflow_policy",
                "Remote overflow policy",
                policy_status,
                policy_message,
                {
                    "reason_code": policy_reason,
                    "remote_overflow_enabled": remote_enabled,
                    "local_only": local_only,
                },
            )
        )
        if policy_status != "pass":
            cards.extend(
                [
                    _card(
                        "local_capacity",
                        "Local AI capacity",
                        "skipped",
                        "Local capacity was not checked for remote overflow because the policy blocked remote overflow.",
                        {"reason_code": "policy_blocked_before_capacity"},
                    ),
                    _card(
                        "remote_request_estimate",
                        "Remote request estimate",
                        "skipped",
                        "The remote request was not estimated because remote overflow is not allowed for this request.",
                        {"reason_code": "policy_blocked_before_estimate"},
                    ),
                    _card(
                        "credit_readiness",
                        "Credit readiness",
                        "skipped",
                        "Credits were not checked because remote overflow is not allowed for this request.",
                        {"reason_code": "policy_blocked_before_credit", "no_credit_hold_created": True},
                    ),
                    _card(
                        "hub_availability",
                        "Hub availability",
                        "skipped",
                        "Hub availability was not checked because remote overflow is not allowed for this request.",
                        {"reason_code": "policy_blocked_before_hub"},
                    ),
                    _card(
                        "remote_worker_authorization",
                        "Remote worker authorization",
                        "skipped",
                        "Authorization was not requested because remote overflow is not allowed for this request.",
                        {"reason_code": policy_reason},
                    ),
                ]
            )
            return RemoteOverflowAssessment(
                status="blocked",
                action="remote_blocked_by_policy",
                reason_code=policy_reason,
                user_message=policy_message,
                authorization_required=False,
                offer_remote=False,
                cards=cards,
            )

        thread_id = str(request.get("thread_id") or request.get("chat_thread_id") or "").strip()
        max_local_concurrency = _as_int(request.get("max_local_concurrency"), 1, minimum=1, maximum=1024)
        capacity = self.local_capacity_provider(thread_id=thread_id, max_local_concurrency=max_local_concurrency)
        local_cards = capacity.get("cards") if isinstance(capacity.get("cards"), list) else []
        if local_cards:
            cards.extend([dict(item) for item in local_cards if isinstance(item, dict)])
        else:
            cards.append(
                _card(
                    "local_capacity",
                    "Local AI capacity",
                    "blocked" if _as_bool(capacity.get("busy"), False) else "pass",
                    str(capacity.get("user_message") or "Local AI capacity was checked."),
                    {"reason_code": str(capacity.get("reason_code") or "")},
                )
            )

        if _as_bool(capacity.get("available_now"), False):
            cards.extend(
                [
                    _card(
                        "remote_request_estimate",
                        "Remote request estimate",
                        "skipped",
                        "The remote request was not estimated because local AI can handle the request now.",
                        {"reason_code": "local_ai_available_before_estimate"},
                    ),
                    _card(
                        "credit_readiness",
                        "Credit readiness",
                        "skipped",
                        "Credits were not checked because local AI can handle the request now.",
                        {"reason_code": "local_ai_available_before_credit", "no_credit_hold_created": True},
                    ),
                    _card(
                        "hub_availability",
                        "Hub availability",
                        "skipped",
                        "Hub availability was not checked because local AI can handle the request now.",
                        {"reason_code": "local_ai_available_before_hub"},
                    ),
                    _card(
                        "remote_worker_authorization",
                        "Remote worker authorization",
                        "skipped",
                        "Authorization was not requested because remote workers are not needed.",
                        {"reason_code": "local_ai_available"},
                    ),
                ]
            )
            return RemoteOverflowAssessment(
                status="not_needed",
                action="run_local",
                reason_code="local_ai_available",
                user_message="Local AI is available now, so remote workers were not checked.",
                authorization_required=False,
                offer_remote=False,
                cards=cards,
            )

        estimate = estimate_remote_request(request)
        cards.append(estimate.card)
        if not estimate.ok:
            cards.extend(
                [
                    _card(
                        "credit_readiness",
                        "Credit readiness",
                        "skipped",
                        "Credits were not checked because the remote request estimate was not safe.",
                        {"reason_code": "estimate_blocked_before_credit", "no_credit_hold_created": True},
                    ),
                    _card(
                        "hub_availability",
                        "Hub availability",
                        "skipped",
                        "Hub availability was not checked because the remote request estimate was not safe.",
                        {"reason_code": "estimate_blocked_before_hub"},
                    ),
                    _card(
                        "remote_worker_authorization",
                        "Remote worker authorization",
                        "skipped",
                        "Authorization was not requested because the remote request estimate was not safe.",
                        {"reason_code": estimate.reason_code},
                    ),
                ]
            )
            return RemoteOverflowAssessment(
                status="blocked",
                action="remote_unavailable",
                reason_code=estimate.reason_code,
                user_message=estimate.message,
                authorization_required=False,
                offer_remote=False,
                cards=cards,
            )

        credit = assess_credit_readiness(request, estimate)
        cards.append(credit.card)
        if not credit.ok:
            cards.extend(
                [
                    _card(
                        "hub_availability",
                        "Hub availability",
                        "skipped",
                        "Hub availability was not checked because the request cannot currently be funded.",
                        {"reason_code": "credit_blocked_before_hub"},
                    ),
                    _card(
                        "remote_worker_authorization",
                        "Remote worker authorization",
                        "skipped",
                        "Authorization was not requested because credit readiness did not pass.",
                        {"reason_code": credit.reason_code},
                    ),
                ]
            )
            return RemoteOverflowAssessment(
                status="blocked",
                action="remote_blocked_by_credit",
                reason_code=credit.reason_code,
                user_message=credit.message,
                authorization_required=False,
                offer_remote=False,
                cards=cards,
            )

        hub = probe_hub_availability(request, estimate, credit)
        cards.append(hub.card)
        if not hub.ok:
            cards.append(
                _card(
                    "remote_worker_authorization",
                    "Remote worker authorization",
                    "skipped",
                    "Authorization was not requested because no compatible remote worker is currently available.",
                    {"reason_code": hub.reason_code},
                )
            )
            return RemoteOverflowAssessment(
                status="unavailable",
                action="remote_unavailable",
                reason_code=hub.reason_code,
                user_message=hub.message,
                authorization_required=False,
                offer_remote=False,
                cards=cards,
            )

        authorization_payload = {
            "kind": "remote_overflow_authorization",
            "simulated": True,
            "reason_local_was_not_used": str(capacity.get("reason_code") or "local_ai_busy"),
            "willing_worker_count": hub.willing_worker_count,
            "configured_credits_per_token": estimate.credits_per_token,
            "estimated_input_tokens": estimate.estimated_input_tokens,
            "authorized_output_token_limit": estimate.max_output_tokens,
            "estimated_max_credits": estimate.estimated_max_credits,
            "minimum_useful_credits": estimate.minimum_useful_credits,
            "credit_readiness": {
                "reason_code": credit.reason_code,
                "bridged_credits": credit.bridged_credits,
                "spendable_credits": credit.spendable_credits,
                "pending_holds": credit.pending_holds,
            },
            "preflight_id": hub.preflight_id,
            "private_worker_prices_exposed": False,
            "no_credit_hold_created": True,
            "expires_at": _utc_now(),
        }
        cards.append(
            _card(
                "remote_worker_authorization",
                "Remote worker authorization",
                "waiting",
                "Choose whether to wait locally or use a remote worker.",
                {
                    "reason_code": "remote_authorization_required",
                    "willing_worker_count": hub.willing_worker_count,
                    "estimated_max_credits": estimate.estimated_max_credits,
                    "simulated": True,
                },
            )
        )
        return RemoteOverflowAssessment(
            status="authorization_required",
            action="authorization_required",
            reason_code="remote_authorization_required",
            user_message="A compatible mock remote worker is available; user authorization is required before simulated remote execution.",
            authorization_required=True,
            offer_remote=True,
            cards=cards,
            authorization_payload=authorization_payload,
        )


class MockHubAIOverflowProvider:
    """Fast simulated hub AI provider used before real remote execution exists."""

    def run(self, request: dict[str, Any], assessment: RemoteOverflowAssessment) -> dict[str, Any]:
        if not assessment.authorization_required or not assessment.offer_remote:
            return {
                "ok": False,
                "status": "blocked",
                "error": "Remote overflow was not authorization-ready.",
                "remote_overflow": assessment.as_dict(),
            }

        delay_ms = _as_int(request.get("mock_thinking_delay_ms"), 120, minimum=0, maximum=2_000)
        if delay_ms:
            time.sleep(delay_ms / 1000.0)

        messages = _messages_from_request(request)
        prompt_preview = _clean_text(" ".join(str(item.get("content") or "") for item in messages))[:280]
        if not prompt_preview:
            prompt_preview = "the submitted chat request"

        content = (
            "Mock remote AI result returned by the hub test provider. "
            "This is simulated overflow output for test flow only; no real remote worker was contacted, "
            "no credits were held, and no credits were spent.\n\n"
            f"Prompt preview: {prompt_preview}"
        )
        result = {
            "source": "mock_hub_ai",
            "simulated": True,
            "thinking_delay_ms": delay_ms,
            "response": {
                "content": content,
                "provider": "mock-hub-ai",
                "model": str(request.get("model") or "mock-overflow-model"),
                "metadata": {
                    "remote_overflow": True,
                    "simulated": True,
                    "mock_hub_ai": True,
                    "preflight_id": (assessment.authorization_payload or {}).get("preflight_id", ""),
                    "willing_worker_count": (assessment.authorization_payload or {}).get("willing_worker_count", 0),
                    "no_real_remote_worker_contacted": True,
                    "no_credit_hold_created": True,
                    "no_credit_spent": True,
                },
            },
            "cards": [
                _card(
                    "remote_worker_execution",
                    "Remote worker execution",
                    "simulated",
                    "This result was produced by the mock hub AI provider for overflow testing. No real remote worker was contacted and no credits were spent.",
                    {
                        "source": "mock_hub_ai",
                        "simulated": True,
                        "thinking_delay_ms": delay_ms,
                        "no_real_remote_worker_contacted": True,
                        "no_credit_hold_created": True,
                        "no_credit_spent": True,
                    },
                )
            ],
            "updated_at": _utc_now(),
        }
        return {
            "ok": True,
            "status": "completed",
            "remote_overflow": assessment.as_dict(),
            "remote_overflow_result": result,
        }


def assess_remote_overflow(
    request: dict[str, Any] | None = None,
    *,
    local_capacity_provider: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return RemoteOverflowDecisionEngine(local_capacity_provider=local_capacity_provider).assess(request).as_dict()


def run_mock_hub_overflow(
    request: dict[str, Any] | None = None,
    *,
    local_capacity_provider: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    request = dict(request or {})
    assessment = RemoteOverflowDecisionEngine(local_capacity_provider=local_capacity_provider).assess(request)
    return MockHubAIOverflowProvider().run(request, assessment)
