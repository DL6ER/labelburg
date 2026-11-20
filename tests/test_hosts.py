"""Host names the server answers: those of the home network and of HOSTS,
also in Origin, so that a website cannot reach it by DNS rebinding."""

import logging
import os

import pytest

from app import create_app, hosts
from config import Config
from tests.helpers import EXAMPLE_FORMDATA, TEXT, make_client

PAGE = '/labeldesigner/'
REFUSED = 'This host name is not allowed. Add it to HOSTS.'


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path)


@pytest.mark.parametrize('host, allowed', [
    ('localhost', True), ('printserver:8013', True), ('labels.local', True), ('x.lan', True),
    ('x.home.arpa', True), ('192.168.1.5:8013', True), ('[fd00::1]:8013', True),
    ('raspberrypi.fritz.box', True), ('printer.internal:8013', True), ('Labels.LOCAL.', True),
    ('example.com', False), ('labels.local.example.com', False), ('', False), ('.', False),
    ('localhost..', False), ('..', False), ('a.lan..', False),
    ('nas.home', True), ('printer.localdomain:8013', True), ('nas.home.example.com', False),
    ('nas.tail1234.ts.net', False), ('labels.duckdns.org', False), ('nas.synology.me', False),
    ('evil-fritz.box', False), ('fritz.box.example.com', False), ('printer.internal.example.com', False)])
def test_host_names_of_the_home_network(host, allowed):
    assert hosts.host_allowed(host) is allowed


def test_further_names_and_only_trusted_ones():
    assert hosts.host_allowed('labels.example.com', ['labels.example.com'])
    assert hosts.host_allowed('a.example.com:8013', ['.example.com'])
    assert not hosts.host_allowed('example.org', ['.example.com'])
    assert not hosts.host_allowed('labels.local', None, ['labels.example.com'])
    assert hosts.host_allowed('labels.example.com', ['labels.example.com'], ['labels.example.com'])


def test_names_in_any_case_and_with_a_trailing_dot():
    assert hosts.host_allowed('Labels.Example.com.', ['labels.example.com'])
    assert hosts.host_allowed('labels.example.com:8013', ['Labels.Example.COM.'])
    assert hosts.host_allowed('A.EXAMPLE.com.:8013', ['.example.com'])
    assert hosts.host_allowed('LABELS.example.com.', None, ['labels.example.com'])
    assert not hosts.host_allowed('labels.example.com..', ['labels.example.com'])
    assert not hosts.host_allowed('Other.Example.com.', ['labels.example.com'])
    assert not hosts.host_allowed('Labels.Local.', None, ['labels.example.com'])


@pytest.mark.parametrize('host', ['192.168.1.5:8013', '[fd00::1]:8013', 'localhost', 'printserver', 'labels.local:8013',
                                  'raspberrypi.fritz.box:8013', 'printer.internal'])
def test_pages_for_names_of_the_home_network(client, host):
    assert client.get(PAGE, base_url=f'http://{host}').status_code == 200


def test_a_public_name_is_refused(client, caplog):
    with caplog.at_level(logging.INFO, logger='app'):
        response = client.get(PAGE, base_url='http://labels.example.com')
    assert response.status_code == 403
    assert response.mimetype == 'text/plain'
    assert response.text == REFUSED
    assert [r.args for r in caplog.records if r.msg == 'Refused a request for host %r'] == [('labels.example.com',)]


def test_names_of_hosts(client):
    client.application.config['HOSTS'] = ['labels.example.com', '.example.org']
    for host in ('labels.example.com', 'a.example.org:8443', 'example.org'):
        assert client.get(PAGE, base_url=f'http://{host}').status_code == 200
    assert client.get(PAGE, base_url='http://other.example.com').status_code == 403


def test_names_of_mcp_hosts_still_count(client):
    client.application.config['HOSTS'] = ['labels.example.com']
    client.application.config['MCP_HOSTS'] = ['mcp.example.com']
    for host in ('labels.example.com', 'mcp.example.com'):
        assert client.get(PAGE, base_url=f'http://{host}').status_code == 200


def test_hosts_and_mcp_hosts_from_the_environment(monkeypatch):
    monkeypatch.setenv('HOSTS', 'labels.example.com, .example.org')
    monkeypatch.setenv('MCP_HOSTS', 'mcp.example.net')
    app = create_app()
    assert app.config['HOSTS'] == ['labels.example.com', '.example.org']
    client = app.test_client()
    for host in ('labels.example.com', 'a.example.org', 'mcp.example.net'):
        assert client.get('/static/manifest.json', base_url=f'http://{host}').status_code == 200


def test_names_as_a_string_in_the_instance_config():
    class Instance(Config):
        HOSTS = 'labels.example.com, .example.org'
        MCP_HOSTS = 'mcp.example.net'
    client = create_app(Instance).test_client()
    for host in ('labels.example.com', 'a.example.org', 'mcp.example.net'):
        assert client.get(PAGE, base_url=f'http://{host}').status_code == 200
    assert client.get(PAGE, base_url='http://other.example.com').status_code == 403

    class Trusted(Config):
        TRUSTED_HOSTS = 'labels.example.com,labels.local'
    client = create_app(Trusted).test_client()
    for host in ('labels.example.com', 'labels.local'):
        assert client.get(PAGE, base_url=f'http://{host}').status_code == 200
    assert client.get(PAGE, base_url='http://printserver').status_code == 403


def test_only_the_names_of_trusted_hosts_when_set(client):
    client.application.config['TRUSTED_HOSTS'] = ['labels.example.com']
    client.application.config['HOSTS'] = ['other.example.com']
    assert client.get(PAGE, base_url='http://labels.example.com').status_code == 200
    for host in ('labels.local', 'other.example.com'):
        response = client.get(PAGE, base_url=f'http://{host}')
        assert response.status_code == 403
        assert response.text == 'This host name is not allowed. Add it to TRUSTED_HOSTS.'


@pytest.mark.parametrize('setting', ['HOSTS', 'TRUSTED_HOSTS'])
def test_pages_for_names_in_any_case_and_with_a_trailing_dot(client, setting):
    client.application.config[setting] = ['labels.example.com']
    for host in ('Labels.Example.com.', 'LABELS.EXAMPLE.COM:8013'):
        assert client.get(PAGE, headers={'Host': host}).status_code == 200
    assert client.get(PAGE, headers={'Host': 'Other.Example.com.'}).status_code == 403


def test_origin_of_another_public_name_is_refused(client):
    client.application.config['HOSTS'] = ['labels.example.com']
    base_url = 'https://labels.example.com'
    response = client.get(PAGE, base_url=base_url, headers={'Origin': 'https://evil.example'})
    assert response.status_code == 403
    assert response.text == REFUSED
    for origin in ('https://labels.example.com', 'http://192.168.1.5:8013', 'http://labels.local'):
        assert client.get(PAGE, base_url=base_url, headers={'Origin': origin}).status_code == 200


def test_opaque_origin_names_no_host(client):
    # Android shares with the installed app with "Origin: null"
    assert client.get(PAGE, headers={'Origin': 'null'}).status_code == 200


def test_dns_rebinding_cannot_open_a_page(client):
    # The attacker's name first leads to its own server, then to this one:
    # Host and Origin are both that name
    response = client.get(PAGE, base_url='http://rebind.example', headers={'Origin': 'http://rebind.example'})
    assert response.status_code == 403


def test_dns_rebinding_cannot_print(client, tmp_path):
    client.application.config['PRINTERS'] = [('QL-800', 'simulation', 'Kitchen')]
    data = {**EXAMPLE_FORMDATA, 'text': TEXT, 'printer': 'Kitchen'}
    response = client.post('/labeldesigner/api/print', data=data, base_url='http://rebind.example',
                           headers={'Origin': 'http://rebind.example'})
    assert response.status_code == 403
    assert not os.path.exists(tmp_path / 'simulated_labels')
    response = client.post('/labeldesigner/api/print', data=data, headers={'Origin': 'http://localhost'})
    assert response.status_code == 200
    assert len(os.listdir(tmp_path / 'simulated_labels')) == 1


def test_static_files(client):
    assert client.get('/static/manifest.json').status_code == 200
    assert client.get('/static/manifest.json', base_url='http://rebind.example').status_code == 403


def test_the_name_behind_a_reverse_proxy(monkeypatch):
    monkeypatch.setenv('PROXY_COUNT', '1')
    client = create_app().test_client()
    forwarded = {'X-Forwarded-Host': 'labels.example.com', 'X-Forwarded-Proto': 'https'}
    assert client.get(PAGE, headers=forwarded).status_code == 403
    client.application.config['HOSTS'] = ['labels.example.com']
    assert client.get(PAGE, headers=forwarded).status_code == 200


# What a rebinding page can send itself: X-Forwarded-Host is no forbidden
# header, a same-origin fetch without referrer has Origin null
SMUGGLED = {'X-Forwarded-Host': 'localhost', 'Origin': 'null', 'X-Requested-With': 'XMLHttpRequest'}


def test_a_forwarded_name_from_a_client_reaching_the_server_directly(monkeypatch, tmp_path):
    monkeypatch.setenv('PROXY_COUNT', '1')
    client = create_app().test_client()
    client.application.config['PRINTERS'] = [('QL-800', 'simulation', 'Kitchen')]
    base_url = 'http://rebind.example:8013'
    assert client.get(PAGE, base_url=base_url, headers=SMUGGLED).status_code == 403
    assert client.get('/labeldesigner/api/settings', base_url=base_url, headers=SMUGGLED).status_code == 403
    data = {**EXAMPLE_FORMDATA, 'text': TEXT, 'printer': 'Kitchen'}
    response = client.post('/labeldesigner/api/print', data=data, base_url=base_url, headers=SMUGGLED)
    assert response.status_code == 403
    assert not os.path.exists(tmp_path / 'simulated_labels')


def test_a_forwarded_name_to_the_mcp_endpoint(monkeypatch):
    monkeypatch.setenv('PROXY_COUNT', '1')
    client = create_app().test_client()
    headers = {**SMUGGLED, 'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-06-18'}
    response = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
                           base_url='http://rebind.example:8013', headers=headers)
    assert response.status_code == 403


def test_proxies_that_pass_their_own_host_or_the_public_name(monkeypatch):
    monkeypatch.setenv('PROXY_COUNT', '1')
    monkeypatch.setenv('HOSTS', 'labels.example.com')
    client = create_app().test_client()
    forwarded = {'X-Forwarded-Host': 'labels.example.com', 'X-Forwarded-Proto': 'https'}
    # nginx (the Host of proxy_pass), a proxy of the same compose project,
    # Traefik and Caddy (the public name)
    for base_url in ('http://127.0.0.1:8013', 'http://labelburg:8013', 'http://labels.example.com'):
        assert client.get(PAGE, base_url=base_url, headers=forwarded).status_code == 200


def test_trusted_hosts_behind_a_proxy(monkeypatch):
    monkeypatch.setenv('PROXY_COUNT', '1')
    monkeypatch.setenv('TRUSTED_HOSTS', 'labels.example.com')
    client = create_app().test_client()
    forwarded = {'X-Forwarded-Host': 'labels.example.com'}
    assert client.get(PAGE, base_url='http://127.0.0.1:8013', headers=forwarded).status_code == 200
    assert client.get(PAGE, base_url='http://labels.example.com', headers=forwarded).status_code == 200
    assert client.get(PAGE, base_url='http://rebind.example', headers=forwarded).status_code == 403
