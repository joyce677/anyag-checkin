"""账号绑定必须同时覆盖浏览器登录、WAF 获取和 HTTP 签到。"""

import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import checkin
from utils.browser import launch_login_context, load_browser_login_settings
from utils.config import AccountConfig, AppConfig
from utils.mlgb import MlgbError
from utils.proxy import (
	ACCOUNT_PROXY_ENV,
	get_playwright_proxy,
	get_proxy_server,
	validate_account_proxy_names,
)

ROUTES = {
	'AgentRouter-1': 'http://127.0.0.1:7891',
	'AnyRouter-1': 'http://127.0.0.1:7892',
	'噜皮': 'http://127.0.0.1:7893',
}


@pytest.fixture(autouse=True)
def account_routes(monkeypatch):
	monkeypatch.setenv(ACCOUNT_PROXY_ENV, json.dumps(ROUTES))
	monkeypatch.setenv('CHECKIN_PROXY_URL', 'http://127.0.0.1:7890')
	monkeypatch.delenv('PROVIDERS', raising=False)


def test_unbound_accounts_keep_provider_default():
	assert get_proxy_server(account_name='unbound', use_proxy=True) == 'http://127.0.0.1:7890'
	assert get_proxy_server(account_name='unbound', use_proxy=False) is None
	assert get_playwright_proxy(account_name='AnyRouter-1', use_proxy=False) == {'server': ROUTES['AnyRouter-1']}


@pytest.mark.asyncio
@pytest.mark.parametrize(('provider', 'name'), [('agentrouter', 'AgentRouter-1'), ('anyrouter', 'AnyRouter-1')])
async def test_waf_and_http_requests_use_the_same_account_route(monkeypatch, provider, name):
	launched = []
	clients = []

	async def fake_launch(**kwargs):
		launched.append(kwargs)
		raise RuntimeError('browser launch intercepted')

	monkeypatch.setattr(checkin, 'launch_async', fake_launch)
	config = AppConfig.load_from_env().get_provider(provider)
	assert config is not None
	with pytest.raises(RuntimeError, match='intercepted'):
		await checkin.get_waf_cookies_with_browser(name, config.domain, ['acw_tc'], use_proxy=config.use_proxy)

	client_class = httpx.Client

	def factory(**kwargs):
		clients.append(kwargs)
		transport = httpx.MockTransport(
			lambda request: httpx.Response(200, json={'success': True, 'data': {'quota': 500000, 'used_quota': 0}})
		)
		return client_class(transport=transport)

	monkeypatch.setattr(checkin.httpx, 'Client', factory)
	cookies = {'session': 'test'}
	account = AccountConfig(cookies=cookies, api_user='1', provider=provider, name=name)
	outcome, _, _ = checkin.run_check_in_requests(cookies, account, name, config, use_proxy=config.use_proxy)
	assert outcome.success
	assert launched[0]['proxy']['server'] == clients[0]['proxy'] == ROUTES[name]


@pytest.mark.asyncio
@pytest.mark.parametrize('persistent', [True, False])
async def test_password_browser_uses_account_route_even_when_provider_defaults_to_direct(monkeypatch, persistent):
	launches = []

	async def fake_launch(*args, **kwargs):
		launches.append(kwargs)
		raise RuntimeError('browser launch intercepted')

	monkeypatch.setitem(
		sys.modules,
		'cloakbrowser',
		SimpleNamespace(launch_async=fake_launch, launch_persistent_context_async=fake_launch),
	)
	settings = load_browser_login_settings('AnyRouter-1', 'anyrouter', persist_profile=persistent)
	with pytest.raises(RuntimeError, match='intercepted'):
		await launch_login_context(settings, account_name='AnyRouter-1', use_proxy=False)
	assert launches[0]['proxy']['server'] == ROUTES['AnyRouter-1']


@pytest.mark.asyncio
async def test_password_login_passes_the_account_identity_to_browser(monkeypatch):
	launch = AsyncMock(side_effect=RuntimeError('browser launch intercepted'))
	monkeypatch.setattr(checkin, 'launch_login_context', launch)
	config = AppConfig.load_from_env().get_provider('anyrouter')
	assert await checkin.login_with_credentials('AnyRouter-1', config, 'anyrouter', 'user', 'password') is None
	assert launch.call_args.kwargs == {'account_name': 'AnyRouter-1', 'use_proxy': False}


def test_mlgb_uses_its_account_route_and_does_not_retry_directly(monkeypatch):
	calls = []

	def fail_proxy(domain, cookies, *, proxy_url=None):
		calls.append(proxy_url)
		raise MlgbError('proxy unavailable')

	monkeypatch.setattr(checkin, 'mlgb_check_in', fail_proxy)
	account = AccountConfig(cookies={'session': 'test'}, provider='mlgb7', name='噜皮')
	outcome, _, _ = checkin.run_mlgb_account('噜皮', account, AppConfig.load_from_env().get_provider('mlgb7'))
	assert not outcome.success
	assert calls == [ROUTES['噜皮']]


def test_failed_http_proxy_is_not_replaced_by_shared_proxy_or_direct_connection(monkeypatch):
	calls = []

	def fail_proxy(**kwargs):
		calls.append(kwargs)
		raise httpx.ProxyError('proxy unavailable')

	monkeypatch.setattr(checkin.httpx, 'Client', fail_proxy)
	cookies = {'session': 'test'}
	account = AccountConfig(cookies=cookies, provider='agentrouter', name='AgentRouter-1')
	outcome, _, _ = checkin.run_check_in_requests(
		cookies, account, 'AgentRouter-1', AppConfig.load_from_env().get_provider('agentrouter'), use_proxy=True
	)
	assert not outcome.success
	assert len(calls) == 1
	assert calls[0]['proxy'] == ROUTES['AgentRouter-1']


@pytest.mark.parametrize('raw', ['[', '[]', '{"AnyRouter-1": ""}', '{"AnyRouter-1": null}'])
def test_invalid_binding_does_not_silently_use_provider_default(monkeypatch, raw):
	monkeypatch.setenv(ACCOUNT_PROXY_ENV, raw)
	with pytest.raises(ValueError, match=ACCOUNT_PROXY_ENV):
		get_proxy_server(account_name='AnyRouter-1', use_proxy=False)


def test_duplicate_names_are_rejected():
	with pytest.raises(ValueError, match='唯一'):
		validate_account_proxy_names([*ROUTES, 'AnyRouter-1'])


@pytest.mark.asyncio
async def test_unknown_account_binding_stops_before_any_login(monkeypatch):
	monkeypatch.setenv(
		'ANYROUTER_ACCOUNTS',
		json.dumps([{'name': 'AgentRouter-1', 'email': 'user', 'password': 'test'}]),
	)
	monkeypatch.delenv('ANYROUTER_ACCOUNTS_EXTRA', raising=False)
	login = AsyncMock()
	monkeypatch.setattr(checkin, 'check_in_account', login)
	with pytest.raises(SystemExit) as exc:
		await checkin.main()
	assert exc.value.code == 1
	login.assert_not_awaited()
