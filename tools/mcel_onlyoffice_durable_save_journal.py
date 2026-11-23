"""Disposable ONLYOFFICE ordered-save journal candidate; NOT a production endpoint.

State is persistent within the disposable workbook root and shared by independent
processes under the *same* OS file lock as the revision fence. A crash after a
file replace but before the journal update leaves a hash mismatch; every next
operation fails closed. Such a mismatch requires explicit recovery, never a
silent reset or acceptance of an old callback.

Issue only after a separate, verified MC session/document authorization. The
journal does NOT authorize callers, network fetches, or JWTs by itself.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time

try:
    from tools.mcel_onlyoffice_shadow_revision import (ShadowRevisionFence,
        RevisionConflict, RevisionInvalid, sha256_bytes)
except ModuleNotFoundError as exc:
    if exc.name not in ('tools','tools.mcel_onlyoffice_shadow_revision'):
        raise
    # Disposable Docker fixture mounts these two candidate files read-only
    # at /lab and deliberately does not mount the repository package tree.
    from mcel_onlyoffice_shadow_revision import (ShadowRevisionFence,
        RevisionConflict, RevisionInvalid, sha256_bytes)


class JournalConflict(RevisionConflict):
    """Replay, stale callback, or unverifiable journal/workbook state."""


class DurableSaveJournal:
    def __init__(self, root, secret, *, clock=time.time):
        self.fence = ShadowRevisionFence(Path(root))
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise RevisionInvalid('disposable journal requires an external 256-bit secret')
        self.secret = secret
        self.clock = clock

    def _state_path(self, relative):
        digest = hashlib.sha256(relative.encode()).hexdigest()
        return self.fence.lock_root / (digest + '.sequence.json')

    def _initialization_marker(self, relative):
        return self._state_path(relative).with_suffix('.initialized')

    def _load(self, relative):
        file = self._state_path(relative)
        marker = self._initialization_marker(relative)
        if file.is_symlink() or marker.is_symlink():
            raise RevisionInvalid('symlink journal or initialization marker denied')
        if not file.exists():
            if marker.exists():
                raise JournalConflict('journal was lost after initialization; refuse silent reissue')
            return None
        if not marker.exists():
            raise JournalConflict('journal missing initialization marker; fail closed')
        if file.is_symlink():
            raise RevisionInvalid('symlink journal denied')
        try:
            state = json.loads(file.read_text(encoding='utf8'))
        except (OSError, ValueError) as exc:
            raise JournalConflict('unreadable journal; deny writes until recovered') from exc
        if (not isinstance(state, dict) or set(state) !=
            {'v','relative','mode','document_key','sha','next','accepted','pending'} or
            state['v'] != 1 or state['relative'] != relative or
            not isinstance(state['mode'], str) or not state['mode'] or
            not isinstance(state['document_key'], str) or not state['document_key'] or
            not isinstance(state['sha'], str) or not re.fullmatch('[0-9a-f]{64}',state['sha']) or
            type(state['next']) is not int or state['next'] < 0 or
            type(state['accepted']) is not int or not 0 <= state['accepted'] <= state['next'] or
            not isinstance(state['pending'], dict)):
            raise JournalConflict('invalid journal schema; fail closed')
        return state

    def _write(self, relative, state):
        file = self._state_path(relative)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf8', delete=False,
                dir=file.parent, prefix='.'+file.name+'.', suffix='.tmp') as out:
                tmp = out.name
                json.dump(state,out,sort_keys=True,separators=(',',':'))
                out.flush(); os.fsync(out.fileno())
            os.replace(tmp,file)
            tmp = None
        finally:
            if tmp is not None:
                try:os.unlink(tmp)
                except FileNotFoundError:pass

    def _checked(self, path, mode, key, *, initialize):
        relative=self.fence._relative(path)
        state=self._load(relative)
        current=sha256_bytes(Path(path).read_bytes())
        if state is None:
            if not initialize:
                raise JournalConflict('journal missing: refused to reconstruct from callback')
            state={'v':1,'relative':relative,'mode':mode,'document_key':key,
                   'sha':current,'next':0,'accepted':0,'pending':{}}
            # Create a persistent marker *before* writing initial state.
            # A crash between these operations then fails closed instead of
            # silently issuing a new sequence from zero.
            marker=self._initialization_marker(relative)
            try:
                with marker.open('x', encoding='utf8') as handle:
                    handle.write('MC-ONLYOFFICE-DURABLE-JOURNAL-V1\n')
                    handle.flush();os.fsync(handle.fileno())
            except FileExistsError as exc:
                raise JournalConflict('journal initialization already attempted') from exc
            self._write(relative,state)
        if state['mode']!=mode or state['document_key']!=key:
            raise JournalConflict('mode or editor-key mismatch')
        if state['sha']!=current:
            raise JournalConflict('journal and workbook disagree: crash recovery required')
        return relative,state

    def _sign(self, fields):
        raw=json.dumps(fields,sort_keys=True,separators=(',',':')).encode()
        payload=base64.urlsafe_b64encode(raw).decode().rstrip('=')
        mac=hmac.new(self.secret,b'mc-oo-durable-seq-v1|'+payload.encode(),hashlib.sha256).hexdigest()
        return payload+'.'+mac

    def _parse(self, token):
        if not isinstance(token,str) or len(token)>800 or token.count('.')!=1:
            raise JournalConflict('invalid callback command identifier')
        payload,tag=token.split('.')
        expected=hmac.new(self.secret,b'mc-oo-durable-seq-v1|'+payload.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(tag,expected):
            raise JournalConflict('tampered callback command identifier')
        try:
            fields=json.loads(base64.urlsafe_b64decode(payload+'==='))
        except (ValueError,UnicodeError) as exc:
            raise JournalConflict('invalid callback payload') from exc
        if (not isinstance(fields,dict) or set(fields)!=
            {'v','mode','relative','key','seq','nonce','exp'} or fields['v']!=1 or
            type(fields['seq']) is not int or fields['seq']<1 or
            type(fields['exp']) is not int or fields['exp']<int(self.clock()) or
            fields['exp']>int(self.clock())+300 or
            not isinstance(fields['nonce'],str) or len(fields['nonce'])!=32):
            raise JournalConflict('invalid callback fields or expiration')
        return fields

    def register(self, path, *, mode, key):
        """Explicit creation step, called ONLY after MC authorizes document creation.

        An ordinary save-command issuance may never manufacture a missing
        journal after its deletion or a filesystem crash.
        """
        relative=self.fence._relative(path)
        with self.fence._document_lock(relative):
            if self._load(relative) is not None:
                raise JournalConflict('document journal is already registered')
            self._checked(path,mode,key,initialize=True)

    def issue(self, path, *, mode, key, lifetime=300):
        if not isinstance(mode,str) or not mode or not isinstance(key,str) or not key:
            raise RevisionInvalid('mode and document key required')
        if not isinstance(lifetime,int) or not 1<=lifetime<=300:
            raise RevisionInvalid('save command lifetime out of range')
        relative=self.fence._relative(path)
        with self.fence._document_lock(relative):
            relative,state=self._checked(path,mode,key,initialize=False)
            seq=state['next']+1
            fields={'v':1,'mode':mode,'relative':relative,'key':key,
                    'seq':seq,'nonce':secrets.token_hex(16),
                    'exp':int(self.clock())+lifetime}
            token=self._sign(fields)
            state['next']=seq
            now=int(self.clock())
            state['pending']={k:v for k,v in state['pending'].items()
                if type(v) is dict and v.get('exp',0)>=now and int(k)>state['accepted']}
            if len(state['pending'])>=256:
                raise JournalConflict('too many pending save commands; fail closed')
            state['pending'][str(seq)]={'nonce':fields['nonce'],'exp':fields['exp']}
            self._write(relative,state)
            return token

    def commit(self, path, *, mode, key, userdata, data, after_replace=None):
        """Called only AFTER callback JWT, key, mode, URL and source are verified.

        `after_replace` is a test-only crash-injection hook; if raised, the
        workbook changes but the journal intentionally stays old and fails
        closed on all subsequent uses until explicitly reconciled.
        """
        fields=self._parse(userdata)
        relative=self.fence._relative(path)
        if fields['mode']!=mode or fields['key']!=key or fields['relative']!=relative:
            raise JournalConflict('callback audience mismatch')
        self.fence._validate_workbook(data,self.fence.max_bytes)
        with self.fence._document_lock(relative):
            if self.fence._relative(path)!=relative:
                raise RevisionInvalid('document path changed during commit')
            relative,state=self._checked(path,mode,key,initialize=False)
            seq=fields['seq']
            pending=state['pending'].get(str(seq))
            if (seq<=state['accepted'] or seq>state['next'] or
                not isinstance(pending,dict) or
                pending!={'nonce':fields['nonce'],'exp':fields['exp']}):
                raise JournalConflict('replayed, stale or unissued save command')
            incoming=sha256_bytes(data)
            if incoming==state['sha']:
                raise JournalConflict('unchanged callback cannot consume a save command')
            tmp=None
            try:
                with tempfile.NamedTemporaryFile('wb',delete=False,dir=Path(path).parent,
                    prefix='.'+Path(path).name+'.',suffix='.tmp') as out:
                    tmp=out.name;out.write(data);out.flush();os.fsync(out.fileno())
                os.replace(tmp,path)
                tmp=None
            finally:
                if tmp is not None:
                    try:os.unlink(tmp)
                    except FileNotFoundError:pass
            if after_replace is not None:after_replace()
            state['sha']=incoming
            state['accepted']=seq
            state['pending']={k:v for k,v in state['pending'].items() if int(k)>seq}
            self._write(relative,state)
            return incoming

    def current(self, path, *, mode):
        """Read the current verified journal without accepting an untrusted key.

        Used only by a previously authenticated, internal document-launch
        operation; not an authorization primitive for incoming HTTP requests.
        """
        relative = self.fence._relative(path)
        with self.fence._document_lock(relative):
            state = self._load(relative)
            if state is None:
                return None
            _, checked = self._checked(path, mode, state['document_key'], initialize=False)
            return {'key': checked['document_key'], 'accepted': checked['accepted'],
                    'next': checked['next'], 'sha': checked['sha']}

    def rotate_key(self, path, *, mode, old_key, new_key):
        """Bind a new editor session after an authenticated document relaunch.

        Monotonic sequence history is retained; earlier editor keys are never
        automatically accepted again. This is not a final-close protocol.
        """
        if not isinstance(new_key, str) or not new_key or len(new_key) > 128:
            raise RevisionInvalid('valid new editor key required')
        relative = self.fence._relative(path)
        with self.fence._document_lock(relative):
            _, state = self._checked(path, mode, old_key, initialize=False)
            if old_key == new_key:
                raise JournalConflict('editor key was not rotated')
            state['document_key'] = new_key
            state['pending'] = {}
            self._write(relative, state)
            return {'old_key': old_key, 'new_key': new_key, 'accepted': state['accepted']}

    def inspect(self, path, *, mode, key):
        relative=self.fence._relative(path)
        with self.fence._document_lock(relative):
            _,state=self._checked(path,mode,key,initialize=False)
            return {'accepted':state['accepted'],'next':state['next'],
                    'sha':state['sha'],'pending':len(state['pending'])}
