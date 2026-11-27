#!/usr/bin/env bash
# 离线 CI 检查：测试套件 + 定价自洽 + 配置/数据契约漂移自检。
# 不需要网络、不需要 ERP token；WARNING（未核实费率等）不阻断，FAIL 才退出非 0。
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== 1/3 离线测试套件 =="
python3 tests/run.py

echo "== 2/3 定价配置自洽 =="
python3 scripts/pricing_calc.py --check

echo "== 3/3 配置与数据契约漂移自检（离线） =="
python3 scripts/health_check.py --skip-net --ci

echo "== CI 全绿 =="
