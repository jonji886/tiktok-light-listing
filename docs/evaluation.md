# Agent Evaluation 与测试口径

> 数据文件：本目录 `evaluation_generated.md` 由 `python3 scripts/generate_evaluation_report.py --out docs/evaluation_generated.md` 从**本次执行**的命令输出与本地落盘文件生成，可复现、不手工维护数字。
> 本文只定义口径与边界——**四类指标互相不能替代**。

## 1. 四类测试的定义与本项目的覆盖现状

### 1.1 Unit Test（单元测试）

- 覆盖：价格计算（`pricing_calc`）、SKU 校验（`test_sku_identity`、`ms_terms.check_sku_selectable`）、数据清洗（`image_gate` 三通道/模板残渣/CJK）、异常分类（`agentlog.classify`、`failure_router`）、译名表（`i18n`）、脱敏（`agentlog.redact`）。
- 执行：`python3 tests/run.py`，离线、零依赖、不联网、不需要 token。
- 结果：见 `evaluation_generated.md` §1。

### 1.2 Integration Test（集成测试，Mock 边界内）

- 覆盖：CLI 参数契约与子命令守护（`test_cli_guards`）、`_post` 故障注入（429/5xx/703/超时/非JSON，`test_fault_recovery`）、状态持久化与断点恢复（`PipelineRecoveryTests`）、任务锁并发（`TaskLockTests`）、判定缓存（`test_judge_cache`）、罗盘 xlsx 回填链路（`test_backflow_import`）。
- **全部 Mock ERP HTTP**（`unittest.mock.patch("urllib.request.urlopen")`），不触真实店铺。
- 与 Unit 同跑于 `tests/run.py`，不区分运行入口，但归属清晰（文件命名对应）。

### 1.3 Agent Evaluation

- 对象：**LLM/Agent 的判断质量**，与脚本逻辑分开。
- 现状（如实）：
  - ✅ 失败归因回测：23 案例黄金集 → 规则路由命中率 100%（`failure_router.py backtest`）。注意这是**确定性规则层**的回测；「未路由失败转 Tier2 由 LLM 诊断」的准确率**没有独立评测**——诊断结果只以「回填规则表后能否命中」间接验证。
  - ✅ 整图判定：判定质量由 `verify` 覆盖率硬闸 + 主图机器证据（`image_fix probe` 的 rec_text）兜底；模型判定与人工复核的一致率**未量化采集**。
  - ❌ 任务理解/工具选择/参数生成的自动化评测 harness：**未建设**（当前无 Agent 单测运行器；任务经 WorkBuddy 会话驱动，行为记录散在会话与 trace）。
  - 标记：`待采集`。
- 黄金集字段完备性（每次生成报告实测）：测试输入 23/23、预期路由结果 23/23、缺字段 0；「预期工具调用/是否重试/是否人工接管」定义在 `config/failure_rules.json` 的 tier/auto_exec，golden set 不逐条断言 → **部分覆盖**。

### 1.4 E2E Test

- 离线 E2E：`publish_one` 全 13 阶段用假 HANDLERS 驱动至 done 的断点/幂等/CP 闸测试（`test_fault_recovery`）——这是**流程结构**验证，不含真实接口。
- 真实 E2E：历史上以真实店铺单品闭环方式验证（2026-11-12 TH 压缩袋、2026-11-19/20 MY 首品等，见 `references/replay-log.md`）。**每次真实 E2E 都需要人工点名授权**，不自动化、不重复跑（避免重复发布/脏草稿）。
- 破坏性场景（超时后服务端已发布等）只在 Mock 层验证，禁止对真实店铺做故障注入。

## 2. 指标口径表

| 指标 | 统计口径 | 数据来源 | 现状 |
|---|---|---|---|
| 商品采集成功率 | 成功采集数/总采集数 | `collect_result.csv` 回执 | 待采集（未跨批聚合） |
| 商品处理成功率 | 完成数据处理数/总处理数 | pipeline state `done_stages` | 待采集（草稿态残留多，见报告口径警告） |
| ERP 认领成功率 | 成功认领/总认领 | `batchClaimMultiShop` 回执+回查 | 待采集（历史未集中记录） |
| 发布成功率 | logs `status=success`/实际提交数 | `tiktok_logs*.csv` | 报告 §3.1（注意：受理≠在售） |
| Agent 任务完成率 | 成功完成任务/总任务 | — | 暂无数据（无 harness） |
| 异常诊断准确率 | 正确诊断/已标注异常 | 黄金集 23 条 | 规则层 23/23；LLM Tier2 未量化 |
| 自动恢复成功率 | 自动恢复成功/可自动恢复异常 | `failure_router apply --execute` | 待采集（样本不足） |
| 人工接管率 | 接管次数/总任务 | trace `human_approve`/`status:hold` | 待采集（2026-11-27 起有数据） |
| 平均执行耗时 | 有效任务耗时均值 | `timing_report.py` 按批 | 按批统计存在，无全局口径 |

平台侧在售的唯一口径是 `productStatus == ACTIVATE`（`site_goods.py status`）；`success:true` 只代表进入发布队列。任何报告不得用受理数代替在售数。

## 3. 禁止事项（写死）

- 不用单元测试通过率代替业务成功率，不用规则回测代替 Agent 评估。
- 不编造成功率/耗时/节省成本/GMV/模型准确率；无法采集的指标一律标注 `待采集`/`暂无数据`/`未验证`。
- 报告数字必须可由 `复现步骤` 段落里的命令重新生成。

## 4. 已知问题

- trace 日志刚接入 `erp_client`/`publish_one`/`site_goods` 三个入口；`batch_listing` 子进程经由 `publish_one`/`erp_client` 间接覆盖，但独立运行的闸门脚本（select_gate 等）尚未打事件（它们是纯本地校验，优先级低）。
- `evaluation_generated.md` §3.2 中 `edit_dry=615` 为历史试验残留，属**数据卫生问题**而非流程问题；清理动作未执行（需人工确认哪些草稿可删）。
- 真实业务漏斗（候选→过闸→发布→在售）的跨批一致性统计未建设，当前按批查看。
