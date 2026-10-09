"""把 Clash 订阅转成固定节点的本地代理端口，供每次 Actions 运行使用。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import yaml

from utils.proxy import ACCOUNT_PROXY_ENV, load_account_mapping

ACCOUNT_NODES_ENV = 'PROXY_ACCOUNT_NODES'
DEFAULT_TEST_URL = 'https://www.google.com/generate_204'


def build_config(
	subscription: dict,
	account_nodes: dict[str, str],
	*,
	port: int = 7890,
	test_url: str = DEFAULT_TEST_URL,
) -> tuple[dict, dict[str, str]]:
	"""每个指定节点对应一个独立 listener，直接指向节点，不经过自动选择组。"""
	proxies = subscription.get('proxies') if isinstance(subscription, dict) else None
	if not isinstance(proxies, list) or not proxies:
		raise ValueError('订阅必须是包含 proxies 列表的 Clash / Mihomo YAML，请勿使用 Base64 通用订阅')
	names: list[str] = []
	for proxy in proxies:
		name = proxy.get('name') if isinstance(proxy, dict) else None
		if not isinstance(name, str) or not name.strip():
			raise ValueError('订阅中的每个节点都必须有非空 name')
		if name in names or name in {'CHECKIN', 'DIRECT', 'REJECT', 'COMPATIBLE', 'GLOBAL'}:
			raise ValueError(f'订阅节点名称重复或与内置名称冲突: {name!r}')
		names.append(name)

	missing = set(account_nodes.values()) - set(names)
	if missing:
		raise ValueError(f'订阅中找不到指定节点: {sorted(missing)!r}；请复制完整名称，包括空格和 emoji')
	for proxy in proxies:
		if proxy['name'] in account_nodes.values() and str(proxy.get('type', '')).lower() in {'direct', 'reject'}:
			raise ValueError('账号必须绑定实际代理节点，不能绑定 DIRECT / REJECT')

	unique_nodes = list(dict.fromkeys(account_nodes.values()))
	if not 1 <= port <= 65535 - len(unique_nodes):
		raise ValueError('PROXY_PORT 及账号代理端口必须在 1 到 65535 之间')
	node_ports = {node: port + index for index, node in enumerate(unique_nodes, start=1)}
	account_urls = {account: f'http://127.0.0.1:{node_ports[node]}' for account, node in account_nodes.items()}
	config = {
		'mixed-port': port,
		'allow-lan': False,
		'ipv6': False,
		'mode': 'rule',
		'log-level': 'warning',
		'unified-delay': True,
		'proxies': proxies,
		'proxy-groups': [
			{
				'name': 'CHECKIN',
				'type': 'url-test',
				'url': test_url,
				'interval': 300,
				'tolerance': 150,
				'lazy': False,
				'proxies': names,
			}
		],
		'rules': ['MATCH,CHECKIN'],
		'listeners': [
			{
				'name': f'checkin-node-{index}',
				'type': 'mixed',
				'listen': '127.0.0.1',
				'port': node_port,
				'proxy': node,
			}
			for index, (node, node_port) in enumerate(node_ports.items(), start=1)
		],
	}
	return config, account_urls


def fetch_subscription(url: str) -> dict:
	"""下载原始节点定义；不把含订阅 Token 的 URL 或响应体写入错误日志。"""
	try:
		response = httpx.get(
			url,
			headers={'User-Agent': 'clash.meta'},
			timeout=45,
			follow_redirects=True,
			trust_env=False,
		)
	except (httpx.HTTPError, httpx.InvalidURL) as exc:
		raise ValueError('订阅下载失败，请检查 PROXY_SUBSCRIPTION_URL 和网络连接') from exc
	if not response.is_success:
		raise ValueError(f'订阅下载失败，HTTP {response.status_code}')
	try:
		data = yaml.safe_load(response.text)
	except yaml.YAMLError as exc:
		raise ValueError('订阅不是有效的 Clash / Mihomo YAML') from exc
	if not isinstance(data, dict):
		raise ValueError('订阅必须使用 Clash / Mihomo YAML 格式')
	return data


def main() -> int:
	try:
		account_nodes = load_account_mapping(ACCOUNT_NODES_ENV)
		subscription_url = os.getenv('PROXY_SUBSCRIPTION_URL', '').strip()
		if not subscription_url:
			raise ValueError('请先设置 PROXY_SUBSCRIPTION_URL')
		port = int(os.getenv('PROXY_PORT', '7890'))
		test_url = os.getenv('PROXY_TEST_URL', DEFAULT_TEST_URL)
		config, account_urls = build_config(
			fetch_subscription(subscription_url), account_nodes, port=port, test_url=test_url
		)
		proxy_dir = Path(os.environ['PROXY_DIR'])
		proxy_dir.mkdir(parents=True, exist_ok=True)
		(proxy_dir / 'config.yaml').write_text(
			yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding='utf-8'
		)
		shared_url = f'http://127.0.0.1:{port}'
		(proxy_dir / 'proxy-env').write_text(
			f'CHECKIN_PROXY_URL={shared_url}\n{ACCOUNT_PROXY_ENV}={json.dumps(account_urls, ensure_ascii=False)}\n',
			encoding='utf-8',
		)
		endpoints = dict.fromkeys([shared_url, *account_urls.values()])
		(proxy_dir / 'proxy-endpoints.txt').write_text('\n'.join(endpoints) + '\n', encoding='utf-8')
		for account, node in account_nodes.items():
			print(f'[PROXY] {account!r} → {node!r} ({account_urls[account]})')
		return 0
	except (ValueError, OSError, KeyError) as exc:
		print(f'[FAILED] 代理配置失败: {exc}')
		return 1


if __name__ == '__main__':
	sys.exit(main())
