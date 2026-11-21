"""Hash-guarded removal of obsolete Code Editor layout policy/selector artifacts.

Run after the new deterministic main-path patch is installed:
  python -m tools.mcel_retire_legacy_layout_artifacts --check
  python -m tools.mcel_retire_legacy_layout_artifacts --execute

Files with unexpected hashes are deliberately never removed.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OBSOLETE = {
    'mcel_apps/code-editor/code-override.json': 'a550fae6b802d207276f106a81fcb5ef917fdaa4edeaef57b83c7252f9414f7c',
    'mcel_apps/code-editor/code-override.js': 'e4a0f028eb35a9bac8e262ad9e6a70e70467502ec086066db6612b401f60f69f',
    'main_computer/mcel_knowledge/semantic_template_classifier.py': 'ce07d7f9392e599b2764e5a1ebed63f7009ebc9793437f639a229a9832774bc7',
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    from main_computer.mcel_semantic_other_apps_host import compile_semantic_app
    from main_computer.mcel_knowledge.semantic_template_pipeline import plan_semantic_workspace
    from main_computer.mcel_knowledge.workspace_plan import plan_workspace
    from tools.mcel_semantic_argument_resolver_smoke import run
    # No old policy is read by this path; this is tested before deletion.
    ir = compile_semantic_app('code-editor', ROOT)
    binding = plan_semantic_workspace(ir)['semanticTemplate']
    if (binding['authority'] != 'canonical-ir-and-deterministic-graph-match' or
        binding['arguments']['grouping'] != 'inferred'):
        raise SystemExit('REFUSED: new direct matcher is not the active pipeline')
    if run(ROOT)['status'] != 'PASS_POLICY_FREE_GRAPH_MATCH':
        raise SystemExit('REFUSED: deterministic argument smoke failed')
    files=[]
    for name, expected in OBSOLETE.items():
        file = ROOT/name
        if not file.exists():
            print('ALREADY_ABSENT', name)
            continue
        actual = hashlib.sha256(file.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit('REFUSED: modified file will not be deleted: '+name+' sha256='+actual)
        files.append(file)
        print('VERIFIED', name)
    if not args.execute:
        print('DRY_RUN', len(files), 'verified obsolete files; add --execute to delete')
        return 0
    saved=[(file,file.read_bytes()) for file in files]
    try:
        for file in files:
            file.unlink()
            print('DELETED', file.relative_to(ROOT))
        # Prove deletion did not change binding and does not break real route.
        ir2 = compile_semantic_app('code-editor', ROOT)
        result=plan_semantic_workspace(ir2)
        if result['semanticTemplate'] != binding:
            raise ValueError('Template binding changed after cleanup')
        from main_computer.mcel_semantic_other_apps_host import render_semantic_page
        page=render_semantic_page('code-editor', ROOT, promoted=True)
        if 'mcel.semantic-template-binding.v1' not in page:
            raise ValueError('Code Editor route no longer emits semantic template')
    except Exception:
        for path, contents in saved:
            path.write_bytes(contents)
        print('ROLLED_BACK cleanup because validation failed')
        raise
    print('MCEL_SEMANTIC_LAYOUT_CLEANUP PASS removed='+str(len(files)))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
