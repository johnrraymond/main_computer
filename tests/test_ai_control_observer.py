from __future__ import annotations

from pathlib import Path

from main_computer.ai_control import (
    ai_call_surface,
    ai_control_calls_snapshot,
    ai_control_handle_profile_action,
    ai_control_profile_catalog,
    ai_control_prompt_catalog,
    ai_control_prompt_text,
    ai_control_save_prompt_override,
    observe_provider,
)
from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers.base import LLMProvider


class _StubProvider(LLMProvider):
    name = "stub"
    model = "stub-model"

    def __init__(self) -> None:
        self.seen_messages = None

    def chat(self, messages):
        self.seen_messages = messages
        return ChatResponse(content="stub response", provider=self.name, model=self.model, metadata={"ok": True})


def test_observed_provider_records_prompt_messages_without_changing_response(tmp_path: Path) -> None:
    provider = _StubProvider()
    observed = observe_provider(provider, runtime_root=tmp_path)
    messages = [
        ChatMessage(role="system", content="system law"),
        ChatMessage(role="user", content="user request"),
    ]

    with ai_call_surface("tests.ai_control"):
        response = observed.chat(messages)

    assert response.content == "stub response"
    assert provider.seen_messages is messages

    snapshot = ai_control_calls_snapshot(tmp_path)
    assert snapshot["ok"] is True
    assert snapshot["call_count"] == 1

    call = snapshot["calls"][0]
    assert call["surface"] == "tests.ai_control"
    assert call["status"] == "ok"
    assert call["provider"] == "stub"
    assert call["model"] == "stub-model"
    assert call["system_message_count"] == 1
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][0]["content"] == "system law"
    assert call["messages"][1]["role"] == "user"
    assert call["response"]["content"] == "stub response"


def test_ai_control_prompt_catalog_exposes_static_prompts_and_structures(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]

    catalog = ai_control_prompt_catalog(tmp_path, repo_root=repo)

    assert catalog["ok"] is True
    assert catalog["schema"] == "main_computer.ai_control.prompt_catalog.v1"
    assert catalog["prompt_count"] >= 8
    assert catalog["message_structures"]

    router_prompt = next(prompt for prompt in catalog["prompts"] if prompt["id"] == "router.system")
    assert router_prompt["source_file"] == "main_computer/router.py"
    assert router_prompt["source_symbol"] == "SYSTEM_PROMPT"
    assert "central local AI layer" in router_prompt["default_content"]
    assert router_prompt["effective_content"] == router_prompt["default_content"]
    assert router_prompt["has_override"] is False

    router_structure = next(item for item in catalog["message_structures"] if item["id"] == "router.chat")
    assert router_structure["provider_call"] == "self.provider.chat(messages)"
    assert router_structure["slots"][0]["prompt_id"] == "router.system"
    assert router_structure["slots"][-1]["role"] == "user"


def test_ai_control_prompt_override_is_runtime_only_and_explicit(tmp_path: Path) -> None:
    assert ai_control_prompt_text("router.system", "source default", runtime_root=tmp_path) == "source default"

    saved = ai_control_save_prompt_override(tmp_path, prompt_id="router.system", content="runtime override")
    assert saved["ok"] is True
    assert saved["override_count"] == 1
    assert ai_control_prompt_text("router.system", "source default", runtime_root=tmp_path) == "runtime override"

    reset = ai_control_save_prompt_override(tmp_path, prompt_id="router.system", reset=True)
    assert reset["ok"] is True
    assert reset["override_count"] == 0
    assert ai_control_prompt_text("router.system", "source default", runtime_root=tmp_path) == "source default"


def test_ai_control_profiles_are_named_sets_of_editable_composables(tmp_path: Path) -> None:
    catalog = ai_control_profile_catalog(tmp_path)

    assert catalog["ok"] is True
    assert catalog["schema"] == "main_computer.ai_control.profiles.v1"
    assert catalog["active_profile_id"] == "factory.operator_safe"
    assert catalog["profile_count"] >= 3
    assert catalog["composable_count"] >= 6

    active = catalog["active_profile"]
    assert active["name"] == "Operator Safe"
    assert "builtin.operator_real_workspace" in active["enabled_composable_ids"]
    assert "Treat the user as a capable operator" in active["compiled_preview"]

    custom_choice = ai_control_handle_profile_action(
        tmp_path,
        {
            "action": "save_composable",
            "label": "Large cat, specifically a lion",
            "kind": "framing",
            "description": "User-defined playful framing.",
            "prompt_text": "The user-defined framing says the user is a large cat, specifically a lion.",
        },
    )
    assert custom_choice["ok"] is True
    lion = next(item for item in custom_choice["composables"] if item["label"] == "Large cat, specifically a lion")
    assert lion["source"] == "user"
    assert lion["can_delete"] is True

    custom_profile = ai_control_handle_profile_action(
        tmp_path,
        {
            "action": "save_profile",
            "name": "Lion Operator",
            "description": "Operator profile with user-defined lion framing.",
            "enabled_composable_ids": ["builtin.operator_real_workspace", lion["id"]],
            "set_active": True,
        },
    )
    assert custom_profile["ok"] is True
    assert custom_profile["active_profile"]["name"] == "Lion Operator"
    assert lion["id"] in custom_profile["active_profile"]["enabled_composable_ids"]
    assert "large cat, specifically a lion" in custom_profile["active_profile"]["compiled_preview"]

    profile_overlay = ai_control_handle_profile_action(
        tmp_path,
        {
            "action": "save_profile",
            "profile_id": custom_profile["active_profile"]["id"],
            "name": "Lion Operator",
            "description": "Operator profile with user-defined lion framing.",
            "enabled_composable_ids": ["builtin.operator_real_workspace", lion["id"]],
            "composable_overrides": {
                lion["id"]: {
                    "label": "Large cat, specifically a lion",
                    "kind": "framing",
                    "description": "Profile-local lion framing.",
                    "prompt_text": "Within this profile, treat the user framing as a large lion operator.",
                }
            },
        },
    )
    active_overlay = profile_overlay["active_profile"]
    assert "large lion operator" in active_overlay["compiled_preview"]
    lion_choice = next(choice for choice in active_overlay["choices"] if choice["id"] == lion["id"])
    assert lion_choice["profile_has_override"] is True
    assert lion_choice["prompt_text"] == "Within this profile, treat the user framing as a large lion operator."

    reset = ai_control_handle_profile_action(
        tmp_path,
        {
            "action": "save_profile",
            "profile_id": "factory.operator_safe",
            "name": "Edited Operator Safe",
            "description": "Edited factory profile.",
            "enabled_composable_ids": ["builtin.next_command"],
        },
    )
    edited = next(profile for profile in reset["profiles"] if profile["id"] == "factory.operator_safe")
    assert edited["has_override"] is True
    assert edited["enabled_composable_ids"] == ["builtin.next_command"]

    factory = ai_control_handle_profile_action(
        tmp_path,
        {"action": "reset_profile", "profile_id": "factory.operator_safe"},
    )
    restored = next(profile for profile in factory["profiles"] if profile["id"] == "factory.operator_safe")
    assert restored["has_override"] is False
    assert "builtin.operator_real_workspace" in restored["enabled_composable_ids"]


def test_ai_control_application_assets_are_registered() -> None:
    repo = Path(__file__).resolve().parents[1]
    applications_html = (repo / "main_computer/web/applications.html").read_text(encoding="utf-8")
    navigation_js = (repo / "main_computer/web/applications/scripts/dom-bindings/navigation.js").read_text(encoding="utf-8")
    routes = (repo / "main_computer/viewport_state.py").read_text(encoding="utf-8")
    dispatch = (repo / "main_computer/viewport_route_dispatch.py").read_text(encoding="utf-8")

    assert 'data-app="ai-control"' in applications_html
    assert "applications/apps/ai-control.html" in applications_html
    assert "applications/scripts/ai-control.js" in applications_html

    ai_control_html = (repo / "main_computer/web/applications/apps/ai-control.html").read_text(encoding="utf-8")
    assert "System profile" in ai_control_html
    assert "Edit selected profile choices" in ai_control_html
    assert "Add user-defined choice" in ai_control_html
    assert "AI Surfaces" in ai_control_html
    assert "ai-control-surface-description" in ai_control_html
    assert "Recent AI Calls" not in ai_control_html
    assert "Message Structures" not in ai_control_html
    assert "ai-control-structure-panel" not in ai_control_html
    assert '"ai-control": ["AI Control"' in navigation_js
    assert "prompt structure" in navigation_js
    assert '"ai-control",' in routes
    assert '"/api/applications/ai-control/prompts"' in dispatch
    assert '"/api/applications/ai-control/profiles"' in dispatch
    assert '"/api/applications/ai-control/prompts/override"' in dispatch
    assert '"/api/applications/ai-control/profiles/action"' in dispatch
