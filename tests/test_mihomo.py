"""固定出口的配置、Secrets 解析和启动失败行为。"""

import json
import os
import subprocess

import httpx
import pytest
import yaml

from utils import mihomo
from utils.proxy import get_proxy_server


@pytest.fixture
def subscription():
	return {
		'proxies': [
			{'name': '🇭🇰 香港 01 [专线]', 'type': 'http', 'server': '127.0.0.1', 'port': 18001},
			{'name': '🇭🇰 香港 010 [专线]', 'type': 'http', 'server': '127.0.0.1', 'port': 18002},
		]
	}


def test_exported_routes_point_directly_to_exact_nodes(monkeypatch, tmp_path, subscription):
	bindings = {
		'AgentRouter-1': '🇭🇰 香港 01 [专线]',
		'AnyRouter-1': '🇭🇰 香港 010 [专线]',
		'噜皮': '🇭🇰 香港 01 [专线]',
	}
	monkeypatch.setenv('PROXY_SUBSCRIPTION_URL', 'https://subscription.example.test/?token=private')
	monkeypatch.setenv('PROXY_ACCOUNT_NODES', json.dumps(bindings))
	monkeypatch.setenv('PROXY_DIR', str(tmp_path))
	monkeypatch.setenv('PROXY_PORT', '17890')
	monkeypatch.setattr(mihomo, 'fetch_subscription', lambda url: subscription)
	assert mihomo.main() == 0
	config = yaml.safe_load((tmp_path / 'config.yaml').read_text())
	for line in (tmp_path / 'proxy-env').read_text().splitlines():
		key, value = line.split('=', 1)
		monkeypatch.setenv(key, value)
	listeners = {f'http://127.0.0.1:{listener["port"]}': listener for listener in config['listeners']}
	for account, node in bindings.items():
		url = get_proxy_server(account_name=account, use_proxy=False)
		assert url is not None
		assert listeners[url]['proxy'] == node
		assert listeners[url]['listen'] == '127.0.0.1'
	assert len(listeners) == 2  # 明确绑定同一节点的账号共享该节点端口
	assert not config.get('proxy-providers')  # 本次运行的节点定义固定，不在中途刷新或切换
	assert len((tmp_path / 'proxy-endpoints.txt').read_text().splitlines()) == 3
	assert 'private' not in (tmp_path / 'proxy-env').read_text()


def test_unknown_node_is_rejected_instead_of_selecting_another(subscription):
	with pytest.raises(ValueError, match='找不到指定节点'):
		mihomo.build_config(subscription, {'AnyRouter-1': '香港 01'})


def test_duplicate_node_names_are_ambiguous(subscription):
	subscription['proxies'].append(subscription['proxies'][0].copy())
	with pytest.raises(ValueError, match='名称重复'):
		mihomo.build_config(subscription, {})


def test_direct_outbound_cannot_be_used_as_an_account_proxy():
	with pytest.raises(ValueError, match='实际代理节点'):
		mihomo.build_config({'proxies': [{'name': '直连', 'type': 'direct'}]}, {'AnyRouter-1': '直连'})


def test_missing_proxy_list_reports_subscription_format():
	with pytest.raises(ValueError, match='Clash / Mihomo YAML'):
		mihomo.build_config({'proxy-groups': []}, {})


def test_default_subscription_still_provides_shared_proxy(subscription):
	config, routes = mihomo.build_config(subscription, {})
	assert routes == {}
	assert config['mixed-port'] == 7890
	assert config['proxy-groups'][0]['type'] == 'url-test'
	assert config['listeners'] == []


def test_port_range_includes_all_account_listeners(subscription):
	with pytest.raises(ValueError, match='端口'):
		mihomo.build_config(subscription, {'AnyRouter-1': subscription['proxies'][0]['name']}, port=65535)


@pytest.mark.parametrize('failure', ['network', 'status', 'yaml'])
def test_subscription_errors_do_not_print_tokens(monkeypatch, capsys, failure):
	url = 'https://subscription.example.test/?token=private-token'
	monkeypatch.setenv('PROXY_SUBSCRIPTION_URL', url)
	monkeypatch.delenv('PROXY_ACCOUNT_NODES', raising=False)

	def fetch(*args, **kwargs):
		if failure == 'network':
			raise httpx.ConnectError(f'failed to connect to {url}')
		if failure == 'status':
			return httpx.Response(403, text='private-token')
		return httpx.Response(200, text='password: private-token\nbroken: [')

	monkeypatch.setattr(mihomo.httpx, 'get', fetch)
	assert mihomo.main() == 1
	output = capsys.readouterr().out
	assert '订阅' in output
	assert 'private-token' not in output


def test_binding_without_subscription_fails_before_starting_proxy():
	env = dict(os.environ)
	env.pop('PROXY_SUBSCRIPTION_URL', None)
	env['PROXY_ACCOUNT_NODES'] = '{"AnyRouter-1": "香港 01"}'
	result = subprocess.run(['bash', 'scripts/setup_mihomo_proxy.sh'], env=env, text=True, capture_output=True)
	assert result.returncode == 1
	assert 'requires PROXY_SUBSCRIPTION_URL' in result.stdout


def test_invalid_binding_does_not_download_subscription(monkeypatch):
	monkeypatch.setenv('PROXY_ACCOUNT_NODES', '[]')
	downloads: list[str] = []
	monkeypatch.setattr(mihomo, 'fetch_subscription', downloads.append)
	assert mihomo.main() == 1
	assert downloads == []
