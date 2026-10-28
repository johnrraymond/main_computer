"""Patch 13: bounded semantic binding language and source-evidence normalization.

Read-only utilities. Never invent a revision, timestamp, completeness, or authority.
Source spelling remains in ``raw``; normalized instants are additional evidence.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Any, Mapping

TIME_FORMATS = frozenset({'iso8601', 'cim-dmtf', 'dotnet-json', 'unix-seconds', 'unix-milliseconds'})
ABSENT_VALUE = object()
_CIM = re.compile(r'^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\.(\d{6})([+-])(\d{3})$')
_DOTNET = re.compile(r'^/Date\((-?\d+)([+-]\d{4})?\)/$')


def normalize_timestamp(raw: Any, source_format: str) -> str:
    """UTC microsecond ISO instant, only from an *explicit* producer format.

    Ambiguous or timezone-less values are rejected, not interpreted using host
    timezone. POSIX epochs require explicit seconds vs milliseconds.
    """
    if source_format not in TIME_FORMATS:
        raise ValueError('unsupported timestamp format')
    try:
        if source_format == 'iso8601':
            if not isinstance(raw, str) or not raw.strip() or raw.strip() != raw:
                raise ValueError('ISO timestamp must be text')
            dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if dt.tzinfo is None or dt.utcoffset() is None:
                raise ValueError('timezone missing')
        elif source_format == 'cim-dmtf':
            if not isinstance(raw, str):
                raise ValueError('CIM timestamp must be text')
            match = _CIM.fullmatch(raw)
            if not match:
                raise ValueError('not a CIM DMTF timestamp')
            year, month, day, hour, minute, second, micro, sign, offset = match.groups()
            offset_min = int(offset)
            if offset_min > 840:
                raise ValueError('invalid CIM offset')
            tz = timezone(timedelta(minutes=offset_min if sign == '+' else -offset_min))
            dt = datetime(int(year),int(month),int(day),int(hour),int(minute),int(second),int(micro),tzinfo=tz)
        elif source_format == 'dotnet-json':
            if not isinstance(raw, str):
                raise ValueError('JSON date must be text')
            match = _DOTNET.fullmatch(raw)
            if not match:
                raise ValueError('invalid .NET JSON date')
            # The first number is a UTC Unix millisecond epoch, regardless of
            # the optional display-offset suffix in this legacy notation.
            dt = datetime(1970,1,1,tzinfo=timezone.utc) + timedelta(microseconds=int(match[1]) * 1000)
        else:
            if type(raw) not in (int, float, str) or isinstance(raw, str) and not re.fullmatch(r'-?\d+(?:\.\d+)?', raw):
                raise ValueError('invalid epoch value')
            if isinstance(raw, float) and not math.isfinite(raw):
                raise ValueError('non-finite epoch')
            num = Decimal(str(raw))
            if source_format == 'unix-milliseconds':
                num /= Decimal(1000)
            micros = int((num * Decimal(1000000)).to_integral_value())
            dt = datetime(1970,1,1,tzinfo=timezone.utc) + timedelta(microseconds=micros)
        return dt.astimezone(timezone.utc).isoformat(timespec='microseconds')
    except (OverflowError, OSError, ValueError, TypeError, InvalidOperation) as exc:
        raise ValueError('invalid timestamp for declared format') from exc


def normalize_declared_timestamp(raw: Any, allowed: Any) -> tuple[str, str]:
    """Resolve an explicitly enumerated set of producer formats (no guessing)."""
    formats = [allowed] if isinstance(allowed,str) else allowed
    if not isinstance(formats,list) or not formats or any(x not in TIME_FORMATS for x in formats):
        raise ValueError('invalid declared source formats')
    hits=[]
    for source_format in formats:
        try:
            hits.append((normalize_timestamp(raw,source_format),source_format))
        except ValueError:
            continue
    if not hits or len({value for value,_ in hits}) != 1:
        raise ValueError('invalid or ambiguous timestamp for declared formats')
    return hits[0]


def format_spec(spec: Any) -> dict[str, Any]:
    """Validate a source binding: dotted path or {path, format}.

    Timestamp formats belong to producer bindings, never visual styles.
    """
    if isinstance(spec, str):
        return {'path': spec}
    if not isinstance(spec, Mapping) or set(spec) - {'path', 'format', 'formats'} or 'path' not in spec or ('format' in spec and 'formats' in spec):
        raise ValueError('source binding requires path and optional format')
    result = dict(spec)
    if 'format' in result and (not isinstance(result['format'], str) or result['format'] not in TIME_FORMATS):
        raise ValueError('unsupported source timestamp format')
    if 'formats' in result:
        formats = result['formats']
        if not isinstance(formats,list) or not formats or len(formats) > 5 or len(set(formats)) != len(formats) or any(not isinstance(f,str) or f not in TIME_FORMATS for f in formats):
            raise ValueError('unsupported source timestamp format list')
    return result


def validate_envelope_spec(envelope: Any, *, checked_path) -> dict[str, Any]:
    """Validate bounded producer observation metadata; no callable expressions."""
    if not isinstance(envelope, Mapping) or set(envelope) - {'producer', 'observedAt', 'revision', 'scope', 'completeness'}:
        raise ValueError('unsupported observation envelope bindings')
    out: dict[str, Any] = {}
    producer = envelope.get('producer')
    if producer is not None:
        if not isinstance(producer, str) or not producer.strip():
            raise ValueError('producer must be a nonempty identifier')
        out['producer'] = producer
    observed_at = envelope.get('observedAt')
    if observed_at is not None:
        parsed = format_spec(observed_at)
        if 'format' not in parsed and 'formats' not in parsed:
            raise ValueError('observedAt requires explicit timestamp format')
        checked_path(parsed['path']);out['observedAt'] = parsed
    revision = envelope.get('revision')
    if revision is not None:
        if not isinstance(revision, Mapping) or set(revision) != {'path', 'domain'}:
            raise ValueError('revision requires path and domain')
        checked_path(revision['path'])
        if revision['domain'] not in ('producer','publication'):
            raise ValueError('revision domain must be producer or publication')
        out['revision'] = dict(revision)
    scope = envelope.get('scope')
    if scope is not None:
        if not isinstance(scope, Mapping) or set(scope) - {'kind','parameters'} or scope.get('kind') not in ('bounded-top-n','filtered','enumeration','unknown'):
            raise ValueError('unsupported observation scope')
        params = scope.get('parameters', {})
        if not isinstance(params, Mapping):
            raise ValueError('scope parameters must be a mapping')
        if any(not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z][\w-]*', key) for key in params):
            raise ValueError('invalid scope parameter')
        for name, path in params.items():
            checked_path(path)
        out['scope'] = {'kind':scope['kind'], 'parameters':dict(params)}
    completeness = envelope.get('completeness')
    if completeness is not None:
        if completeness not in ('unknown','bounded','producer-reported-complete'):
            raise ValueError('unknown completeness declaration')
        if completeness == 'producer-reported-complete':
            raise ValueError('completeness requires producer proof, unsupported in foundation cut')
        out['completeness'] = completeness
    return out


def envelope_for_source(snapshot: Mapping[str, Any], spec: Mapping[str, Any] | None, *, lookup, absent) -> dict[str, Any]:
    spec = spec or {}
    output: dict[str, Any] = {'producer':spec.get('producer'), 'observedAt':None,
         'revision':None, 'scope':{'kind':'unknown','parameters':{}},
         'completeness':'unknown', 'evidence':'declared-binding-and-observed-source-only'}
    if 'observedAt' in spec:
        cfg = spec['observedAt']; raw=lookup(snapshot,cfg['path'])
        evidence={'path':cfg['path'], 'sourceFormat':cfg.get('format',cfg.get('formats')), 'raw':None if raw is absent else deepcopy(raw)}
        if raw is absent or raw is None:
            evidence['state']='missing'
        else:
            try:
                evidence['normalized'], evidence['matchedFormat'] = normalize_declared_timestamp(raw,evidence['sourceFormat']); evidence['state']='present'
            except ValueError:
                evidence['state']='invalid-value'
        output['observedAt']=evidence
    if 'revision' in spec:
        cfg=spec['revision'];raw=lookup(snapshot,cfg['path'])
        output['revision']={'domain':cfg['domain'],'source':cfg['path'],
            'value':None if raw is absent else deepcopy(raw),
            'state':'present' if type(raw) in (str,int) and raw != '' else 'missing' if raw is absent else 'invalid-value'}
    if 'scope' in spec:
        cfg=spec['scope']; params={}
        for name,path in cfg['parameters'].items():
            raw=lookup(snapshot,path)
            params[name]={'source':path,'state':'missing' if raw is absent else 'present',
                          'raw':None if raw is absent else deepcopy(raw)}
        output['scope']={'kind':cfg['kind'],'parameters':params}
    if 'completeness' in spec:
        output['completeness']=spec['completeness']
    return output
