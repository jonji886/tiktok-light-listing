# tiktok-light-listing

> 一个真实业务闭环驱动的 **AI Agent 工程项目**：用 Agent Skill + Python CLI + ERP 接口集成，把 TikTok Shop 跨境「铺货」（选品 → 1688 采集 → 认领 → 图片本地化 → 定价 → 发布 → 回查）的六段人工链路，收敛成一条**有状态、可断点续跑、写操作需人工授权、任务级可观测**的自动化流水线。

![format](https://img.shields.io/badge/format-Agent%20Skill-6E4AFF)
![python](https://img.shields.io/badge/python-3.8%2B-3776AB)
![tests](https://img.shields.io/badge/offline%20tests-305-brightgreen)
![deps](https://img.shields.io/badge/dependencies-stdlib%20only-success)
![license](https://img.shields.io/badge/license-MIT-blue)

---

## 1. 解决什么问题

跨境小卖家（1688 一件代发 → 国内货代 → TikTok 中转仓，店铺主营家居用品）铺 150 款商品 ≈ **40 小时纯体力活**：六个环节每个都要人工点几十下，而真正的大头是逐张判定并清理 1688 素材里的中文 B2B 招商图。

这个项目的回答是把**判断与执行拆开**：

- **Agent（LLM）**：任务理解、整图质量判定、异常分析、流程编排决策；
- **SKILL.md**：把业务规程写成 Agent 可执行的确定性规则——阶段输入/输出/成功条件、哪些自动、哪些必须人工授权；
- **Python CLI（48 个脚本，核心零第三方依赖）**：闸门校验、定价计算、报文构造、写操作、回查——所有确定性逻辑；
- **ERP 接口**：真实数据采集与发布，未公开内部接口 + 官方 OAuth/MCP 通道分层使用；
- **人工接管**：登录、货代确认、发布授权、低置信度判定。

## 2. 真实成果（不夸大）

| 成果 | 证据 |
|---|---|
| 全链路接口化闭环：采集→认领→编辑→发布→四路验证，TH/MY 多品真实在售（27 款平台侧 `ACTIVATE`） | `references/replay-log.md` 逐批记录；`erp_data` 本地快照 |
| 单品全链路状态机 + 断点续跑 + 两个人工检查点（CP1 edit / CP2 图片） | `scripts/publish_one.py`，13 阶段 |
| 一条命令铺货 N 品（波次预检→模板编辑→图片闸门→三闸→可选发布） | `scripts/batch_listing.py`，模板品目标 ≤15 分钟/品 |
| 五道发布前硬闸（选品/价格/图片/汉字与属性/商品卡质量），退出码 1 禁止发布 | `select_gate` / `price_gate` / `image_gate verify` / `prepublish_guard` / `listing_quality_gate` |
| 失败路由：发布 fail 原文 → 15 条实测规则（诊断+修复命令+证据），黄金集 23 案例回测 100% 命中；未命中转 LLM 诊断后回填（数据飞轮） | `failure_router.py` + `config/failure_rules.json` |
| 任务级可观测：task_id 全阶段轨迹（JSONL、写盘前统一脱敏）+ 阶段耗时账本 | `agentlog.py` / `timing.py`，2026-11-27 接入 |
| 可靠性：写操作超时=结果未知禁止盲重试（先回查）、读操作指数退避+抖动 ≤3 次、任务级进程锁 | `erp_client._post` / `tasklock.py` + `tests/test_fault_recovery.py`（10 类故障场景 Mock） |
| 离线回归 305 项（unit + integration + 故障场景 + 安全卫生），不联网约 10s | `python3 tests/run.py` |
| 定价全成本模型（佣金/手续费/物流藏价/退款预留/净利目标反推 markup 与采购价上限） | `pricing_calc.py --check/--limits` |

**明确不是**：规模化无人值守运营。当前站点主攻 MY（TH/VN 存量维护），成交/出单/入池仍处冷启动验证期，未回填的经营指标在 `docs/evaluation.md` 中如实标「待采集」。

## 3. 系统架构

```
用户输入 ──► Agent Runtime ──► SKILL.md（执行规程） ──► 编排层
            (batch_listing / publish_one / precheck)
                         │
        ┌────────────────┴────────────────┐
        ▼                                 ▼
  AI 判断层                        Python 工具执行层
  整图判定/异常分析/               业务规则层（纯本地可回归）      Provider 适配层
  低置信转人工                     select_gate·price_gate·        erp_client·site_goods·
                                  image_gate·prepublish_guard·   erp_upload·kdzs_fastmoss·
                                  pricing_calc·failure_router…   aidge_http·bailian·linkfox
        │                                 │            │
        └──────────► ERP API / 外部业务系统 ◄───────────┘
                         │
              任务状态/轨迹/耗时  ──►  Agent 继续推进 / failure_router 诊断 / 人工接管
```

- 详细架构图（Mermaid：总体、发布流程、异常恢复）见 [`docs/architecture.md`](docs/architecture.md)。
- **两层解耦**：业务规则层只吃本地快照 + `config/*.yaml` 阈值，不感知具体 ERP；适配层负责协议/鉴权/报文。换平台重写适配层，闸门/定价/SKU 校验/失败路由是可复用资产。
- 判断（选什么、改哪张图、多少钱）归模型与规则；执行（发什么请求、怎么重试、怎么落盘）归确定性代码。两者只通过 CLI 契约耦合。

## 4. 核心技术设计

**① 按「错了谁承担后果」划自动化边界**（不是按「能不能自动化」）：

| 分层 | 判据 | 例 |
|---|---|---|
| AI 自动 | 错了可回滚/无损失 | 取数、扫描、判定回填、算价、dry-run 构造 |
| 硬红线自动阻断 | 规则可穷举且错了有罚金 | 品牌侵权、带电液体、跨类目、库存超限、汉字残留 |
| 人工授权 | 写线上且部分不可逆 | 认领、edit --save、发布提交（CP1/CP2 检查点） |
| 人工接管 | 规则穷举不了 | 登录、货代确认、低置信图判定 |

**② 统一写操作流**：`dry-run → 数据预览 → 风险校验 → 人工授权（绑定 task_id、一次性）→ 执行 → 回查业务结果 → 更新状态`。「接口 `success:true` 只代表进队列」是所有回查设计的出发点。

**③ 超时三分法**：请求成功 / 请求失败 / **结果未知**。写操作超时或 5xx 一律标 `unknown`，禁止重发，先回查 items/ttl-list/logs/平台侧 ACTIVATE；读操作才允许指数退避+抖动重试（≤3 次，429/707 单独策略，703/业务码零重试）。

**④ 图片闸门**（本项目差异化最大的部分）：三通道扫描（主图/详情长图/SKU 规格图）→ 多模态**整图**判定（L0 整删 / L1 擦字 / L2 功能字翻译 / L3 保留）→ description 整段重建 → `verify` 发布前硬闸（判定表必须覆盖全部当前图，缺判定 FAIL）。脚本不假装能看图，OCR 关键字表只做文本兜底。

**⑤ 失败路由数据飞轮**：`failure_router` 把 fail 原文路由到已验证规则并给修复命令；路由不到的转 LLM 读踩坑档案诊断，确诊后回填规则表 + golden set——规则只收实测案例，不收推测。

## 5. 工程可靠性与安全机制

| 机制 | 一句话 |
|---|---|
| 断点续跑 | 每个 offer 一份状态文件（阶段指针 + done_stages + attempts），进程中断后 `next` 从原阶段恢复，成功阶段不重跑 |
| 并发互斥 | `tasklock.py` fcntl 任务锁，双进程跑同一任务后者立即失败 |
| 幂等 | 认领/发布前查重（平台侧 ACTIVATE、已认领回查）；重发需 `--force`；防双链接自我竞争 |
| 任务级日志 | `agentlog.py` JSONL 轨迹：task_id/event_id/attempt/stage/error_type/duration_ms，`trace/stats/failures` 查询 CLI |
| 脱敏 | 统一过滤器写盘前必经：JWT/Cookie/AccessKey/手机号/邮箱；`set_secret` 精确登记会话密钥；仓库卫生扫描进测试（明文凭证必红） |
| 凭证边界 | 店铺 ID/token 全走 `.env`；代码占位符 + `require_shop` fail-fast；历史泄露披露与处置见 `docs/security.md` |
| 离线 CI | GitHub Actions：305 项测试 + 定价自洽 + 接口契约漂移自检（不需要网络/token） |

## 6. 测试与评估

四类指标分开、不能互替（口径详见 [`docs/evaluation.md`](docs/evaluation.md)）：

| 类型 | 载体 | 结果（2026-11-27 实测） |
|---|---|---|
| Unit + Integration | `python3 tests/run.py`（Mock HTTP，不触真实店铺） | 305 项，通过 304 / 跳过 1 / 失败 0，约 10s |
| Agent 评估（失败归因） | `failure_router.py backtest` | 黄金集 23/23 命中；LLM Tier2 诊断准确率未量化（如实标注） |
| 真实业务 | 本地快照统计（`generate_evaluation_report.py`） | 发布日志按 itemId 去重：success 149 / fail 5 / processing 10（口径=ERP 受理，≠平台在售）；平台侧 ACTIVATE 27 |
| 故障场景 | `tests/test_fault_recovery.py` | 10 类：采集中断续跑、认领超时回查、发布超时但已发、429/5xx、703、校验失败、重复执行、并发锁、CP 接管恢复 |

自动报告：`python3 scripts/generate_evaluation_report.py --out docs/evaluation_generated.md`。

## 7. 环境与使用

前置：Python 3.8+（核心全标准库；`erp_token_grab` 可选依赖 cramjam）、本人已授权的同款跨境 ERP 账号 + TikTok Shop 跨境店。

```bash
git clone https://github.com/jonji886/tiktok-light-listing.git && cd tiktok-light-listing

# 1) 配置（店铺标识/凭证全部走 .env，仓库不存真实值）
cp .env.example .env && set -a; source .env; set +a

# 2) 登录态（浏览器登录 ERP 后自动提取，不手工复制）
python3 scripts/erp_token_grab.py --save --check

# 3) 开工自检
python3 scripts/health_check.py --skip-net
python3 scripts/pricing_calc.py --check
python3 tests/run.py

# 4) 只读冒烟
python3 scripts/erp_client.py token --check
python3 scripts/erp_client.py ttl-list --region MY --size 20
```

Agent 用法：把本目录作为 Skill 挂载（WorkBuddy 或兼容 SKILL.md 的运行时），按 `SKILL.md` 的 Step 0–7 规程执行。典型一条命令铺货（默认止步闸门，发布需显式 `--publish`）：

```bash
python3 scripts/batch_listing.py --target 10 --site MY \
    --waves pool.csv,pool2.csv --run-tag 20261201_my10
```

任务轨迹与失败诊断：

```bash
python3 scripts/agentlog.py trace --task-id task_<offerId>   # 完整执行轨迹
python3 scripts/agentlog.py stats --by error_type            # 异常类型统计
python3 scripts/failure_router.py classify --text "<fail 原文>"
```

## 8. 已知限制与合规说明

- **依赖未公开内部 ERP 接口**：无契约无版本，字段随时可变；`health_check` 只能发现漂移不能预防。接口异常的第一处置是读错误原文，不是重试。
- **不做规模化承诺**：写操作必须人工点名清单；登录/货代/低置信判定永远人工。
- **图片判定依赖模型目视**：文本扫描只能兜底 HTML 节点，图内中文艺术字/印章字必须多模态判定；AI 生成小语种文字的幻觉字风险以逐张验收条款约束。
- **成交数据仍在冷启动期**：入池率是当前主 KPI，出单为结果指标，样本不足以支撑转化结论。
- **历史 git 记录含店铺标识**（sellerId/店铺名，非可登录凭证）：工作区与代码已占位化，历史改写需所有者决策，见 `docs/security.md` §1。
- **合规**：仅用于操作者拥有或已获书面授权的 ERP 账号与店铺；不含且拒绝任何绕过平台风控的手段（验证码识别/指纹伪装/代理池）；面向未公开接口的自动化存在违反服务条款风险，使用前自行确认平台条款。公开平台域名与类目 ID 保留原值以便核查。

## 9. 目录结构

```
├── SKILL.md                 # Agent 执行规程（动作分层 + Step 0–7 + 人工授权点）——运行契约
├── README.md
├── docs/
│   ├── architecture.md          # Mermaid：总体架构 / 发布流程 / 异常恢复 + 模块职责
│   ├── evaluation.md            # 四类测试口径 + 指标定义 + 禁止事项
│   ├── evaluation_generated.md  # 自动生成的真实数据报告
│   └── security.md              # 凭证边界 / 脱敏 / 写保护 / 泄露披露
├── config/                  # 阈值全外置：selection/pricing/image_gate/failure_rules(15条)…
├── scripts/                 # 业务规则层 + Provider 适配层 + 编排（职责表见 docs/architecture.md §2）
├── tests/                   # 离线回归 run.py：unit/integration/故障场景/安全卫生
├── references/              # 选品方法论(9章)/接口踩坑(14章)/批次复盘/成果矩阵/接口契约
└── samples/ · .github/workflows/ci.yml
```

## 10. 更新记录

**2026-11-27（工程化完善）**
- 安全整改：`_archive` 快照真实 sellerId/店铺名/`sourceSellerId` 全部占位化；`ERP_BASE_URL`/`ERP_SOURCE_SELLER_ID` 等改为 `.env` 读取 + fail-fast（兑现 `.env.example` 承诺）；新增仓库卫生扫描测试（明文凭证/JWT/店铺 ID 进测试必红）。git 历史未改写，披露见 docs/security.md。
- 可观测性：新增 `agentlog.py`（task_id 统一、event_id/attempt、JSONL 轨迹、写盘前统一脱敏、异常十类归一、trace/stats/failures 查询）；`publish_one` 接入阶段事件与 human_approve 审计；`timing.py` 继续作耗时账本。
- 可靠性：`erp_client._post` 重写为「网络/HTTP/业务」三层差异化策略——写=单次机会+超时记 unknown，读=指数退避+抖动 ≤3 次，707/429 单独通道，703/业务码零重试；超时配置集中 env 化；新增 `tasklock.py` 任务级进程锁接入 `do_next`。
- 测试：新增 46 项（observability / fault_recovery 10 故障场景 / security 卫生），总数 259→305。
- 文档：README 重写为作品视图并同步真实口径（图片闸门为**三通道**、发布前**五道闸**、四路验证、发布默认人工）；新增 docs/architecture.md、docs/evaluation.md、docs/security.md、`generate_evaluation_report.py`。原「工程笔记」章节并入 references（其中「保存与发布互斥」旧结论已被 2026-11-11 实测推翻，随旧版删除）。

**2026-11-25～26（流水线化）**：`batch_listing.py` 一条命令铺货、`publish_one` 状态机、`failure_router` + 黄金集、`timing` 账本、图片判定 A/B/C 提效（judge_cache/OCR 缓存/通道分级）、MY 主攻转型。更早各批演进见 `references/replay-log.md`，不再在此罗列。

## License

MIT —— 见 [LICENSE](LICENSE)。
