"""Reproducible, narrow Patch 13 authoring-accounting audit.

The legacy Task Manager v2 view is the sole comparable handwritten baseline.
Catalog creation, shared engine code, tests, and future apps are NOT claimed as
free savings. Regexes below recognize only the frozen, audited checked-in JS
view; any drift invalidates the audit rather than inventing a favorable count.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

from . import SemanticCatalogError, load_catalog
from .presentation_policy import SCHEMA as POLICY_SCHEMA

ROOT = Path(__file__).resolve().parents[2]
LEGACY = ROOT / 'main_computer/web/applications/scripts/mcel-task-manager-process-contract.js'


def authoring_audit(policy: Mapping[str, Any], *, legacy_path: Path = LEGACY) -> dict[str, Any]:
    text = legacy_path.read_text(encoding='utf-8')
    if not all(x in text for x in ('const processView=Object.freeze({', 'mcel.semantic-collection-spec.v2',
                                  'reconciliation:{generationFrom:"created_at"}',
                                  'global.McelTaskManagerProcessContract')):
        raise SemanticCatalogError('Legacy baseline drifted; audit must be revised')
    match = re.search(r'sourceFields:\[([^\]]+)\]', text, flags=re.S)
    if match is None:
        raise SemanticCatalogError('Legacy source fields not recognized')
    source_fields = re.findall(r'"([\w-]+)"', match.group(1))
    old_keys = re.findall(r'\{key:"([^"\n]+)"', text)
    old = {
        'sourceFieldNames': len(source_fields),
        'fieldDeclarations': len(old_keys),
        'fieldKindAnnotations': len(re.findall(r'\{key:"[^"]+"[^}]*?kind:"', text, re.S)),
        'fieldSourceMappings': len(re.findall(r'\bfrom:"', text)),
        'labels': len(re.findall(r'\blabel:"', text)),
        'tiers': len(re.findall(r'\btier:"', text)),
        'explicitPriorities': len(re.findall(r'\bpriority:', text)),
        'availabilityLinks': len(re.findall(r'availabilityFrom:', text)),
        'generationLinks': len(re.findall(r'generationFrom:', text)),
        'derivedRoleRules': int('derive:{kind:"first-true"' in text),
        'derivedRoleBranches': len(re.findall(r'\{field:"is_(?:current_process|main_computer)"', text)),
        'byteUnitDeclarations': len(re.findall(r'units:"bytes"', text)),
        'disclosureChoices': len(re.findall(r'\b(?:maxChars|compactDisclosure):', text)),
    }
    expected = {
        'sourceFieldNames': 10, 'fieldDeclarations': 7, 'fieldKindAnnotations': 7,
        'fieldSourceMappings': 6, 'labels': 7, 'tiers': 7, 'explicitPriorities': 4,
        'availabilityLinks': 2, 'generationLinks': 1, 'derivedRoleRules': 1,
        'derivedRoleBranches': 2, 'byteUnitDeclarations': 1, 'disclosureChoices': 2,
    }
    if old != expected:
        raise SemanticCatalogError(f'Legacy baseline drifted; audit not certified: {old}')
    if policy.get('schema') != POLICY_SCHEMA or policy.get('observation') != 'task-manager.processes':
        raise SemanticCatalogError('Wrong compatibility policy in authoring audit')
    cat = load_catalog()
    ob = cat['observations']['task-manager.processes']
    native = {p['name']: p for p in ob['properties']}
    derived = {p['name']: p for p in ob.get('derivedProperties', [])}
    if set(policy['fields']) - set(native) - set(derived) or 'role' not in derived:
        raise SemanticCatalogError('Not all view fields are knowledge-derived')
    if not all(name in native and native[name]['type'] for name in ['pid','name','state','command','resident_memory','created_at']):
        raise SemanticCatalogError('Missing native computer-domain meanings')
    if not (native['resident_memory']['availabilityFrom'] == 'memory_availability' and
            native['state']['availabilityFrom'] == 'status_availability' and
            ob['identity']['fields'] == ['pid','created_at'] and
            derived['role']['rule']['op'] == 'first-true'):
        raise SemanticCatalogError('No semantic equivalent for removed legacy declarations')
    current = {
        'fieldSelections': len(policy['fields']),
        'labels': len(policy['labels']),
        'primaryTierExceptions': len(policy['primary']),
        'detailTierExceptions': len(policy['detail']),
        'disclosureChoices': sum(len(opts) for opts in policy['presentation'].values()),
        'displayValueLabels': sum(len(opts) for opts in policy['valueLabels'].values()),
        'sourceFieldNames': 0,
        'fieldKindAnnotations': 0,
        'fieldSourceMappings': 0,
        'availabilityLinks': 0,
        'generationLinks': 0,
        'roleDerivationRules': 0,
        'byteUnitDeclarations': 0,
        'explicitPriorities': 0,
    }
    eliminated = {
        'sourceFieldNames': old['sourceFieldNames'],
        'fieldKindAnnotations': old['fieldKindAnnotations'],
        'fieldSourceMappings': old['fieldSourceMappings'],
        'availabilityLinks': old['availabilityLinks'],
        'generationLinks': old['generationLinks'],
        'derivedRoleRules': old['derivedRoleRules'],
        'derivedRoleBranches': old['derivedRoleBranches'],
        'byteUnitDeclarations': old['byteUnitDeclarations'],
        'explicitPriorities': old['explicitPriorities'],
    }
    return {
        'schema': 'mcel.patch13-authoring-audit.v1',
        'status': 'MEASURED',
        'baseline': 'checked-in Task Manager process v2 contract, no hypothetical other apps',
        'legacy': old,
        'compatibilityOverlay': current,
        'eliminatedFromViewButRepresentedBySharedKnowledge': eliminated,
        'legacyDeclarationInstances': sum(old.values()),
        'overlayDeclarationInstances': sum(current.values()),
        'scopeCaveat': ('Counts are visible view declarations, NOT total system LOC or person-hours. '
                        'Catalog, compiler, shared presentation adapter, tests and data producers '
                        'remain real engineering cost. No File Explorer or Worker authoring savings claimed.'),
        'fullyUnhinted': False,
        'pixelEquivalent': False,
        'identityExtraField': 'mcelIdentity is retained as an extra required primary field in v2',
    }
