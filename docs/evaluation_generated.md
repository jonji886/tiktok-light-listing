# Evaluation Report（自动生成）

> 由 `python3 scripts/generate_evaluation_report.py` 生成；本报告所有数字均来自本次执行的命令输出或本地文件实测，未标注来源的数字不存在。

## 运行信息

| 项 | 值 |
|---|---|
| 生成时间（本地） | 2026-11-27T17:41:04 |
| 代码版本 | commit `506dd10`（2026-11-27） |
| 测试命令 | `python3 tests/run.py`（离线，不联网、不需要 token） |
| Python | 3.14.3 |
| 环境 | macOS 本机；CI 另有 GitHub Actions ubuntu-3.12 跑同一套件 |

## 1. 测试通过率（Unit + Integration，离线）

| 指标 | 值 |
|---|---|
| 用例总数 | 305 |
| 通过 | 304 |
| 跳过 | 1（缺 PIL 环境相关用例） |
| 失败 | 0 |
| 耗时 | 16.6s |

口径说明：这**只**覆盖纯本地确定性逻辑（闸门规则、定价公式、SKU 校验、失败路由、脱敏、重试策略、故障场景 Mock），**不等于**真实业务成功率。ERP 网络调用全部为 Mock（`unittest.mock`），不触网、不改真实店铺。

## 2. Agent 黄金测试集（失败路由回测）

| 指标 | 值 |
|---|---|
| 案例数 | 23 |
| 命中 | 23 |
| 命中率 | 100.0% |

字段完备性（tests/fixtures/failure_golden_set.json 实测扫描）：

| 检查项 | 结果 |
|---|---|
| 含测试输入（error_text） | 23/23 |
| 含预期路由结果（expect） | 23/23 |
| 缺字段案例 | 无 |

注：`预期工具调用 / 是否重试 / 是否人工接管` 三项在规则表 `config/failure_rules.json` 的 tier/auto_exec 字段中定义，golden set 目前只断言路由结果（expect）；已如实标注为**部分覆盖**。

## 3. 真实业务数据（本地落盘快照统计）

### 3.1 发布日志（唯一口径：tiktok_logs*.csv 的 status 列）

| 指标 | 值 | 来源 |
|---|---|---|
| 日志快照文件数 | 51 | erp_data/**/tiktok_logs*.csv glob |
| 各状态商品数（按 itemId 最新去重） | fail=5, processing=10, success=149 | 同上 |

口径警告：`success` 是 **ERP 发布日志受理成功**，不等于平台侧在售数（在售唯一口径 = `productStatus == ACTIVATE`，见 `site_goods.py status`）；跨批快照混合了不同店铺与历史下架品，不能当连续漏斗读。

### 3.2 单品流水线状态机（erp_data/_run/pipeline/*.json）

| 指标 | 值 |
|---|---|
| 有状态文件的任务数 | 663 |
| 走到 done（四路验证通过归档） | 27 |
| 停留阶段分布（top） | edit_dry=615, done=27, wait=8, review_img=5, claim=4, verify4=1, abandoned=1, ?=1 |

口径警告：`edit_dry` 大量堆积是**历史批量 start 后未推进的草稿态状态文件**（预检/试验残留），不代表 615 个真实在途任务；`reached_done` 与本地快照的平台侧 ACTIVATE 数（results-matrix 记 27）一致性待核。

## 4. 未采集指标（如实标注，不补位）

| 指标 | 状态 | 原因 |
|---|---|---|
| 商品采集成功率 | 待采集 | collect 回执有成功/失败数，但未跨批聚合落盘 |
| ERP 认领成功率 | 待采集 | 认领走 batchClaimMultiShop 回执，历史未集中记录 |
| Agent 任务完成率（真实环境） | 暂无数据 | 任务多经由 agent 会话驱动，无独立评测 harness |
| 异常诊断准确率 | 部分 | 黄金集回测 23/23 为**规则命中**口径；「LLM 判断异常」准确率未测 |
| 自动恢复成功率 | 待采集 | failure_router apply --execute 使用次数少，样本不足 |
| 人工接管率 | 待采集 | trace 日志自 2026-11-27 起才有 human_approve 事件 |
| 平均执行耗时 | 见 timing 账本 | `python3 scripts/timing.py report --run <id>`（按批统计，无全局聚合口径） |

## 5. 复现步骤

```bash
python3 tests/run.py                      # 全部离线测试
python3 scripts/failure_router.py backtest # 黄金集回测
python3 scripts/generate_evaluation_report.py --out docs/evaluation_generated.md
```

