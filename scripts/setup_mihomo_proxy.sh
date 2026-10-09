#!/usr/bin/env bash
# 下载 Clash 订阅，启动共享代理和按账号固定节点的代理端口。
# 环境变量:
#   PROXY_SUBSCRIPTION_URL  订阅链接（必填才启用）
#   PROXY_ACCOUNT_NODES     可选 JSON：账号 name -> 订阅中的完整节点名
#   PROXY_TEST_URL          探测目标，默认 https://www.google.com/generate_204
#   PROXY_PORT              本地 mixed-port，默认 7890
# 配置了订阅后，任何初始化失败都会停止签到，不自动改成直连。

set -euo pipefail
umask 077

if [[ -z "${PROXY_SUBSCRIPTION_URL:-}" ]]; then
	if [[ -n "${PROXY_ACCOUNT_NODES:-}" ]]; then
		echo "[FAILED] PROXY_ACCOUNT_NODES requires PROXY_SUBSCRIPTION_URL"
		exit 1
	fi
	echo "[INFO] PROXY_SUBSCRIPTION_URL not set, skip proxy setup"
	exit 0
fi

export PROXY_DIR="${RUNNER_TEMP:-/tmp}/checkin-proxy"
export PROXY_PORT="${PROXY_PORT:-7890}"
export PROXY_TEST_URL="${PROXY_TEST_URL:-https://www.google.com/generate_204}"
MIHOMO_VERSION="${MIHOMO_VERSION:-v1.19.0}"

mkdir -p "${PROXY_DIR}"
uv run --frozen python -m utils.mihomo
cd "${PROXY_DIR}"

echo "[INFO] Downloading mihomo ${MIHOMO_VERSION}..."
ARCHIVE="mihomo-linux-amd64-${MIHOMO_VERSION}.gz"
curl --retry 3 --retry-delay 5 --retry-all-errors --connect-timeout 15 --max-time 120 -fsSL -o "${ARCHIVE}" \
	"https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/${ARCHIVE}"
gunzip -f "${ARCHIVE}"
chmod +x "mihomo-linux-amd64-${MIHOMO_VERSION}"
MIHOMO_BIN="${PROXY_DIR}/mihomo-linux-amd64-${MIHOMO_VERSION}"

if ! "${MIHOMO_BIN}" -t -d "${PROXY_DIR}" -f config.yaml > validation.log 2>&1; then
	echo "[FAILED] Mihomo rejected the subscription configuration; check node types and definitions"
	exit 1
fi

echo "[INFO] Starting mihomo with shared and account-specific proxy ports..."
nohup "${MIHOMO_BIN}" -d "${PROXY_DIR}" -f config.yaml > mihomo.log 2>&1 &
MIHOMO_PID=$!
echo "${MIHOMO_PID}" > mihomo.pid

while IFS= read -r PROXY_URL; do
	READY=false
	for attempt in 1 2 3; do
		if curl -fsS --noproxy '' -x "${PROXY_URL}" --max-time 20 "${PROXY_TEST_URL}" -o /dev/null 2>/dev/null; then
			READY=true
			break
		fi
		echo "[INFO] Waiting for ${PROXY_URL} (${attempt}/3)..."
		sleep 2
	done
	if [[ "${READY}" != "true" ]]; then
		echo "[FAILED] Proxy health check failed: ${PROXY_URL}; check the bound node above"
		kill "${MIHOMO_PID}" 2>/dev/null || true
		exit 1
	fi
	echo "[SUCCESS] Proxy is ready: ${PROXY_URL}"
done < proxy-endpoints.txt

echo "[INFO] Proxy routing applies to account login and check-in requests only"
if [[ -n "${GITHUB_ENV:-}" ]]; then
	cat proxy-env >> "${GITHUB_ENV}"
fi
