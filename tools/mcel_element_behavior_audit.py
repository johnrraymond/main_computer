#!/usr/bin/env python3
"""Read-only trace of registered MCEL elements to implementation and test candidates.

A textual reference is a navigation hint, never runtime proof. Element definitions
are largely declarative; one library renderer may implement several elements.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SOURCE = Path('main_computer/web/applications/scripts')
DEFINITION = SOURCE / 'mcel-elements-core.js'
TABBED = {
    'element.toolkit.tabbed-workspace': ('workspace-and-exclusive-panels', 'renderer-mount'),
    'element.toolkit.tab-list': ('tablist-keyboard-navigation', 'renderer-mount'),
    'element.toolkit.tab': ('tab-activation-and-aria', 'renderer-mount'),
    'element.toolkit.tab-panel': ('panel-association-and-preservation', 'renderer-mount'),
    'element.toolkit.tab-controller': ('typed-selection-effect-refusal', 'renderer-mount'),
}
TABBED_RUNTIME = [
    (SOURCE / 'mcel-tab-workspace.js').as_posix(),
    (SOURCE / 'mcel-view-transition.js').as_posix(),
]
CORE_TOOLKIT = {
    **{x: ('core-allowed-children-at-serializer', 'main_computer/web/applications/scripts/mcel-element-registry.js')
       for x in ('element.core.app','element.core.region','element.core.panel',
                 'element.core.toolbar','element.core.workflow','element.core.collection')},
    **{x: ('toolkit-public-contract-evaluation', 'main_computer/web/applications/scripts/mcel-toolkit-core.js')
       for x in ('element.toolkit.foundation-token','element.toolkit.collection-view',
                 'element.toolkit.contract-pattern','element.toolkit.view-resolver')},
}
FILE_BASKET = {
    id_: ('file-basket-consumer-bounded-model-controller', implementation)
    for id_, implementation in {
        'element.resource.file-basket-model': (SOURCE / 'mcel-file-basket-model.js').as_posix(),
        'element.resource.file-boundary': (SOURCE / 'mcel-file-basket-model.js').as_posix(),
        'element.resource.directory-tree': (SOURCE / 'mcel-file-basket-model.js').as_posix(),
        'element.resource.tree-selection-model': (SOURCE / 'mcel-file-basket-controller.js').as_posix(),
        'element.toolkit.selection-controller': (SOURCE / 'mcel-file-basket-controller.js').as_posix(),
        'element.toolkit.bulk-selector': (SOURCE / 'mcel-file-basket-controller.js').as_posix(),
    }.items()
}
FILE_BASKET_TESTS = ['tests/test_mcel_file_basket_runtime_probe.py']
CORE_TOOLKIT_TESTS = ['tests/test_mcel_core_toolkit_runtime_probe.py']
TABBED_TESTS = [
    'tests/test_mcel_notebook_projection.py',
    'tests/test_mcel_view_transition_composition.py',
]


def audit(repo: Path, definitions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Trace every registry ID; report unknown/missing evidence instead of guessing."""
    file = repo / DEFINITION
    source = file.read_text(encoding='utf-8') if file.is_file() else ''
    source_lines = source.splitlines()
    # Exact quoted definitions, not substring matches (tab vs tab-list).
    declarations: dict[str, list[int]] = {}
    pattern = re.compile(r'\bdef\(\s*(["\'])(element\.[\w.-]+)\1')
    for index, line in enumerate(source_lines, 1):
        match = pattern.search(line)
        if match:
            declarations.setdefault(match.group(2), []).append(index)
    # Candidate test references are exact quoted IDs, excluding this harness's
    # own tests and giant catalog checks whose mention is not behavioral proof.
    tests = []
    test_dir = repo / 'tests'
    if test_dir.is_dir():
        tests = sorted(test_dir.glob('test_mcel_*.py'))
    test_sources = {p: p.read_text(encoding='utf-8', errors='replace') for p in tests}
    rows: dict[str, dict[str, Any]] = {}
    for definition in definitions:
        id_ = definition['id']
        locations = declarations.get(id_, [])
        exact = re.compile(r'(?<![\w.-])' + re.escape(id_) + r'(?![\w.-])')
        references = [p.relative_to(repo).as_posix() for p, content in test_sources.items()
                      if exact.search(content)]
        if id_ in CORE_TOOLKIT:
            for test in CORE_TOOLKIT_TESTS:
                if (repo / test).is_file() and test not in references:
                    references.append(test)
        if id_ in FILE_BASKET:
            for test in FILE_BASKET_TESTS:
                if (repo / test).is_file() and test not in references:
                    references.append(test)
        if id_ in TABBED:
            for test in TABBED_TESTS:
                if (repo / test).is_file() and test not in references:
                    references.append(test)
        # The shared renderer is a *candidate*: the per-ID link is established
        # by the bounded DOM witness separately, not by this file inventory.
        candidates = [p for p in TABBED_RUNTIME if (repo / p).is_file()] if id_ in TABBED else []
        if id_ in CORE_TOOLKIT and (repo / CORE_TOOLKIT[id_][1]).is_file():
            candidates.append(CORE_TOOLKIT[id_][1])
        if id_ in FILE_BASKET:
            for file in ((SOURCE / 'mcel-file-basket-model.js').as_posix(),
                         (SOURCE / 'mcel-file-basket-controller.js').as_posix()):
                if (repo / file).is_file():
                    candidates.append(file)
        state = definition.get('stateModel') or {}
        interaction = definition.get('interactionModel') or {}
        fixtures = definition.get('proofFixtures') or []
        obligations = []
        if definition.get('renderer'):
            obligations.append('runtime-rendering-and-presentation')
        if state:
            obligations.append('state-valid-invalid-and-persistence')
        if interaction:
            obligations.append('interaction-transition-and-boundaries')
        if definition.get('accessibility'):
            obligations.append('accessibility-and-keyboard')
        if definition.get('layoutLaws'):
            obligations.append('multi-viewport-layout-and-overflow')
        if definition.get('riskPolicy', {}).get('risk') not in (None, 'none', 'safe'):
            obligations.append('forbidden-consequential-effects')
        if fixtures:
            obligations.append('declared-proof-fixtures')
        rows[id_] = {
            'id': id_,
            'definitionLocation': {'path': DEFINITION.as_posix(), 'lines': locations},
            'definitionLocated': len(locations) == 1,
            'registryKind': definition.get('kind'),
            'rendererDescriptor': definition.get('renderer') or {},
            'stateModelDeclared': bool(state),
            'interactionModelDeclared': bool(interaction),
            'declaredProofFixtures': fixtures,
            'existingTestCandidates': sorted(set(references)),
            'targetedBehaviorTestCandidates': [x for x in TABBED_TESTS if (repo / x).is_file()]
                if id_ in TABBED else
                ([x for x in CORE_TOOLKIT_TESTS if (repo / x).is_file()]
                 if id_ in CORE_TOOLKIT else
                 [x for x in FILE_BASKET_TESTS if (repo / x).is_file()]
                 if id_ in FILE_BASKET else []),
            'runtimeImplementationCandidates': candidates,
            'candidateWitness': TABBED.get(id_, [None])[0] if id_ in TABBED else
                (CORE_TOOLKIT[id_][0] if id_ in CORE_TOOLKIT else
                 FILE_BASKET[id_][0] if id_ in FILE_BASKET else None),
            'outstandingContractDimensions': obligations,
            'auditAssessment': 'definition-and-candidates-only',
            'implementationExistence': 'UNKNOWN',
            'fullBehaviorProvenByStaticAudit': False,
        }
    return rows
