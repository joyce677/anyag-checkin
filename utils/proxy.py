"""账号代理优先于平台默认代理，供浏览器和 HTTP 客户端共用。"""

from __future__ import annotations

import json
import os

ACCOUNT_PROXY_ENV = 'CHECKIN_ACCOUNT_PROXY_URLS'


def load_account_mapping(env_name: str) -> dict[str, str]:
	"""读取按账号名索引的 JSON 对象；错误配置不能静默变成直连。"""
	raw = os.getenv(env_name, '').strip()
	if not raw:
		return {}
	try:
		mapping = json.loads(raw)
	except json.JSONDecodeError as exc:
		raise ValueError(f'{env_name} 必须是有效的 JSON 对象') from exc
	if not isinstance(mapping, dict) or any(
		not isinstance(name, str) or not name.strip() or not isinstance(value, str) or not value.strip()
		for name, value in mapping.items()
	):
		raise ValueError(f'{env_name} 必须是非空账号名到非空字符串的 JSON 对象')
	return mapping


def validate_account_proxy_names(account_names: list[str]) -> None:
	"""在发起登录请求前发现拼错或重复的账号名，避免绑定未生效。"""
	mapping = load_account_mapping(ACCOUNT_PROXY_ENV)
	if not mapping:
		return
	unknown = set(mapping) - set(account_names)
	if unknown:
		raise ValueError(f'代理绑定的账号名不存在: {", ".join(sorted(unknown))}')
	if len(account_names) != len(set(account_names)):
		raise ValueError('配置账号代理时，每个账号的 name 必须唯一')


def get_proxy_server(*, account_name: str | None = None, use_proxy: bool = True) -> str | None:
	"""优先使用账号绑定；未绑定的账号沿用平台的 use_proxy 设置。"""
	account_proxies = load_account_mapping(ACCOUNT_PROXY_ENV)
	if account_name is not None and account_name in account_proxies:
		return account_proxies[account_name]
	if not use_proxy:
		return None
	server = os.getenv('CHECKIN_PROXY_URL', '').strip()
	return server or None


def get_playwright_proxy(*, account_name: str | None = None, use_proxy: bool = True) -> dict[str, str] | None:
	server = get_proxy_server(account_name=account_name, use_proxy=use_proxy)
	if not server:
		return None
	return {'server': server}
