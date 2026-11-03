"""CLI-independent SEH state/registration and Coolify control operations."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from main_computer.slh import SLHControlBackend
from main_computer.slh.coolify import CoolifyHTTP, SEHDeploymentError
from . import identity as st


class SEHControl:
    def __init__(self, root: Path):
        self.root = Path(root)

    def list(self, network: str) -> dict:
        _, binding, doc = st.read(self.root, network)
        canonical = st.public_deployment(doc, network)
        if canonical is not None:
            return {'status': 'READ_ONLY', 'network': network,
                    'generation': binding.binding.generation, 'deployment': canonical}
        records = st.network_doc(doc,network).get('sehs') or {}
        if type(records) is not dict:
            raise st.SEHIdentityError('Malformed legacy SEH registry')
        return {'status': 'READ_ONLY', 'network': network,
                'generation': binding.binding.generation,
                'sehs': [st.public_inspect(doc, network, name) for name in sorted(records)]}

    def migrate_single(self, network: str, expected_generation: int, operation_id: str) -> dict:
        if type(expected_generation) is not int:
            raise st.SEHIdentityError('Migration requires an explicit observed Mother generation')
        result = st.mutate(self.root, network, expected_generation, operation_id,
                           lambda doc, gen, base: st.migrate_one(doc, network))
        _, current, doc = st.read(self.root, network)
        canonical = st.public_deployment(doc, network)
        if canonical is None and st.single_legacy(doc, network) is None:
            return {'status': 'NO_DEPLOYMENT', 'network': network,
                    'generation': current.binding.generation, 'remote_mutation': False}
        if canonical is None:
            raise st.SEHIdentityError('Canonical deployment migration did not persist')
        return {**result, 'network': network, 'deployment': canonical,
                'remote_mutation': False}

    def sources(self, network: str | None = None) -> dict:
        lookup = network or 'mainnet'
        _, state, doc = st.read(self.root, lookup)
        return {'status':'READ_ONLY','generation':state.binding.generation,
                'sources':st.sources(doc, network)}

    def inspect(self, network: str, seh: str | None = None) -> dict:
        listing = self.list(network)
        if 'deployment' in listing:
            if seh:
                raise st.SEHIdentityError('SEH name selection is retired')
            return {'status': 'READ_ONLY', 'network': network,
                    'generation': listing['generation'], 'registered': True,
                    **listing['deployment']}
        legacy = listing.get('sehs', [])
        selected = ([row for row in legacy if row['seh'] == seh] if seh else legacy)
        if len(selected) != 1:
            raise st.SEHIdentityError('Legacy SEH selection missing or ambiguous')
        return {'status':'READ_ONLY', 'generation': listing['generation'], **selected[0]}

    def change(self, command: str, network: str, seh: str, generation: int | None, operation_id: str,
               *, slh_id: str = '', source: str = '', alias: str = '',
               replace_identity: bool = False, changes: dict | None = None, service_uuid: str = '') -> dict:
        def update(doc, old_generation, base_dir):
            if st.deployment_record(doc, network) is not None:
                raise st.SEHIdentityError('Legacy named SEH mutation refused after canonical migration')
            if command=='reserve':
                st.reserve(doc,network,seh,slh_id)
            elif command=='coolify-copy':
                st.copy_identity(doc,network,seh,alias,source,old_generation,base_dir,replace=replace_identity)
            elif command=='coolify-refresh':
                rec=st.seh_doc(doc,network,seh)
                prev=rec['coolify']['identities'].get(alias)
                if not prev:
                    raise st.SEHIdentityError('Identity to refresh does not exist')
                st.copy_identity(doc,network,seh,alias,prev['copied_from'],old_generation,base_dir,replace=True)
            elif command=='coolify-select':
                st.select_identity(doc,network,seh,alias)
            elif command=='configure':
                st.configure(doc,network,seh,changes or {})
            elif command=='record-service':
                record=st.seh_doc(doc,network,seh)
                st.ident(service_uuid,'service_uuid')
                old=record['deployment'].get('service_uuid')
                if old and old!=service_uuid:
                    raise st.SEHIdentityError('Service UUID conflict; refused replacement')
                record['deployment']['service_uuid']=service_uuid
            else:
                raise st.SEHIdentityError('Unsupported state mutation')
        result=st.mutate(self.root,network,generation,operation_id,update)
        return {'seh':seh,'network':network,'operation':command,**result}

    def prepare_git(self, network: str, seh: str, **kwargs) -> dict:
        from .git_deployment import prepare
        return prepare(self.root, network, seh, **kwargs)

    def preflight_git(self, network: str, seh: str, **kwargs) -> dict:
        from .git_deployment import preflight
        return preflight(self.root, network, seh, **kwargs)

    def deploy_git(self, network: str, seh: str, *, operation_id: str, **kwargs) -> dict:
        from .git_deployment import deploy
        return deploy(self.root, network, seh, operation_id=operation_id, **kwargs)

    def status_git(self, network: str, seh: str, **kwargs) -> dict:
        from .git_deployment import status
        return status(self.root, network, seh, **kwargs)

    def bind_slh(self, network: str, seh: str, **kwargs) -> dict:
        from .git_deployment import bind_slh
        return bind_slh(self.root, network, seh, **kwargs)

    def diagnostic_slh_trust(self, network: str, seh: str, **kwargs) -> dict:
        from .git_deployment import diagnostic_slh_trust
        return diagnostic_slh_trust(self.root, network, seh, **kwargs)

    def observed_public_origin(self, network: str, seh: str, **kwargs) -> dict:
        """Read a domain directly from the exact registered Coolify application."""
        from .git_deployment import observed_public_origin
        return observed_public_origin(self.root, network, seh, **kwargs)

    def resolved(self, network: str, seh: str):
        _, state, doc=st.read(self.root,network)
        return st.resolve(doc,network,seh,state.binding.generation)

    def plan(self, network: str, seh: str) -> dict:
        res=self.resolved(network,seh)
        result=SLHControlBackend().seh_plan(res.target)
        return {**result,'seh':seh,'network':network,'identity_name':res.identity_name,
                'private_state_generation':res.generation}

    def preflight(self, network: str, seh: str, *, transport=None) -> dict:
        res=self.resolved(network,seh)
        client=transport or CoolifyHTTP(res.target.coolify_url,res.token)
        result=SLHControlBackend().seh_preflight(res.target,client)
        return {**result,'seh':seh,'network':network,
                'private_state_generation':res.generation}

    def deploy(self, network: str, seh: str, *, generation: int | None = None,
               digest: str, target_id: str, operation_id: str, transport=None) -> dict:
        res=self.resolved(network,seh)
        if generation is not None and generation!=res.generation:
            raise st.SEHIdentityError('Private-state generation changed since approval')
        if res.target.image.endswith('.invalid/main-computer/seh') or res.target.image_digest=='sha256:'+'0'*64:
            raise st.SEHIdentityError('Placeholder image cannot be deployed')
        # Exact backend approvals; no implicit remote mutation.
        client=transport or CoolifyHTTP(res.target.coolify_url,res.token)
        receipt=SLHControlBackend().seh_create(res.target,client,digest=digest,target_id=target_id)
        try:
            committed=self.change('record-service',network,seh,res.generation,operation_id,
                                  service_uuid=receipt['service_uuid'])
        except Exception:
            # A resource may now exist; do not retry POST. Recover by exact UUID.
            raise st.SEHIdentityError('Coolify created resource but identity commit failed; run recover with observed UUID') from None
        return {**receipt,'seh':seh,'network':network,'private_state_generation':committed['generation'],
                'identity_committed':True}

    def start(self, network: str, seh: str, *, generation: int | None = None, digest: str, uuid: str, transport=None) -> dict:
        res=self.resolved(network,seh)
        if generation is not None and res.generation!=generation:
            raise st.SEHIdentityError('Private-state generation changed since approval')
        client=transport or CoolifyHTTP(res.target.coolify_url,res.token)
        receipt=SLHControlBackend().seh_start(res.target,client,digest=digest,service_uuid=uuid)
        return {**receipt,'seh':seh,'network':network,'private_state_generation':res.generation}

    def status(self, network: str, seh: str, *, transport=None) -> dict:
        res=self.resolved(network,seh)
        client=transport or CoolifyHTTP(res.target.coolify_url,res.token)
        try:
            remote=SLHControlBackend().seh_preflight(res.target,client)
            state=remote.get('status','UNKNOWN')
        except SEHDeploymentError:
            state='REQUIRES_RECONCILIATION'
        return {'status':state,'seh':seh,'network':network,
                'service_uuid':res.target.service_uuid,
                'generation':res.generation,'live_seh_verified':False}

    def recover(self, network: str, seh: str, *, generation: int | None = None,
                uuid: str, operation_id: str, transport=None) -> dict:
        res=self.resolved(network,seh)
        if generation is not None and generation!=res.generation:
            raise st.SEHIdentityError('Private-state generation changed since approval')
        st.ident(uuid,'service_uuid')
        if res.target.service_uuid and res.target.service_uuid!=uuid:
            raise st.SEHIdentityError('Existing service UUID differs; recovery refused')
        client=transport or CoolifyHTTP(res.target.coolify_url,res.token)
        target=replace(res.target,service_uuid=uuid)
        check=SLHControlBackend().seh_preflight(target,client)
        if check['status']!='EXISTING_SEH_READ_ONLY':
            raise st.SEHIdentityError('Remote Coolify service ownership not verified')
        return self.change('record-service',network,seh,res.generation,operation_id,service_uuid=uuid)
