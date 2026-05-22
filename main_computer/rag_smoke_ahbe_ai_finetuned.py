from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_computer.config import MainComputerConfig
from main_computer.models import ChatMessage
from main_computer.providers import LLMProvider, OllamaProvider, OpenAIProvider
from main_computer.rag_retriever import DeterministicRagRetriever, RagRetrieverConfig


SCENARIO = "rag_smoke_ahbe_ai_finetuned"
AHBE_AI_RELATIVE_PATH = "knowledge/rag_smoke_ahbe_ai_finetuned.md"


ENGLISH_EXTENDED_AHBE_GRAMMAR = """# Ahbe-English Grammar v0.5

Ahbe-English, pronounced ahh-bay, is an idea-compression language.

It is not XML.
It is not byte-perfect.
It is not judged by exact surface syntax.

Ahbe-English preserves ideas in a compact form so a second model can decode the
ideas and solve the original task.

Common shapes:
- ~doc id=<name>
- !lex short="expanded idea"
- #actor id=<id> ...
- #item id=<id> ...
- #place id=<id> ...
- #edge id=<id> ...
- #problem id=<id> ...

Common idea markers:
- #kind introduces an idea record.
- key:value attaches an idea to a record.
- @x points to another idea.
- $x expands through !lex.
- [a,b,c] is a compact list.
- {k:v} is a compact map.
- free English is allowed when it preserves meaning.

For route problems, preserve these ideas:
- places
- directed connections
- edge costs
- access requirements
- forbidden or avoided route properties
- carried access item
- target item
- who unlocks the target item
- unlock condition
- seal or key used by the target item
- the actual question

Important semantic roles:
- access_item: what the traveler has/carries to satisfy route requirements
- target_item: the object being sought or described
- unlocker: actor who can unlock the target item
- unlock_when: condition/time when unlocking can happen
- seal: seal/key/color marker associated with the target item
- avoid: route tags that must not be used

When decoding:
- Decode aliases and references by meaning.
- cd, @cd, $cd, CanalDock, and Canal Dock may refer to the same place if the
  encoding makes that clear.
- Edge labels such as CanalDockToVelinArchive may encode a route edge.
- Do not invent routes.
- Do not use forbidden/avoided routes.
- Do not confuse the carried access item with the target item.
- Solve the original task represented by the Ahbe-English ideas.
"""


SOURCE_PROBLEM_ENGLISH = """A compact routing test for Ahbe-English:

Mira is the keeper. Orbix is a blue tool owned by Mira. Mira is the only one who
can unlock Orbix after sunset, and Orbix uses the blue seal.

The traveler starts at Canal Dock and must reach the Vault Atrium. The traveler
has a brass token. Avoid red and forbidden routes. The carried thing is the
brass token. Orbix is the target item, not the carried access token.

Known places:
- Canal Dock
- Velin Archive, floor 7, access requires a brass token
- Moon Bridge
- Vault Atrium

Declared directed routes:
- Canal Dock to Velin Archive costs 3, uses stairs, requires nothing, and is safe.
- Velin Archive to Moon Bridge costs 2, uses a gantry, requires the brass token, and is safe.
- Moon Bridge to Vault Atrium costs 4, uses a lens path, requires nothing, and is safe.
- Canal Dock to Vault Atrium costs 1, uses the red zone, requires nothing, and is tagged red and forbidden.

There is no direct route from Velin Archive to Vault Atrium.
There is no direct route from Canal Dock to Moon Bridge.
There is no route that may skip Moon Bridge unless a declared allowed edge says so.

Solve for the lowest-cost allowed route to the Vault Atrium and identify who unlocks Orbix.
"""


EXPECTED_IDEAS = {
    "route": ["Canal Dock", "Velin Archive", "Moon Bridge", "Vault Atrium"],
    "safe_edge_costs": [3, 2, 4],
    "forbidden_edge_cost": 1,
    "cost": 9,
    "carry": "brass token",
    "item": "Orbix",
    "owner": "Mira",
    "unlocker": "Mira",
    "unlock_when": "sunset",
    "seal": "blue seal",
    "avoid": ["red", "forbidden"],
}


ENCODER_SYSTEM_PROMPT = """You compress English tasks into Ahbe-English.

Ahbe-English is an idea encoding, not byte-perfect XML.

Your job is NOT to solve the task.
Your job is to preserve the ideas needed for another model to solve the task.

Return only one fenced ```ahbe block.

Preserve these ideas:
- Mira is keeper/owner/unlocker.
- Orbix is target item, blue tool, uses blue seal.
- Orbix is not the carried access token.
- Brass token is the carried access item.
- Unlock happens after sunset.
- Route graph and route costs.
- Red/forbidden route must be avoided.
- The task asks for the lowest-cost allowed route and who unlocks Orbix.

You may use compact ids, aliases, references, lists, maps, or short English.
Do not drop an idea merely to save space.
Do not invent facts.
Do not include the final answer.
"""


SOLVER_SYSTEM_PROMPT = """You decode Ahbe-English and solve the original task.

Ahbe-English is an idea encoding. Decode the ideas, not just literal bytes.

Use only the Ahbe-English prompt and grammar.
Do not invent routes.
Do not use red/forbidden routes.
Do not treat Orbix as the carried access token.
The carried access item is the brass token.
The target item is Orbix.
The unlocker is the actor who unlocks Orbix, not the access token.
The seal is the seal associated with Orbix.
Sum route edge costs when costs are present.

Return JSON only if possible:
{
  "route": ["places or edge labels in order"],
  "route_places": ["places in order if known"],
  "route_edges": ["edges in order if known"],
  "cost": 0,
  "carry": ["items carried"],
  "item": "target item",
  "unlocker": "who unlocks it",
  "unlock_when": "when",
  "seal": "seal",
  "answer": "short answer"
}

Edge labels such as CanalDockToVelinArchive are acceptable, but the route must
still represent Canal Dock -> Velin Archive -> Moon Bridge -> Vault Atrium.
"""


PLACE_ALIASES = {
    "Canal Dock": ["Canal Dock", "CanalDock", "cd", "@cd", "$cd", "canal", "dock"],
    "Velin Archive": ["Velin Archive", "VelinArchive", "va", "@va", "$va", "velin", "archive"],
    "Moon Bridge": ["Moon Bridge", "MoonBridge", "mb", "@mb", "$mb", "moon", "bridge"],
    "Vault Atrium": ["Vault Atrium", "VaultAtrium", "vt", "@vt", "$vt", "vault", "atrium"],
}

THING_ALIASES = {
    "brass token": ["brass token", "brassToken", "bt", "@bt", "$bt", "token"],
    "blue seal": ["blue seal", "blueSeal", "bs", "@bs", "$bs", "seal"],
    "Mira": ["Mira", "mira", "@mira", "$mira"],
    "Orbix": ["Orbix", "orbix", "@orbix", "$orbix"],
    "sunset": ["sunset", "after sunset", "dusk"],
    "red": ["red", "red zone", "rz", "$rz"],
    "forbidden": ["forbidden", "avoid", "blocked", "disallowed"],
}

EDGE_IDEAS = [
    ("Canal Dock", "Velin Archive", 3),
    ("Velin Archive", "Moon Bridge", 2),
    ("Moon Bridge", "Vault Atrium", 4),
    ("Canal Dock", "Vault Atrium", 1),
]


@dataclass(frozen=True)
class AiCallRecord:
    kind: str
    prompt: str
    response: str
    provider: str
    model: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IdeaCheck:
    name: str
    ok: bool
    evidence: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AhbeAiFinetunedReport:
    ok: bool
    run_id: str
    scenario: str
    output_dir: str
    report_path: str
    retrieved_paths: list[str]
    encoded_ahbe_prompt: str
    solver_raw_response: str
    solver_json: dict[str, Any]
    normalized_solver: dict[str, Any]
    encoded_checks: list[IdeaCheck]
    solver_checks: list[IdeaCheck]
    calls: list[AiCallRecord]
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    strict: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["encoded_checks"] = [check.to_dict() for check in self.encoded_checks]
        data["solver_checks"] = [check.to_dict() for check in self.solver_checks]
        data["calls"] = [call.to_dict() for call in self.calls]
        return data


def _default_run_id() -> str:
    return "rag_ahbe_ai_finetuned_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def _json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dumps(data) + "\n", encoding="utf-8")


def _provider_from_env() -> LLMProvider:
    config = MainComputerConfig.from_env()

    if config.provider == "openai":
        return OpenAIProvider(
            model=config.model,
            base_url=config.openai_base_url,
            fallback=config.fallback,
        )

    return OllamaProvider(
        model=config.model or "gemma4:26b",
        base_url=config.ollama_base_url,
        timeout_s=config.ollama_timeout_s,
        fallback=config.fallback,
    )


def _provider_summary(provider: LLMProvider) -> tuple[str, str]:
    return (
        str(getattr(provider, "name", provider.__class__.__name__)),
        str(getattr(provider, "model", "")),
    )


def _chat(provider: LLMProvider, *, system: str, user: str) -> tuple[str, str, str]:
    response = provider.chat(
        [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=user),
        ]
    )
    provider_name = str(getattr(response, "provider", getattr(provider, "name", provider.__class__.__name__)))
    model_name = str(getattr(response, "model", getattr(provider, "model", "")))
    return str(response.content or ""), provider_name, model_name


def _extract_fenced_block(text: str, language: str = "ahbe") -> str:
    raw = str(text or "").strip()
    pattern = rf"```{re.escape(language)}\s*(.*?)```"
    match = re.search(pattern, raw, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()

    generic = re.search(r"```\s*(.*?)```", raw, flags=re.DOTALL)
    if generic:
        return generic.group(1).strip()

    return raw


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("Model returned empty JSON response.")

    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))

    if not isinstance(parsed, dict):
        raise ValueError("Expected a JSON object.")

    return parsed


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _compact_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize_text(value))


def _flatten_payload(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {_flatten_payload(item)}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(_flatten_payload(item) for item in value)
    return str(value or "")


def _contains_any(text: str, aliases: list[str]) -> tuple[bool, list[str]]:
    lower = _normalize_text(text)
    compact = _compact_text(text)
    evidence: list[str] = []

    for alias in aliases:
        alias_lower = _normalize_text(alias)
        alias_compact = _compact_text(alias)

        if alias_lower and alias_lower in lower:
            evidence.append(alias)
            continue

        if alias_compact and alias_compact in compact:
            evidence.append(alias)
            continue

        if 1 < len(alias_compact) <= 4:
            pattern = rf"(?<![a-z0-9])[@$]?{re.escape(alias_compact)}(?![a-z0-9])"
            if re.search(pattern, lower):
                evidence.append(alias)

    return bool(evidence), evidence


def _canonical_place(value: Any) -> str | None:
    text = str(value or "").strip()
    compact = _compact_text(text)

    for place, aliases in PLACE_ALIASES.items():
        for alias in aliases:
            if compact == _compact_text(alias):
                return place

    return None


def _canonical_thing(value: Any) -> str:
    text = str(value or "").strip()
    compact = _compact_text(text)

    for canonical, aliases in THING_ALIASES.items():
        for alias in aliases:
            if compact == _compact_text(alias):
                return canonical

    return text


def _edge_from_token(value: Any) -> tuple[str, str] | None:
    compact = _compact_text(value)

    for source, target, _cost in EDGE_IDEAS:
        source_forms = [_compact_text(alias) for alias in PLACE_ALIASES[source]]
        target_forms = [_compact_text(alias) for alias in PLACE_ALIASES[target]]

        source_hits = [form for form in source_forms if form and form in compact]
        target_hits = [form for form in target_forms if form and form in compact]

        if not source_hits or not target_hits:
            continue

        source_index = min(compact.find(form) for form in source_hits)
        target_index = min(compact.find(form) for form in target_hits)

        if source_index <= target_index:
            return source, target

    return None


def _edges_to_place_route(edges: list[tuple[str, str]]) -> list[str]:
    if not edges:
        return []

    route = [edges[0][0], edges[0][1]]

    for source, target in edges[1:]:
        if route[-1] == source:
            route.append(target)
        elif target not in route:
            route.append(target)

    return route


def _split_route_string(value: str) -> list[str]:
    raw = str(value or "")
    parts = re.split(r"\s*(?:->|=>|,|\||;|\bthen\b)\s*", raw, flags=re.IGNORECASE)
    return [part.strip() for part in parts if part.strip()]


def _route_items_to_places(value: Any) -> list[str]:
    if isinstance(value, str):
        value = _split_route_string(value)

    if not isinstance(value, list):
        return []

    edge_hits: list[tuple[str, str]] = []
    place_hits: list[str] = []

    for item in value:
        edge = _edge_from_token(item)
        if edge:
            edge_hits.append(edge)
            continue

        place = _canonical_place(item)
        if place:
            place_hits.append(place)

    if edge_hits:
        return _edges_to_place_route(edge_hits)

    return place_hits


def _route_from_solver_payload(data: dict[str, Any], raw_response: str) -> list[str]:
    for key in ("route_places", "route", "path", "places"):
        route = _route_items_to_places(data.get(key))
        if route:
            return route

    for key in ("route_edges", "edges"):
        route = _route_items_to_places(data.get(key))
        if route:
            return route

    text = _flatten_payload(data) + " " + raw_response
    edge_hits: list[tuple[str, str]] = []

    for source, target, _cost in EDGE_IDEAS:
        for candidate in (
            f"{source} to {target}",
            f"{source}->{target}",
            f"{source}To{target}",
            f"{source} {target}",
        ):
            ok, _evidence = _contains_any(text, [candidate])
            if ok:
                edge_hits.append((source, target))
                break

    if edge_hits:
        return _edges_to_place_route(edge_hits)

    ordered_places: list[tuple[int, str]] = []
    compact = _compact_text(text)

    for place, aliases in PLACE_ALIASES.items():
        indexes = []
        for alias in aliases:
            alias_compact = _compact_text(alias)
            if alias_compact and alias_compact in compact:
                indexes.append(compact.find(alias_compact))
        if indexes:
            ordered_places.append((min(indexes), place))

    return [place for _index, place in sorted(ordered_places)]


def _cost_from_solver_payload(data: dict[str, Any], raw_response: str) -> int | None:
    for key in ("cost", "total_cost", "route_cost"):
        value = data.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())

    text = _normalize_text(_flatten_payload(data) + " " + raw_response)
    if re.search(r"(?<!\d)9(?!\d)", text) or "nine" in text:
        return 9

    return None


def _canonical_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_canonical_thing(item) for item in value]


def _field_or_text_idea(data: dict[str, Any], raw_response: str, keys: list[str], aliases: list[str]) -> tuple[bool, list[str]]:
    evidence: list[str] = []

    for key in keys:
        value = data.get(key)
        if value is None:
            continue

        if isinstance(value, list):
            for item in value:
                canonical = _canonical_thing(item)
                ok, hits = _contains_any(canonical, aliases)
                if ok:
                    evidence.extend(hits)
        else:
            canonical = _canonical_thing(value)
            ok, hits = _contains_any(canonical, aliases)
            if ok:
                evidence.extend(hits)

    text = _flatten_payload(data) + " " + raw_response
    ok, hits = _contains_any(text, aliases)
    if ok:
        evidence.extend(hits)

    return bool(evidence), sorted(set(evidence))


def _check_idea(name: str, ok: bool, evidence: list[str] | None = None, missing: list[str] | None = None, severity: str = "error") -> IdeaCheck:
    return IdeaCheck(
        name=name,
        ok=ok,
        evidence=evidence or [],
        missing=missing or ([] if ok else [name]),
        severity=severity,
    )


def _encoded_idea_checks(encoded: str) -> list[IdeaCheck]:
    checks: list[IdeaCheck] = []

    for name, aliases in [
        ("Mira idea", THING_ALIASES["Mira"]),
        ("Orbix idea", THING_ALIASES["Orbix"]),
        ("brass token idea", THING_ALIASES["brass token"]),
        ("blue seal idea", THING_ALIASES["blue seal"]),
        ("sunset idea", THING_ALIASES["sunset"]),
        ("red avoid idea", THING_ALIASES["red"]),
        ("forbidden avoid idea", THING_ALIASES["forbidden"]),
    ]:
        ok, evidence = _contains_any(encoded, aliases)
        checks.append(_check_idea(name, ok, evidence))

    for place, aliases in PLACE_ALIASES.items():
        ok, evidence = _contains_any(encoded, aliases)
        checks.append(_check_idea(f"place idea: {place}", ok, evidence))

    for source, target, cost in EDGE_IDEAS[:3]:
        edge_texts = [
            f"{source} to {target}",
            f"{source}->{target}",
            f"{source}To{target}",
            f"{source} {target}",
        ]
        edge_ok, edge_evidence = _contains_any(encoded, edge_texts)
        cost_ok, cost_evidence = _contains_any(encoded, [f"cost:{cost}", f"cost={cost}", f"cost {cost}", f"c:{cost}", str(cost)])
        checks.append(
            _check_idea(
                f"safe edge idea: {source} to {target} cost {cost}",
                edge_ok and cost_ok,
                edge_evidence + cost_evidence,
                [] if edge_ok and cost_ok else [f"{source}->{target} cost {cost}"],
            )
        )

    shortcut_ok, shortcut_evidence = _contains_any(
        encoded,
        [
            "Canal Dock to Vault Atrium",
            "CanalDockToVaultAtrium",
            "cd vt",
            "cd->vt",
            "red zone",
            "shortcut",
        ],
    )
    shortcut_cost_ok, shortcut_cost_evidence = _contains_any(
        encoded,
        ["cost:1", "cost=1", "cost 1", "c:1", "1"],
    )
    checks.append(
        _check_idea(
            "forbidden shortcut idea",
            shortcut_ok,
            shortcut_evidence + shortcut_cost_evidence,
            [] if shortcut_ok else ["red/forbidden shortcut"],
            severity="warning",
        )
    )

    task_ok, task_evidence = _contains_any(encoded, ["solve", "lowest", "route", "unlock", "who unlocks", "goal"])
    checks.append(_check_idea("task idea", task_ok, task_evidence))

    return checks


def _normalized_solver_payload(data: dict[str, Any], raw_response: str) -> dict[str, Any]:
    route = _route_from_solver_payload(data, raw_response)
    cost = _cost_from_solver_payload(data, raw_response)

    carry_ok, carry_evidence = _field_or_text_idea(data, raw_response, ["carry", "have", "access_item"], THING_ALIASES["brass token"])
    item_ok, item_evidence = _field_or_text_idea(data, raw_response, ["item", "target", "target_item"], THING_ALIASES["Orbix"])
    unlocker_ok, unlocker_evidence = _field_or_text_idea(data, raw_response, ["unlocker", "who", "actor"], THING_ALIASES["Mira"])
    sunset_ok, sunset_evidence = _field_or_text_idea(data, raw_response, ["unlock_when", "when", "condition"], THING_ALIASES["sunset"])
    seal_ok, seal_evidence = _field_or_text_idea(data, raw_response, ["seal", "key", "marker"], THING_ALIASES["blue seal"])

    return {
        "route": route,
        "cost": cost,
        "carry_ok": carry_ok,
        "carry_evidence": carry_evidence,
        "item_ok": item_ok,
        "item_evidence": item_evidence,
        "unlocker_ok": unlocker_ok,
        "unlocker_evidence": unlocker_evidence,
        "sunset_ok": sunset_ok,
        "sunset_evidence": sunset_evidence,
        "seal_ok": seal_ok,
        "seal_evidence": seal_evidence,
    }


def _solver_idea_checks(data: dict[str, Any], raw_response: str) -> tuple[list[IdeaCheck], dict[str, Any]]:
    normalized = _normalized_solver_payload(data, raw_response)
    checks: list[IdeaCheck] = []

    route = normalized["route"]
    route_ok = route == EXPECTED_IDEAS["route"]
    checks.append(
        _check_idea(
            "allowed route idea",
            route_ok,
            route,
            [] if route_ok else [f"expected {EXPECTED_IDEAS['route']}, got {route}"],
        )
    )

    cost = normalized["cost"]
    cost_ok = cost == EXPECTED_IDEAS["cost"]
    checks.append(
        _check_idea(
            "total cost idea",
            cost_ok,
            [str(cost)] if cost is not None else [],
            [] if cost_ok else [f"expected 9, got {cost!r}"],
        )
    )

    checks.append(
        _check_idea(
            "carried access token idea",
            bool(normalized["carry_ok"]),
            normalized["carry_evidence"],
            [] if normalized["carry_ok"] else ["brass token"],
        )
    )
    checks.append(
        _check_idea(
            "target item idea",
            bool(normalized["item_ok"]),
            normalized["item_evidence"],
            [] if normalized["item_ok"] else ["Orbix"],
        )
    )
    checks.append(
        _check_idea(
            "unlocker idea",
            bool(normalized["unlocker_ok"]),
            normalized["unlocker_evidence"],
            [] if normalized["unlocker_ok"] else ["Mira"],
        )
    )
    checks.append(
        _check_idea(
            "unlock condition idea",
            bool(normalized["sunset_ok"]),
            normalized["sunset_evidence"],
            [] if normalized["sunset_ok"] else ["sunset"],
        )
    )
    checks.append(
        _check_idea(
            "seal idea",
            bool(normalized["seal_ok"]),
            normalized["seal_evidence"],
            [] if normalized["seal_ok"] else ["blue seal"],
        )
    )

    text = _normalize_text(_flatten_payload(data) + " " + raw_response)
    shortcut_endorsed = (
        "red zone" in text
        and not any(word in text for word in ("avoid", "forbidden", "disallow", "not use", "excluded", "reject"))
    )
    checks.append(
        _check_idea(
            "forbidden shortcut rejected",
            not shortcut_endorsed,
            ["red/forbidden shortcut not endorsed"] if not shortcut_endorsed else [],
            [] if not shortcut_endorsed else ["red zone appears without rejection"],
        )
    )

    return checks, normalized


def _messages_from_checks(prefix: str, checks: list[IdeaCheck]) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []

    for check in checks:
        if check.ok:
            continue
        message = f"{prefix}: {check.name} missing {check.missing}"
        if check.severity == "warning":
            warnings.append(message)
        else:
            failures.append(message)

    return failures, warnings


def write_fixture_corpus(corpus_dir: Path) -> Path:
    path = corpus_dir / AHBE_AI_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n\n".join(
            [
                "# English-Extended Ahbe Grammar",
                ENGLISH_EXTENDED_AHBE_GRAMMAR,
                "# Source English Task",
                SOURCE_PROBLEM_ENGLISH,
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    decoy = corpus_dir / "knowledge" / "rag_smoke_ahbe_ai_finetuned_decoy.txt"
    decoy.write_text(
        "Decoy: a red-zone shortcut looks attractive, but red and forbidden routes must be avoided.\n",
        encoding="utf-8",
    )

    return path


def build_encoder_prompt(grammar: str, problem: str) -> str:
    return "\n\n".join(
        [
            "Compress this English task into Ahbe-English.",
            "Do not solve it.",
            "Preserve the ideas so another model can decode and solve it.",
            "Return only one fenced ```ahbe block.",
            "Use compact ids and aliases if useful.",
            "Do not worry about byte-perfect syntax.",
            "Do not drop places, costs, requirements, carried-token, target-item, unlocker, sunset, or seal ideas.",
            "Remember the semantic distinction:",
            "- brass token = carried access item",
            "- Orbix = target item",
            "- Mira = unlocker and owner",
            "- blue seal = Orbix seal",
            "- sunset = unlock condition",
            "GRAMMAR:",
            grammar,
            "ENGLISH TASK:",
            problem,
        ]
    )


def build_solver_prompt(grammar: str, encoded_ahbe: str) -> str:
    return "\n\n".join(
        [
            "Decode this Ahbe-English idea encoding and solve the original task.",
            "Return JSON only if possible.",
            "Do not explain.",
            "Do not invent routes.",
            "Do not use red/forbidden routes.",
            "Do not treat Orbix as the carried access token.",
            "Semantic roles:",
            "- carried access item: brass token",
            "- target item: Orbix",
            "- unlocker/owner: Mira",
            "- unlock condition: sunset",
            "- target seal: blue seal",
            "Route goal:",
            "- start: Canal Dock",
            "- goal: Vault Atrium",
            "- allowed route must include declared safe connections through Velin Archive and Moon Bridge if no other allowed edge exists",
            "- sum edge costs to compute total route cost",
            "Output JSON keys:",
            "route, route_places, route_edges, cost, carry, item, unlocker, unlock_when, seal, answer",
            "GRAMMAR:",
            grammar,
            "AHBE-ENGLISH IDEAS:",
            encoded_ahbe,
        ]
    )


def run_rag_smoke_ahbe_ai_finetuned(
    *,
    repo_dir: Path,
    output_root: Path | None = None,
    run_id: str | None = None,
    provider: LLMProvider | None = None,
    strict: bool = False,
    verbose: bool = True,
) -> AhbeAiFinetunedReport:
    run_id = run_id or _default_run_id()
    output_dir = (output_root or (repo_dir / "diagnostics_output" / "rag_runs")) / run_id
    output_dir.mkdir(parents=True, exist_ok=False)

    corpus_dir = output_dir / "corpus"
    write_fixture_corpus(corpus_dir)

    retriever = DeterministicRagRetriever(
        RagRetrieverConfig(
            repo_dir=corpus_dir,
            max_context_chars=18_000,
            max_candidates=6,
            max_chunks=4,
        )
    )

    retrieval = retriever.retrieve(
        [
            "Ahbe-English idea grammar route Orbix Mira brass token blue seal sunset red forbidden",
            "Canal Dock Velin Archive Moon Bridge Vault Atrium",
            "idea compression decode solve route task semantic roles",
        ],
        extra_paths=[AHBE_AI_RELATIVE_PATH],
    )

    retrieved_paths = [candidate.path for candidate in retrieval.candidates]
    if AHBE_AI_RELATIVE_PATH not in retrieved_paths:
        raise RuntimeError(f"RAG retrieval did not include fixture: {AHBE_AI_RELATIVE_PATH}")

    provider = provider or _provider_from_env()
    calls: list[AiCallRecord] = []
    warnings: list[str] = []
    failures: list[str] = []

    encoder_prompt = build_encoder_prompt(ENGLISH_EXTENDED_AHBE_GRAMMAR, SOURCE_PROBLEM_ENGLISH)
    encoder_response, encoder_provider, encoder_model = _chat(
        provider,
        system=ENCODER_SYSTEM_PROMPT,
        user=encoder_prompt,
    )
    calls.append(
        AiCallRecord(
            kind="encoder",
            prompt=encoder_prompt,
            response=encoder_response,
            provider=encoder_provider,
            model=encoder_model,
        )
    )

    encoded_ahbe = _extract_fenced_block(encoder_response, "ahbe")
    encoded_checks = _encoded_idea_checks(encoded_ahbe)
    encoded_failures, encoded_warnings = _messages_from_checks("encoded ideas", encoded_checks)
    failures.extend(encoded_failures)
    warnings.extend(encoded_warnings)

    solver_prompt = build_solver_prompt(ENGLISH_EXTENDED_AHBE_GRAMMAR, encoded_ahbe)
    solver_response, solver_provider, solver_model = _chat(
        provider,
        system=SOLVER_SYSTEM_PROMPT,
        user=solver_prompt,
    )
    calls.append(
        AiCallRecord(
            kind="solver",
            prompt=solver_prompt,
            response=solver_response,
            provider=solver_provider,
            model=solver_model,
        )
    )

    solver_json: dict[str, Any] = {}
    try:
        solver_json = _parse_json_object(solver_response)
    except Exception as exc:
        warnings.append(f"Solver did not return parseable JSON; validating raw text instead: {exc}")

    solver_checks, normalized_solver = _solver_idea_checks(solver_json, solver_response)
    solver_failures, solver_warnings = _messages_from_checks("solver ideas", solver_checks)
    failures.extend(solver_failures)
    warnings.extend(solver_warnings)

    if strict:
        failures.extend(warnings)

    (output_dir / "encoded_ahbe_prompt.ahbe").write_text(encoded_ahbe + "\n", encoding="utf-8")
    (output_dir / "solver_raw_response.txt").write_text(solver_response + "\n", encoding="utf-8")
    _write_json(output_dir / "retrieval.json", retrieval.as_dict())
    _write_json(output_dir / "solver_json.json", solver_json)
    _write_json(output_dir / "normalized_solver.json", normalized_solver)
    _write_json(output_dir / "encoded_idea_checks.json", [check.to_dict() for check in encoded_checks])
    _write_json(output_dir / "solver_idea_checks.json", [check.to_dict() for check in solver_checks])
    _write_json(output_dir / "calls.json", [call.to_dict() for call in calls])

    report_path = output_dir / "ahbe_ai_finetuned_rag_smoke_report.json"
    report = AhbeAiFinetunedReport(
        ok=not failures,
        run_id=run_id,
        scenario=SCENARIO,
        output_dir=str(output_dir),
        report_path=str(report_path),
        retrieved_paths=retrieved_paths,
        encoded_ahbe_prompt=encoded_ahbe,
        solver_raw_response=solver_response,
        solver_json=solver_json,
        normalized_solver=normalized_solver,
        encoded_checks=encoded_checks,
        solver_checks=solver_checks,
        calls=calls,
        warnings=warnings,
        failures=failures,
        strict=strict,
    )
    _write_json(report_path, report.to_dict())

    if verbose:
        provider_name, model_name = _provider_summary(provider)
        print("[rag-smoke-ahbe-ai-finetuned] validation report:")
        print(
            _json_dumps(
                {
                    "ok": report.ok,
                    "run_id": report.run_id,
                    "scenario": report.scenario,
                    "provider": provider_name,
                    "model": model_name,
                    "retrieved_paths": report.retrieved_paths,
                    "encoded_chars": len(encoded_ahbe),
                    "normalized_solver": normalized_solver,
                    "solver_json": solver_json,
                    "encoded_checks": [check.to_dict() for check in encoded_checks],
                    "solver_checks": [check.to_dict() for check in solver_checks],
                    "warnings": report.warnings,
                    "failures": report.failures,
                    "report_path": report.report_path,
                }
            )
        )

    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the fine-tuned idea-level AI Ahbe-English RAG smoke test.")
    parser.add_argument(
        "--repo-dir",
        type=Path,
        default=Path.cwd(),
        help="Repository root used for diagnostics output. Defaults to current working directory.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Optional diagnostics output root. Defaults to diagnostics_output/rag_runs.",
    )
    parser.add_argument("--run-id", default=None, help="Optional deterministic run id.")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose smoke diagnostics.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        report = run_rag_smoke_ahbe_ai_finetuned(
            repo_dir=args.repo_dir,
            output_root=args.output_root,
            run_id=args.run_id,
            strict=args.strict,
            verbose=not args.quiet,
        )
    except Exception as exc:
        print(f"rag_smoke_ahbe_ai_finetuned failed: {exc}", file=sys.stderr)
        return 1

    print(f"Scenario: {report.scenario}")
    print(f"Run: {report.run_id}")
    print(f"Output: {report.output_dir}")
    print(f"Report: {report.report_path}")
    print(f"Retrieved paths: {report.retrieved_paths}")
    print(f"Encoded Ahbe prompt chars: {len(report.encoded_ahbe_prompt)}")
    print(f"Normalized solver: {_json_dumps(report.normalized_solver)}")
    print(f"Solver JSON: {_json_dumps(report.solver_json)}")
    print(f"Status: {'passed' if report.ok else 'failed'}")

    if report.warnings:
        print("Warnings:")
        for warning in report.warnings:
            print(f"  - {warning}")

    if report.failures:
        print("Failures:")
        for failure in report.failures:
            print(f"  - {failure}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())