"""Patch 2: guarded one-record Mother migration. No network or key mutation."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from seh_mutate_harness import _parser, run, HarnessError
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import prepare_private_state_bootstrap, install_verified_private_state
from tools.seh_control import identity as st
from tools.seh_control.commands import SEHControl

APP = 'itr2ynyxjs4iy6dsj4fvekmn'
TOKEN = '1|COOLIFY-PRIVATE-SECRET-DO-NOT-PRINT'
SOURCE = 'networks.mainnet.coolify.controllers.coolify-c'


def populated(tmp_path: Path, *, copies=1, sehs=1, selected=True):
    root = tmp_path / 'runtime' / 'state'
    controller = {'url': 'http://159.203.184.182:8000/', 'api_token': TOKEN,
                  'project_uuid': 'vbja82onq0xpuzy6mwi3v11r',
                  'server_uuid': 'xsuyj7sxd0kymhhyz2ctcwl4'}
    entry = {'controller': deepcopy(controller), 'copied_from': SOURCE,
             'source_generation': 29, 'copied_at': '2026-11-02T00:00:00Z'}
    record = {'schema': 'main-computer.seh-identity.v3', 'slh_id': 'my-slh',
              'coolify': {'active_identity': 'primary' if selected else None,
                          'identities': {'primary': entry}},
              'deployment': {'application_uuid': APP, 'enabled': False,
                             'git': {'mode': 'coolify-git', 'enabled': True,
                                     'application_name': 'mc-seh-company-primary',
                                     'environment_uuid': 'xgkuuosdiwxacky2btgsoz0h',
                                     'project_uuid': 'vbja82onq0xpuzy6mwi3v11r',
                                     'server_uuid': 'xsuyj7sxd0kymhhyz2ctcwl4'}}}
    if copies > 1:
        record['coolify']['identities']['other'] = deepcopy(entry)
    if sehs > 1:
        records = {'company-primary': record, 'another': deepcopy(record)}
    else:
        records = {'company-primary': record}
    original = {'kind': 'main_computer.mother.private_state.v1', 'schema_version': 1,
                'networks': {'mainnet': {'coolify': {'controllers': {'coolify-c': deepcopy(controller)}},
                                        'foundationdb': {'must_remain': True},
                                        'hubs': {'keep': 1}, 'sehs': records}}}
    paths = MotherPaths(runtime_state_root=root).resolve_private_state_paths()
    op = OperationIdentity('seh-p2-fixture', 'seh-p2-fixture', 'mainnet', 'MOTHER-OP-IDENTITY-ROTATION')
    closure = prepare_private_state_bootstrap(paths, original,
            updated_at='2026-11-02T00:00:00Z', updated_by_action_id='fixture', operation=op)
    install_verified_private_state(paths, closure, None, operation=op)
    return root, original


def test_single_migration_preserves_exact_application_placement_and_controller(tmp_path):
    root, original = populated(tmp_path)
    control = SEHControl(root)
    before = control.list('mainnet')
    assert before['generation'] == 1 and len(before['sehs']) == 1
    result = control.migrate_single('mainnet', 1, 'migrate-first')
    assert result['status'] == 'COMMITTED' and result['generation'] == 2
    assert result['remote_mutation'] is False
    _, verified, doc = st.read(root, 'mainnet')
    n = doc['networks']['mainnet']
    assert verified.binding.generation == 2
    assert 'sehs' not in n
    assert n['hubs'] == original['networks']['mainnet']['hubs']
    assert n['foundationdb'] == original['networks']['mainnet']['foundationdb']
    assert n['coolify'] == original['networks']['mainnet']['coolify']
    successor = n['seh_deployment']
    old = original['networks']['mainnet']['sehs']['company-primary']
    expected_deployment = deepcopy(old['deployment'])
    expected_deployment['git'].pop('application_name')
    assert successor['deployment'] == expected_deployment
    assert successor['coolify']['controller'] == old['coolify']['identities']['primary']['controller']
    assert successor['coolify']['source'] == SOURCE
    assert successor['coolify']['source_generation'] == 29
    assert set(successor) == {'schema', 'coolify', 'deployment'}
    for disallowed in ('company-primary', 'primary', 'my-slh', 'organization_id', 'active_identity', 'identities'):
        # Existing Coolify application display name is intentionally preserved
        # until the separate remote-metadata patch; no identity selectors survive.
        assert disallowed not in json.dumps(successor)
    report = control.inspect('mainnet')
    assert report['deployment']['application_uuid'] == APP
    assert 'seh' not in report and 'active_identity' not in report
    assert TOKEN not in json.dumps(report)
    assert TOKEN not in json.dumps(result)
    assert control.migrate_single('mainnet', 2, 'migrate-again')['status'] == 'UNCHANGED'
    assert st.read(root, 'mainnet')[1].binding.generation == 2


def test_stale_generation_refused_before_mutation(tmp_path):
    root, _ = populated(tmp_path)
    with pytest.raises(st.SEHIdentityError, match='generation conflict'):
        SEHControl(root).migrate_single('mainnet', 31, 'stale')
    assert st.read(root, 'mainnet')[1].binding.generation == 1


@pytest.mark.parametrize('copies,sehs,selected', [(2,1,True), (1,2,True), (1,1,False)])
def test_ambiguous_or_missing_selection_refuses_without_changing_state(tmp_path, copies, sehs, selected):
    root, _ = populated(tmp_path, copies=copies, sehs=sehs, selected=selected)
    before = st.read(root, 'mainnet')[1].binding
    with pytest.raises(st.SEHIdentityError):
        SEHControl(root).migrate_single('mainnet', 1, 'must-refuse')
    assert st.read(root, 'mainnet')[1].binding == before


def test_migrate_cli_demands_explicit_generation_and_both_approvals(tmp_path):
    root, _ = populated(tmp_path)
    def argv(*opts):
        return _parser().parse_args(['migrate-state', '--runtime-state-root', str(root), *opts])
    for options in ((), ('--execute-mutations',), ('--yes-i-know-this-mutates-seh',),
                    ('--execute-mutations', '--yes-i-know-this-mutates-seh')):
        with pytest.raises(HarnessError):
            run(argv(*options))
    with pytest.raises(HarnessError):
        run(argv('--approve-generation', '1', '--seh', 'company-primary',
                 '--execute-mutations', '--yes-i-know-this-mutates-seh'))
    result = run(argv('--approve-generation', '1', '--execute-mutations',
                      '--yes-i-know-this-mutates-seh'))
    assert result['status'] == 'COMMITTED'
    assert result['deployment']['deployment']['application_uuid'] == APP
    assert st.read(root, 'mainnet')[1].binding.generation == 2


def test_remote_legacy_mutations_refused_after_migration(tmp_path):
    root, _ = populated(tmp_path)
    c=SEHControl(root)
    c.migrate_single('mainnet', 1, 'migrate')
    with pytest.raises(st.SEHIdentityError, match='after canonical migration'):
        c.change('reserve','mainnet','new',2,'create')
    with pytest.raises(st.SEHIdentityError, match='retired'):
        c.resolved('mainnet','company-primary')
    assert st.read(root,'mainnet')[1].binding.generation == 2


def test_legacy_v1_without_org_id_migrates_without_creating_identifier(tmp_path):
    root, _ = populated(tmp_path)
    st.mutate(root, 'mainnet', 1, 'test-legacy-version',
              lambda doc, gen, base: doc['networks']['mainnet']['sehs']['company-primary'].update(schema='main-computer.seh-identity.v1'))
    result = SEHControl(root).migrate_single('mainnet', 2, 'migrate-legacy-v1')
    assert result['status'] == 'COMMITTED'
    assert st.read(root, 'mainnet')[2]['networks']['mainnet']['seh_deployment']['deployment']['application_uuid'] == APP


def test_copied_controller_origin_change_refuses_preserving_legacy(tmp_path):
    root, _ = populated(tmp_path)
    def alter(doc, gen, base):
        doc['networks']['mainnet']['coolify']['controllers']['coolify-c']['url'] = 'https://unrelated.example.org/'
    st.mutate(root, 'mainnet', 1, 'change-origin', alter)
    before = st.read(root, 'mainnet')[1].binding
    with pytest.raises(st.SEHIdentityError, match='origin differs'):
        SEHControl(root).migrate_single('mainnet', 2, 'reject-origin-change')
    assert st.read(root, 'mainnet')[1].binding == before


def test_legacy_missing_application_uuid_refuses_migration(tmp_path):
    root, _ = populated(tmp_path)
    def alter(doc, gen, base):
        del doc['networks']['mainnet']['sehs']['company-primary']['deployment']['application_uuid']
    st.mutate(root, 'mainnet', 1, 'remove-uuid', alter)
    before = st.read(root, 'mainnet')[1].binding
    with pytest.raises(st.SEHIdentityError, match='application UUID missing'):
        SEHControl(root).migrate_single('mainnet', 2, 'reject-missing-uuid')
    assert st.read(root, 'mainnet')[1].binding == before


def test_mixed_old_and_new_state_blocks_all_automatic_selection(tmp_path):
    root, _ = populated(tmp_path)
    c = SEHControl(root)
    c.migrate_single('mainnet', 1, 'migration')
    def introduce_conflict(doc, gen, base):
        doc['networks']['mainnet']['sehs'] = {'arbitrary': {'this': 'must-refuse'}}
    st.mutate(root, 'mainnet', 2, 'conflicting-legacy-entry', introduce_conflict)
    with pytest.raises(st.SEHIdentityError, match='coexist'):
        c.list('mainnet')
    with pytest.raises(st.SEHIdentityError, match='coexist'):
        c.migrate_single('mainnet', 3, 'do-not-overwrite')
    assert st.read(root, 'mainnet')[1].binding.generation == 3


def test_cli_can_inspect_canonical_without_any_alias_or_remote_lookup(tmp_path):
    root, _ = populated(tmp_path)
    SEHControl(root).migrate_single('mainnet', 1, 'migration')
    before = st.read(root, 'mainnet')[1].binding
    result = run(_parser().parse_args(['inspect', '--runtime-state-root', str(root)]))
    assert result['status'] == 'READ_ONLY' and result['registered'] is True
    assert result['deployment']['application_uuid'] == APP
    assert result['coolify']['source'] == SOURCE
    assert 'seh' not in result and 'primary' not in json.dumps(result)
    assert TOKEN not in json.dumps(result)
    diagnostic = run(_parser().parse_args(['diagnostic', '--runtime-state-root', str(root)]))
    assert diagnostic['status'] == 'REFUSED' and diagnostic['mutation_performed'] is False
    assert st.read(root, 'mainnet')[1].binding == before
