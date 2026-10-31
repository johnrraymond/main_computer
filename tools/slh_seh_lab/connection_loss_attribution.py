"""Cautious causal ordering for SLH Worker disconnections.

A TCP EOF, WebSocket 1006, and relay 1000 are observations, *not* proof
of who initiated shutdown. Only an explicit close instruction observed earlier
in the joined timeline may be offered as a candidate. Never report payloads,
keys, exception strings, socket addresses, or untrusted close reasons.
"""
from __future__ import annotations

# There is no generic 'first EOF == culprit' rule.
_EXPLICIT = {
    'explicit_close_requested', 'explicit_socket_close_invoked',
    'keepalive_socket_close_invoked', 'reader_socket_close_invoked',
    'gateway_worker_close_requested', 'slh_origin_close_instruction',
    'slh_writer_origin_close_instruction', 'slh_received_seh_close_instruction',
    'relay_initiated_invalid_frame_close', 'relay_initiated_backpressure_close',
    'relay_slh_requested_close', 'adapter_initiating_worker_tcp_shutdown',
    'adapter_initiating_websocket_close', 'slh_local_socket_shutdown_invoked',
}
_OBSERVATIONS = {
    'socket_recv_eof', 'worker_tcp_recv_eof', 'slh_gateway_tcp_eof',
    'reader_exception', 'gateway_handler_exception', 'relay_websocket_ended',
    'relay_client_receive_exception', 'worker_tcp_read_or_relay_send_exception',
    'slh_gateway_read_or_tunnel_send_error', 'websocket_send_failed',
    'gateway_worker_send_failed', 'keepalive_send_failed',
    'websocket_close_frame_received', 'gateway_paid_worker_failed',
}


def summarize_loss_evidence(timeline: dict) -> dict:
    """Return bounded *observations* and a conservative close-origin candidate.

    Uses monotonic clocks within one physical lab host. Note that independent
    threads may log the same event at slightly different times; event order is
    evidence for investigation, not definitive ownership of the first FIN.
    """
    events = []
    sources = (
        ('worker', [ev for worker in (timeline.get('workers') or {}).values()
                    for ev in worker.get('worker_transport_events', [])]),
        ('adapter', timeline.get('adapter_events') or []),
        ('relay', timeline.get('seh_relay_stream_events') or []),
        ('slh', timeline.get('slh_gateway_stream_events') or []),
        ('gateway', timeline.get('gateway_events') or []),
    )
    for component, rows in sources:
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get('event')
            timestamp = row.get('monotonic_ns')
            if (name not in _EXPLICIT and name not in _OBSERVATIONS) or not isinstance(timestamp, int):
                continue
            record = {'component': component, 'event': name, 'monotonic_ns': timestamp}
            for field in ('exception_class', 'errno', 'websocket_close_code',
                          'received_close_code', 'sent_close_code', 'cause'):
                if field in row and (field != 'cause' or isinstance(row[field], str)):
                    record[field] = row[field]
            events.append(record)
    events.sort(key=lambda r: r['monotonic_ns'])
    # Ignore close commands recorded *after* the first failure observation;
    # those are often merely proper cleanup.
    first_observation = next((r for r in events if r['event'] in _OBSERVATIONS), None)
    first_explicit = next((r for r in events if r['event'] in _EXPLICIT), None)
    candidate = (first_explicit if first_explicit and (
        first_observation is None or first_explicit['monotonic_ns'] < first_observation['monotonic_ns'])
        else None)
    return {
        'status': 'EXPLICIT_CLOSE_PRECEDED_OBSERVED_FAILURE' if candidate else 'ORIGIN_NOT_ESTABLISHED',
        'first_observed_failure': first_observation,
        'first_explicit_close': first_explicit,
        'candidate_prior_close': candidate,
        'warning': 'First EOF/1006 is not an initiating cause; timing alone cannot prove FIN origin.',
        'ordered_events': events[:80],
        'events_total': len(events),
    }
