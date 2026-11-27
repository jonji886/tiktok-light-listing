# 安全与发布保护

## 1. 凭证与敏感数据边界

| 类别 | 存放 | 是否入库 |
|---|---|---|
| ERP 登录 JWT | `.env` 的 `ERP_TOKEN` 或 `scripts/erp_token.txt` | 否（`.gitignore` 含 `**/erp_token.txt`、`**/.env` 全目录兜底） |
| 1688 浏览器 Cookie | `scripts/ali_cookie.txt` | 否（`**/ali_cookie.txt`） |
| 店铺 sellerId / 店铺名 / sourceSellerId | `.env`（`*_SELLER_ID` / `*_SHOP_NICK` / `ERP_SOURCE_SELLER_ID`） | 否；代码只留占位符，`require_shop()` fail-fast 拦截占位符进入写接口 |
| 阿里云 AccessKey / DASHSCOPE key | `.env` | 否 |
| 任务轨迹/耗时日志 | `erp_data/_run/{trace,timing,pipeline}/` | 否（`erp_data/_run/` 整体 gitignore） |
| 运行时快照（采集箱/日志 CSV） | `erp_data/*` | 否，白名单只放行类目树、README、候选清单与历史归档接口快照 |

回归测试 `tests/test_security_repo_hygiene.py` 每次运行扫描**全部 git 跟踪文件**：JWT 模式、真实 sellerId 枚举、硬编码 sourceSellerId、`.env.example` 敏感键空值、`.gitignore` 凭证覆盖、trace 目录不入库。

### ⚠ 历史泄露与处置（如实披露）

- 2026-11-27 审计确认：commit `506dd10` 及之前入库的 `erp_data/_archive/2026-10-29_全链路破解测试/` 快照含**真实店铺标识**（JingShanShop 系列 sellerId、公司名）与 ERP `sourceSellerId`。工作区文件已占位化（`<SELLER_MY>` 等），代码与文档中的明文值已清除。
- **但普通提交不改写 git 历史**：GitHub 远端历史中这些值仍可检出。这些是店铺 ID/名称而非可复用凭证（登录仍需 JWT，JWT 从未入库——历史扫描 `eyJ` 模式 0 命中），风险为**隐私暴露与可关联性**，不构成账号可被直接访问。
- 处置建议（需仓库所有者决策，均有代价）：
  1. **改写历史后强推**（`filter-repo` + `push --force`）：彻底，但破坏他人克隆；本次未执行。
  2. 维持现状：接受店铺标识在历史中公开（ID 本身不可登录）。
  3. 若认为该店铺矩阵敏感（同一 JingShanShop 前缀横跨 PH/SG/MY/VN/TH/MX/BR/TW 等站点可被关联），可考虑在 ERP/TikTok 侧更换店铺显示名。
- 本次整改保证：**新增泄露必红**——卫生扫描进 `tests/run.py`，CI 自动执行。

## 2. 日志脱敏（统一过滤器，不在业务模块重复实现）

`scripts/agentlog.py::redact()` 是所有轨迹事件的写盘前必经步骤：

- 模式规则：JWT 三段式、`qnquerystring/Authorization/Cookie` 头的值、`token=/secret=/password=` 字段值、阿里云 `LTAI*` AccessKey、`sk-*` API key、中国手机号（11 位精确边界，不误伤 12/13/19 位商品 ID）、邮箱。
- 精确密钥登记：`erp_client`/`site_goods` 每次请求前 `agentlog.set_secret(tok)`，JWT 原文在任何日志/错误 meta 中出现即整串替换 `<secret-hidden>`。
- 纪律：禁止打印完整请求 Header（`_post` 错误输出只含 path 与脱敏后的 err 摘要 ≤300 字符）；异常堆栈不携带 token（token 从不进入异常消息构造路径）。
- 测试：`TestRedaction` + `test_jwt_never_in_trace` 钉死。

## 3. 写操作统一执行流程

```
dry-run（默认） → 数据预览（摘要/diff） → 风险校验（闸门退出码0）
→ 人工授权（与 task_id 绑定，一次性） → 执行写操作
→ 回查业务结果（不信任 success:true） → 更新任务状态
```

| 保护 | 实现 |
|---|---|
| 未授权不能执行高风险写 | `publish_one` CP1/CP2 检查点（`do_next` 阶段闸 + trace `human_approve` 事件）；`batch_listing` 默认止步闸门，发布需显式 `--publish` |
| 校验失败不能绕过 | 四闸（price_gate / image_gate verify / prepublish_guard / listing_quality_gate）退出码 1 = 链路拒绝推进；`image_gate apply --save` 要求 review 判定表全覆盖当前图，`NO_REVIEW` FAIL；`--no-require-review` 已被纪律禁用 |
| 授权与任务绑定、不无限复用 | `approved` 记录在单品状态文件内；`approve --which edit` 只放行紧接的一次 `edit_save`；阶段推进后写操作前不再自动复用 |
| 发布结果未知不重复提交 | `WRITE_PATHS` 集中判定；写请求超时/5xx → trace `status:"unknown"` + 非零退出，调用方（`stage_claim`/`stage_publish`）先回查（items 已认领 / ttl-list / logs / `site_goods status`）再决定；重发必须 `--force`，且 `stage_publish` 先查 ACTIVATE 跳过在售品 |
| 重复发布保护 | `cmd_start` 对已在售货源直接拒绝；`filterAlreadyPublish` + `--force` 语义在文档与代码注释双侧固化 |
| 可追溯 | 关键操作全部落 `task_id` 轨迹：`python3 scripts/agentlog.py trace --task-id task_<offerId>` |

## 4. 第三方接口授权边界

- **ERP 接口为未公开内部 Web 接口**（登录态 JWT 重放，无官方开放 API 契约）：本项目保留技术兼容，但——
  - 不将其描述为官方 API（README/文档统一措辞「未公开内部接口」）；
  - 接口无版本承诺，`health_check.py` 的前端版本指纹 + 依赖哈希漂移检测负责**发现变更**，不能预防变更；
  - 仅限操作者本人已登录会话的数据域，sellerId 越界查询不存在（所有列表接口按登录态过滤）。
- **1688 数据**：官方 OAuth 通道（LinkFox `alibaba1688/sku`，用户本人授权店铺）为主；详情页抓取受 baxia 风控，授权过期不绕过验证页。
- **LinkFox / 百炼 / Aidge**：各自 API key/账户计费，凭证走 `.env`；LinkFox 调用只能经 MCP 注入凭证的通道，脚本不直连网关。
- **TikTok 罗盘/机会中心**：无官方直连能力，仅人工导出/抄录后离线解析（`backflow_import.py` 只读本地 xlsx），不碰 cookie/签名/设备指纹，**不使用任何风控绕过手段**（验证码识别、指纹伪装、代理池为硬性禁区）。

## 5. 超时实现说明（诚实边界）

`urllib` 单 `timeout` 参数为整体 socket 超时，连接与读取不可拆分（标准库限制）。连接超时由系统 TCP 栈兜底（通常 <75s 报错）。如需严格 connect/read 拆分需换 socket 层实现，当前收益不足以引入该复杂度——如实标注，不假称已实现。
