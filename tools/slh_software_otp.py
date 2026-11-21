"""Software authenticator CLI for SLH admins; no private P-256 key leaves LMS.

Pair authenticates a selected P-256 identity through pinned TLS, binds the
locally generated TOTP secret to that SLH, and never prints the pairing secret.
Show reveals only the current time-limited code by explicit operator request.
"""
from __future__ import annotations

import argparse
import sys
import time

from main_computer.slh.authenticator_store import LocalIdentityStore, IdentityStoreError
from main_computer.mcel_slh_network_client import NetworkSlhClient, NetworkConnectionError


def main(argv=None):
    p=argparse.ArgumentParser(description='SLH software authenticator (TOTP)')
    sub=p.add_subparsers(dest='command',required=True)
    pair=sub.add_parser('pair',help='Pair a local identity with a pinned SLH')
    pair.add_argument('--identity',required=True)
    pair.add_argument('--address',required=True)
    pair.add_argument('--port',type=int,required=True)
    pair.add_argument('--confirm-pin',help='Previously verified TLS SHA-256; never trust an unverified pin')
    reset=sub.add_parser('reset',help='Revoke paired factor on SLH and remove local OTP file')
    reset.add_argument('--identity',required=True)
    reset.add_argument('--address',required=True)
    reset.add_argument('--port',type=int,required=True)
    reset.add_argument('--confirm-pin')
    reset.add_argument('--confirm-reset',action='store_true',help='Explicitly authorize resetting this factor')
    show=sub.add_parser('show',help='Reveal current one-time code for one SLH')
    show.add_argument('--identity',required=True)
    show.add_argument('--service-fingerprint',required=True,help='SLH public signing-key fingerprint from pair')
    a=p.parse_args(argv)
    store=LocalIdentityStore()
    if a.command=='show':
        current=store.current_software_otp(a.identity,a.service_fingerprint)
        print('Current code:',current['code'])
        print('Expires in:',max(0,current['expires_at']-int(time.time())),'seconds')
        return 0
    if a.command=='reset' and not a.confirm_reset:
        raise ValueError('Add --confirm-reset to authorize revocation and local removal')
    client=NetworkSlhClient()
    connection=client.connect(a.address,a.port,a.identity,confirm_pin=a.confirm_pin)
    if connection['pairingRequired']:
        print('Untrusted SLH TLS certificate; independently verify fingerprint before pairing.')
        print('TLS SHA-256:',connection['tlsSha256'])
        return 2
    ticket=connection['sessionTicket']
    try:
        if connection['role']!='admin':
            raise ValueError('Selected P-256 identity is not an administrator on this SLH')
        peer=client.admin_request(ticket,'GET','/v1/org/identity')
        fingerprint=peer.get('service_fingerprint','')
        if not isinstance(fingerprint,str) or len(fingerprint)!=64:
            raise ValueError('Remote SLH did not disclose its canonical service fingerprint')
        if a.command=='reset':
            result=client.admin_request(ticket,'POST',
                '/v1/admin/authenticator/software-otp/revoke',{})
            if result.get('status')!='REVOKED':
                raise ValueError('SLH did not confirm revocation')
            store.reset_software_otp(a.identity,fingerprint)
            print('Software authenticator revoked and local factor removed.')
            return 0
        registered=client.admin_request(ticket,'GET','/v1/admin/authenticator/software-otp')
        if registered.get('active') is True:
            if registered.get('device_id')!=a.identity:
                raise ValueError('SLH factor belongs to another administrator identity')
            store.current_software_otp(a.identity,fingerprint)
            print('Software authenticator already paired with the verified SLH.')
            print('SLH service fingerprint:',fingerprint)
            return 0
        try:
            # Existing on-disk factors are reused after a transient failure.
            local=store.current_software_otp(a.identity,fingerprint)
        except IdentityStoreError as exc:
            if exc.code!='SOFTWARE_OTP_NOT_ACTIVATED':
                raise
            store.activate_software_otp(a.identity,fingerprint)
            local=store.current_software_otp(a.identity,fingerprint)
        # Secret is read by this trusted local management process, not exposed
        # in the browser, console, or a remotely retrievable API response.
        secret=store.software_otp_pairing_secret(a.identity,fingerprint)
        result=client.admin_request(ticket,'POST','/v1/admin/authenticator/software-otp',{
            'secret':secret,'code':local['code'],'service_fingerprint':fingerprint})
        if result.get('status')!='REGISTERED':
            raise ValueError('SLH did not confirm factor registration')
        print('Software authenticator paired with the verified SLH.')
        print('SLH service fingerprint:',fingerprint)
        print('The pairing proof consumed the current time step; enrollment starts with the next code rotation.')
        print('Reveal later: python -m tools.slh_software_otp show --identity',a.identity,
              '--service-fingerprint',fingerprint)
        return 0
    finally:
        client.disconnect(ticket)


if __name__=='__main__':
    try:
        sys.exit(main())
    except (IdentityStoreError,NetworkConnectionError,ValueError) as exc:
        print('Software authenticator refused:',getattr(exc,'code',str(exc)),file=sys.stderr)
        sys.exit(1)
