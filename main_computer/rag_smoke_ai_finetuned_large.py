from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import math
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


SCENARIO = "rag_smoke_ahbe_ai_fintuned_large"
AHBE_LARGE_RELATIVE_PATH = "knowledge/rag_smoke_ahbe_ai_fintuned_large.md"


ENGLISH_EXTENDED_AHBE_GRAMMAR = """# Ahbe-English Grammar v0.6 Large Problem Mode

Ahbe-English, pronounced ahh-bay, is an idea-compression language.

It is not XML.
It is not byte-perfect.
It is not judged by exact surface syntax.

Ahbe-English preserves ideas in compact form so another model can decode the
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

For route/search problems, preserve these ideas:
- places
- directed connections
- edge costs
- access requirements
- forbidden or avoided route properties
- carried access items
- target item
- owner/unlocker
- unlock condition
- seal/key/marker
- the actual task

Compression economics:
- A local dictionary has overhead.
- A shared dictionary amortizes that overhead across many problems.
- The encoder should use aliases only when repeated ideas make them worthwhile.
"""


PLACES = [
    ("cd", "Canal Dock", "old water gate, public start area"),
    ("va", "Velin Archive", "floor 7 archive, brass-token access"),
    ("mb", "Moon Bridge", "safe bridge across the canal"),
    ("vt", "Vault Atrium", "sealed atrium below the archive"),
    ("gm", "Glass Market", "market of mirrored stalls"),
    ("al", "Amber Lift", "lift operated by amber gears"),
    ("ns", "North Scriptorium", "quiet map room"),
    ("qg", "Quartz Gate", "gate with quartz locks"),
    ("lc", "Lantern Court", "open courtyard"),
    ("es", "Echo Stair", "spiral stair with echo markers"),
    ("sg", "Silver Garden", "garden under silver glass"),
    ("do", "Dawn Observatory", "goal chamber above the city"),
]

SAFE_EDGES = [
    ("cd", "va", 3, [], "stairs"),
    ("va", "mb", 2, ["brass token"], "gantry"),
    ("mb", "vt", 4, [], "lens path"),
    ("vt", "gm", 5, ["blue seal"], "sealed door"),
    ("gm", "al", 3, [], "market aisle"),
    ("al", "ns", 4, ["mirror pass"], "amber lift"),
    ("ns", "qg", 6, [], "scribe tunnel"),
    ("qg", "lc", 2, ["brass token"], "quartz lock"),
    ("lc", "es", 3, [], "lantern walk"),
    ("es", "sg", 5, ["blue seal"], "echo stair"),
    ("sg", "do", 4, [], "silver path"),
]

FORBIDDEN_EDGES = [
    ("cd", "vt", 1, [], "red zone", ["red", "forbidden"]),
    ("va", "gm", 2, [], "collapsed stacks", ["forbidden"]),
    ("mb", "qg", 2, [], "flood gate", ["red", "flooded"]),
    ("al", "sg", 3, [], "broken lift", ["forbidden"]),
    ("qg", "do", 1, [], "red observatory ladder", ["red"]),
    ("gm", "do", 4, [], "flooded arcade", ["flooded", "forbidden"]),
]

EXPECTED_ROUTE_IDS = [place_id for place_id, _name, _detail in PLACES]
EXPECTED_ROUTE_NAMES = [name for _place_id, name, _detail in PLACES]
EXPECTED_COST = sum(edge[2] for edge in SAFE_EDGES)

EXPECTED_IDEAS = {
    "route": EXPECTED_ROUTE_NAMES,
    "cost": EXPECTED_COST,
    "carry": ["brass token", "blue seal", "mirror pass"],
    "item": "Orbix",
    "owner": "Mira",
    "unlocker": "Mira",
    "unlock_when": "sunset",
    "seal": "blue seal",
    "avoid": ["red", "forbidden", "flooded"],
}


ENCODER_SYSTEM_PROMPT = """You compress large English tasks into Ahbe-English.

Ahbe-English is an idea encoding, not byte-perfect XML.

Your job is NOT to solve the task.
Your job is to preserve the ideas needed for another model to solve the task.

Return only one fenced ```ahbe block.

Preserve:
- all important actors
- target items and ownership/unlock relationships
- carried access items
- all places
- the route graph
- edge costs
- edge requirements
- forbidden/avoid tags
- the actual task

Use compact ids, aliases, references, lists, maps, or short English.
A dictionary is useful only when repeated ideas make it pay for itself.
Do not drop an idea merely to save space.
Do not invent facts.
Do not include the final answer.
"""


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
class SavingsReport:
    source_english_chars: int
    encoded_chars: int
    observed_dictionary_chars: int
    encoded_without_dictionary_chars: int
    gross_savings_chars: int
    gross_savings_pct: float
    shared_dictionary_savings_chars: int
    shared_dictionary_savings_pct: float
    one_off_ratio: float
    shared_dictionary_ratio: float
    dictionary_break_even_problem_count: int | None
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AhbeLargeEncoderReport:
    ok: bool
    run_id: str
    scenario: str
    output_dir: str
    report_path: str
    retrieved_paths: list[str]
    source_english: str
    encoded_ahbe_prompt: str
    savings: dict[str, Any]
    idea_score: float
    idea_checks: list[IdeaCheck]
    calls: list[AiCallRecord]
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    strict: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["idea_checks"] = [check.to_dict() for check in self.idea_checks]
        data["calls"] = [call.to_dict() for call in self.calls]
        return data


def _default_run_id() -> str:
    return "rag_ahbe_ai_fintuned_large_" + datetime.now().strftime("%Y%m%d_%H%M%S")


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


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _compact_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize_text(value))


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

    return bool(evidence), sorted(set(evidence))


def _place_aliases(place_id: str, name: str) -> list[str]:
    return [
        name,
        name.replace(" ", ""),
        place_id,
        f"@{place_id}",
        f"${place_id}",
    ] + [part for part in name.split() if len(part) > 3]


def _thing_aliases(*values: str) -> list[str]:
    result: list[str] = []
    for value in values:
        result.extend(
            [
                value,
                value.replace(" ", ""),
                value.lower(),
                f"@{value.lower().replace(' ', '')}",
                f"${value.lower().replace(' ', '')}",
            ]
        )
    return result


def build_large_english_problem() -> str:
    lines: list[str] = []

    lines.append("A large routing and access-control test for Ahbe-English:")
    lines.append("")
    lines.append(
        "Mira is the keeper of Orbix. Orbix is a blue survey tool owned by Mira. "
        "Mira is the only actor who can unlock Orbix after sunset. Orbix uses the blue seal."
    )
    lines.append(
        "The traveler starts at Canal Dock and must reach the Dawn Observatory. "
        "The traveler carries a brass token, a blue seal, and a mirror pass. "
        "Avoid any route tagged red, forbidden, or flooded. Orbix is the target item, "
        "not the carried access token."
    )
    lines.append("")
    lines.append("Known places:")

    for _place_id, name, detail in PLACES:
        lines.append(f"- {name}: {detail}.")

    lines.append("")
    lines.append("Declared directed safe routes:")

    id_to_name = {place_id: name for place_id, name, _detail in PLACES}

    for source, target, cost, requirements, via in SAFE_EDGES:
        req_text = "nothing" if not requirements else ", ".join(requirements)
        lines.append(
            f"- {id_to_name[source]} to {id_to_name[target]} costs {cost}, uses {via}, "
            f"requires {req_text}, and is safe."
        )

    lines.append("")
    lines.append("Tempting routes that must not be used:")

    for source, target, cost, requirements, via, tags in FORBIDDEN_EDGES:
        req_text = "nothing" if not requirements else ", ".join(requirements)
        tag_text = ", ".join(tags)
        lines.append(
            f"- {id_to_name[source]} to {id_to_name[target]} costs {cost}, uses {via}, "
            f"requires {req_text}, and is tagged {tag_text}."
        )

    lines.append("")
    lines.append("No undeclared routes exist. Do not skip intermediate places unless a declared allowed edge says so.")
    lines.append(
        "Solve for the lowest-cost allowed route from Canal Dock to Dawn Observatory, "
        "list the carried access items, identify the target item, and identify who unlocks Orbix, "
        "when it can be unlocked, and which seal it uses."
    )

    return "\n".join(lines)


SOURCE_PROBLEM_ENGLISH = build_large_english_problem()


def build_encoder_prompt(grammar: str, problem: str) -> str:
    return "\n\n".join(
        [
            "Compress this large English task into Ahbe-English.",
            "Do not solve it.",
            "Preserve the ideas so another model can decode and solve it.",
            "Return only one fenced ```ahbe block.",
            "Use compact ids and aliases when they reduce repeated text.",
            "Do not worry about byte-perfect syntax.",
            "Do not drop places, route edges, costs, requirements, carried items, avoid tags, target item, unlocker, sunset, or seal ideas.",
            "The goal of this run is to measure compression savings and dictionary break-even.",
            "GRAMMAR:",
            grammar,
            "ENGLISH TASK:",
            problem,
        ]
    )


def _check_idea(
    name: str,
    text: str,
    aliases: list[str],
    *,
    severity: str = "error",
) -> IdeaCheck:
    ok, evidence = _contains_any(text, aliases)
    return IdeaCheck(
        name=name,
        ok=ok,
        evidence=evidence,
        missing=[] if ok else [" / ".join(aliases[:8])],
        severity=severity,
    )


def _edge_texts(source_name: str, target_name: str, source_id: str, target_id: str) -> list[str]:
    return [
        f"{source_name} to {target_name}",
        f"{source_name}->{target_name}",
        f"{source_name}To{target_name}",
        f"{source_name} {target_name}",
        f"{source_id}->{target_id}",
        f"{source_id} {target_id}",
        f"@{source_id}->@{target_id}",
        f"${source_id}->${target_id}",
        f"{source_id}{target_id}",
    ]


def _idea_checks(encoded: str) -> list[IdeaCheck]:
    checks: list[IdeaCheck] = []
    id_to_name = {place_id: name for place_id, name, _detail in PLACES}

    checks.append(_check_idea("Mira owner/unlocker idea", encoded, _thing_aliases("Mira", "mira", "@mira", "$mira")))
    checks.append(_check_idea("Orbix target item idea", encoded, _thing_aliases("Orbix", "orbix", "@orbix", "$orbix")))
    checks.append(_check_idea("brass token access idea", encoded, _thing_aliases("brass token", "BrassToken", "bt", "$bt")))
    checks.append(_check_idea("blue seal idea", encoded, _thing_aliases("blue seal", "BlueSeal", "bs", "$bs")))
    checks.append(_check_idea("mirror pass idea", encoded, _thing_aliases("mirror pass", "MirrorPass", "mp", "$mp")))
    checks.append(_check_idea("sunset unlock idea", encoded, _thing_aliases("sunset", "after sunset")))
    checks.append(_check_idea("red avoid idea", encoded, _thing_aliases("red", "red zone", "rz", "$rz")))
    checks.append(_check_idea("forbidden avoid idea", encoded, _thing_aliases("forbidden", "avoid forbidden")))
    checks.append(_check_idea("flooded avoid idea", encoded, _thing_aliases("flooded", "avoid flooded")))

    for place_id, name, _detail in PLACES:
        checks.append(_check_idea(f"place idea: {name}", encoded, _place_aliases(place_id, name)))

    for source, target, cost, requirements, _via in SAFE_EDGES:
        source_name = id_to_name[source]
        target_name = id_to_name[target]
        edge_ok, edge_evidence = _contains_any(encoded, _edge_texts(source_name, target_name, source, target))
        cost_ok, cost_evidence = _contains_any(encoded, [f"cost:{cost}", f"cost={cost}", f"cost {cost}", f"c:{cost}", str(cost)])
        req_ok = True
        req_evidence: list[str] = []

        for requirement in requirements:
            ok, evidence = _contains_any(encoded, _thing_aliases(requirement, requirement.replace(" ", "")))
            if not ok:
                req_ok = False
            req_evidence.extend(evidence)

        ok = edge_ok and cost_ok and req_ok
        missing: list[str] = []
        if not edge_ok:
            missing.append(f"{source_name}->{target_name}")
        if not cost_ok:
            missing.append(f"cost {cost}")
        if not req_ok:
            missing.append(f"requirements {requirements}")

        checks.append(
            IdeaCheck(
                name=f"safe edge idea: {source_name} to {target_name} cost {cost}",
                ok=ok,
                evidence=edge_evidence + cost_evidence + req_evidence,
                missing=missing,
                severity="error",
            )
        )

    for source, target, cost, _requirements, _via, tags in FORBIDDEN_EDGES:
        source_name = id_to_name[source]
        target_name = id_to_name[target]
        edge_ok, edge_evidence = _contains_any(encoded, _edge_texts(source_name, target_name, source, target))
        tag_ok = True
        tag_evidence: list[str] = []

        for tag in tags:
            ok, evidence = _contains_any(encoded, _thing_aliases(tag))
            if not ok:
                tag_ok = False
            tag_evidence.extend(evidence)

        cost_ok, cost_evidence = _contains_any(encoded, [f"cost:{cost}", f"cost={cost}", f"cost {cost}", f"c:{cost}", str(cost)])

        checks.append(
            IdeaCheck(
                name=f"forbidden edge idea: {source_name} to {target_name}",
                ok=edge_ok and tag_ok,
                evidence=edge_evidence + tag_evidence + cost_evidence,
                missing=[] if edge_ok and tag_ok else [f"{source_name}->{target_name} tags {tags}"],
                severity="warning",
            )
        )

    task_ok, task_evidence = _contains_any(encoded, ["solve", "lowest", "route", "cost", "Dawn Observatory", "unlock", "who unlocks"])
    checks.append(
        IdeaCheck(
            name="large task idea",
            ok=task_ok,
            evidence=task_evidence,
            missing=[] if task_ok else ["solve lowest-cost route and unlocker task"],
            severity="error",
        )
    )

    return checks


def _idea_score(checks: list[IdeaCheck]) -> float:
    error_checks = [check for check in checks if check.severity == "error"]
    if not error_checks:
        return 1.0
    passed = sum(1 for check in error_checks if check.ok)
    return round(passed / len(error_checks), 4)


def _messages_from_checks(checks: list[IdeaCheck]) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []

    for check in checks:
        if check.ok:
            continue
        message = f"{check.name} missing {check.missing}"
        if check.severity == "warning":
            warnings.append(message)
        else:
            failures.append(message)

    return failures, warnings


def _estimate_dictionary_chars(encoded: str) -> int:
    total = 0

    for line in encoded.splitlines():
        stripped = line.strip()
        if stripped.startswith("!lex") or stripped.startswith("#lex") or stripped.startswith("lex:"):
            total += len(line) + 1

    return total


def _savings_report(source: str, encoded: str) -> SavingsReport:
    source_chars = len(source)
    encoded_chars = len(encoded)
    dictionary_chars = _estimate_dictionary_chars(encoded)
    encoded_without_dictionary = max(0, encoded_chars - dictionary_chars)

    gross_savings = source_chars - encoded_chars
    shared_savings = source_chars - encoded_without_dictionary

    gross_pct = round((gross_savings / source_chars) * 100, 2) if source_chars else 0.0
    shared_pct = round((shared_savings / source_chars) * 100, 2) if source_chars else 0.0

    one_off_ratio = round(encoded_chars / source_chars, 4) if source_chars else 1.0
    shared_ratio = round(encoded_without_dictionary / source_chars, 4) if source_chars else 1.0

    per_problem_savings_with_shared_dict = source_chars - encoded_without_dictionary
    if dictionary_chars > 0 and per_problem_savings_with_shared_dict > 0:
        break_even = max(1, math.ceil(dictionary_chars / per_problem_savings_with_shared_dict))
    elif dictionary_chars == 0:
        break_even = 0
    else:
        break_even = None

    notes = [
        "gross_savings assumes the encoded prompt carries its local dictionary every time.",
        "shared_dictionary_savings assumes the observed dictionary can be reused and not resent per problem.",
        "dictionary_break_even_problem_count is the number of similarly sized problems needed to amortize the observed dictionary.",
    ]

    if dictionary_chars == 0:
        notes.append("No explicit !lex/#lex/lex dictionary was detected, so dictionary break-even is reported as 0.")
    if gross_savings <= 0:
        notes.append("The one-off encoded prompt is not shorter than the English source.")
    if shared_savings > gross_savings:
        notes.append("Moving the dictionary out of the per-problem payload improves savings.")

    return SavingsReport(
        source_english_chars=source_chars,
        encoded_chars=encoded_chars,
        observed_dictionary_chars=dictionary_chars,
        encoded_without_dictionary_chars=encoded_without_dictionary,
        gross_savings_chars=gross_savings,
        gross_savings_pct=gross_pct,
        shared_dictionary_savings_chars=shared_savings,
        shared_dictionary_savings_pct=shared_pct,
        one_off_ratio=one_off_ratio,
        shared_dictionary_ratio=shared_ratio,
        dictionary_break_even_problem_count=break_even,
        notes=notes,
    )


def write_fixture_corpus(corpus_dir: Path) -> Path:
    path = corpus_dir / AHBE_LARGE_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n\n".join(
            [
                "# English-Extended Ahbe Grammar",
                ENGLISH_EXTENDED_AHBE_GRAMMAR,
                "# Large Source English Task",
                SOURCE_PROBLEM_ENGLISH,
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    decoy = corpus_dir / "knowledge" / "rag_smoke_ahbe_ai_fintuned_large_decoy.txt"
    decoy.write_text(
        "Decoy: shorter red, flooded, or forbidden shortcuts exist, but the source task says those must be avoided.\n",
        encoding="utf-8",
    )

    return path


def run_rag_smoke_ahbe_ai_fintuned_large(
    *,
    repo_dir: Path,
    output_root: Path | None = None,
    run_id: str | None = None,
    provider: LLMProvider | None = None,
    min_idea_score: float = 0.80,
    strict: bool = False,
    verbose: bool = True,
) -> AhbeLargeEncoderReport:
    run_id = run_id or _default_run_id()
    output_dir = (output_root or (repo_dir / "diagnostics_output" / "rag_runs")) / run_id
    output_dir.mkdir(parents=True, exist_ok=False)

    corpus_dir = output_dir / "corpus"
    write_fixture_corpus(corpus_dir)

    retriever = DeterministicRagRetriever(
        RagRetrieverConfig(
            repo_dir=corpus_dir,
            max_context_chars=30_000,
            max_candidates=6,
            max_chunks=4,
        )
    )

    retrieval = retriever.retrieve(
        [
            "Ahbe-English large problem compression dictionary break-even route graph",
            "Orbix Mira brass token blue seal mirror pass Dawn Observatory",
            "Canal Dock Velin Archive Moon Bridge Vault Atrium Glass Market Amber Lift",
        ],
        extra_paths=[AHBE_LARGE_RELATIVE_PATH],
    )

    retrieved_paths = [candidate.path for candidate in retrieval.candidates]
    if AHBE_LARGE_RELATIVE_PATH not in retrieved_paths:
        raise RuntimeError(f"RAG retrieval did not include fixture: {AHBE_LARGE_RELATIVE_PATH}")

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
            kind="large_encoder",
            prompt=encoder_prompt,
            response=encoder_response,
            provider=encoder_provider,
            model=encoder_model,
        )
    )

    encoded_ahbe = _extract_fenced_block(encoder_response, "ahbe")
    checks = _idea_checks(encoded_ahbe)
    check_failures, check_warnings = _messages_from_checks(checks)
    warnings.extend(check_warnings)

    score = _idea_score(checks)
    if score < min_idea_score:
        failures.append(f"idea preservation score {score} is below required minimum {min_idea_score}.")
        failures.extend(check_failures)
    else:
        warnings.extend(f"non-fatal idea miss: {message}" for message in check_failures)

    savings = _savings_report(SOURCE_PROBLEM_ENGLISH, encoded_ahbe)

    if savings.gross_savings_chars <= 0:
        warnings.append(
            f"one-off encoding is not smaller: source={savings.source_english_chars}, encoded={savings.encoded_chars}"
        )

    if strict:
        failures.extend(warnings)

    (output_dir / "source_english_problem.txt").write_text(SOURCE_PROBLEM_ENGLISH + "\n", encoding="utf-8")
    (output_dir / "encoded_ahbe_prompt.ahbe").write_text(encoded_ahbe + "\n", encoding="utf-8")
    (output_dir / "encoder_raw_response.txt").write_text(encoder_response + "\n", encoding="utf-8")
    _write_json(output_dir / "retrieval.json", retrieval.as_dict())
    _write_json(output_dir / "savings.json", savings.to_dict())
    _write_json(output_dir / "idea_checks.json", [check.to_dict() for check in checks])
    _write_json(output_dir / "calls.json", [call.to_dict() for call in calls])
    _write_json(
        output_dir / "expected_large_problem.json",
        {
            "route_ids": EXPECTED_ROUTE_IDS,
            "route_names": EXPECTED_ROUTE_NAMES,
            "expected_cost": EXPECTED_COST,
            "expected_ideas": EXPECTED_IDEAS,
        },
    )

    report_path = output_dir / "ahbe_ai_fintuned_large_report.json"
    report = AhbeLargeEncoderReport(
        ok=not failures,
        run_id=run_id,
        scenario=SCENARIO,
        output_dir=str(output_dir),
        report_path=str(report_path),
        retrieved_paths=retrieved_paths,
        source_english=SOURCE_PROBLEM_ENGLISH,
        encoded_ahbe_prompt=encoded_ahbe,
        savings=savings.to_dict(),
        idea_score=score,
        idea_checks=checks,
        calls=calls,
        warnings=warnings,
        failures=failures,
        strict=strict,
    )
    _write_json(report_path, report.to_dict())

    if verbose:
        provider_name, model_name = _provider_summary(provider)
        print("[rag-smoke-ahbe-ai-fintuned-large] validation report:")
        print(
            _json_dumps(
                {
                    "ok": report.ok,
                    "run_id": report.run_id,
                    "scenario": report.scenario,
                    "provider": provider_name,
                    "model": model_name,
                    "retrieved_paths": report.retrieved_paths,
                    "idea_score": report.idea_score,
                    "savings": report.savings,
                    "encoded_chars": len(encoded_ahbe),
                    "warnings": report.warnings,
                    "failures": report.failures,
                    "report_path": report.report_path,
                }
            )
        )

    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the large Ahbe-English encoder savings smoke test.")
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
    parser.add_argument(
        "--min-idea-score",
        type=float,
        default=0.80,
        help="Minimum required idea preservation score for error-severity checks.",
    )
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose smoke diagnostics.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        report = run_rag_smoke_ahbe_ai_fintuned_large(
            repo_dir=args.repo_dir,
            output_root=args.output_root,
            run_id=args.run_id,
            min_idea_score=args.min_idea_score,
            strict=args.strict,
            verbose=not args.quiet,
        )
    except Exception as exc:
        print(f"rag_smoke_ahbe_ai_fintuned_large failed: {exc}", file=sys.stderr)
        return 1

    print(f"Scenario: {report.scenario}")
    print(f"Run: {report.run_id}")
    print(f"Output: {report.output_dir}")
    print(f"Report: {report.report_path}")
    print(f"Retrieved paths: {report.retrieved_paths}")
    print(f"Idea score: {report.idea_score}")
    print(f"Savings: {_json_dumps(report.savings)}")
    print(f"Encoded Ahbe prompt chars: {len(report.encoded_ahbe_prompt)}")
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