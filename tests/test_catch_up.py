"""过滤后的补签：某个槽位被 GitHub 丢掉时，别让那批账号空一整天。

真实案例：10-13 傍晚那次运行被 GitHub 丢弃（当天 18:00~24:00 一次都没跑），
于是 10-14 00:01 的 ag+mlgb7 签到整天空了 —— 而 03:12 那次落在早晨窗口、
只签 anyrouter，不会带上它们。
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from checkin import processed_today, split_stale_accounts
from utils.config import AccountConfig

TODAY = '2026-10-14'


def account(name: str, provider: str) -> AccountConfig:
	return AccountConfig(cookies={'session': 'x'}, provider=provider, name=name)


ANY = account('any主帐号', 'anyrouter')
AG_LD = account('agLD', 'agentrouter')
AG_GH = account('agGithub', 'agentrouter')
MLGB = account('mlgb7', 'mlgb7')


# --- processed_today ------------------------------------------------------------


def test_no_state_means_not_processed():
	assert processed_today({}, AG_LD, TODAY) is False


def test_same_day_counts_as_processed():
	state = {'agLD': {'day': TODAY, 'last_total': 100.0}}

	assert processed_today(state, AG_LD, TODAY) is True


def test_previous_day_is_not_processed():
	state = {'agLD': {'day': '2026-10-13', 'last_total': 100.0}}

	assert processed_today(state, AG_LD, TODAY) is False


def test_nameless_account_is_conservatively_not_processed():
	"""没有 name 就定位不到 state —— 宁可多跑一次也别漏签。"""
	nameless = AccountConfig(cookies={'session': 'x'}, provider='anyrouter')

	assert processed_today({'Account 1': {'day': TODAY}}, nameless, TODAY) is False


# --- split_stale_accounts -------------------------------------------------------


def test_stale_accounts_are_carried_for_catch_up():
	"""经典场景：当晚那次被丢，早晨那次只签 anyrouter，ag 两个号要被补上。"""
	state = {'any主帐号': {'day': TODAY}}  # 刚在早晨窗口签过

	carried, remaining = split_stale_accounts(state, [AG_LD, AG_GH], TODAY)

	assert [a.name for a in carried] == ['agLD', 'agGithub']
	assert remaining == []


def test_accounts_processed_today_are_left_out():
	state = {'agLD': {'day': TODAY}, 'agGithub': {'day': TODAY}}

	carried, remaining = split_stale_accounts(state, [AG_LD, AG_GH], TODAY)

	assert carried == []
	assert [a.name for a in remaining] == ['agLD', 'agGithub']


def test_mixed_state_is_split_correctly():
	state = {'agLD': {'day': TODAY}, 'agGithub': {'day': '2026-10-13'}}

	carried, remaining = split_stale_accounts(state, [AG_LD, AG_GH, MLGB], TODAY)

	assert [a.name for a in carried] == ['agGithub', 'mlgb7']
	assert [a.name for a in remaining] == ['agLD']


def test_no_skipped_accounts_is_a_noop():
	carried, remaining = split_stale_accounts({}, [], TODAY)

	assert carried == []
	assert remaining == []


def test_mlgb7_without_user_info_still_counts_once_day_is_recorded():
	"""mlgb7 不返回 user_info，靠主流程在成功时补写 day，才不会每次都被补签。"""
	state = {'mlgb7': {'day': TODAY}}

	assert processed_today(state, MLGB, TODAY) is True
	assert split_stale_accounts(state, [MLGB], TODAY) == ([], [MLGB])
