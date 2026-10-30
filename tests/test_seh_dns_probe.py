"""Pure, deterministic DNS status diagnostics; no external DNS or Coolify calls."""
import socket

from tools.seh_control.dns_probe import domain_hosts, probe

URL = 'https://external.greatlibrary.io'
APP = {'domains':'https://external.greatlibrary.io:8080'}


def test_cloudflare_ipv4_ipv6_with_matching_coolify_domain_is_ready():
    result = probe(URL, APP, resolver=lambda host: ['104.21.10.9', '2606:4700:3030::6815:a09'])
    assert result['hostname'] == 'external.greatlibrary.io'
    assert result['status'] == 'DNS_CLOUDFLARE_EDGE'
    assert result['cloudflare_edge_addresses'] is True
    assert result['coolify_domain_matches'] is True
    assert result['ready'] is True
    assert result['resolver'] == 'system'


def test_dns_resolves_direct_origin_but_does_not_qualify_as_cloudflare_protected():
    result = probe(URL, APP, resolver=lambda host: ['159.203.184.182'])
    assert result['status'] == 'DNS_NOT_CLOUDFLARE'
    assert result['ready'] is False


def test_dns_matches_cloudflare_but_coolify_domain_is_different():
    result = probe(URL, {'fqdn':'https://some-other-seh.example.net:8080'},
                   resolver=lambda host:['172.67.1.2'])
    assert result['status'] == 'DNS_CLOUDFLARE_EDGE'
    assert result['coolify_domain_matches'] is False
    assert result['ready'] is False


def test_cloudflare_dns_without_coolify_domain_does_not_claim_ready():
    result = probe(URL, {}, resolver=lambda host:['104.16.12.3'])
    assert result['coolify_domain_matches'] is None
    assert result['ready'] is False


def test_dns_nxdomain_does_not_raise_or_override_coolify_health():
    def nxdomain(host):
        raise socket.gaierror(socket.EAI_NONAME, 'no such name')
    result = probe(URL, APP, resolver=nxdomain)
    assert result['status'] == 'DNS_NOT_FOUND'
    assert result['ready'] is False
    assert result['addresses'] == []


def test_dns_transport_outage_is_not_misreported_as_nxdomain():
    def outage(host):
        raise socket.gaierror(getattr(socket, 'EAI_AGAIN', -3), 'temporarily unavailable')
    assert probe(URL, APP, resolver=outage)['status'] == 'DNS_LOOKUP_FAILED'


def test_mixed_edge_origin_and_private_dns_refused():
    mixed = probe(URL, APP, resolver=lambda host:['104.16.12.3','159.203.184.182'])
    assert mixed['status'] == 'DNS_MIXED_CLOUDFLARE_AND_ORIGIN'
    assert not mixed['ready']
    private = probe(URL, APP, resolver=lambda host:['192.168.1.2'])
    assert private['status'] == 'DNS_NONPUBLIC_ADDRESSES'
    assert not private['ready']


def test_coolify_domain_formats_without_coolify_origin_leak():
    assert domain_hosts({'domains':'https://external.greatlibrary.io:8080,https://another.example.org:8080',
                         'fqdn':['https://extra.example.org']}) == [
        'another.example.org','external.greatlibrary.io','extra.example.org']


def test_unavailable_or_invalid_target_requires_no_dns_request():
    def no_dns(host):
        raise AssertionError('DNS lookup unexpectedly attempted')
    assert probe(None, APP, resolver=no_dns)['status'] == 'DNS_TARGET_UNAVAILABLE'
    assert probe('http://external.greatlibrary.io', APP, resolver=no_dns)['status'] == 'DNS_TARGET_INVALID'


def test_dns_lookup_error_contained():
    def bad(host):
        raise OSError('DNS resolver unavailable')
    assert probe(URL, APP, resolver=bad)['status'] == 'DNS_LOOKUP_FAILED'
