# 系统架构

> 本文件所有模块、命令、文件路径均对应仓库内真实代码（2026-11-27 核对）。
> 运行时规程的权威来源是 `SKILL.md`；本文只描述结构，不复述业务规则。

## 1. 系统总体架构

运行环境是 WorkBuddy（或任何支持 `SKILL.md` 的 Agent Runtime）。核心设计：**AI 判断与确定性程序执行分离**，两层只通过 CLI 参数契约耦合。

```mermaid
flowchart TD
    U[用户/操作者<br/>点名清单、确认价格表、人工接管] --> AR[Agent Runtime<br/>WorkBuddy]
    AR -- 加载执行规程 --> SK[SKILL.md<br/>动作分层 + 阶段输入输出 + 人工授权点]
    SK --> ORCH[编排层<br/>batch_listing.py 一条命令铺货<br/>publish_one.py 单品状态机<br/>precheck.py 批量预检]
    ORCH --> AIL[AI 判断层<br/>多模态整图判定 review.json<br/>异常分析 Tier2 / 同款判定 / 低置信转人工]
    ORCH --> CLI[Python CLI 确定性执行层]
    subgraph RULE[业务规则层 - 纯本地、离线可回归]
        CLI --> G1[select_gate / route_gate / price_gate<br/>image_gate / prepublish_guard<br/>listing_quality_gate]
        CLI --> G2[pricing_calc / ttl_edit / set_price<br/>fix_sku / category_template / i18n]
        CLI --> G3[failure_router 规则路由<br/>backflow 数据回流]
    end
    subgraph PROV[Provider 适配层 - 协议/鉴权/报文/落盘]
        CLI --> P1[erp_client / site_goods / erp_upload<br/>erp_token_grab / kdzs_fastmoss]
        CLI --> P2[aidge_http 图片修复 / bailian_sourcing<br/>aibuy_rank_client / linkfox_oauth MCP 两段式]
    end
    P1 --> ERP[跨境 ERP SaaS 未公开内部接口<br/>erp.kuaidizs.cn]
    P2 --> EXT[外部服务: FastMoss透传 / 1688 / Aidge / LinkFox MCP]
    ERP --> ST[任务状态与观测<br/>_run/pipeline/&lt;offerId&gt;.json 状态机<br/>_run/timing/*.jsonl 耗时账本<br/>_run/trace/&lt;task_id&gt;.jsonl 任务轨迹]
    ST --> ORCH
    AIL --> ORCH
    G1 -- 退出码1 硬阻断 --> STOP[禁止下一步写操作]
    ORCH -- CP1/CP2 检查点 --> U
```

## 2. 核心模块职责

| 模块 | 真实载体 | 职责边界 |
|---|---|---|
| Agent Runtime | WorkBuddy 会话 | 任务理解、编排决策、多模态判定、异常分析（Tier2）；不直接触碰 HTTP 报文 |
| Skill Layer | `SKILL.md` + `references/` + `config/*.yaml` | Agent 执行规程：阶段顺序、输入输出、成功条件、人工授权点、错误码表；阈值全部外置 |
| Workflow / Orchestrator | `scripts/batch_listing.py`（波次级）、`scripts/publish_one.py`（单品状态机）、`scripts/precheck.py`（批量预检）、`scripts/publish_one.py` 内 `do_next` | 阶段推进、检查点闸、断点续跑、失败保状态 |
| AI Model | Runtime 内多模态模型 | 整图判定回填 `review.json`、低置信转人工、未路由失败的诊断；**不生成价格/类目等事实数据**（拿不到就留空） |
| Python CLI | `scripts/` 全部入口 | 确定性执行：闸门、定价、编辑、发布、回查、日志 |
| Business Rules | `select_gate` `price_gate` `image_gate` `prepublish_guard` `listing_quality_gate` `route_gate` `pricing_calc` `failure_router` | 校验/拦截/分类；只吃本地快照+config，不感知是哪个 ERP |
| ERP Client | `erp_client.py`（`_post` 统一入口：鉴权头、限流间隔、超时、写操作识别）+ `site_goods.py`（平台侧在售）+ `erp_upload.py`（OSS 直传） | 协议/鉴权/报文/落盘；换平台只重写这一层 |
| State Management | `erp_data/_run/pipeline/<offerId>.json`（阶段+approved+edit 参数+attempts）+ `tasklock.py`（fcntl 进程锁）+ `agentlog.set_current_task` | 断点恢复、幂等复用已完成阶段、并发互斥 |
| Logging / Observability | `timing.py`/`timing_report.py`（阶段耗时 JSONL）+ `agentlog.py`（任务轨迹 JSONL，task_id/event_id/attempt/error_type，写盘前统一脱敏） | 事后定位「哪个商品、哪一步、哪次尝试、什么错误、多久」 |
| Human Approval | `publish_one approve --which edit/images`（CP1/CP2）+ `batch_listing` 默认止步闸门 + `--publish` 显式开关 + 各写脚本 `--dry-run` 默认 | 授权与具体任务绑定、只放行一次、不落盘即不执行 |

## 3. 商品发布流程图

对应 `publish_one.py` 的 `STAGES`（13 阶段）与 `batch_listing.py` 的波次编排：

```mermaid
flowchart TD
    IN[候选商品 offerId<br/>已过 select_gate + route_gate 硬闸] --> COL[collect 采集进公共采集箱]
    COL --> CLA[claim 认领到 TikTok 站点店铺<br/>超时→回查采集箱，不盲目重试]
    CLA --> DET[detail 拉草稿落盘]
    DET --> ED1[edit_dry 模板/定价/SKU 一次生成<br/>dry-run 摘要]
    ED1 --> CP1{CP1 人工授权<br/>approve --which edit}
    CP1 -- 未确认 --> CP1
    CP1 -- 确认 --> ED2[edit_save 写回 ERP]
    ED2 --> DET2[重读 detail 刷新异常标签]
    DET2 --> SCAN[image_gate scan 三通道下图<br/>主图/详情长图/SKU图]
    SCAN --> REV[多模态整图判定 L0-L3<br/>低置信度→CP2 人工目视]
    REV --> AP[apply 重建 description + uri_map<br/>verify 退出码0 才放行]
    AP --> GUARD[三闸: price_gate → prepublish_guard --refresh<br/>→ listing_quality_gate]
    GUARD -- 任一退出码1 --> BLOCK[阻断，转 failure_router 诊断]
    GUARD -- 全过 --> PUB[ttl-publish 提交发布队列<br/>真实 weight/dims；已ACTIVATE跳过]
    PUB --> WAIT[wait_for 轮询 logs 终态<br/>success=True 只代表入队]
    WAIT --> V4[四路验证: ERP日志 / 采集箱状态<br/>铺货配额 / 后台在售数*]
    V4 --> OK[done 归档 → backflow 排 T+2/T+7/T+20]
    V4 -- fail --> FR[failure_router classify<br/>命中规则→修复计划; 未命中→Tier2 LLM诊断→回填规则]
    FR --> GUARD
    BLOCK --> FR
    REV -- 低置信 --> HUMAN[人工接管: 图判定/货代确认/登录]
    HUMAN --> REV
    style V4 fill:#e8f5e9
    style CP1 fill:#fff3e0
    style HUMAN fill:#fce4ec
```

\* 第四路「TikTok 后台在售数」无接口，人工核验，文档中不得标 VERIFIED。

## 4. 异常恢复流程图

对应 `agentlog.classify` + `erp_client._post` 重试策略 + `failure_router`：

```mermaid
flowchart TD
    E[异常发生<br/>HTTP 4xx/5xx / 业务码 / 闸门退出码1 / 超时] --> LOG[trace 落一条 failed/unknown 事件<br/>error_type + attempt + task_id]
    LOG --> CLS{classify 异常分类}
    CLS -- authentication_error 703 --> AUTH[终止 + 人工刷新登录态<br/>erp_token_grab --save]
    CLS -- validation/business_rule --> FIX[终止，不重试<br/>修复数据: failure_router 出诊断+修复命令]
    CLS -- rate_limit 707/429 --> RL[读: 10s 退避重试 ≤3 次<br/>写: 交由调用方稍后再提]
    CLS -- timeout/network/5xx --> WR{读还是写?}
    WR -- 读 --> RETRY[指数退避+抖动重试 ≤3 次<br/>失败则终止报 network_error]
    WR -- 写 --> UNK[status=unknown 禁止盲重发]
    UNK --> VERIFY[回查业务实际状态<br/>items已认领 / ttl-list / logs / site_goods status]
    VERIFY -- 已生效 --> SKIP[复用结果，阶段推进<br/>幂等：成功阶段不重跑]
    VERIFY -- 未生效 --> RESUB[带原 task_id 重提交<br/>attempt+1；重发须 --force]
    VERIFY -- 查不清 --> MAN[人工核查]
    FIX --> RESUME[修完重跑 publish_one next<br/>done_stages 跳过已完成阶段]
    RESUB --> DONE[最终状态确认<br/>四路验证 + trace 完整可回放]
    SKIP --> DONE
    RESUME --> DONE
    MAN --> DONE
```

## 5. 可靠性机制落点（代码索引）

| 机制 | 实现位置 | 行为 |
|---|---|---|
| 幂等·写前查重 | `publish_one.stage_publish`（先查平台侧 ACTIVATE）、`cmd_start`（在售货源拒绝重复 start）、`stage_claim`（超时回查 items 已认领） | 不产生第二份草稿/重复认领 |
| 幂等·阶段复用 | `do_next` 的 `done_stages` + `stage` 指针 + `detail` 回跳修正 | 恢复时已成功阶段不重跑 |
| 超时集中配置 | `erp_client.HTTP_TIMEOUT`（默认 30s）、`site_goods.SITE_TIMEOUT`（180s）、`publish_one.run(timeout=240)`，均可 env 覆盖 | 无无限等待；连接/读取拆分受标准库限制，见 docs/security.md |
| 重试策略 | `erp_client._post`：写=单次机会；读=429/707/超时/网络/5xx 指数退避+抖动 ≤3 次 | 不对所有错误统一重试；703/业务码零重试 |
| 结果未知 | `status_unknown_write` → trace 记 `status:"unknown"` | 超时≠未执行，先回查（SKILL 硬纪律代码化） |
| 并发互斥 | `tasklock.py`（fcntl 独占锁），`do_next` 拿不到锁退出码 7 | 双进程不会互相覆盖 state |
| 断点续跑 | `erp_data/_run/pipeline/<offerId>.json` + `batch_listing --claimed` 续跑 | 进程中断后从当前 stage 恢复 |
| 任务轨迹 | `agentlog.py` JSONL（append-only，多进程安全）+ `timing.py` | task_id 可完整还原执行过程 |

已知边界（不夸大）：
- 人工授权不可无限期复用：`approve --which edit` 仅放行一次 `edit_save`，阶段推进后需重新确认；但「授权不跨任务复用」目前靠状态机 stage 指针保证，无独立签名机制。
- `wait` 阶段轮询到 `processing` 超时后任务停在原阶段，需人工决定继续 `next` 还是介入——未自动升级 unknown。
- 文件锁只覆盖 `publish_one.do_next` 入口；直接手打单条 `erp_client ttl-claim` 不经过锁（历史遗留用法，纪律上应避免）。
