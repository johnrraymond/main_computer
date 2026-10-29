"""Existing Worker live-session client adapted to SLH's walletless Known Device mode."""
from __future__ import annotations

from pathlib import Path
from main_computer.viewport_routes_energy import _WorkerHubLiveSessionClient
from main_computer.slh.authenticator import sign_object
from main_computer.slh.managed_gateway import MANAGED_PATH
from tools.slh_authenticator_cli import load_identity


class ManagedSLHWorkerClient(_WorkerHubLiveSessionClient):
    endpoint_path = MANAGED_PATH

    def __init__(self, *, device_id:str, identity_dir:Path, tls_fingerprint:str, **kwargs):
        if len(tls_fingerprint)!=64 or any(c not in '0123456789abcdef' for c in tls_fingerprint.lower()):
            raise ValueError('Managed SLH requires a SHA-256 pinned TLS certificate fingerprint')
        if not kwargs.get('hub_url','').startswith('https://'):
            raise ValueError('Managed SLH requires TLS')
        self.managed_slh_fingerprint=tls_fingerprint.lower()
        self.device_id=device_id
        self.identity_dir=Path(identity_dir)
        self.identity_key,self.public_key=load_identity(self.identity_dir,device_id)
        super().__init__(**kwargs)
        # Do not transmit local filesystem paths or transport configuration.
        self.auth_message.pop('managed_slh', None)
        self.fingerprint+='|slh:'+self.managed_slh_fingerprint+'|device:'+device_id

    def _recv_json(self):
        frame=super()._recv_json()
        if frame.get('type')=='slh.auth.challenge':
            challenge=frame.get('challenge')
            if not isinstance(challenge,dict) or challenge.get('device_id')!=self.device_id:
                raise RuntimeError('SLH challenge does not match this managed device')
            self._send_json({'type':'slh.auth.proof','device_id':self.device_id,
                             'signature':sign_object(self.identity_key,challenge)})
            return super()._recv_json()
        return frame
