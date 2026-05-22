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


SCENARIO = "rag_smoke_ahbe_ai_ideas"
AHBE_AI_RELATIVE_PATH = "knowledge/rag_smoke_ahbe_ai_ideas.md"


ENGLISH_EXTENDED_AHBE_GRAMMAR = """# Ahbe-English Grammar v0.4

Ahbe-English, pronounced ahh-bay, is an idea-compression language.

It is not meant to be byte-perfect XML.
It is meant to preserve ideas in a compact, decodable form.

The encoder may use any compact Ahbe-English shape as long as the important
ideas survive. Common forms include:

~doc id=<name>
!lex short="expanded idea"
#actor id=<id> ...
#item id=<id> ...
#place id=<id> ...
#edge id=<id> ...
#problem id=<id> ...

Common idea markers:
- #kind introduces an idea record.
- key:value attaches an idea to a record.
- @x points to another idea.
- $x expands through !lex.
- [a,b,c] is a compact list.
- {k:v} is a compact map.
- free English is allowed when it helps preserve meaning.

For route problems:
- Preserve places.
- Preserve directed connections.
- Preserve costs.
- Preserve access requirements.
- Preserve forbidden or avoided route properties.
- Preserve the actual question.

When decoding:
- Decode aliases and references by meaning.
- Do not require exact spelling if the idea is clear.
- Do not invent routes.
- Do not use forbidden/avoided routes.
- The carried access item is not necessarily the target item.
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
    "cost": 9,
    "carry": "brass token",
    "item": "Orbix",
    "unlocker": "Mira",
    "unlock_when": "sunset",
    "seal": "blue seal",
    "avoid": ["red", "forbidden"],
}


ENCODER_SYSTEM_PROMPT = """You compress English tasks into Ahbe-English.

Ahbe-English is an idea encoding, not a byte-perfect syntax.
Do not solve the task.
Preserve the ideas needed for another model to solve the task.

Return only one fenced ```ahbe block.

Your encoding should preserve:
- people and roles
- important objects
- ownership/unlock relationships
- places
- route graph ideas
- edge costs
- access requirements
- avoid/forbidden constraints
- the actual user task

You may use compact ids, aliases, references, lists, maps, or short English.
Do not remove an idea merely to save space.
Do not invent facts.
Do not include the final answer.
"""


SOLVER_SYSTEM_PROMPT = """You decode Ahbe-English and solve the original task.

Ahbe-English is an idea encoding. Decode the ideas, not just the literal bytes.

Use only the Ahbe-English prompt and grammar.
Do not invent routes.
Do not use forbidden or avoided routes.
Do not treat the target item as the carried access token.
Sum route costs when costs are present.
Return the answer to the original task.

Return JSON only if possible:
{
  "route": ["places or ids in order"],
  "cost": 0,
  "carry": ["items carried"],
  "item": "target item",
  "unlocker": "who unlocks it",
  "unlock_when": "when",
  "seal": "seal",
  "answer": "short answer"
}

If a field is best represented with compact Ahbe symbols such as cd, @mira, or $bs,
that is acceptable as long as the idea is clear.
"""


ALIAS_TO_TEXT = {
    "cd": "Canal Dock",
    "@cd": "Canal Dock",
    "$cd": "Canal Dock",
    "canaldock": "Canal Dock",
    "canal": "Canal Dock",
    "dock": "Canal Dock",

    "va": "Velin Archive",
    "@va": "Velin Archive",
    "$va": "Velin Archive",
    "velinarchive": "Velin Archive",
    "velin": "Velin Archive",
    "archive": "Velin Archive",

    "mb": "Moon Bridge",
    "@mb": "Moon Bridge",
    "$mb": "Moon Bridge",
    "moonbridge": "Moon Bridge",
    "moon": "Moon Bridge",
    "bridge": "Moon Bridge",

    "vt": "Vault Atrium",
    "@vt": "Vault Atrium",
    "$vt": "Vault Atrium",
    "vaultatrium": "Vault Atrium",
    "vault": "Vault Atrium",
    "atrium": "Vault Atrium",

    "bt": "brass token",
    "@bt": "brass token",
    "$bt": "brass token",
    "brasstoken": "brass token",
    "token": "brass token",

    "bs": "blue seal",
    "@bs": "blue seal",
    "$bs": "blue seal",
    "blueseal": "blue seal",

    "mira": "Mira",
    "@mira": "Mira",
    "$mira": "Mira",

    "orbix": "Orbix",
    "@orbix": "Orbix",
    "$orbix": "Orbix",
}


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AhbeAiSmokeReport:
    ok: bool
    run_id: str
    scenario: str
    output_dir: str
    report_path: str
    retrieved_paths: list[str]
    encoded_ahbe_prompt: str
    solver_raw_response: str
    solver_json: dict[str, Any]
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
    return "rag_ahbe_ai_ideas_" + datetime.now().strftime("%Y%m%d_%H%M%S")


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


def _canonical_text(value: Any) -> str:
    raw = str(value or "").strip()
    compact = _compact_text(raw)
    lower = raw.lower()
    return ALIAS_TO_TEXT.get(raw, ALIAS_TO_TEXT.get(lower, ALIAS_TO_TEXT.get(compact, raw)))


def _flatten_payload(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{k} {_flatten_payload(v)}" for k, v in value.items())
    if isinstance(value, list):
        return " ".join(_flatten_payload(item) for item in value)
    return str(value or "")


def _contains_idea(text: str, options: list[str]) -> tuple[bool, list[str]]:
    lower = _normalize_text(text)
    compact = _compact_text(text)
    evidence: list[str] = []

    for option in options:
        opt_lower = _normalize_text(option)
        opt_compact = _compact_text(option)

        if not opt_lower:
            continue

        if opt_lower in lower or opt_compact in compact:
            evidence.append(option)
            continue

        canonical = _canonical_text(option)
        canonical_lower = _normalize_text(canonical)
        canonical_compact = _compact_text(canonical)

        if canonical_lower in lower or canonical_compact in compact:
            evidence.append(option)
            continue

        if len(opt_compact) <= 4:
            pattern = rf"(?<![a-z0-9])[@$]?{re.escape(opt_compact)}(?![a-z0-9])"
            if re.search(pattern, lower):
                evidence.append(option)

    return bool(evidence), evidence


def _idea_check(name: str, text: str, groups: list[list[str]]) -> IdeaCheck:
    evidence: list[str] = []
    missing: list[str] = []

    for group in groups:
        ok, hits = _contains_idea(text, group)
        if ok:
            evidence.extend(hits)
        else:
            missing.append(" / ".join(group))

    return IdeaCheck(name=name, ok=not missing, evidence=evidence, missing=missing)


def _encoded_idea_checks(encoded: str) -> list[IdeaCheck]:
    return [
        _idea_check("person and role", encoded, [["Mira", "mira", "@mira"]]),
        _idea_check("target item", encoded, [["Orbix", "orbix", "@orbix"]]),
        _idea_check(
            "route places",
            encoded,
            [
                ["Canal Dock", "CanalDock", "cd", "@cd", "$cd"],
                ["Velin Archive", "VelinArchive", "va", "@va", "$va"],
                ["Moon Bridge", "MoonBridge", "mb", "@mb", "$mb"],
                ["Vault Atrium", "VaultAtrium", "vt", "@vt", "$vt"],
            ],
        ),
        _idea_check(
            "route costs",
            encoded,
            [
                ["cost:3", "cost=3", "cost 3", "c:3", "3"],
                ["cost:2", "cost=2", "cost 2", "c:2", "2"],
                ["cost:4", "cost=4", "cost 4", "c:4", "4"],
                ["cost:1", "cost=1", "cost 1", "c:1", "1"],
            ],
        ),
        _idea_check("access token", encoded, [["brass token", "brassToken", "bt", "$bt"]]),
        _idea_check("avoid constraints", encoded, [["red", "red zone", "rz"], ["forbidden", "avoid"]]),
        _idea_check("question/task", encoded, [["solve", "lowest", "route", "goal"], ["unlock", "who unlocks"]]),
    ]


def _route_from_solver_payload(data: dict[str, Any], raw_response: str) -> list[str]:
    route_value = data.get("route")
    if isinstance(route_value, list):
        return [_canonical_text(item) for item in route_value]

    text = _flatten_payload(data) + " " + raw_response
    route: list[str] = []
    for canonical, aliases in [
        ("Canal Dock", ["Canal Dock", "CanalDock", "cd", "@cd", "$cd"]),
        ("Velin Archive", ["Velin Archive", "VelinArchive", "va", "@va", "$va"]),
        ("Moon Bridge", ["Moon Bridge", "MoonBridge", "mb", "@mb", "$mb"]),
        ("Vault Atrium", ["Vault Atrium", "VaultAtrium", "vt", "@vt", "$vt"]),
    ]:
        ok, _hits = _contains_idea(text, aliases)
        if ok:
            route.append(canonical)
    return route


def _cost_from_solver_payload(data: dict[str, Any], raw_response: str) -> int | None:
    value = data.get("cost")
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


def _solver_idea_checks(data: dict[str, Any], raw_response: str) -> list[IdeaCheck]:
    text = _flatten_payload(data) + " " + raw_response

    route = _route_from_solver_payload(data, raw_response)
    route_ok = route == EXPECTED_IDEAS["route"]

    cost = _cost_from_solver_payload(data, raw_response)
    cost_ok = cost == EXPECTED_IDEAS["cost"]

    checks = [
        IdeaCheck(
            name="allowed route idea",
            ok=route_ok,
            evidence=route,
            missing=[] if route_ok else [f"expected route idea {EXPECTED_IDEAS['route']}, got {route}"],
        ),
        IdeaCheck(
            name="total cost idea",
            ok=cost_ok,
            evidence=[str(cost)] if cost is not None else [],
            missing=[] if cost_ok else [f"expected cost 9, got {cost!r}"],
        ),
        _idea_check("carried access item", text, [["brass token", "brassToken", "bt", "$bt"]]),
        _idea_check("target item", text, [["Orbix", "orbix", "@orbix"]]),
        _idea_check("unlocker and condition", text, [["Mira", "mira", "@mira"], ["sunset"]]),
        _idea_check("seal idea", text, [["blue seal", "blueSeal", "bs", "$bs"]]),
    ]

    red_zone_ok = True
    red_zone_missing: list[str] = []
    lower = _normalize_text(text)
    if "red zone" in lower and not any(word in lower for word in ("avoid", "forbidden", "disallow", "not use", "excluded")):
        red_zone_ok = False
        red_zone_missing.append("red zone appears without being rejected")

    checks.append(
        IdeaCheck(
            name="forbidden shortcut rejected",
            ok=red_zone_ok,
            evidence=["red/forbidden not endorsed"] if red_zone_ok else [],
            missing=red_zone_missing,
        )
    )

    return checks


def _failures_from_checks(prefix: str, checks: list[IdeaCheck]) -> list[str]:
    failures: list[str] = []
    for check in checks:
        if not check.ok:
            failures.append(f"{prefix}: {check.name} missing {check.missing}")
    return failures


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

    decoy = corpus_dir / "knowledge" / "rag_smoke_ahbe_ai_ideas_decoy.txt"
    decoy.write_text(
        "Decoy: a red-zone shortcut looks attractive, but the task says red and forbidden routes must be avoided.\n",
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
            "Do not drop places, costs, requirements, or forbidden-route ideas.",
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
            "The carried access token is the brass token.",
            "Sum edge costs to compute total route cost.",
            "GRAMMAR:",
            grammar,
            "AHBE-ENGLISH IDEAS:",
            encoded_ahbe,
        ]
    )


def run_rag_smoke_ahbe_ai(
    *,
    repo_dir: Path,
    output_root: Path | None = None,
    run_id: str | None = None,
    provider: LLMProvider | None = None,
    strict: bool = False,
    verbose: bool = True,
) -> AhbeAiSmokeReport:
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
            "Ahbe-English idea grammar route Orbix Mira brass token red forbidden",
            "Canal Dock Velin Archive Moon Bridge Vault Atrium",
            "idea compression decode solve route task",
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
    failures.extend(_failures_from_checks("encoded ideas", encoded_checks))

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

    solver_checks = _solver_idea_checks(solver_json, solver_response)
    failures.extend(_failures_from_checks("solver ideas", solver_checks))

    if strict and warnings:
        failures.extend(warnings)

    (output_dir / "encoded_ahbe_prompt.ahbe").write_text(encoded_ahbe + "\n", encoding="utf-8")
    (output_dir / "solver_raw_response.txt").write_text(solver_response + "\n", encoding="utf-8")
    _write_json(output_dir / "retrieval.json", retrieval.as_dict())
    _write_json(output_dir / "solver_json.json", solver_json)
    _write_json(output_dir / "encoded_idea_checks.json", [check.to_dict() for check in encoded_checks])
    _write_json(output_dir / "solver_idea_checks.json", [check.to_dict() for check in solver_checks])
    _write_json(output_dir / "calls.json", [call.to_dict() for call in calls])

    report_path = output_dir / "ahbe_ai_idea_rag_smoke_report.json"
    report = AhbeAiSmokeReport(
        ok=not failures,
        run_id=run_id,
        scenario=SCENARIO,
        output_dir=str(output_dir),
        report_path=str(report_path),
        retrieved_paths=retrieved_paths,
        encoded_ahbe_prompt=encoded_ahbe,
        solver_raw_response=solver_response,
        solver_json=solver_json,
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
        print("[rag-smoke-ahbe-ai-ideas] validation report:")
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
                    "encoded_checks": [check.to_dict() for check in encoded_checks],
                    "solver_json": solver_json,
                    "solver_checks": [check.to_dict() for check in solver_checks],
                    "warnings": report.warnings,
                    "failures": report.failures,
                    "report_path": report.report_path,
                }
            )
        )

    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the idea-level AI Ahbe-English RAG smoke test.")
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
        report = run_rag_smoke_ahbe_ai(
            repo_dir=args.repo_dir,
            output_root=args.output_root,
            run_id=args.run_id,
            strict=args.strict,
            verbose=not args.quiet,
        )
    except Exception as exc:
        print(f"rag_smoke_ahbe_ai failed: {exc}", file=sys.stderr)
        return 1

    print(f"Scenario: {report.scenario}")
    print(f"Run: {report.run_id}")
    print(f"Output: {report.output_dir}")
    print(f"Report: {report.report_path}")
    print(f"Retrieved paths: {report.retrieved_paths}")
    print(f"Encoded Ahbe prompt chars: {len(report.encoded_ahbe_prompt)}")
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