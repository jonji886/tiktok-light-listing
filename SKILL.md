---
name: tiktok-light-listing
description: "TikTok Shop 跨境店铺货（快递助手ERP跨境版，一件代发模式）：选品(免token拉FastMoss单品数据→官方商品机会中心信号先过opportunity_intake.py判读闸门/榜单取需求信号→1688找货→红线与供应链门槛→履约/内容力/实净利/站内竞争四维评分+价格带校验出清单)→1688链接采集→公共采集箱→认领到TikTok站点店铺(主攻MY，TH/VN暂缓)→二次编辑(批量公式改价/图片擦文字翻译/首图白底/修SKU规格名与库存)→发布→四路验证→记录统计→T+7/T+20数据回流。全链路接口化，仅人工接管登录与货代确认。已跑通真实闭环(2026-11-12 泰国店在售；2026-11-19 MY 首品经机会中心路线 20 分钟到在售)。仅适用于 erp.kuaidizs.cn + 本人名下 MY/TH/VN 三个店铺(2026-11-19起主攻MY，TH/VN存量维护不投新品)，店铺主营类目为家居用品(选品仅限该类目)。不做其他ERP/其他平台。"
---

# TikTok Shop 跨境铺货（选品 → 1688采集 → 认领 → 编辑图片/翻译/定价 → 发布）

## Overview

在快递助手ERP跨境版内，把1688货源铺到 TikTok Shop 东南亚店（店铺 TH/VN/MY 均已开，**2026-11-19 用户决定：主攻 MY（马来西亚），TH/VN 暂缓**——TH/VN 存量在售链接只做维护与数据回流，不再投入新品；strategy 第七章的"破零期主攻 VN"结论已被此决定覆盖）。店铺主营类目为**家居用品**。
MY 站已知点状态（2026-11-19 首品闭环后更新）：
① **ERP 马来语自动翻译 = VERIFIED 可用**（首品认领后标题自动马来语化，如 "Apron separuh panjang laris Amazon..."；SKU 规格名"颜色"自动译 "Warna"）。
   ⚠ 但自动译文质量不可控（首品标题混入 "laris Amazon" 竞品平台词、"celemek kerja rumah" 与 "apron" 混用）——
   发布前必须目视标题，必要时用 `i18n.py` 查表替换；`config/i18n/ms.csv` 从"空表待验证"转为"按需积累替换词条"。
   详情图英文文字（POCKETS/STRAP 等）在 MY 可直接保留（英语通用），只删中文图。
② MY 免佣期到期日仍未核（后台为准，到期连锁改 `pricing.yaml`，见其 MY 段注释）。
③ 1688 授权过期**不影响 collect/claim/publish**（2026-11-19 实测：授权显示已过期仍全链路成功）——仅影响 selection_intake 抓详情页图片。

链路分6段：
0. **选品**：FastMoss 单品数据（免token）+ 官方后台取需求信号（**2026-11-20 优先级重排：P0=热门关键词收割 `opportunity_keywords.py`（周常，喂标题词根+pool 两表）、P0=在售品提报关联（每3天，流量入口）> P1=机会卡片路线（抄卡片→`opportunity_intake.py` 判读→`opportunity_to_sourcing.py` 1688 相似款，见 0.2④）**）→ 1688 找货 → 红线+供应链门槛 → 四维评分+价格带校验 → **按发货城市聚簇 + 查货代覆盖（TIKTOK 标签）** → 出候选清单
1. **采集**：1688 offerId → ERP公共采集箱（复用旧接口）
2. **认领**：公共采集箱 → 认领到指定TikTok站点店铺（MY 主攻；TH/VN 存量）
3. **编辑**：TikTok采集箱里逐品二次编辑——标题已自动翻译，图片要处理（擦广告字/翻译功能字/首图白底），SKU 规格名与库存要过平台校验，价格用**批量公式改价**
4. **发布**：提交到TikTok发布队列（附真实重量/尺寸）→ **四路验证**
5. **统计**：发布记录回查 + T+7/T+20 真实曝光/点击/履约数据回流

**本版本的模型前提（2026-11-11 重构，与前版不同）**：新人 · 无自有货源 · 1688/拼多多单件下单 → 国内货代 → TikTok 中转仓。
由此产生的三条硬结论，写在 `references/selection-strategy.md` 第一至三章，执行前必读：
- 官方时效为 **48h 打包 + 3 个自然日到仓扫描**（不是 96 小时）→ **判据是「发货地到货代仓的快递天数」，不是「同省」**：≤1 天绿档、2 天卡死、≥3 天必超（见 Step 0.0）
- 国内段成本是 **单件快递 5 元 + 货代 2 元**（不是批量均摊 1 元），采购价上限由价格带反推，用 `scripts/pricing_calc.py` 校验
- **暂不开达人佣金**（2026-11-11 决定），只走货架场；`creator` 档在配置里 `enabled: false` 备查。
  代价（放弃 SKPP 资格）与三个重估触发条件已显式记录在 strategy §8.4 与 `selection.yaml → channel_tradeoff`，
  别再当成"没有成本的选择"。

**本版已达成（2026-11-12 首单闭环，可作为标准剧本）**：真空压缩袋经全接口链路铺到 **TH 店**，
TikTok 后台确认为「在售」，฿129–199，3 SKU。链路 = 采集 → 认领 → `set_price` 公式改价 → `fix_sku` 修规格名/库存
→ `ttl-publish` → **四路验证**（ERP 日志 / 采集箱状态 / 铺货配额 / 店铺后台在售数）。

**MY 标准剧本 v2（2026-11-20 提效改造后；同类目模板品目标 ≤15 分钟/品）**：

> v2 的实质：把「每品现场决策」压成「模板一次固化、每品直接套用」。
> 三大新工具 = 类目模板表（消灭查类目/定重量档/写描述 ≈12 分）+ 款型主图预检（消灭图片闸门中途换款 ≈8 分）
> + ttl_edit 一次到位（4 次手写 edit → 1 次）。首品走 v1 全流程，发布成功后 `category_template.py record` 沉淀模板。

机会卡片抄录 → `opportunity_intake.py` 判读 → `opportunity_to_sourcing.py`（三通道 bailian|linkfox|aibuy）→
`selection_intake.py image --no-detail`（成本/库存走 OAuth，详情页只补重量/主图组）→
**`linkfox_oauth.py scan → MCP sku 落盘 → apply`**（价格三源裁决已固化：详情页值与 OAuth 差>30% 自动覆盖+留痕）→
**`hero_main_images.py`（款型主图供给预检——选品期就定款型，禁止留到图片闸门中途换款）** →
`select_gate.py` 硬闸 → `erp_client.py collect → ttl-claim` →
**`category_template.py match`（命中模板）→ `ttl_edit.py --template <名> --keep-prefix <款型SKU前缀> --save`**
（一次 edit 到位：类目/重量/尺寸/SKU精简/公式定价/主图/描述渲染，默认 dry-run）→
`image_gate.py scan → apply --save`（模板品描述走 `category_template render`，占位未填=拒绝）→
**⚠ 顺序铁律：`scan` 必须在 `ttl_edit --save` 之后、且此后不再 `ttl_edit --save`**——edit 改写 detail
会让旧 review 判定整批作废（683 实测 apply 时报"21/24 失效"重扫重判）。mains/desc 定稿 → scan → 判定 → apply，
不得回环（`image_gate apply --save` 自身也会写回 detail，但它的 cleaned_detail 正是由这份 review 生成的，
属链路终点不算回环；此后如再 ttl_edit --save，判定表需重扫）。→
`prepublish_guard → price_gate`（子集隔离，历史脏草稿会拖累全量退出码）→ `erp_client.py ttl-publish --weight/--dims` →
四路验证 → `backflow.py sync`（2026-11-24 起已自动 glob 扫全部 `erp_data/**/tiktok_list*.csv` 落盘，
带 --out 也不漏新批次；旧「先落顶层 CSV 再 sync 否则假新增0」的规避步骤作废）。

**首品（无模板）仍走 v1**：多模态逐张判定 + 手写 edit 分步——发布成功后必跑
`python3 scripts/category_template.py record --ttl-id <id> --name <模板名> --aliases <词根>` 沉淀，
同类目第二件起走 v2。48h 证据拦截当前为 `off`（2026-11-20 13:18 用户指示），见「MY 首品闭环新增硬规则」第 1 条。

**凡是接口相关的坑，先查 `references/erp-pitfalls.md`**（body 结构、错误码、逆向方法、浏览器边界全在里面）。

## 适用范围

- **适用**：erp.kuaidizs.cn + **MY 店（`<MY_SELLER_ID>`，2026-11-19 起主攻）** + TH 店（`<TH_SELLER_ID>`，存量维护）+ VN 店（`<VN_SELLER_ID>`，存量维护）；sellerId/店铺名走 `.env`，仓库不存真实值
- **不适用**：其他ERP、TikTok自运营店、其他站点(PH/SG后续再加)、Shopee/Lazada

## 动作分层

- **AI自动**：FastMoss 单品数据（免token）、官方后台取需求信号、1688找货、四维评分、价格带校验（`pricing_calc.py`）、红线与供应链门槛自动剔除、**collect 前选品硬闸（`select_gate.py`，退出码 1 禁止采集）**、采集、认领、列表对账、价格计算、**批量公式改价**、**修 SKU 规格名/库存/类目/主图**、**两通道图片扫描与整图判定（多模态目视）**、**description 重建**、**图片闸门 `verify`**、保留图的擦字/翻译功能字/首图白底、发布提交、**四路验证**、记录统计
- **硬红线自动阻断**：品牌侵权/IP联名、液体化工、带电、食品保健医疗、宗教、易碎、活体、多规格价差超限、平台限售需认证、跨类目（复用shopee selection-rules + TikTok限售/类目约束）
- **人工接管**：ERP登录/短信验证码、**1688 授权（凭证仅约 10 小时有效，批次前必查）**、**货代覆盖确认（ERP「货代管理」按城市查、筛 TIKTOK 标签，无接口）**、供应商 48h 发货的书面确认、**整图判定低置信度时转人工**、图片处理结果抽检（前10个人工过一眼）、候选清单最终确认（写操作前）

## 代码两层解耦（可移植性边界）

- **业务规则层**（纯本地、确定性、可离线回归）：`select_gate` / `price_gate` / `image_gate` / `prepublish_guard` / `listing_quality_gate` / `route_gate` / `pricing_calc` / `failure_router` / `precheck` / `publish_one` / `backflow`。只吃本地快照与 `config/*.yaml` 阈值，不感知具体 ERP 或站点。
- **Provider 适配层**（协议/鉴权/报文/落盘，不含业务判断）：`erp_client` / `site_goods` / `erp_token_grab` / `erp_upload` / `kdzs_fastmoss` / `aibuy_rank_client` / `linkfox_oauth` / `aidge_http`。外部系统换平台时只重写这一层；闸门规则、定价模型、SKU 身份校验、失败路由属可复用资产。

## 执行前约定

- 登录态：`scripts/erp_token_grab.py --save` 自动取 JWT → `scripts/erp_token.txt`；接口头 `qnquerystring` + `appType:3`
- 接口为未公开内部接口：**报错先读 `失败原因`/`errorMessage` 原文，按 `references/erp-pitfalls.md` 的码表定位**，
  不要用同一份数据重试；确认是接口不可用时再回退浏览器操作
- 所有写操作(claim/edit/publish/del)仅对用户明确点名的清单执行；改价、修 SKU 一律先 `--dry-run`
- **标题/描述/规格名译名只查表**：`config/i18n/<lang>.csv`（`MY→ms.csv`、`TH→th.csv`、`VN→vi.csv`——
  MY 自动翻译已 VERIFIED 可用但质量不可控（首品混入 "laris Amazon"），ms.csv 按需积累替换词条、发布前目视标题），
  入口 `scripts/i18n.py`。加新品 = 加一行，不改代码；查不到返回 `None`，由调用方转人工或阻断。
  **禁止按 SKU 序号套模板**（2026-11-13 删除的旧实现会轮流贴「粉/蓝/灰/黑/白/绿」，与真实颜色无关）。
- **拿不到数据就写"拿不到"，不要用旧快照或推测值补位**
- **发布前 `image_gate.py verify` 退出码必须为 0**（退出码 1 = 图片闸门拦截，禁止 `ttl-publish`；
  详见 Step 4.3 / 5.1）。脚本不会看图，整图判定由多模态模型回填 `review.json`，未判定即拒绝 apply

## 复盘结论速查（动手前先读这 11 条；全文见 `references/replay-log.md`）

> **2026-11-24 MY5 批固化总闸（本批 6 小时里 ~4 小时是末端拦截的返工，已全部前移为确定性规则）**：
> ① `select_gate.py` 新增三列硬闸：`category_id` 必填且必须在家居树 600001 内（桌布跨类目教训）、
> `sku_attr_names_ascii`/`seller_sku_ascii`（ERP 不译属性名、货号即 seller_sku，oauth apply 自动产指纹，
> false=阻断并给 ttl_edit 修复命令；**oauth apply 现为 collect 前置必做**，scan 同时兜详情页 ≤¥1 假主价）。
> ② `ttl_edit.py` 扩 `--title/--attr-names/--sku-map/--sku-code` + 提交前 PAYLOAD_CJK 终检——
> [12052250] 汉字拒从此只能出现在闸门，不可能出现在发布回执。
> ③ 判定回填用 `batch_review.py`（sheet 目视结论→review.json 确定性映射），**verify 恢复硬要求，
> 禁止 --no-require-review**；新顺序定稿 scan→判定回填→ttl_edit --save（不带 --mains-file）→apply→verify。
> ④ `backflow.py sync` 自动 glob 扫全部 ttl_list 落盘（带 --out 不再假新增 0）。
> ⑤ `selection_intake.py image` 的 prefill 改按 offer_id 合并（同目录重跑不丢旧行）。
> ⑥ 全部脚本 ROOT 统一 realpath()（skill 目录是 symlink，save_to 分裂实测发生过）。

> **2026-11-18 破零三闸（最高优先级，先于一切铺货动作）**：本 skill 旧版 768 行只管「能不能发出去」，
> 零行管「发出去有没有人看」——这是 22 天 7 款零单的结构性根因。现补三道强制闸，**不跑等于没做破零**：
> ① 选品期 `select_gate.py` 的 **价格竞争力闸**（挂价 ≤ 站内同款中位价×1.05，详见 Step 0.5）；
> ② 发布前 `listing_quality_gate.py` 的 **商品卡质量闸**（标题词根/5 主图/属性/视频，详见 Step 5.1 第 0d 项）；
> ③ T+2 `backflow.py index` 的 **入池体检**（48h 强制、陌生账号搜词 + 7 天曝光，详见 Step 6.1）。
> 判据从「上架数/出单」改为「入池率」——出单是结果，入池是唯一你能控的过程指标。
> 同日再补两条执行闸：T+0 **免费流量三件套**（`backflow.py traffic-due`，24h 强制）与
> T+7 **曝光×加购率四象限**（低曝光+高转化 = 搜索不匹配 → 改标题补词，**禁止 kill**）。

> 四批实测（2026-11-12）与三个真实 case 的原文已全部下沉到 `references/replay-log.md`。
> 这里只留「会改变当前动作」的结论；相互冲突时以 `replay-log.md` 中时间最新者为准。

1. **Step 0 四道门槛必须真执行，且已代码化**：类目属家居树 / 干净主图 ≥3 / 抛重后价格带 / 最高 SKU 采购价。
   collect 前跑 `select_gate.py`（退出码 1 禁止采集）——第二批 10 品白跑 6 个，就是门槛只写在文档里没执行。
2. **一件代发禁止 2 件套**：`pieces` 永远 1，`pricing_calc.py` 对 >1 直接退 3；`select_gate` 双重拦截。
3. **FastMoss 用当地语言长尾词池，不用销量总榜**：总榜会把低达人出单率的货架场品埋掉
   （2000 条里家居 $5–8 仅 35 条、出单率 ≤30% 只剩 1 条）；`selection_intake.py pool` 批量拉并按出单率分档。
4. **写操作时序**：`ttl-claim` 每批 ≤3、超时先回查不盲目重试（重试过→重复 20 条）、`edit` 回执恒 `{}` 必须回读、
   `success:true` 只代表进队列、`publishConfig` 整批统一（重量尺寸不同必须拆单品提交）。
5. **发布前必跑 `prepublish_guard.py --refresh --ids ...`**：`image_gate verify` 不扫 `product_attributes`，
   中文属性会以 `[12052250]` 拒发（本轮 #3 实测）。
6. **SKU 规格名逐品对齐 1688 原始 `skus`**，禁止整批套同一套颜色名（写错 = 买家选 A 收 B）。
7. **重量尺寸显式按品传**：卖家 `itemWeight` 全填 0，净重/尺寸藏在详情参数表里；三套默认值打架，
   推算值一律标 `INFERRED`。`price_gate` 用本批子集（`--csv`）避免被历史脏草稿拖成退出码 1。
8. **别拿「款数不够」解释零单**：官方「约 150 款出首单」是统计规律不是承诺；当前 22 天 7 款的瓶颈是
   **入池率 0**，不是差一个数量级——判据是 `backflow.py index-stats`，不是累计上架数。
   配额与后台在售数当前无脚本能力，如实标「未验证」。

### 现行硬规则速查（按主题；各批次原文、实测数字与事故追溯见 `references/replay-log.md`）

> 以下条目是当前仍生效的动作规则。批次背景、样本数字、已修 bug 的排查过程不留在主文件，按批次索引查 replay-log。

**履约与货代**
- 48h 强拦截当前 `off`（`selection.yaml → seller_ships_48h_proof: off`，用户指示）：缺证据降级 WARN `48H_PROOF_SKIPPED` 不阻断。机验档能力已就绪，启用改 `machine_or_chat`（晚发必赔=Y 直通 / 揽收率≥0.95 放行+WARN / 皆无回退人工书面）；`pickup_24h_rate=0.0` 一律 UNKNOWN，不判 FAIL。重估触发：起量或出延迟发货/LDR 事故。
- 货代仓以已绑定行为准：`route_gate.py` 校验发货城市、已绑定仓、≤1 天到仓证据，缺证据禁止 collect。

**认领与写操作**
- 写操作先查重再分批：认领每批 ≤3，按 offerId 精确查重；回执为空必须回读；`success` 只代表进队列，不等于平台在售。
- 三个 ID 别混：`offer_id`（1688 货源 = 日志 itemId = 站点 outerId）、`itemCenterNumIid`（ERP 内部 ID，喂详情接口必 does not exist）、`numIid`（TikTok 商品 ID = 日志 platformItemId）。判在售唯一口径 `productStatus == ACTIVATE`；`INITIAL` 待审核不算上架。命令：`site_goods.py status --offer <1688ID>`。
- 重发必须 `--force`（`filterAlreadyPublish` 静默跳过已提交 ttl_id）；`stage_publish` 先查是否已 ACTIVATE，已售跳过避免双链接竞争。
- verify4 的 `logs_success` 只看最新一条（CSV 会留上一轮 fail 行，全文正则会误判）。
- 公网图必须回读核验：`verify_public_images.py` 逐张下载比对 SHA-256/像素，错配短链不得进 payload。

**图片与变体**
- 删详情图前先读图：重量/尺寸红线数据常藏在「包装信息」图里，多模态判定顺带抄参数表，别只填 verdict。
- 变体缺主图 `[12052522]`：变体图在 `skus[].sales_attributes[0].sku_img.uri`；删图前先算好每个变体的替代图，用 `ttl_edit --sku-img-seq` 一次补齐；apply 删空时主图轮换补位。
- `mains.txt` 先出 contact sheet 目视剔除带中文的图，再写清单；首图含中文只软标不硬杀（可擦字救回）。
- 主图凑不足 5 张：state 写 `allow_few_mains=true` + 原因，降级留痕，**禁止靠脏图凑数**；T+7 看曝光再补图换源。

**SKU 与定价**
- 重复 `seller_sku` 用 `--sku-map-seq` / `--sku-code-seq`（`prep_one.py` 自动产出，重复组 `-2/-3` 后缀），否则 dict 互相覆盖只剩 1 条。
- SKU 翻译值 ≤40 字符，否则 `[12052251]` 重复（长值被截断后撞车）；词表一步到位写短值。
- ERP 自动价破带是常态：走 `price_gate → batch_prep/ttl_edit 公式改价 → 复检` 后再 publish，禁止手填。低价品破**上限**用 `--markup` 压回；破**下限**才是提高 markup 进带，方向别混。
- 品牌判定 = known_brands 名单 + 词典核实；全大写词 ≠ 品牌（KATUN=马来语"棉布"），title-case 品牌靠名单兜。
- 1688 授权过期不中断 collect/claim/publish，仅详情页抓图受阻。
- ⚠ 能力缺口：`ttl_edit` 只有 `--keep-prefix`（按货号前缀），**不能按 SKU ID 定点剔除高价破带规格**；遇到只能整品放弃。

**证据降级与检查器诚实性**
- Aidge 欠费（`Service suspended due to overdue payment`）：`evidence_local.py --review <review.json>` 走本地 macOS Vision OCR 真实识别回填 evidence（`evidence_source=local_vision_ocr`），不是伪造证据——本地 OCR 读到中文照样被 CJK 检查拦。顺序：`auto_verdict.py` 先跑（定 verdict），`evidence_local.py` 后跑（只给 L3_keep 主图补证据）。
- 手工 `publish_one approve --which images` 前先确认 state 有 `review_file`，没有就补 `erp_data/image_gate/<ttl_id>/review.json`，否则 apply 报 `KeyError: 'review_file'`。
- 两个静默失效已修：`health_check --only aidge` 同时探 ImageRecognition；`assemble_guard` 按映射后的值判 SKU 长度与维度内重复。

**已固化编排工具（脚本细节见 References）**
- 批量预检 `precheck.py`（`--target/--wave/--c-jobs`）；单品状态机 `publish_one.py`；批量发布/回查 `batch_run.py` / `verify_batch.py`；选品期左移预检 `prefilter.py`；前置校验 `assemble_guard.py`；六步合一 `auto_assemble.py`；SKU 短值词表 `sku_dict.py`；标题模板 `title_pool.py`；IP 形象词预警 `ip_risk.py`；阶段耗时账本 `timing.py` / `timing_report.py`。
- **一条命令铺货 `batch_listing.py`（2026-11-26 P0 固化）**：waves(precheck×预算闸) → edit(模板+`config/i18n/ms_terms.csv` 词表+款型挑选+汉字货号码表+`--backfill-dim-images`) → 图片闸门(scan→image_review→apply→verify) → 三闸 → [--publish 才发布+四路验证+backflow]。默认止步闸门；`--card-mode attrs-warn`（默认）= 仅 ATTRS_LT5 放行、标题/主图硬伤仍阻断。**下次铺货默认走它，不要再人肉串波次。** 配套硬化（同日）：

> **ATTRS_LT5 口径（2026-11-26 用户拍板）**：维持现状——不破解类目属性写入接口，driver 默认 `attrs-warn` 放行该项（证据：历史 47/47 BLOCK 但全部发布成功，属阈值与能力脱节而非本批数据缺陷）；TITLE_KEYWORD_LOW / 主图类硬伤仍阻断。若日后官方类目属性接口打通，再切 `strict`。
  - `erp_client.require_shop()` + `health_check` 店铺配置自检：`*_SELLER_ID` 缺失/占位符 → claim/publish 启动即死（2026-11-26 事故：.env 空占位符让 20 品 claim 各白烧 90s 回查，一波 -35min）
  - `publish_one.stage_claim` 错误分类：`code=500/For input string` 业务拒绝**跳过回查轮询**立即失败；只有网络/超时才回查
  - `precheck.py` A 段增强：pool 带 `ship_city/qty_begin/price_low` 列时认领前就砍（A③发货地白名单外 / A④MOQ>1 / A⑤SKU最低价<¥2）；dld/图搜回包有这些字段就回填 pool CSV
  - **`--b2-batch`（2026-11-26 6 品实测 opt_b2batch_6d）**：B0 由逐品状态机改整波一次 `collect×1 + claim×1 + ttl-list轮询合一 + 并行 detail`。基线逐品 `claim` 26.8s/品（其中 `wait_for.claim` 85.9s），批量路径 6 品 B0=34.1s（claim 调用 **0 次重复**，采集箱不再翻倍脏数据）。driver 用 `--b2-batch` 透传；A③ 同时修了「浙江省金华市」全称匹配（旧 `rstrip("市")` 只认纯城市名，会把带省份的全称全批误杀）。**坑**：认领成功后 state 必须停在 `detail`（不是 `claim`），否则 `publish_one next` 会重跑 `stage_claim` 导致每品重复认领——6d 前版本实测 6 品白烧 6 次 `ttl-claim×6s`。
  - `batch_listing.py` 入口自动加载 `.env`（同 `health_check`）：driver 经 subprocess 调 precheck/erp_client，外层 shell 没 `source .env` 时 claim 全收占位符 sellerId → 500；现在一条命令自洽，不依赖操作者记得 source
  - **图片判定 A/B/C 提效（2026-11-26，用户"按 A→B→C 顺序做"）**：第一性原理——判定本质是「这张图的字节有没有中文/水印」，是图片内容的属性，与在哪一步判无关；同一批图原本被判 2–3 遍（prefilter 首图 / C 段全册 / Phase3 probe）。**A 消重**：`judge_cache.py` 按 content_sha256 存判级+evidence，scan 重建 review 后自动回填；`image_review.can_reuse()` 跳过 L0_drop 与 sku/desc 的 L3_keep 的 Aidge probe（main+L3_keep 仍 probe，保 678 非文字水印硬闸）——49 图样本 probe 从 49→2 张（省 ¥0.94/品）。**B 三判合一**：`ocr_cache.json` 存原始 OCR items，prefilter 首图判过的结果 C 段按 sha 命中免跑 swift——5 图实测 OCR 命中率 5/5。**C 通道分级**：`auto_verdict --stop-after 5` 按 main>sku>desc 分批判、凑够 5 张唯一干净图即停，剩余标 `unjudged`；40 图样本只判 9 张达标，C2_ocr_verdict 26.5s→1.1s（-96%）。**红线（实测钉死）**：fast OCR 中文召回 **0/6**（全部漏判）→ 绝不可用 fast 替代 accurate，只减张数不减精度；关闭开关 `PRE_STOP_AFTER=0`（回退全册）、`image_review --force-probe`（全 probe）、`prefilter --no-ocr-cache`。端到端 `driver_abc_opt` 墙钟 2.0min（与优化前 1.9min 持平——单模块收益被样本量 3→4 品与 ERP 网络波动掩盖；样本量一致时 C2_ocr_verdict -96% 可复现）。**同批草稿 A/B 对照（2026-11-26，脚本 `erp_data/_run/opt_test/ab_compare.py`）**：固定同一份已认领草稿的 review+本地图，只切开关，A=全册OCR+force-probe、B=stop-after+缓存+复用，repeat=3 中位数——干净品 A 21.6s→B 13.1s（-39%，判 9/40 达标，probe 40→33 张）；脏品（49 图仅 3 干净）暴露 **stop-after 无闸时比全册判慢 45%**（分批冷启动摊 6 次），已加**预算闸 `--stop-max 20`（env `PRE_STOP_MAX`）**：判满 20 张不足 5 干净即放弃（返回 5 让 C⑦ 淘汰），修复后脏品 A 40.7s→B 25.6s（-37%）。**判定安全性核对**：B 判过的 9 张与 A 全册判定 9/9 一致、B 选出的干净图全部是 A 也认可的 → stop-after 只减张数不放宽标准。**环境限制**：Aidge 欠费暂停（`Service suspended due to overdue payment`），probe 全失败回退本地 OCR，故 A/B 对照里 probe 阶段两组合计仅 0.3-0.6s，"省 ¥0.94/品"是费用口径不是当前墙钟口径。
  - `auto_verdict.py --sample 8 --need 5` 抽样早停（precheck C 段默认启用，`PRE_SAMPLE=0` 关）：前 8 张(main>sku>desc)干净<5 → 预杀，省 ~38s/品全册 OCR
  - `ttl_edit --backfill-dim-images`：双维度货源第二维无 sku_img → image_gate [12052252] 必死坑，edit 时自动借同 SKU 首维图补齐（2026-11-26 本批 4 品实证）
- **20261126_my10b 三项硬化（同日，见该 run 的 `timing_analysis.md`，VERIFIED）**：
  - **D2 模板匹配大小写修复**（`batch_listing.edit_one`）：ERP 自动翻译后标题是马来语（`2026 Apron lengan…`），aliases 小写 `apron` 匹配不上 → 命中失败报「无类目」跳过（实证 750277293130 手工补 5min）。修复=匹配前标题/别名都 `.lower()`，另加第二层兜底：标题全落空时按 detail 中文类目路径（`toCategoryNameTree`）末级反查模板 `erp_category_name_tree`。`--claimed` 续跑路径 info 空，这层兜底是唯一活路。
  - **D3 B⑦「SKU 可编辑性」预检前移到 precheck**：过闸品在 edit 二次死于「无合格 SKU」每品白烧一轮 B0（认领+detail ~60s）+ 人工回合。根因**不是可译性**——833710035247/971253800616 可译 10/10、有货、价在带，真凶是**重复 seller_sku 的 drop 等值砍**（driver 传 `--sku-code` 是 ssid 字典；pick_skus 选中某 ssid 的 2 款，drop 名单按等值把同 ssid 全部 5 款带走 → kept∩drop=∅ → 「无合格SKU」）。修复=新增 `scripts/ms_terms.py` 作词表（`load_terms`/`tr_value`）唯一出处（`precheck` 与 `batch_listing` 同源 import，杜绝口径漂移），内含 `check_sku_selectable`（复刻 pick_skus 选款 + ttl_edit drop/零库存三层链）；precheck B⑦ 认领前即砍。回归验证 6 品全对（2 死命中、4 活不误伤）。
  - **D1 组 wave 路线约束**：68 候选→10 品，泛词词搜（dld/productSearch 新店）转化 2.6%、图搜种子路线（`alibaba1688/imageSearch` 喂历史在售品主图找同款）转化 **43%（16×）**，C⑦ 图脏杀 83%。下批组 wave **默认走图搜种子**，泛词候选设预算上限，别靠多轮 wave 凑数。**⚠ 20261126_my10c 修正口径**：种子路线转化**分品类**——围裙/罩衣帆布系种子 19→7、7→1、7→3（合计 33/52=63% 候选进过闸评估、11 过闸），但洗衣袋种子 26→0（该品类供给图普遍带营销字，C⑦ 全灭）。结论改为「图搜种子按品类验证，围裙系可默认；新品类先小波试 5 个再放量」。
  - **D6 edit 前草稿漂移（20261126_my10c 发现，20261126_my10d 端到端验证通过）**：precheck 快照里 value_name 是 ERP 译好的马来语（Merah），线上草稿仍是中文（长袖熊红色）→ pick_skus 按旧快照算 vals/code，ttl_edit 读线上体报 PAYLOAD_CJK。修复=edit_one 计算选款前先 `ttl-detail` 重刷，以最新线上体为准。**my10d 实测：10 品 edit 阶段 PAYLOAD_CJK=0、"无类目"=0。**
  - **D7 变体值去重后缀被截断（20261126_my10d 发现并当日闭环修复）**：692626145767/683279316695 首发 fail [12052251] 变体值重复——pick_skus 对同译文 key 加 " A" 后缀，但 `(t+" A")[:40]` 把后缀又截掉（两值本就被截到同一 40 字前缀）。修复=`(t[:38]+" A")[:40]` 给后缀留位。重发 41s 双 success。教训：**40 字截断 + 事后加后缀 = 必须预留后缀位**。
  - **make_waves.py 组池脚本化（20261126_my10d）**：MCP 回包 JSON（mcp_write 落盘）→ 历史去重/MOQ/价格/红线过滤 → 按品类切 ≤7 品小波（`--kw-order` 控波序，高转化品类在前）。全灭波损失封顶 ~3min/品类（my10c laundry 26 品全灭烧 7min 的根治）；agent 组池 6 轮手搓收敛到 2 次脚本调用。**下次铺货组池默认走它，不再人肉拼 CSV。**
  - **深页种子红利（20261126_my10d 意外发现）**：`alibaba1688/imageSearch` 用 `page:2`（避开浅页已铺同款）转化 14 品 10 过闸（**71%**，浅页 33%）。同日多轮铺货后浅页供给被消耗殆尽，**扩池优先翻深页而不是换种子图**。
  - **P0-1 发布闭环脚本化（2026-11-26 改码，待 my10e 端到端验证）**：driver 新增 `--auto-publish`——闸门全过且过闸数==target 时自动发布→wait_for 轮询→logs 终态→**一次** site_goods list blob 匹配在售→backflow sync→落 `run_dir/publish_result.json`。前置不满足（过闸≠target）不盲发，仍止步报告。纪律：验证一律用批量 list blob，**禁止逐品 `site_goods status`**（my10d 实测逐品 ~2.5min vs 批量 5s）。
  - **P0-2 make_waves 吃原始回包**：`--raw <linkfox_mcp_json...>` 直接解析 proxy_call 原始回包（imageSearch/dld 两形态，路径探测 data.products|list|items），免手搓 TSV。注意 imageSearch 无 levelName → 红线过滤退到 title 命中。MCP 回包流程改为：调完原样存盘 → `make_waves --raw` 一步出波。
  - **P0-3 品类黑名单（含守卫）**：make_waves 读 word_pool_feedback.csv，累计 sent≥15 且过闸率<10% 的品类默认拒收（--allow-dead 放行）。**表可信守卫**：全表非'?'行 passed 恒 0 时黑名单整体停用（历史数据 kw 回填缺口，否则误杀 apron）。配套修复：driver 旧版把过闸全记在 kw='?'（sent 有、passed 无）——已在 Phase 1 从波文件回填 entry['kw']，**my10e 起 feedback 表的 kw 列才可信**。
  - **P1 edit→gate 直通流水（同批改码，待验证）**：Phase 2/3 两 barrier 合并为 per-item edit→gate（全局仍 --jobs 3），timing 模块改名 driver.edit/driver.image_gate 按品记录 + 顶层 driver.edit_chain。
- 别再尝试：品内并发 OCR（瓶颈是 swift 冷启动，更慢）；标题 B2B 词硬杀整册脏图（无区分度）；用宽高比/文件大小预测干净率（两组区间重叠）。
- 改动脚本时保留既有埋点：`precheck.py` 的 A/B/C 段、`publish_one.py` 的 `pipeline.<stage>` 与 `sub.*`、`prefilter.py` / `auto_assemble.py` 整脚本一条。

## 这批数据的三个硬提示（2026-11-12 后台回执，别违反）

1. **描述不是「有就行」**：后台「2 个问题」= 描述格式（要分段）+ 描述质量（≥500 字符）。软提示不阻断，
   但本批 10 品里 9 品 <500 字符、8 品不足 2 段——根因是我们自己把整段塞进一个 `<p>`。
   标准：`localized_text` 纯文本 ≥500 字符、≥2 段，`apply` 逐段包 `<p>`，`prepublish_guard` 默认拦截。
2. **站点商品是另一套模块**：采集箱（发布前草稿）≠ 平台侧在售商品（`numIid` 是 TikTok 商品 ID）。
   改在售商品走 `site_goods.py`；保存后**立刻回读是旧值**（ERP 有数分钟缓存），别过早判失败。
3. **上下架接口未实测**：`itemBatchUpDownShelf` 只探明必填 `ops`/`list`，**禁止凭猜测调用**。

## 工作流

### ⚡ 2026-11-25 流水线化改造（效率纪律，动手前先读）
本 skill 的固定链路已全部脚本化，**agent 禁止在对话里逐步手打链路命令**（2026-11-24 实测：5 品链路 75 次 agent 轮次 + 40 次 JSON 转录，占全程 ~60% 时间）。新工具与分工：

| 环节 | 用什么 | 不再做什么 |
|---|---|---|
| **批量预检（新）** | `scripts/precheck.py --ids a,b,c --site MY`：四道前置闸（来源价≥¥2 / 主图指纹去重 / SKU 库存 / 体积重量）+ 全图集判级 + 自动生成 `mains.txt` + 建议类目，一条命令把一批候选筛到「可发状态」 | 逐个采集试错（2026-11-25 实测 5 个废品白跑 20 分钟） |
| 单品全链路 | `scripts/publish_one.py start/next/approve/status`：collect→claim→detail→edit_dry(**CP1人工**)→edit_save→scan→image_review(**CP2仅存疑时**)→apply→guards→publish→wait→verify4，状态存 `erp_data/_run/pipeline/<offerId>.json` 可断点续跑 | 逐条手打 erp_client/ttl_edit/image_gate/prepublish_guard/site_goods |
| MCP 取数落盘 | `scripts/mcp_write.py --kind search/sku`：agent 把 MCP 返回转成**瘦表 TSV**（一行一商品）交脚本落盘，read/apply 完全兼容 | 手抄完整 JSON（转录=漏字段/错位的高危点，且占取数环节~1/3时间） |
| 图片判定 | `scripts/image_review.py --review <review.json>`：probe-first（Aidge OCR 并发取证→无文字水印自动 L3_keep→仅存疑图出小 sheet 目视一次） | 先拼全量 contact sheet 目视再 probe（实测目视不可靠，基本白跑） |
| 发布等待 | `scripts/wait_for.py publish/claim --match <id>` 指数退避轮询 | 手打 sleep + logs 重查 |

**发布结果判读纪律（2026-11-25 实测教训）**：`ttl-publish` 返回 `success=True` **只代表「任务已入队」**，
不等于发出去了。马桶刷（offerId `975479953760`）实测：接口 success、`wait_for` 首次也报 success，
但 `logs` 最终 `fail`（「主图上传失败：TikTok商店 上传图片失败: 参数无效」），采集箱状态始终为 0。

→ **唯一口径 = `erp_client.py logs` 落盘 csv 里的 `status` 字段**（不是接口返回值、不是采集箱状态）。
→ `publish_one.stage_wait` 已加 logs 二次复核：等 6s × 3 轮，读到 `fail` 立即返回失败并写 `publish_error`。
→ 连带纪律：`updatePlatformItem`（在售换图）**nginx 504 不等于失败**，后端可能已执行，必须回读详情确认；
  且 ERP 有 2–3 分钟缓存，立刻回读会看到旧值。
| 机会中心抄录 | `scripts/opp_center.py parse/record/list`：DOM 快照解析卡片预览按钮 node_id（dom_cua_click 替代坐标盲试）+ 结构化写信号 CSV（去重/列序/转义交给脚本） | 坐标猜点、heredoc 手拼 CSV |
| ttl_edit 试错 | 失败时自动打印：草稿实际 seller_sku 清单 / 未覆盖的汉字维度名 / 描述 CJK 命中词 | 用参数穷举搜索 |

**机会中心接口重放路线已排除（2026-11-25 四重证据，别再尝试）**：①Keychain/Cookie 提取被 WorkBuddy 沙箱 SIGTERM；②browser-use skill 硬约束禁 CDP/命令行浏览器；③匿名 curl 页面壳 302；④TikTok 后台带签名头。且弹窗正文（参考价/类目）不进 DOM 可访问树——**弹窗内容仍必须"开弹窗→截图→多模态读"，抄卡是全链路唯一不可压缩的人工环节**。

**由此推出的选品策略闸门（P3，优先级高于抄卡）**：机会卡片是**方向池不是货源池**（三批实测单卡命中≈1/6；2026-11-24 抄满 17 张卡仅产出 1 个过闸品；**2026-11-25 又抄 17 张卡，同样只出 1 个**，而同日 7 个围裙全部来自词池）。**机会卡自 2026-11-25 起降级为「需求信号池」，8–12 周回看一次即可，不再作为选品入口。** 执行顺序：
1. 先跑 `FastMoss 词池 + 已验证高通过率品类` 出候选。**选词比选品更决定通过率**——同一品类换关键词，
   首图干净率差 6 倍（2026-11-25 实测：「围裙 背带 帆布 可爱」47%，同批 4/4 一次过闸；
   「围裙 防水 厨房 家用」只有 8%）。机理：前者筛出「成品服装型供应商」（挂拍白底），
   后者筛出「内销批发型供应商」（图上印满规格参数）——**图脏是供应商类型的属性，不是图片的缺陷**。
   词库分级与黑名单见 `config/word_pool.py`，用 `precheck.py` 时自动告警；
2. 机会中心只抄**价格带内 × 高通过率品类**的卡片（`opp_center.py parse` 拿到骨架后先按 tabpanel 标题过一遍，不逐卡开弹窗）；
3. 同款供应商不足时用 `alibaba1688/imageSearch` 图搜扩供（实测围裙 20 家同款，一发命中 3 个过闸品）。

### Step 0 选品（取需求信号 → 1688找货 → 评分 → 按发货城市收敛货代 → 出offerId清单）
先读 `references/selection-strategy.md`（九章，尤其第一至三、六章）与 `config/selection.yaml`，按其中规则执行。

#### 0.0 地域收敛（选品先行、货代做约束）
**顺序是「选品 → 按发货城市聚簇 → 查货代覆盖 → 定稿」，不是「先锁一个城市再选品」。**

2026-11-11 实测 ERP「货代管理」：筛选器是**城市级**（服务平台 TIKTOK/SHOPEE/SHOPEES × 货代位置 东莞市/义乌市/广州市/泉州市/金华市/海外），**不是省级**。且货代明确支持「国内快递也可发」→ 货源不必同城同省，**判据是"快递到仓几天"，不是"地理远近"**。

1. **先按产品力出不限地域的候选**（0.2–0.4 全流程不看地域）。
2. **按 1688 发货城市聚簇**（不是省；省太粗会放过 2 天时效差）。
3. **查货代覆盖（人工接管）**：进 ERP「货代管理」，筛 `服务平台=TIKTOK` + 逐个城市，看有哪家、报价、送仓时效，按 strategy 2.3 的卡片评分表打分（≥7 分才添加）。**ERP 无货代接口（`erp_client.py` 未收录），只能人工确认，不要假装能自动查。**
4. **按时效分档决策**：
   - **≤1 天（绿）** → 保留并加权重铺该产业带
   - **2 天（黄）** → 需 24h 发货 + 次日达 + 书面确认；无缓冲，遇周末即超
   - **≥3 天（红）** → **先换发货地不换品**（图搜/同款开发找同款其他发货地，同款多发货地极常见），找不到才剔除
   - 多个货代城市可用 → **只挑 1–2 个集中铺**，不要一次开 5 个（拆包错发率随城市数上升）
5. 候选清单必须带 `ship_city` / `forwarder_city` / `ship_transit_days`，**`ship_transit_days ≥3` 的不进 Step 1**。

**两个待用户确认的前置问题**（会改铺货节奏，别猜）：① 我这类货会分配到哪个仓（实测义乌卡片写的是"东莞仓+义乌仓"多仓运营，选义乌可能实际从东莞发）？② 某单发货地与分配仓不同城时，ERP 默认走哪条线？

#### 0.1 登录检查（人工接管）
导航 ERP，若未登录则请用户接管（可让 Agent 打开登录页，用户自己输账号密码/验证码）。
登录后取 token，**不要让用户手工复制**：

```bash
python3 scripts/erp_token_grab.py --save --check    # 从 Chrome Session Storage 自动解析 JWT 并验鉴权
```

- 原理：ERP 把 JWT 放在 `sessionStorage.qnquerystring`，Chrome 持久化到 LevelDB，
  值用 **UTF-16LE + snappy 压缩**，直接 grep 必失败（脚本已处理）
- **WorkBuddy 浏览器通道没有 execute_js**，读不到 sessionStorage，只能走本地 LevelDB 这条路
- 官方后台 `seller.tiktokshopglobalselling.com` 登录态同样由用户接管

#### 0.2 取需求信号（L2 单品批量筛 + L1 官方复核）
**分工：FastMoss 批量拿候选（广度）→ 官方后台复核需求真伪（深度）→ 1688 供给侧（能不能供货）。三层定义见 strategy 第四章。**
推荐组合（首单验证有效）：C 路径批量拉 → `creatorOrderRate` 筛 → 官方信号复核 → 成本边界筛。

**① L2 单品批量层：FastMoss——已打通，走 ERP 后端透传，免 token 免订阅**（2026-11-11 实测，✅ 可用；这是广度来源，先跑它）
  - 接口：`POST https://erpg.kuaidizs.cn/item/fastmoss/product/search`（`/item/fastmoss/category/list` 同样免鉴权）。
    返回 `day7UnitsSold` / `day28UnitsSold` / `videoCount` / `creatorCount` / `creatorOrderRate` / `floorPrice` / `isCrossBorder`。
  - **参数必须嵌在 `filter` 里**（`region`、`shopType:2`=跨境店、`priceRange:{min,max}`、`creatorCountRange:{max}`），
    分页用 `page`/`pagesize`，排序字段是 **snake_case**（`day7_units_sold`/`creator_count`/`day7_gmv`…），不是驼峰。
  - CLI：`python3 scripts/kdzs_fastmoss.py --region MY --usd-min 2.2 --usd-max 8 --max-creator 100 --min-video 10 --top 40`
    / `--categories` 看类目树 / `--live-fx` 拉实时汇率。（2026-11-19 起 `--region` 默认 MY；TH/VN 存量期不再拉新池）
  - **多词批量池（推荐，2026-11-12 新增）**：`python3 scripts/selection_intake.py pool --region MY \
    --keywords-file erp_data/keywords_my.txt --usd-min 2.2 --usd-max 8 --max-creator 100 --min-video 10`
    ⚠ pool 词文件用**马来语品长词**（FastMoss 实测中文词命中 0；另建 `erp_data/keywords_my.txt`，
    不要把标题词根表 `config/keywords/my.txt` 直接喂给 pool——词根粒度太粗，拉不出发可拍的品）。
    → 逐词拉取、去重、按 `creatorOrderRate` 分 `ideal/shelf/mixed` 档并剔除纯达人驱动，落 `selection_pool.csv`。
    实测 3 个泰语词（TH 历史样本）24+46+34 条去重到 91 条、入池 71 条；MY 站用马来语词池，换成 18 词就是几百条，不用再人工抄榜单。
  - ⛔ **不需要**买 FastMoss 订阅：ERP 后端透传是 **TikTok 需求数据的唯一通道**（免 token）。
    **LinkFox 的 TikTok 数据直连（`fastmoss/productSearch` / `echotik/listProduct` / `echotik/batchProductDetail`）自 2026-11-20 起停用**：
    不再新增调用、不作为任何评分维度输入。理由：数据为第三方快照（`THIRD_PARTY_ONLY`），
    与 ERP 透传口径不一致（同一商品销量两源差近一倍，如 VN 压缩袋 `1736063102162208569`：
    FastMoss 2999 vs EchoTik 6002），混源会污染 `creatorOrderRate`/销量排序；ERP 透传同源、免费、字段齐全。
    **LinkFox 在本 skill 中仅保留 1688 找货**（`dld/productSearch` / `alibaba1688/imageSearch` / `alibaba1688/sku` OAuth）
    与 **TH 外观专利预检**（`ruiguan/detectionPatentDesign`）。
    ⚠️ 关键词仍必须用当地语言长尾词（中文词命中 0）。
  - **判读优先看 `creatorOrderRate`（达人出单率）而不是周销排行**：出单率 = 0 说明销量几乎全靠自然搜索/货架场，
    这正是「不开达人佣金」的新人最该抢的品；出单率 100% 说明全靠达人带货，你不开佣金就拿不到这个量（详见 strategy 4.7）。
  - 降级仍保留：接口不可用时按 `content.proxy_mode` 打分，上限 6 分，标注 `content_source: proxy`。**不得因缺数据给假高分。**

**② L1 官方后台（浏览器，深度复核需求真伪）**：商品机会中心（MY 站 URL 带 `shop_region=MY`）**优先级按 2026-11-20 重排：P0=热门关键词收割（周常）、P0=提报关联（每3天，见 T+0 纪律2）、P1=高潜力商品卡片（每周方向池）、P2=低竞争类目**。三 tab 使用规则：

**GUI 抄录/操作降本守则（2026-11-23 P4 固化，全部来自 11-20 实测踩坑）**：
1. **有 node_id 就不猜坐标**：先 `dom_cua_get_visible_dom`，按钮/链接/分页一律按 node_id 点（DOM 点击不受滚动偏移影响）；只有复选框等无 a11y 节点才用坐标。11-20 提报弹窗"下一步/返回"用坐标点错 3 次，node_id 一发入魂。
2. **toggle 控件一次一步**：复选框是 toggle 语义，**禁止把"勾A+勾B+点下一步"打包成一次动作序列**——点偏一格变反选，整包作废回退重做。正确：勾一个 → 截图确认状态 → 再下一个。
3. **表格抄录=定步长重叠滚动+离线逐判**：每次 `scroll_y` 固定 ≈整屏 80%（留 20% 重叠防漏行），一滚一截，**先截完全部再离线逐屏抄**；不要边滚边记（上下文来回切换最慢）。11-20 漏抄「apron comel」一行就是边滚边记+重叠区没对齐。
4. **截图超时别死等**：SPA 渲染期 `Page.captureScreenshot`/`Runtime.evaluate` 常 timeout，等 4–6 秒**只重试一次**；再超时改走 `dom_cua_get_visible_dom`（DOM 通道不依赖截图管线，表格文本基本够用），截图只留给需要看图的场景。
5. **抄完对账尾部**：最后一屏必须拍到分页控件或列表尾（如「共 N 条」/翻页 1/1），行数对不上立刻补滚，不进下一步。

| 优先级 | tab | 数据本质 | 正确用法 | 禁止用法 |
|---|---|---|---|---|
| **P0（三 tab 中唯一直接打"入池/被搜到"瓶颈的）** | 热门关键词 | **TikTok 站内搜索词**（真实需求信号） | 按搜索次数排序抄前 2-3 页进 `erp_data/opportunity_keywords_my.csv`（`--init-template` 出模板，volume 记档位原文+copied_at）→ `python3 scripts/opportunity_keywords.py --csv ... --site my`（dry-run 看拆根计划）→ `--apply` 同时更新**两张表**：词根表 `config/keywords/my.txt`（供 `listing_quality_gate.py` 标题命中校验）+ 整串表 `erp_data/keywords_my.txt`（供 pool 拉 FastMoss 候选）→ 重跑 `listing_quality_gate.py --site MY --summary` 看在售款标题缺口 → 缺口款按 T+7 规则只改标题补词。修饰词表 `config/keywords/modifiers_my.txt` 排除 tahan lasak/murah 类修饰语不拆根 | 只抄单词不记搜索量档位；把修饰词当独立品类词；把词根表直接喂 pool（粒度太粗） |
| **P1** | 高潜力商品→「基于市场趋势的热门商品」(`sub_tabs=shp_top_products`) | **外部平台热销榜**（URL 参数指向 Shopee 类榜单，INFERRED），卡片"3.2k+ 销量"是**外部销量不是 TikTok 需求**；"无竞争对手"= 平台侧供给缺口 | **只当方向池**：浏览器抄录卡片字段进 `erp_data/opportunity_signals.csv`，跑 `python3 scripts/opportunity_intake.py --csv ...` 出 reject/direction_only/candidate 三档；candidate 才值得进 1688 找货。单卡命中概率实测 1/6，广度仍靠 FastMoss 词池 | ⛔ 照单铺货。实测 6 卡片里 3 个是品牌跟卖陷阱（WATSONS/Winstar）、1 个价格带压不进（RM0.55 起）、趋势图 30 天平直线=无站内动态证据 |
| **P2** | 低竞争类目 | 类目级缺口（在售 0-5 款+有搜索量） | 类目词根进选品方向池，与官方重点类目表交叉 | 当单品清单用 |

- **机会详情弹窗四项站内指标（市场GMV/库存可用性/可用商品数/商家总数）显示 "--" = 平台无 TikTok 侧数据**（实测纸尿裤袋全空）→ CSV 里 `tiktok_metrics=no`，脚本自动降级 direction_only。
- **提报/添加同款有流量扶持奖励**（官方文档 VERIFIED：获批品进商城搜索、推荐流、直播商品池优先曝光）——
  对新人零单店，**这是机会页最大的直接收益，且与"选品"无关**：
  ① 已在售品 → 相关机会词/卡片走「我的提报」关联提报（每 3 天纪律，见 T+0 纪律 2）；
  ② 提报的品必须是自己在售/即将在售的品，**不是照抄机会卡片**；官方要求子类目/品牌型号/款式主功能三对齐，
  对不齐会被拒（提报记录页可查拒绝原因，被拒的改品后重新提报）。
  2026-11-20 结论（外部多源一致，VERIFIED 妙手/UpSeller/官方学院）：三家 ERP 对商机页口径一致——
  **线索源不是答案**；"换一家店不一定卖得动"（货叮咚原话），照抄铺货=用外部销量赌 MY 站内需求。
- 关键词榜单补充源：`/compass/search-analytics/keyword-rank`（稀缺/热门词）。抄录当地语言词 → 翻回中文品类词。

**③ 遨虾榜单（免登录，仅方向信号）**：本 skill 已内置 `scripts/aibuy_rank_client.py`（复制自 shopee-light-listing，
权威源与哈希登记在 `config/dependency_contract.json`），`--platform TikTok_XS --site MY`（TH/VN 存量期不拉）拉热销/飙升/新品榜（类目选 Home Supplies 等）。
  - ⚠️ **`opprank` 榜单只有 14 个字段（标题/下游价/1688同款数/同款中位价/类目/图/rankId），没有销量、没有评价数、没有达人数**。它只能给方向，不能支撑需求打分。

**④ 机会中心选品路线（2026-11-19 用户确认新增，正式路线）**：「商品机会 → 1688 相似款 → 铺货」三段式，
把 ② 的信号直接变成候选，链路已胶水化：
```bash
# 1) 浏览器抄录卡片（标题/销量/参考价/类目/站内指标是否有数/图URL/中文品类词）
python3 scripts/opportunity_intake.py --init-template      # 出 erp_data/opportunity_signals.csv
#    （抄录列 image_url / search_keyword 由 opportunity_to_sourcing 约定，模板外手工加列）
# 2) 判读 + 检索编排（内部复用 intake.judge；reject 行自动跳过，不浪费检索配额）
python3 scripts/opportunity_to_sourcing.py --dry-run       # 先看计划
# 2a) 主通道 LinkFox（MCP，凭证由 WorkBuddy 注入，无 mtop token 过期问题）——两段式：
python3 scripts/opportunity_to_sourcing.py --linkfox plan   # 判读后出 erp_data/linkfox/sourcing_tasks.json
#     → Agent 逐任务调 LinkFox MCP proxy_call（toolName=arguments 见任务文件），返回转瘦表 TSV 用
#     scripts/mcp_write.py --kind search 落盘 save_to（2026-11-25 起禁止手抄完整 JSON；
#     alibaba1688/imageSearch 或 dld/productSearch，积分计费；禁止脚本直连网关，硬约束同 linkfox_sourcing.py）
python3 scripts/opportunity_to_sourcing.py --linkfox read    # 读落盘 → erp_data/opportunity_sourcing.csv
# 2b) 百炼官方通道（2026-11-20 新增，脚本直连、¥0.058/次、无需 MCP 落盘两段式）：
python3 scripts/opportunity_to_sourcing.py --channel bailian    # 一步出候选，带晚发必赔/揽收率机验列
#     凭证=skill .env 的 DASHSCOPE_API_KEY；无城市/多图字段 → 城市证据仍走 linkfox/详情页
# 2c) fallback 遨虾（免积分，mtop 逆向、token 会过期；仅在 LinkFox 积分不可用时用）：
python3 scripts/opportunity_to_sourcing.py --channel aibuy
# 3) 逐行执行输出里的 next_step：selection_intake.py image 抄 1688 字段 → 回填判断 → select_gate 硬闸
```
  - 实测（2026-11-19）：`search` 与 `search-image` 双通道均通（各 20 条）；今日 6 卡片 → 3 REJECT、2 无检索输入、1 candidate 出 5 条候选。
  - 实测（2026-11-20 切换日）：`dld/productSearch` 与 `alibaba1688/imageSearch` 实调均通（20 条/次，errcode=200）；plan→read 端到端在真实 signals 上跑通（16 行候选，含 data_source/ship_city_raw/consign_price_cny 新列；未落盘的行如实标 SEARCH_FAIL(linkfox)+fallback 提示，不崩不猜）。
  - 实测（2026-11-20 第二品闭环，机会卡片路线第 2 单）：卡片「Ready Stock apron…」→ intake candidate → 词检索 rank4 `683331773332`（¥8.2-9.5/义乌帝诺/1件包邮 VERIFIED/48h揽收100%）→ select_gate 过 → 发布 success，
    平台侧在售 numIid=1738670773360624727。**新坑两条**：
    ① **主图域名：三个域 × 两条通道全部实测完毕（2026-11-25 定论）**——
      **只有 `cbu01.alicdn.com`（1688 CDN）能被 TikTok 拉取**，任何本地加工产物都进不了发布序列：

      | 通道 | 图源域 | 结果 | 报错 |
      |---|---|---|---|
      | `ttl-publish` 发布 | cbu01 | ✅ | — |
      | `ttl-publish` 发布 | kdzs-jxc-vpc（ERP OSS） | ❌ | 主图上传失败：参数无效 |
      | `ttl-publish` 发布 | dashscope（Aidge 签名链） | ❌ | 同上，一字不差 |
      | `updatePlatformItem` 在售换图 | cbu01 | ✅ 65s | 转存为 `tos-alisg-i-*.ibyteimg.com` |
      | `updatePlatformItem` 在售换图 | kdzs-jxc-vpc | ❌ | nginx **504**（服务端拉不到） |
      | `updatePlatformItem` 在售换图 | dashscope(https) | ❌ | 同样 **504** |

      → **「下载→调 erp_upload 上传→换图」不成立**：`erp_upload.py` 唯一落点就是 kdzs-jxc-vpc。
      → dashscope 虽然本机 https 200，TikTok 服务端仍拉不到（跨境 + 签名链 UA 限制）。
      → 代码侧有两道白名单：`ttl_edit.py:43` `OK_MAIN_HOST="cbu01.alicdn.com"`、
        `image_gate.py:482` `bad_hosts=("dashscope","kdzs-jxc-vpc")`。
      → **订正旧结论**：vpc 域「1 小时 404」的锅不在 vpc 域本身（11-19 上传的 6 张 11-25 仍 200，
        存活 6 天）；真凶是 Aidge 的 **dashscope 带签名短链**（Expires≈30 天）会到期。
      → **已发布商品不受源站影响**：发布成功后主图已转存到 TikTok 自己的 CDN（`tos-alisg-i-*`），
        1688 原图以后 404 也不影响在售商品。
    ② **主图不必只用原 5 张 main_images：整册重组（2026-11-25 最大突破）**
      - 现象：1688 主图常带营销字，但 **SKU 图 / 描述图往往干净**（SKU 图多为白底单品图）。
        2026-11-25 实测 733714051312：原 5 张 main 只有 2 张干净，但整册 29/40 张干净。
      - 做法：`image_gate.py scan`（本来就扫 main+desc+sku 三通道）→ 判级 → 从 `L3_keep` 池
        按 **main > sku > desc** 优先级挑 5 张 → 写 `mains.txt` → `ttl_edit.py --mains-file`。
      - `--mains-file` 接受任意 URL（每行一个），会把 `main_images` **整体替换**；
        内置白名单只放行 cbu01（见 ①）。
      - 纪律：**必须按 `content_sha256` 去重**——1688 同店复用素材，URL 不同但字节相同，
        否则同一张图会占掉两个主图位（954592404608 实测 5 张里 2 张同图）。
      - 战果：当日 11 个品里 **6 个靠它救活**（原主图干净数 1–4 张不等），占产出 55%。
      - 已固化为 `scripts/precheck.py` 的 C 段（自动判全图集 + 生成 mains.txt）。
    ③ **主图供给必须选品期预检**（hero_main_images 教训第 3 次验证）：本品只有 3 张干净 cbu01 主图，
      到发布闸才暴露，被迫 `prepublish_guard --allow-few-mains`（3 干净 > 5 带脏，显式降级留痕，T+7 曝光差即换源补图）。
      词/图检索候选标题带「跨境/亚马逊」的品，主图常直接印 **amazon logo**（914257511246 实测）——红线在图不在标题，intake 抓不到，必须目视。
  - **通道纪律（2026-11-20，三通道版）**：输出行带 `data_source`（bailian|linkfox|aibuy），**三源销量口径全不同**
    （bailian `pay_ord_cnt_30d`=30天成交单数 / linkfox `salesQuantity`=周期件数 / aibuy `monthSold`），
    同一批次只按一个口径排序，**禁止混源比较**；失败不跨通道自动补位（防静默混源），read 失败行 next_step 明示重试通道。
  - **三条纪律**：a) 检索结果排序只是预排（linkfox 默认保留检索原序=`--rank-by source`，`sales` 才按 salesQuantity 重排；aibuy 按 monthSold 降序），**同款判定归人工/多模态**（aibuy 原话"禁止盲取第一条"）；
    b) 1688 月销是**供给侧热度**，不代表 MY 需求——真实需求验证只认 T+7 回流；
    c) 机会品**不享受任何跳闸特权**，与 FastMoss 路线同过 `select_gate.py`（白跑 6 品的教训）。
  - 与 FastMoss 主路线的分工：FastMoss 仍是**广度主来源**（站内真实销量+内容力字段全）；
    本路线是**官方供给缺口信号源**，每周回看一次即可，单卡命中概率实测 1/6，别指望它是主力。

#### 0.3 1688 找货（按站点成本边界筛，不是无脑搜）
- **主通道（2026-11-20 起）= LinkFox MCP**：`dld/productSearch`（中文品类词，可加 `proxyRights:"4360897"` 一件代发包邮 / `sendTime:"24,48"` 时效筛选）与 `alibaba1688/imageSearch`（卡片/竞品主图 URL，`filters:["isOnePsale","shipIn48Hours"]`）拿 offerId——凭证 WorkBuddy 注入，无 mtop token 过期问题；imageSearch 带 `sendGoodsAddressText`（发货地）与 `consignPrice`（代发价），词搜带 `company`+`deliveryTime`。**数据为第三方快照（THIRD_PARTY_ONLY），积分计费**。
- **官方通道（2026-11-20 新增）= 百炼「1688智能选品」MCP**（`--channel bailian`，脚本直连、¥0.058/次、官方数据）：
  唯一带 `晚发必赔/24h揽收率/30天成交单数` 平台级履约标签的通道，select_gate 的 `machine_or_chat` 档直接吃它；
  但**无发货城市/无多图/无重量** → 城市证据仍走 LinkFox imageSearch 或详情页。
- fallback 顺序：主检索 bailian 或 linkfox 按需求选（要履约标签→bailian，要城市→linkfox，都要→bailian 出候选+linkfox 补城市）
  → LinkFox 积分不可用 → 遨虾 `--channel aibuy`（免积分，token 会过期属预期）→ 浏览器进 1688 跨境专供（需用户接管登录）。
  - ⚠️ 原生 1688 搜索 URL 的关键词必须 **GBK percent-encoding**（UTF-8 会搜出完全无关的品）；带「深度思考」AI 模块的结果页滚动必超时，改用更长更具体的词（「衣物压缩袋」而非「压缩袋」）。详见 `references/erp-pitfalls.md` 第五章。
- **先算成本再挑品**：`python3 scripts/pricing_calc.py --cost <采购价> --scheme shelf --pieces 1 --site MY`，返回 `in_band`。
  ⚠️ **`--pieces` 一律填 1**（2026-11-12 第二批纠正，见 4.4）：一件代发不做 2 件套，下方「2 件套」数字仅定价模型备查。
  本节凡出现「组合装 / 2件套 / 同店可搭配 SKU」的条件均为 $5 下限时代残留，**不作选品依据**。
- 采购价上限（150g / shelf，`--limits` 实算）：**MY 单件 34.1**（免佣期红利；到期必须重算）；TH 单件 22.4、VN 单件 16.7（存量维护参考）。重量上去上限缓降（MY 400g 单件仍 32.2，头程便宜 63% 所致）。**超上限 = 换品，不要硬调 markup。**
- 供应链门槛全部要过（见 strategy 5.2）：跨境专供 / 一件代发 / **无隐形起订量** / 库存≥200 / **承诺48h发货（要客服书面确认）** / **快递到货代仓 ≤1 天** / 非预售非定制 / 主图可复用 / **无中文干净消费者向主图 ≥3 张 + 详情图可重建**（`selection.yaml` → `supplier_gates.clean_consumer_images_min`；
  全脏的货源在 1688 找货阶段就换掉，别等铺进采集箱才发现；**且修复后须能凑 ≥5 张**，见 `clean_images_after_fix_min`） / **实重 ≤400g 且抛重 ≤400g**
  （对应体积 ≤2400cm³，约 20×15×8cm）/ **真包邮（`true_free_shipping: required`，硬门槛）**：
  1 件起包邮且无满件/隐形条件，须在 `free_shipping_proof` 留核查证据，警惕「满 3 件才包邮」的假包邮（strategy 3.4）。
  - 尺寸一律按**抛重**判：`抛重(g) = 长×宽×高(cm) ÷ 6000 × 1000`。实重轻不代表运费低（假花/收纳筐这类必算）。
  - 以上门槛不再靠人工记忆：落清单后由 `scripts/select_gate.py` 机器校验，退出码 1 禁止 collect（见 Step 0.5）。
- **1688 双轨定价**：同一件货有"包邮价（1件起）"和"批发价（2件起）"，把两个含邮总价并列取低者。买1件看包邮价，买2件看批发价。

#### 0.4 AI 评分（四维，规则见 strategy 第六章）
红线剔除 → 供应链门槛 → **需求过滤（不打分）** → 四维打分：

| 维度 | 权重 | 数据来源 |
|---|---|---|
| 履约确定性 fulfillment | 0.30 | **发货城市→货代仓快递天数**/承诺时效/库存/起订量（<6 分剔除） |
| 内容力 content | 0.25 | FastMoss 的 `creatorCount`/`videoCount`/`creatorOrderRate`；无数据时按可拍性判，上限 6 |
| 实净利 margin | 0.25 | `pricing_calc` 全成本净利率（<8% 剔除） |
| 站内竞争 competition | 0.20 | TikTok 站内在售款数/官方标签（**不是 1688 同款数**） |

1688 同款数只作"跟卖时间窗"修正（<10 款 +1；>300 款 −2），不作为竞争度打分。

**打分前先过 `creatorOrderRate`（达人出单率）这道硬闸**（`selection.yaml` → `content.creator_order_rate`）：
- ≤5% → 几乎全靠自然搜索，**优先**（不开佣金也能吃到量）
- 30%–80% → 混合结构，可做但要看站内在售款数
- ≥80% → 纯达人驱动，**不开佣金 = 拿不到量**，直接剔除或降级

配套两条否决规则：① 标题含品牌名（大写英文词）= 已被品牌方运营，撞侵权红线；② 一批达人反复带同款、每达人周销 <2 件 = 铺达人打法，不是真实需求。

  - **IP 红线自动检测能力边界（2026-11-18 实测，见 `references/linkfox-ip-detection-coverage.md`）**：
    LinkFox 睿观 5 个工具里**只有外观专利支持 TH**（须显式 `regions:"TH"`），
    文本/图形商标与实用新型**均不支持 TH/VN**，版权检测无区域参数且噪声大。
    因此 brand_ip 红线**主判据仍是"标题大写英文词 + 主图目视"**，不可托付给睿观。
    TH 站可选加一道外观专利预检：仅当 `radarResult.same==true` 或 `troCase==true` 才拦截，
    **禁止用 similarity 阈值自动否决**（通用包装图实测虚高到 0.69）；VN 站如实标 UNKNOWN 转人工。

#### 0.5 出候选清单 + select_gate 采集前硬闸（退出码 1 禁止 Step 1）
**「第二批实测」第 1 条的四道门槛已代码化为 `scripts/select_gate.py`，不再靠人工记得执行。**
历史教训：门槛本来就在 `selection.yaml`/strategy 5.2 里，但本轮选品只用遨虾搜索结果（不返回图片）、
没拉主图目视，5 品跑完采集→认领→编辑→图片闸门才发现 0 张干净主图，1 品才发现只能落
`NON_MAIN_CATEGORY`，6 个品全流程白跑。

1. 首次执行生成带表头的候选模板：
```bash
python3 scripts/select_gate.py --init-template     # 生成 erp_data/selection_candidates.csv
```
   **可选：先把抄录自动化，人只填判断**（`scripts/selection_intake.py`，2026-11-12 新增）
```bash
python3 scripts/selection_intake.py image --from-csv <遨虾 search 结果.csv> --region MY
python3 scripts/selection_intake.py seed --batch erp_data/selection_intake/prefill.json
```
   `image` 自动抄录成本/最高 SKU 价/库存/起订量/重量/发货城市，并拼 contact sheet 供多模态整品判定；
   `seed` 直接生成下面这套骨架行，把「抄录」这一最耗工的环节省掉（历史候选只有 14 条的根因就是这里）。
   ⚠ 1688 详情页对无 Cookie 直连有 IP 级风控，拿不到全 5 张图时会降级为遨虾单图并如实标注，不要当成已达标。
2. 按 strategy 第九章字段回填候选行，**人工/多模态目视的结论必须落成证据列**：
   `clean_main_images`（≥3）/`desc_rebuildable`/
   `clean_main_images_after_fix`（**2026-11-19 新增 P0-3**：预测修复后可用主图 ≥5——发布硬闸
   `min_publish_main_images=5` 会拦掉不足品，围裙 5→1 白跑教训；口径 = L3_keep + image_fix probe
   判可修的 L1/L2 + 可提升的干净 SKU/详情图；证据 = `selection_intake.py image` 拉的主图 +
   `image_fix.py probe` 的 seen_text，采集前 ¥0.02/张即可预测）/
   `seller_48h_proof`/`free_shipping`+`free_shipping_proof`/
   `ship_transit_days`/`weight_g`+`dims_cm`/`max_sku_cost_cny`/`redline_check`（含 pass）。
   脚本不联网、不看图（与 image_gate 同一哲学），只保证这些证据不缺失、达标且与定价配置自洽。
   **2026-11-24 新增三列（本批 5 品在发布末端才被拦、多跑 2 轮全量 edit 的教训，全部前移到 collect 前）**：
   - `category_id`（TikTok 类目 ID，**必填且必须在家居树 600001 内**）：桌布 600204/椅套 600203 在
     「家纺布艺」独立根下=跨类目，选到即弃；别等 ERP 自动错配"节日装饰"造成已认领假象。
   - `sku_attr_names_ascii` / `seller_sku_ascii`（true/false，由 `linkfox_oauth.py apply` 自动产出指纹）：
     ERP 认领**不译属性名**（颜色→照抄进 payload）、**货号即 seller_sku**（`立体罩90*30*50` 直接进发布）——
     两处含汉字必被 [12052250] 拒，但都是货源的确定性属性，oauth 回包里就能判。false 时 select_gate 阻断，
     处方固定：`ttl_edit.py --attr-names "颜色=Warna,尺寸=Saiz,规格=Saiz"`（MY）+
     `--sku-code erp_data/sku_code_map.json`（apply 自动生成，汉字货号→ASCII 码确定性映射）。
   - 同批固化：`linkfox_oauth.py scan` 现把「详情页采购价 ≤¥1」也列为必补（promotionPrice 误读已 3 例），
     价格以 OAuth 官方值为第一来源，不再事后靠裁决补救。
3. collect 前必须跑闸门，**退出码 0 才允许把 offerId 传入 Step 1**：
```bash
python3 scripts/select_gate.py --csv erp_data/selection_candidates.csv
python3 scripts/route_gate.py --csv erp_data/selection_candidates.csv --warehouses 义乌市,东莞市,广州市
```
   闸门会现场用 `pricing_calc` 按**最高 SKU 采购价**+实重/抛重重算价格带（不信任人工填的 in_band），
   并校验类目树、真包邮、48h 书面确认、库存/MOQ、pieces=1 等全部硬门槛；issue code 与修复指引逐条打印。
   `route_gate.py` 只复核已绑定仓和到仓证据：不猜同城/同省时效，公司名不能替代发货城市。

#### 0.5b 价格竞争力闸（2026-11-18 新增，P0：解决「挂价没人点」）

**价格带校验只回答「这单赚不赚钱」，不回答「挂这个价有没有人点」。** 实测在售 7 款挂价 ฿129–201
全部高于站内家居中位价 ฿113（$3.37）——比价场直接被同款淘汰。旧版只有自设 $3.5–8 带内校验，不是市场价。

`select_gate.py` 会**检测候选表头**：含下列 4 列时自动启用价格竞争力闸，缺列则只 WARN（旧清单向后兼容）：

| 列名 | 含义 | 取数来源（按可信度） |
|---|---|---|
| `market_median_price_usd` | 站内同款中位价（USD） | 官方商家后台「价格诊断」> FastMoss `floorPrice` > 遨虾 opprank 同款中位价 |
| `market_price_source` | 来源标识 | 必填，无来源即阻断（`MARKET_PRICE_NO_SOURCE`） |
| `market_price_n` | 样本量 | < `market_price_min_sample`（默认 5）只 WARN，中位价可能被单款带偏 |
| `market_price_sampled_at` | 采集日期 | 价格是时效数据，无日期判不出过期 |

判定：`pricing_calc` 重算的挂价 `order_usd > 中位价 × market_price_tolerance`（默认 1.05）→ `PRICE_ABOVE_MARKET` 阻断，
**换货源/换品，不许靠压净利硬调**。注意比价口径必须一致（都含税或都不含税；TH 买家侧还有 17.7% 进口税楔）。
`--init-template` 已把这 4 列写进模板头。

#### 0.5c 官方 2026 ✅ 重点类目信号（2026-11-18 新增）

官方《新商破零》「1.1 2026 年重点品类一览」把家居树内 18 个类目标 ✅（家居收纳、纸巾湿巾、
卫浴、五金工具、办公用品、灯具电气、节日派对装饰、宠物食品、孕妇/哺乳用品等）。
`config/official_focus_categories.json` 收录这份白名单，`select_gate.py` 在
「`category_id` 对应类目名 + `title_zh`」上匹配：

- 命中 → 结果标 `[官方✅: 类目名]`，同等条件下优先排进候选清单；
- 未命中 → 只 WARN，**不阻断 collect**（✅ 是平台优先方向，不是准入资格，家居树内仍可发品）。

**来源时效**：官方页面元数据 `Updated 09/19/2024`，正文署名「招商团队 2026/01」且标题写「2026 年重点品类」。
两个时间对不上，类目表按 2026 版理解，但别当成当天实时数据；以商家大学「选品指南 → 家居品类材料」复核为准。

落盘 `erp_data/selection_candidates.csv`，字段见 strategy 第九章（必须含 `landed_cny`/`price_local`/`in_band`/`pieces`/`content_source`/`creator_order_rate`/`competition_source`/`fulfill_check`/
`clean_main_images`/`desc_rebuildable`——后两项是图片闸门的源头门槛，缺了就等于把脏图判断推迟到发布前）。
`pieces` 一律填 1（见 4.4，select_gate 与 pricing_calc 双重拦截非 1 取值）。

#### 0.6 清单确认（人工接管）
输出建议清单给用户确认（目标150款），确认后 offerId 进入 Step1。写操作仅对用户点名的清单执行。
**产能提醒**：历史落盘候选仅 14 条，而目标是 150 款。若清单产量不足，先检查是不是评分环节又退回到人工读后台——任何依赖人工转录的维度都不能进自动化流程。

### Step 1 采集1688链接（复用）
前置条件：`scripts/select_gate.py` 与 `scripts/route_gate.py` 对同一候选 CSV 均退出码 0（见 Step 0.5）。
退出码 1 时禁止 collect——先在 1688 换货源或补齐证据，不要让不达门槛的 offerId 进采集箱。
```bash
python3 scripts/erp_client.py collect --ids <offerId1,offerId2,...>
```

### Step 2 公共采集箱对账
```bash
python3 scripts/erp_client.py items --size 50
```
确认货源ID都在采集箱。

### Step 3 认领到TikTok站点店铺（写操作）
**接口 body 已破解（2026-10-29 实测成功）**：`POST /item/crossBorder/commonCollect/batchClaimMultiShop`。

**破解方法（第一性原理，修正此前 4 条失败路径的盲区）**：
- 此前失败根因：拦截全在 **JS 层且注入时机晚于应用启动**——前端缓存了原生 XHR/fetch 引用，原型替换无效；`bu.network_requests()` 只暴露 url/method/status 无 body；Webpack 字符串混淆致接口名静态搜索 0 命中
- **突破口：CDP `Page.addScriptToEvaluateOnNewDocument` 在 document_start（页面任何脚本执行前）注入 hook**，替换 `XMLHttpRequest.prototype.open/send` + `fetch`，把 `/item/crossBorder` 请求 body 记录到 `window.__reqLog` → reload 页面 → 浏览器走认领流程 → 从 `__reqLog` 读真实 body
- 实测抓到的真实请求体（ground truth）：
```json
{"numIids":["1565301310345342976"],
 "targetShops":[{"targetPlatform":"tiktok",
   "sellerId":"<MY_SELLER_ID>","sellerNick":"<MY_SHOP_NICK>"}]}
```
- **字段要点**：`numIids` 是 **ERP内部ID（numIid）数组**（不是1688 offerId，不是扁平 itemIds）；`targetShops` 是**嵌套对象数组**（targetPlatform/sellerId/sellerNick 三件套）。此前 24 组扁平字段（itemIds/targetSellerIds）全失败正是因为缺这两点。
- 💡 **以后再遇到未收录接口，优先读前端 chunk 逆向**（拉 OSS 上的 `*.js`，用正则提取路径字面量与函数体），
  比 CDP 注入便宜一个数量级，且能直接看到参数枚举与默认值。方法见 `references/erp-pitfalls.md` 第一章。

**接口认领（已可用，替代浏览器路径）**：
```bash
python3 scripts/erp_client.py ttl-claim --ids <货源ID或numIid逗号分隔> --region MY
```
- 脚本自动：查询公共采集箱拿 numIid → 构造上述 body → 提交
- 成功返回：`[{"operationId":"Y101_<MY_SELLER_ID>_<MY_SHOP_NICK>","success":true}, ...]`（每商品一条）
- 实测：单条/批量（4 个同发）均 success:true；认领后 ttl-list 立即可见，标题自动当地语言化、类目自动匹配、带 IS_REQUIRED_FIELDS 异常（正常草稿态）

**浏览器认领（保留为备选路径，7+次验证稳定）**：
公共采集箱勾选 → 批量认领 → 下拉选 Tiktok → 弹窗选店铺 MY 店（当前主攻；TH/VN 存量）→ 确定 → 认领成功弹窗

**认领陷阱**：认领弹窗默认选中未绑定的 PH 店，必须手动选目标店（当前 MY）；免责声明弹窗需先处理；已删除记录的货源重新认领可能不产生新记录（公共采集箱认领标记残留）。

**接口删除（2026-10-29 破解，写操作）**：`POST /item/crossBorder/platformCollect/batchOps`，body=`{"idList":["<ttl_id>"],"platform":"tiktok","opsType":3}`（**opsType 枚举：1=类目 / 2=售价 / 3=删除 / 4=备注**，同一接口复用）。CLI：
```bash
python3 scripts/erp_client.py ttl-del --ids <ttl_id逗号分隔>
```
- 返回 `{"failCount":0,"successCount":1,"totalCount":1}`；实测删除后 ttl-list total 立即减 1（2026-10-29：Y105→19条、Y103→18条均验证）
- **删除重认领是"修复手段"不是日常流程**：`ttl-del --ids <ttl_id>` → `ttl-claim --ids <货源ID> --region MY` 可恢复草稿态 itemModel，
  但**图片/类目/价格全部回到 1688 原始值**（只保留标题当地语言）。只在商品数据被错误 body 写坏时用（见 Step 4.5）。
- 同一接口 `opsType:2` 是批量改价（官方唯一正确的改价入口），见 Step 4.4。

### Step 4 TikTok采集箱二次编辑

#### 4.1 拉取采集箱列表
```bash
python3 scripts/erp_client.py ttl-list --region MY
```
认领后ERP自动：标题翻译成当地语言、类目自动匹配、品牌默认无品牌。

#### 4.2 拉取编辑详情
```bash
python3 scripts/erp_client.py ttl-detail --id <ttl_id>
```
核心数据在 `reqInfo.tikTokItemModel.productCreateRequestV2`：
- `title`：已自动翻译（若仍是中文，调 `/item/crossBorder/translate`）
- `main_images[]`：**图片通道 ①**，主图URL数组，每项 `{uri: "..."}`（实测恒 5 张）
- `description`：**图片通道 ②**，1688 详情 HTML，里面 `<img src>` 才是详情页里 8–25 张长图；
  还常带中文文本节点、中文 `alt`、`usemap`/`styleType`/`offer-template` 模板残渣。**旧流水线只处理通道 ①**。
- `skus[]`：SKU价格/库存
- `category_id`：类目（ERP已自动填）

#### 4.2b 产能分层（2026-11-12 新增：按每天 3 小时倒推，不定分层就等于放弃 150 款）

官方口径是「上传约 150 款后多数商家出首单，上架到出单约 20 天」。以每天 3 小时、20 天 = 60 小时计，
单款全部预算只有 **24 分钟**。而按旧流程（5 张主图 + 8–25 张详情图**逐张**多模态判定 + 逐条编辑），
单款 30–60 分钟 → 150 款要 75–150 小时，直接超预算 2 倍以上；实测两周只铺了 3 款。

| 档 | 数量 | 处理方式 | 单款预算 |
|---|---|---|---|
| **Hero** | 10–20 款 | 现状全流程（两通道扫描 + 逐张多模态判定 + 擦字/翻译 + 首图白底 + 重建 description） | 45 分钟 |
| **长尾** | 100–130 款 | **只留 ≥1 张干净主图 + ≥500 字符/≥2 段的可复用当地语言模板 + 批量公式改价**；详情长图整段丢弃，**不做逐张判定** | **≤3 分钟** |

**为什么长尾可以不做图片判定**：Step 4.3 的三条硬规则是「description 非空 / 主图 ≥1 / 无中文残留」，
外加第三批实测的两条描述质量要求「纯文本 ≥500 字符 / ≥2 段」。
用一个**分段、≥500 字符的可复用当地语言模板**替换整个 1688 详情，五条同时满足，且不需要看 20 张图。
判定成本从"每款 20 次目视"降到"0 次"，这是唯一能把 150 款压进 60 小时的路径。
⚠️ 模板必须分段且够长——旧版写「短 description」会直接触发后台「描述格式 / 描述质量」两条优化提示
（见上方「第三批实测」）。

**Hero 挑选规则**：只挑「已算出毛利 ≥20% + 站内搜索词明确 + 有已售数据」的前 10–20 个。其余全部走长尾档。
**长尾档的底线不变**：主图少于 1 张干净图 → 换货源（`min_clean_main_images`），不要留脏图凑数。

#### 4.3 图片闸门（本Skill核心，2026-11-12 重写；2026-11-13 补第三通道）

**先认清三条图片通道**（实测 10 个真实 `ttl_detail_*.json`）：

| 通道 | 字段 | 实测张数 | 旧流水线是否处理 |
|---|---|---|---|
| 主图 | `main_images[].uri` | 1–5 张 | ✅ 旧 Step 4.3 只管这里 |
| 详情长图 | `description` 里的 `<img src>` | **0–25 张** | ❌ 一张都没碰 |
| SKU 规格图 | `skus[].sales_attributes[].sku_img.uri` | **32/35 个属性带图** | ❌ 完全在扫描范围外（2026-11-13 补） |

SKU 图是买家**选规格时第一眼看到**的图，且实测全部指向 `cbu01.alicdn.com` 的 1688 原图——
中文/水印会原样发布。`apply` 对它的处置与主图一致：被判 `L0_drop` 的**置空 `sku_img`**，
其余按 `uri_map` 换 URL。

「跨境电商专用 / 工厂直供 / 一件代发 / 支持OEM / 5000平大仓库」这类字，
大面积漏进 TikTok 商品详情页的是**详情长图**，不是主图。真实样本还带 133 个中文文本节点
（「产品注意事项…下载素材包」）、中文 `alt`（「预览状态下无法点击，发布后可点击跳转…」）
和 `usemap / styleType / offer-template` 模板残渣——这些会原样发布出去。

**核心结论：这类图不要擦，整张删。**
它不是「干净商品图 + 一行字」，是整版为招商设计的中文排版：擦掉几处还剩工厂照片、
参数表、批发承诺、二维码；AI 补图还会注入幻觉字（见下方缺陷清单）。
**判定单位是整张图，不是文字块。**

**判定不能用 OCR 关键字表**：艺术字、竖排字、印章字 OCR 会漏。用多模态模型逐张看整图，
输出结构化判定（是否 B2B 素材 / 命中文字 / 置信度 / 处置建议），低置信度转人工。
`image_gate.py` **不假装能看图**：`scan` 只负责下载三通道的图并出待判定清单，
判定由模型回填 `review.json` 的 `verdict`。`config/image_gate.yaml` 里的 B2B 词表
只是 `verify` 阶段的**文本残留兜底**，不是判定规则。

**主图必须加机器证据（2026-11-19 678 事故）**：目视拼缩略图判主图必漏——678 把带中文横幅
「灰色/蓝色三件套」的 main_02/03 在 260px 拼图上看走眼填了 L3_keep，上架后详情页中文被用户抓到。
现 `apply` 内置 `main_gate_violations` 硬闸（只强制 main 通道）：L3_keep 必须有 `image_fix.py probe`
证据且 rec_text 无中文，换链 URL 不得落 dashscope/kdzs-jxc-vpc 域。**流程固化：scan → 目视判 verdict
→ `probe --only L3_keep`（¥0.02/张补证据）→ apply**。tests/test_main_gate_cjk.py 8 项钉死。

| 级别 | 判定 | 处置 |
|---|---|---|
| `L0_drop` | B2B 渠道标注、参数表、工厂/仓库/证书图、整版中文 | **整图删**（主图移除；描述图从 HTML 移除；SKU 图置空） |
| `L1_inpaint` | 干净消费者向商品图，只叠了一行营销字 | 保留，列入待擦除清单 |
| `L2_localize` | 产品功能词 | 保留，擦原文 + 原位写泰文/越南文 |
| `L3_keep` | 无中文无水印 | 原样保留 |
| `pending` | 未判定 | **apply 默认拒绝执行**（或显式 `--assume-pending drop` 保守全删） |

**执行（必须先 `ttl-detail` 落盘，全流程纯标准库）**：
```bash
python3 scripts/image_gate.py scan --detail erp_data/ttl_detail_<id>.json
# 【多模态模型】逐张打开 erp_data/image_gate/<id>/ 下的图（用 images[].local_file，
#   不要用 local_path —— 绝对路径在 Skill 目录搬家后失效），回填 review.json：
#   images[].verdict / seen_text / confidence / note
#   localized_text：当地语言纯文本段落（必填，否则重建后描述为空）
#   uri_map：L1/L2 处理完的新图 URL（旧 URL → 新 URL）
python3 scripts/image_gate.py apply --detail erp_data/ttl_detail_<id>.json \
    --review erp_data/image_gate/<id>/review.json          # dry-run，只出文件
python3 scripts/image_gate.py verify --detail erp_data/image_gate/<id>/cleaned_detail.json \
    --review erp_data/image_gate/<id>/review.json
python3 scripts/image_gate.py apply --detail erp_data/ttl_detail_<id>.json \\
    --review erp_data/image_gate/<id>/review.json --save  # 写回 ERP（写操作，需确认）
```

**三条硬规则**：
1. **description 要重建，不能删空、不能只写一小段**。`apply` 丢掉整个 1688 模板（`<img>` / `usemap` /
   `styleType` / 中文文本节点），只回填保留的干净图 + `localized_text`。发布自检要求
   description 长度 > 0（错误码见 5.2），且按第三批实测要求**纯文本 ≥500 字符、≥2 段**
   （`localized_text` 用空行分段，`apply` 逐段包 `<p>`；`prepublish_guard.py` 发布前拦截）。
   没给当地语言文本时脚本直接中止，**不会替你编一段**。
2. **主图删完必须 ≥1 张**（`config/image_gate.yaml` → `min_clean_main_images`）。
   一张不剩 = 换货源，**不要留一张脏图凑数**。
3. **发布前必须 `verify` 且退出码 0，且必须带 `--review`**。检查：描述非空 / 主图 ≥1 /
   文本与 `img alt` 无汉字 / B2B 词无残留 / 无模板残渣 / 被判 `L0_drop` 的 URL 未在三个通道残留 /
   **判定表覆盖全部当前图（`REVIEW_MISSING_URL`、`REVIEW_PENDING` 均阻断）**。
   退出码 1 = 禁止 `ttl-publish`。⚠️ 文本扫描看不见图里的中文字，只能兜底，判定表不能省；
   不给 `--review` 时 `NO_REVIEW` 直接 FAIL——否则"发布前 verify 必须 0"这条纪律可以在
   **一张图都没目视过**的情况下被满足。
4. **判定表过期必须重扫**。`apply` 会反向校验：判定表里有 3 条以上（或 >20%）URL
   已不在当前 detail，说明 detail 被 ERP 改写/重新落盘过，旧判定对现在的商品无效，
   直接拒绝执行。（2026-11-13 前无此校验，实测 4 次 `apply` 的 before/after 完全相同，
   却报告 `dropped_images=12~17` —— 闸门在空转。）
   ⚠️ **2026-11-13 之前生成的 review.json 只覆盖主图+详情图**，现在会因未覆盖 SKU 图而拒绝 `apply`，
   属预期：重跑 `scan` 重新判定即可（实测同一商品从 19 张变 38 张，多出的 19 张全是 SKU 图）。

**L1/L2 保留图的擦字/翻译/白底走 `scripts/image_fix.py`**（2026-11-19 实测定型，Aidge 专用 API + ERP 图床转存）：
1. `image_fix.py probe --review <review.json>`：逐张跑智能元素识别（ImageRecognition），
   把 `seen_text` 证据写回 review.json。**判 L0 必须先有证据**——治"空判过杀"（围裙 5 张主图
   被删到 1 张、31 条判定 seen_text 全空的根因之一）。探针提示"少量可擦文字"但判了 L0 的图 = 疑似过杀，目视复核改判。
2. `image_fix.py fix --review <review.json> --dst ms|th|vi [--matting-first]`：
   - L1 → `ImageRemove` 元素模式 `NonObjectRemoveElements=[1,2,3,4]`（自动擦非主体的文字/水印/牛皮癣，
     mask 外像素不动；实测四宫格 4 标签全净、商品布标正确保留——布标属主体，合规）
   - 复检残留图形元素（图标/底板）→ 该图加 `fix_boxes:[x1,y1,x2,y2]` 重跑（走 `Position` 参数；
     ⚠ `Mask` 要 RLE 编辑器编码，传矩形会被静默忽略——2026-11-19 实测踩坑）
   - L2 → `ImageTranslationPro`（实测小字标注直接擦除不留痕、数字保留；语言码无效会 InvalidParameter，脚本回退 en 并标注）
   - 首图 → `ImageMatting WHITE_BACKGROUND`（⚠ 抠图会把非商品元素一并抠掉，挂架/场景图慎用，先目视再定稿）
   - 每张结果：下载 → `erp_upload.py` 转存 ERP OSS → 新直链写 `uri_map`（Aidge 临时链约 30 天过期，禁止直接进 uri_map）
   - 补刀起点自动接 `uri_map` 上一轮结果（链式修复，不重复擦已净区域）
3. **复检五条款**（目视 `fixed_*`）：①文字完整正确 ②无中文残留 ③无幻觉字 ④无URL残留 ⑤商品本体未被改动。
   过 → `recheck:"ok"`；不过 → 补刀限 1 轮，仍不行 → verdict 改 `L0_drop`（宁缺毋滥，不无限返修）
4. `image_fix.py finalize --review <review.json>`：校验 L1/L2 全部有 URL 且复检完毕，退出码 0 才 `apply`。
   **finalize 内置主图尺寸硬核验（2026-11-19 教训代码化）**：主图序列 + 全部升级候选（含探针疑似过杀图）
   逐张解析宽高——**非方形（±12%）或短边 <600px 直接判"不可升主图"**，主图序列含不合格图即 exit 1。
   回测：垃圾袋 7 张竖版详情图（790×1200 等）全拦、2 张方形放行，与人工目视结论一致。
   也可单独跑 `image_fix.py check --review <review.json>` 在判定阶段提前盘点（先算尺寸账再花目视时间）。
   背景：desc_08"加厚抗穿刺"内容极好但是竖版、desc_25 只有 265×320——这类账靠目视必漏，靠人记必忘。
5. **不用 wan2.7-image 修图**（2026-11-19 A/B 实测）：指令式编辑=整图重绘——织纹变细、色调偏移、
   分辨率重采样、五金件形变，"更好看但非实物"，命中 TikTok「真实反映收到商品」合规线；
   且质检需逐区域比对原图，人力成本远超 API 差价。留给未来 AI 场景图（本版不做）。

**AI 生成泰文/越南文的高发缺陷（2026-10-29 真实case实测，必须逐张校验）**：
- **幻觉字高发**：AI 会在无关区域（金属反光、玻璃、花盆、空白处）注入杂散伪泰文字符甚至反向英文（实测图2右侧火花区出现"ตกหก/ตาน"、图3玻璃区出现"ARIEL 反向字样"），**原图没有的元素不能信任生成结果**，生成后必须逐区域目视
- **修正方法**：发现幻觉字后重新 `image_edit`，prompt 明确"仅保留指定文字区域，其他区域不得出现任何文字"，必要时二次/三次修正
- **泰文 OCR 不可靠**：内置 Read OCR、macOS Vision OCR 对泰文小字均不稳定（同一行多次读出矛盾结果），**判定泰文正确性用"裁剪单字符超大放大目视"**（sips 裁剪 + 放大到字符≥原始10倍，逐字符确认形状）
- **逐张验收标准**：①指定翻译文字完整、拼写正确；②无中文残留；③无杂散伪字/幻觉字；④无 URL/网址残留（网址必擦）；⑤首图为白底

#### 4.4 定价
**不要手算，用脚本**（参数唯一来源 `config/pricing.yaml`）：
```bash
python3 scripts/pricing_calc.py --cost <采购价> --scheme shelf --pieces 1 --site MY
python3 scripts/pricing_calc.py --limits    # 反推采购价上限；改费率后必跑
python3 scripts/pricing_calc.py --check     # 校验 markup 与费率是否吻合、有无"无解"组合
```
公式（2026-11-11b：跨境段物流改为按计费重量线性计费，不再是固定 10 元）：
```
到岸成本 = 采购价×件数 + 国内快递(5，同城3.5/跨省6) + 货代(2) + 包材(0.6)
          + 物流藏价( max(实重, 抛重) × ¥0.0198/g × 件数 )
抛重(g)  = 长×宽×高(cm) ÷ 6000 × 1000
售价(本币) = 到岸成本 × 汇率 × markup
```
两档 markup（差异只在是否开达人佣金；**当前只用 shelf**，`--limits` 默认只列启用档，加 `--all-schemes` 看全部）：

| 站点 | 货架场 shelf（affiliate 0） | 达人场 creator（affiliate 20%，净利 15%） |
|---|---|---|
| **MY（主攻）** | **1.295** ✅ 净利 12%，**免佣期扣点仅 10.78%**（到期切 1.556，见 pricing.yaml MY 段） | 1.844 ⏸ 停用 |
| TH | **1.62** ✅ 净利 12%（存量维护；起量后回调 2.05 / 25%） | 2.58 ⏸ 停用 |
| VN | **1.515** ✅ 净利 12% + **免交易手续费窗口**（扣点 22%，非 28%；存量维护） | 2.326 ⏸ 停用 |

> ⚠ VN 的 1.515 只在「入驻后 90 天免交易手续费」期内成立（本店约剩 60 天）。
> 窗口一结束扣点回到 28%，markup 必须改 1.75（仍 12% 净利）；到期前一天就要改，否则每单少算 6%。
> 到期日按 `YYYY-MM-DD` 回填到 `pricing.yaml → VN.schemes.shelf.waiver_expires` 并置
> `waiver_expires_confirmed: true`（当前为 `UNCONFIRMED`，`pricing_calc.py --check` 会持续 WARNING）；
> 日期已过而豁免开关未关时，`pricing_calc.py` 退出码 1 直接阻断全部 VN 定价计算。
> 同理 `commission_rate_verified: false` 期间，MY/TH/VN 佣金率未经后台费率表核实，`--check` 会输出 WARNING。

采购价上限（150g、shelf、`pricing_calc.py --limits` 实算，**不是手填**；2026-11-12 重算）：

| 站点 | 单件 | 2 件套（单件等效） |
|---|---|---|
| TH | 22.4 元 | 9.4 元 |
| VN | **26.1 元** | **11.3 元** |

**四条硬规则**：
- **价格带按「客单价（订单总额）」判，不按单件价判**（2026-11-12 修正，`pricing_calc.py` 已改）。
  旧口径 `单件价 ≥ 下限` 会让所有多件装永远判超带。但判带口径 ≠ 履约能力，见下条。现价带 TH $3.5–8 / VN $3.5–8.5。
- 下限从 $5 降到 **$3.5**：实测跨境家居头部价格中位 $3.37(TH)/$3.85(VN)，81%/78% 在 $5 以下。卡 $5 = 放弃八成货盘，这才是"只筛出 14 条候选"的真因。**也正因下限是 $3.5，单件 @250g/400g 就已落带内，根本不需要靠多件装抬客单价。**
- **开 20% 达人佣金必须同时把净利降到 15%**。若一边开邀请佣金一边要求 25% 净利，该组合无解。
- ⛔ **一件代发链路禁止做 2 件套 / 禁止按 2 件成本定价或宣称「2 件装」**（2026-11-12 第二批实测纠正，
  本条覆盖下方 `bundle_pieces` 与 strategy 3.3 的旧结论）：认领后 SKU 是 1688 单件规格、
  代发只向供应商下 1 件，按 2 件收钱发 1 件属**履约欺诈**。ERP 是否支持「1 SKU 对应多件采购」
  **未验证（UNKNOWN）**，在验证出捆绑发货能力前一律按 `pieces=1` 定价。
  上表「2 件套」列与 `bundle_pieces: 2` / `bundle_allow_same_offer` 只保留作定价模型备查，**不得据此铺货**。

算完先输出建议售价表给用户确认。**确认后不要用 `edit` 逐 SKU 填 `skus[].price`**——改用官方「批量设置售价」接口：

```bash
python3 scripts/set_price.py --id <ttl_id> --rate 2.8 --add 39.26          # dry-run，打印改前/预期/body
python3 scripts/set_price.py --id <ttl_id> --rate 2.8 --add 39.26 --save   # 提交
```

- 接口：`POST /item/crossBorder/platformCollect/batchOps`，`opsType:2` + `priceType:2`（公式模式）
- 公式：`新价 = 当前价 × rate + add`，**保留 SKU 之间的价格梯度**（比逐 SKU 填死价安全）
- 倍数反解：`rate = (目标高价 − 目标低价) / (当前高价 − 当前低价)`，`add = 目标高价 − 当前高价 × rate`
- 脚本自动做**改前/改后完整性校验**（描述长度、主图数、SKU 数、类目），任一丢失即告警
- ✅ 实测：32.05/47.81/57.05 → 129/173/199 THB，描述 1578 字符、5 主图、3 SKU 全部保留
- 细节与 `opsType` 全枚举见 `references/erp-pitfalls.md` 第三章

**⚠️ 认领后的售价不是你算的**：ERP 认领时会用内置线性公式自动铺价。实测反解三档 SKU
（¥2.98→47.81、¥4.20→57.05、¥0.90→32.05）完全吻合 **`THB = 采购价¥ × 7.574 + 25.24`**
（此为三点拟合的反推值，非官方文档口径）。它的毛病是**不区分重量、不区分类目费率、固定加价吞掉低价款利润**，
所以认领后必须用 `set_price.py` 按 `pricing_calc.py` 的结果重算，**不要直接采信采集箱里的初始价**。

**不要改全局定价模板**（ERP `#/cross/distribution/priceSetting`）来调价：模板是店铺级的，
会影响其他商品，且只能保持线性关系。单商品调价一律走 `set_price.py`。

#### 4.5 保存编辑（body 结构 2026-11-11 校正；旧版写法会把数据写坏）

接口：**`POST /item/crossBorder/edit`**（不是 platformCollect/save）。

**正确 body（来源：前端 `buildTKRequest` 返回值；✅ 2026-11-11 实测，保存后发布成功）**：
```json
{"id":"<ttl_id>","platform":"tiktok","sellerId":"<toSellerId>","currency":"THB",
 "toCategoryId":"852744","toCategoryIdTree":"600001,851848,852744",
 "toCategoryNameTree":"家居用品,家居收纳,收纳包","title":"<标题>",
 "itemModel":{"shopeeItemModel":null,"temuItemModel":null,
   "tikTokItemModel":{"...":"原 ttm","productCreateRequestV2":"<改后的 v2>"}}}
```
三条硬规律（写错就报废数据）：
1. `itemModel` = **整个 reqInfo 结构**（含 shopee / temu / tikTok 三个键），**不是**内层 `tikTokItemModel`
2. **不能**再传顶层 `reqInfo`（旧版这么写，保存后 `tikTokItemModel: null`、列表变 `MISSING_ITEM_DATA`、售价区间变空）
3. 顶层必须有 **`sellerId`**；类目三件套 `toCategoryId` / `toCategoryIdTree` / `toCategoryNameTree` 缺则"当前类目获取失败"

改动写进 `itemModel.tikTokItemModel.productCreateRequestV2`：类目 `category_id`、重量 `package_weight`、主图 `main_images[].uri`、SKU 价格/库存/规格名。

**保存成功判定 = 列表 serviceTagList（异常标签）减少/清空 + 类目生效 + 回读 getDetail 做语义校验**：
`description` 非空 / 主图 ≥1 / 无中文残留 / 无 B2B 词 / 被判 `L0_drop` 的图 URL 未残留 / SKU 数与价格未缩水。
⚠️ **不要用「description 长度不变、主图数不变」当判据**：图片闸门重建后长度本来就该变，
长度相等既不代表内容正确，也不能证明没被清空。`fix_sku.py` 的回读断言已按语义校验改写。
接口 `success:true` 仅表示请求被接受。

**getDetail 行为规律（关键）**：**有异常标签**的商品 getDetail 返回 `reqInfo.tikTokItemModel`；**异常清空后不再返回**（ERP 设计：getDetail 只服务待完善商品）——**不是数据丢失**。
⚠️ **但发布不受影响**：✅ 2026-11-11 实测，`fix_sku.py` 保存后（异常仅剩 `MISSING_WEIGHT`）直接 `ttl-publish` **成功**。
旧版 SKILL 由此得出的「保存即清空 → 发布必败」结论**已被推翻**，根因是 body 结构写错而非 ERP 设计。

**恢复被写坏的商品**：`ttl-del --ids <ttl_id>` → `ttl-claim --ids <货源ID> --region MY`。
注意重认领会**丢设置**（图片回原图、类目回未匹配、价格回原始值），只有标题保留当地语言——别把它当日常手段。

**图片上传通道（2026-11-19 破解，替代旧"外链捷径"）**：`getUploadPolicy` curl 可通——之前 500 的根因是 body 缺唯一必填参数
`{"fileUploadTypeEnum":"PIC"}`（与鉴权/浏览器无关，逆向证据见 `tiktok-api.md` 待补第 2 条）。
处理后的图用 `python3 scripts/erp_upload.py <files>` 上传得到官方 OSS 公网直链，再填 `main_images[].uri` 保存。
⚠️ aka.doubaocdn 外链只是"能保存"，发布时 302→json 会被平台拒图（pitfalls 13.4），**发布用途一律走 erp_upload.py**。

**图片与 description 重建写回走 `image_gate.py apply --save`**（按官方 edit body 结构，自动回读验收）；
只改 SKU/库存用 `fix_sku.py`，只改价格用 `set_price.py`，补重量用 `prep_publish.py`。
**不要用 `erp_client.py ttl-save`**：该子命令已于 2026-11-13 从 CLI 移除（body 是旧结构，
且从 `d["data"]` 取店铺/类目/币种而实测该字段恒为 null → 会写出 `MISSING_ITEM_DATA`）。
`prep_publish.py --images` 按逗号分隔替换时数量必须与主图数一致（不一致即中止，防错位）。

**保存后编辑弹窗实测表现（2026-10-29，重要）**：
- 接口保存成功后，**打开浏览器"编辑"弹窗，主图区显示"已选 0 张"**——页面端读取通道（getDetail）已被"异常清空"关闭，**已保存的泰文图无法在 ERP 页面编辑弹窗中看到/复核**
- 这不是数据丢失（接口确实写入成功），是 ERP 的 getDetail 只服务"待完善草稿"的设计所致
- **含义**：图片语言正确性必须在保存前（本地文件层面）逐张验证完毕，保存后无法再通过 ERP 复核

### Step 5 发布（写操作）

**接口**：`POST /item/distribute/batchPublish`（2026-10-29 破解，2026-11-11 参数修正）。此前"未找到该平台采集记录"
的根因是旧 body 结构错误（缺 publishConfig、itemId 用了 itemCenterNumIid、缺 sourceSellerId、categoryIds 类型错）。

```json
{"publishConfig":{"weight":0.3,"weightSet":1,"packageLength":30,"packageWidth":20,
  "packageHeight":5,"packageSet":1,"filterAlreadyPublish":true,"forbiddenWordDetectMode":0,
  "priceLimitEnable":false,"priceLimitMultiple":"1","useTiktokMultiCountryConfig":false,
  "tiktokOverflowConfig":{"detailEnabled":true,"titleEnabled":true,"titleLimit":255,
    "titleTruncateType":"END","skuNameEnabled":true,"skuNameLimit":20,"skuNameTruncateType":"END",
    "skuValueEnabled":true,"skuValueLimit":50,"skuValueTruncateType":"END","skuTotalEnabled":false}},
 "distribItemVos":[{"itemId":"<ttl_id>","sourcePlatform":"kdzs_erp",
   "targetPlatform":"tiktok","sourceSellerId":"1323965","targetSellerId":"<sellerId>",
   "targetSellerNick":"<MY_SHOP_NICK>",
   "categoryIds":"600001,851848,852744","categoryNames":"家居用品,家居收纳,收纳包"}]}
```

**要点**：
- `itemId` = **ttl_id**（TikTok 采集箱记录 id），**不是** itemCenterNumIid（用 center ID 会报"未找到该平台采集记录"）
- `sourceSellerId` 必填（实测 1323965）；`targetSellerNick` 必须与 sellerId 对应（认领时不选店会默认 PH）
- `categoryIds` / `categoryNames` 是**逗号分隔字符串**（不是数组），**传真实类目树**（旧版传空串虽能过，但类目校验信息会丢）
- `weight` / `packageLength|Width|Height` **按真实包裹填**——发布配置会自动把这三个值写到商品上，
  留默认 1kg / 10×10×10 等于给轻小件虚报体积重，直接影响头程

```bash
python3 scripts/erp_client.py ttl-publish --ids <ttl_id> --weight 0.3 --dims 30x20x5 --dry-run   # 先看参数
python3 scripts/erp_client.py ttl-publish --ids <ttl_id> --weight 0.3 --dims 30x20x5            # 提交
```

#### 5.1 发布前自检（勾完再提交，能省掉 90% 的 fail）
0. **`python3 scripts/price_gate.py` 退出码 0**（2026-11-12 新增，最高优先级，**先跑这个**）：
   逐条校验售价是否落在价格带、库存是否超 999999、类目/SKU 数据是否坏。
   退出码 1 = 禁止 `ttl-publish`。实测首次全量跑：20 条里 **19 条阻断**（价格破带 / 库存 9 位数 / 类目未匹配），
   其中含一个**已在售**的 29.13 THB（$0.87）牙刷架——每单净亏 ¥10+，旧版没有任何机制拦它。
0b. **`python3 scripts/image_gate.py verify --detail <cleaned_detail.json> --review <review.json>` 退出码 0**（2026-11-12；2026-11-13 收紧）：
   三通道（主图 / 详情图 / SKU 规格图）无中文/B2B 素材、description 已重建、被判删的图未残留；
   **且判定表必须覆盖当前全部图**——`--review` 已是硬要求，缺判定直接 FAIL（`NO_REVIEW`）。
   确实逐张人工确认过才用 `--no-require-review` 放行。退出码 1 时**禁止发布**。
0c. **`python3 scripts/prepublish_guard.py --refresh --ids <ttl_id,...>` 退出码 0**（2026-11-12 第二批新增）：
   递归扫整个 `productCreateRequestV2` 的汉字（`image_gate` 只扫 description/图片，**扫不到 `product_attributes`**）、
   规格名/库存/类目/重复认领，**并查描述质量（纯文本 ≥500 字符 / ≥2 段，第三批新增；
   不达标可用 `--allow-short-desc` 降级为提醒）**。**主图 <5 张硬拦**（2026-11-19 提级，
   `min_publish_main_images: 5`——围裙删到 1 张仍过闸是「有曝光没人点」直接根因；补足走 image_fix 修图保量，凑不出换货源）。
   `--refresh` 必加，否则读到 edit 之前的旧快照会误判。
   **本轮 #3 就是两个闸门全过、仍被 `[12052250] 不允许使用汉字` 拒发**（属性残留「悬挂/塑料」）。
0d. **`python3 scripts/listing_quality_gate.py --ids <ttl_id,...>` 退出码 0**（2026-11-18 新增，P0：解决「有曝光没人点」）：
   商品卡质量闸——前三个闸只保证「干净能发布」，不保证「能被搜到、有人点」。本闸校验本地 `ttl_detail_*.json`：
   标题 ≥40 字符且命中 ≥3 个当地热搜词根、主图 ≥5 张、属性 ≥5 项、≥1 条商品视频。
   `--all` 扫全部、`--summary` 落 `erp_data/card_quality.csv`、`--strict` 连主图角色证据缺失也阻断。
   **实测全量 34 款 全部阻断**（33 款标题词根不足、34 款属性 <5、26 款主图 <5、13 款无视频）——
   这是「发出去没人点」的直接原因。热搜词表放 `config/keywords/{my,th,vn}.txt`，用
   `--keywords-template --site MY` 生成骨架后**填真实后台热搜词**（骨架里的占位词不算数）；
   拉丁语系按词边界、泰语按子串匹配。退出码 1 = 禁止发布。
1. `ttl-detail` 能读到 `reqInfo.tikTokItemModel.productCreateRequestV2`（读不到 = 数据被写坏）
2. `description` 长度 > 0，且**无中文文本节点 / 无 B2B 字样 / 无 1688 模板残渣**（由第 0 项保证）；
   **纯文本 ≥500 字符、≥2 段**（第三批实测的后台优化项，由第 0c 项拦截）
3. 类目三件套齐全且在**家居用品**树下
4. 每个 SKU：`price` 在价格带内、`inventory` ≤ **999999**（建议统一 1000）
5. 每个 SKU 规格名：**纯尺寸 + 当地语言**，不含「自/最/第一/包退/工厂/包邮」这类词
6. 主图数 = `main_images` 数（闸门删完仍须 ≥1），首图白底、无中文残留；图片必须是回读一致的最终直链，禁用 `aka.doubaocdn.com` 跳转短链
7. 包裹重量与长/宽/高逐品真实，VN 缺尺寸由 `prepublish_guard.py` 阻断；禁止用默认 `10×10×10` 或估计值补位
8. `--dry-run` 输出的 publishConfig / 类目树 / 店铺都对；若返回 `[12052585] 存款金额不足`，先去卖家后台「资产/财务 → 保证金」补齐，不是商品文本问题

#### 5.2 平台前置校验错误码（fail 时逐条对，全部是商品数据问题不是接口问题）

| 错误码/原文 | 含义 | 修法 |
|---|---|---|
| `[12052250] 不允许使用汉字。修改产品属性的属性值名称: 悬挂` | **`product_attributes` 里残留中文**（2026-11-12 #3 沥水篮实测）。图片闸门不扫这个字段，前两个闸门全过也会 fail | 清空 `product_attributes=[]` 后重发即 success；发布前用 `prepublish_guard.py --refresh` 递归扫 v2 全部汉字 |
| `[12052153] 产品可变选项…内容词 (自)` | SKU 规格名含平台敏感词（「立体打印**自**透气」的「自」被判违规） | 规格名改纯尺寸当地语言 `ขนาด 50x70x30 ซม.`，去掉一切功能描述 |
| `[12052055] SKU inventory quantity … between 0 and 999999` | 库存沿用 1688 原值（9,981,384）超上限 | `fix_sku.py --stock 1000` 统一收敛 |
| `主图上传失败: 无效的参数` | 图片仍用 `aka.doubaocdn.com` 短链；短链 302 到签名直链但跳转响应 `Content-Type: application/json`，平台取图失败 | 解析到最终 `byteimg.com` 直链，逐张下载并与本地图 SHA-256 一致后回写负载 |
| `[12052018/12052019/12052020] 包裹长/宽/高是必需的` | VN 发布把包裹尺寸列为必填；负载缺真实长/宽/高 | 回货源详情/规格表补齐真实包裹尺寸；查不到就保留草稿，禁止默认 `10×10×10` 或猜测补位 |
| `[12052585] 您的存款金额不足` | 店铺保证金/账户资金不足，非商品字段问题；东南亚保证金通常在卖家后台「资产/财务 → 保证金」处理 | 人工登录 TikTok 卖家后台核验并充值；商品文本/图片闸门不能绕过该账号状态 |
| `商品描述不能为空` | `description` HTML 长度 = 0 | 编辑侧补描述 |
| `类目不能为空` | 重认领后类目回空、自动匹配失败 | 显式写类目三件套 |
| `tikTokItemModel不能为空` | `edit` body 结构错，商品数据被写坏 | 见 Step 4.5 正确结构；或删除重认领 |
| `TikTok店铺此地区没有设置开启的默认销售仓库` | TikTok 后台未建默认仓 | 人工去 seller 后台建仓 |

详见 `references/erp-pitfalls.md` 第四章（含实测时序与四路验证）。

#### 5.3 发布后四路验证（单一信号会骗人，必须交叉）
| 信号 | 命令/入口 | 通过值 |
|---|---|---|
| ERP 发布日志 | `erp_client.py logs --months 1` | `status = success` |
| 采集箱状态 | `erp_client.py ttl-list --region MY` | `状态 = 1` |
| 铺货配额 | `erp_client.py quota --region MY` | `已用` +1（2026-11-12 已封装，只读） |
| **TikTok 后台在售数** | 商品管理页 `在售 N` | N +1（最权威） |

`processing` 可能持续数分钟，**不要在 processing 阶段宣布成功**。

⚠️ **能力边界（2026-11-12 更新）**：铺货配额**已封装成 `erp_client.py quota`**
（`/trade/crossItemDistrib/quotaQuery`，body 传 `{}` 即可，返回按 sellerId 的
`totalNum/consumeNum/remainingNum` 与月度重置窗口；实测 TH 已用 8/5000、VN 0/5000）——**这一路可以脚本验证**。
**TikTok 后台在售数仍无接口**，只能人工看商品管理页。
发布成功时如实写「ERP 日志 success + 采集箱状态 1 + 配额已用 +1，后台在售数未验证」，
**不要把后台在售数写成已验证**。同理 `success:true` 只代表进队列，#10 曾先 `processing` 后转 `success`。

#### 5.4 商品编辑与发布的顺序（2026-11-11 实测后的推荐顺序）
```
claim → getDetail 落盘 → image_gate scan（主图+description 两通道）→ 多模态逐张判定回填 review.json
      → image_gate apply 重建（cleaned_detail.json / report.json）→ apply --save 写回图与描述
      → fix_sku / prep_publish 补类目·重量·SKU → set_price 公式改价（batchOps，只动价格）
      → image_gate verify（用 cleaned_detail.json，退出码必须为 0）→ ttl-publish → 四路验证
```
- **改价放在 edit 之后**：`batchOps opsType:2` 是最小写操作，只碰价格，不会回写 itemModel；
  放最后做可以避免 edit 把价格覆盖回原值
- **edit 正确结构保存后可以直接发布**（✅ 已实测），不需要再"删除重认领"
- 若确实需要重建：`ttl-del --ids <ttl_id>` + `ttl-claim --ids <货源ID> --region MY`（会丢图片/类目/价格设置）

**浏览器发布路径（保留备选，接口不可用时）**：采集箱勾选 → 批量发布 → "发布提示"点"继续全部发布" →
"发布产品"弹窗把**重量/尺寸改成真实值**（默认 1kg / 10×10×10 会虚报体积重）→ 确定发布。
- 带异常（如 `MISSING_WEIGHT`）的商品**可以**直接发布，发布配置会自动补重量
- 发布按钮可能在视口外需先滚动；发布前清理残留弹窗避免 no-modal
- 失败弹窗若报 `tikTokItemModel不能为空`，说明该商品曾被错误 body 写坏 → 走删除重认领

**公共采集箱定位（2026-10-28 实测）**：
- 公共采集箱"货源ID"输入框匹配的是 **ERP内部ID（numIid）**，不是 1688 offerId——用 `python3 scripts/erp_client.py items --ids <offerId>` 先查 numIid
- 已认领记录默认不进"未认领"列表，重新认领前需先在公共采集箱定位（按 numIid 搜索）

### Step 6 发布记录统计 + 数据回流
```bash
python3 scripts/erp_client.py logs --months 1
```
status: success/fail/processing/examine。

#### 6.1 T+2 / T+7 / T+20 数据回流（必做，否则第二批无法"放大赢家"）
**回流不再是"记得就填"**：排期与到期提醒已脚本化（`scripts/backflow.py`，2026-11-12 新增）。

```bash
python3 scripts/backflow.py sync                 # 从 ttl-list(状态=1) + logs(success 时间) 自动排期
python3 scripts/backflow.py due                  # 列出到期/逾期未填（退出码 1 = 有逾期）
python3 scripts/backflow.py mark --ttl-id <id> --stage t2 --t2-exposure 120
python3 scripts/backflow.py judge                # 按 cadence 阈值算 kill/培养/放大
python3 scripts/backflow.py stats                # 判定分布 + 赢家/待下架清单
```

**罗盘导出批量回填（2026-11-25 新增，P0-1）**：商家后台「罗盘 → 商品数据分析 → 详细信息」导出商品级 xlsx，
脚本只读本地文件批量解析回填，不联网、不碰 cookie/签名。关联链：罗盘商品 ID(numIid) → 发布后快照 outerId(货源ID) → 本地 list 的 ttl_id。

```bash
# 商品有新增时先刷新平台商品快照（翻页聚合全量 tiktok，numIid→货源ID 映射来源）
python3 scripts/site_goods.py list --platform tiktok --out erp_data/site_list_tiktok_full.json
# 预览（默认 dry-run，不写盘）；--stage t2 只填曝光 / t7 曝光点击加购 / t20 订单GMV
python3 scripts/backflow_import.py --file ~/Downloads/product_list.xlsx --stage t7 --region MY
# 确认匹配无误后写回 backflow.csv（已有非空值默认保护，--overwrite 才覆盖）
python3 scripts/backflow_import.py --file ~/Downloads/product_list.xlsx --stage t7 --region MY --apply
```

- 注意：「商品流量」页导出的是**渠道汇总表**（联盟/直播/视频/商品卡，无商品 ID），不能用于商品级回填；必须用「详细信息」页导出。
- `-` / `--` / 空 = 缺失，留空不填；`0` / `RM0.00` = 真实零值。T+20 的退款率、延迟发货率罗盘商品表没有，保持留空，不估算。
- 仍无官方 Open API 直连：离线复刻接口被设备指纹签名（msToken/X-Bogus/X-Gnarly）与风控卡住，短期不做；合规正路是申请官方 Open API 权限。

**入池体检（2026-11-18 新增，P0：破零最关键，独立于 T+7 数据回流）**：
不要等到 T+7 才发现没曝光。上架满 **48h** 就强制体检一次，两条证据都要记：

```bash
python3 scripts/backflow.py index-due            # 列出满 48h 仍未体检的款（退出码 1 = 有逾期，强制补做）
python3 scripts/backflow.py index --ttl-id <id> --found yes|no --keyword '<核心短词>' --exposure-7d <数>
python3 scripts/backflow.py index-stats          # 入池率 + 待换主图清单
```
1. 陌生账号（未登录过店铺、无浏览记录）搜 2–3 个核心短词，能否搜到本款 → `--found`；
2. 后台「商品分析 → 近 7 天曝光」→ `--exposure-7d`。
判定（`backflow.yaml` → `index_exposure_min`，默认 100）：搜不到或曝光 <100 = **未入池** →
满 48h 后换主图强制重收录（48h 内禁止动，见下方纪律 1）。`--found unknown` 不算通过，会进 `index-due` 催办。
**实测 2026-11-18：7 款在售全部逾期未检（挂 6–22 天一次没做）**——把「入池率」当成新 KPI，
两周后看「7 天曝光 >100 的款数占比」，不看出单。出单是结果，入池是过程。

TikTok 商家后台商品分析 → 回填到 `erp_data/backflow.csv`：

| 时点 | 字段 | 用途 |
|---|---|---|
| **T+2（48h）** | **推荐/商城曝光** | **入池检查（新增，破零最关键）**：曝光 <100 = 未入池 → 换主图强制重收录；≥100 才算发布成功 |
| T+7 | 曝光、点击、CTR、加购 | **四象限判定**：低曝光 + 高加购率(≥10%) = 搜索不匹配 → 只改标题补热搜词根，**禁止 kill**；高曝光低点击 → 换主图；高曝光低转化 → 调价。零曝光且无转化信号 → 等 T+20 |
| T+20 | 曝光、点击、成交单量、成交额、退款率、**履约率/延迟发货率** | 决定 kill / 培养 / 放大 |

**T+0～T+2 两条纪律（旧版完全没有，是"铺了没曝光"的常见原因）**：
1. **上架后 48 小时内不要改标题/主图**——改动会触发重新收录，等于把刚拿到的初始曝光清零。
2. **每 3 天回后台「商品 → 商品机会」提报关联（P0 动作，优先级高于铺新品）**：把「高潜力商品」「热门关键词」
   里与**已在售品**相关的词/卡片全部关联提报（商品机会页 → 找到匹配在售品的机会 → 「选择商品」提报，
   或卡片「添加同款商品」对齐子类目/品牌/款式三条件），平台推这些词时会顺带推你的商品；
   提报后回「我的提报」页核对审核状态，获批的在 `backflow.py traffic --opportunity-apply true` 记日期。
   这是不开达人佣金时**唯一能主动拿到的免费推流**，而旧版只把商品机会中心当"选品信号源"读，从来没把它当"流量入口"用。
   **零单期顺序纪律：先提报现有品拿量 → 再谈铺新品**；两者冲突时提报优先（铺新品有 1/6 命中率成本，提报是确定性收益）。
   **提报前置检查（2026-11-20 实测 VERIFIED；2026-11-23 工具化）**：关联提报走「机会详情→绑定现有商品→选同三级类目近30天在售品→添加关键词→提交」，
   第2步会**逐品校验标题违禁词**，任一被勾品命中（如首品标题里的 "Amazon" 竞品平台词）→ 提交按钮禁用、整单发不出去。
   → **进 GUI 前先跑 `python3 scripts/report_ready_check.py`**（一条命令输出在售品「可提报 ✓ / 被拦 ✗」两张清单：
   竞品平台词=词表 `config/banned_platform_words.txt`（连写变体 larisamazon 也抓）+ 汉字残留扫描；
   `--json` 供编排），弹窗里只勾 ✓ 品；✗ 品先改标题（ERP 注入词按 `config/i18n/ms.csv` 替换）再回来提报，不要硬提。
   实测闭环：apron chef 机会 → 绑定 Apron Kanvas(173863601339888) → 审核通过1、激励=获得商城页曝光（提报记录页 VERIFIED）。

**T+0 免费流量三件套（2026-11-18 新增，官方优先级：销售加速 > 早鸟价 > 广告）**：
旧版只写在 `references/selection-strategy.md` §8.3，没有任何检查项 —— 7 款零入池时这三项一个没做。
现已在 `backflow.py` 脚本化，上架满 24h 未完成即进 `traffic-due` 逾期清单：

```bash
python3 scripts/backflow.py traffic-due       # 列出未完成三件套的款（退出码 1 = 有逾期）
python3 scripts/backflow.py traffic --ttl-id <id> \
  --new-user-discount true --discount-set-at YYYY-MM-DD \
  --accelerator-task true --accelerator-done-at YYYY-MM-DD \
  --opportunity-apply true --opportunity-apply-at YYYY-MM-DD
```
只勾 `true` 不记日期 = 自证不清，按未完成处理。`judge` 会把未完成项追加进 action
（记录在 `erp_data/traffic_actions.csv`），不再只是文档里的一句话。

判定规则（`selection.yaml` → `cadence`）：T+20 仍无曝光无转化 → 下架换品；有成交 → 进第二批放大的方向池。
**履约率是链路信号不是选品信号**：延迟发货率上升 → 先换货代或转小批量备货（>30 单/日），不要靠继续换品掩盖。

### Step 7 真实 case 现状（详情见 `references/replay-log.md`）

- **Case C｜真空压缩袋（2026-11-11 ✅ 标准剧本）**：采集 → `ttl-claim` → `set_price` → `fix_sku` → `ttl-publish`
  → 四路验证，已确认在售（`1738505558347981911`，฿129–199，3 SKU）。它确立三条：
  `edit`（正确 body）→ `batchPublish` 可行、改价只走 `batchOps opsType:2`、
  getDetail 保存后不返回 itemModel ≠ 数据丢失。
- **Case A/B（2026-10-29 失败）**：`tikTokItemModel不能为空` 根因是 body 结构写错，已被 Case C 推翻。
  仍然成立两条：类目自动匹配不可靠（必须显式写三件套）、重认领会丢图片/类目/价格设置。
- **失败第一动作**：读 `失败原因` 原文，按 5.2 错误码表逐条修，不要用同一份数据重试。
- **接口化程度表**：见 `references/replay-log.md` 末节（已含新增的选品取证、数据回流两行）。

## References

- `references/results-matrix.md`：**成果与边界矩阵（作品视图）**——已验证闭环 / 未验证缺口、量化指标、事故→规则→回归测试追溯案例、可复用资产与路线图。对外介绍或评审先读这份。
- `references/replay-log.md`：**各批复盘与真实 case 完整原文**——第二批 10 品、第三批后台回执、第四批站点商品模块、Case A/B/C、MY 首品 / batch12 / batch13、流程优化与耗时账本。主文件只留「改变动作」的结论，追溯结论来源读这里。
- `references/erp-pitfalls.md`：**接口逆向与踩坑实录（出问题先查这个）**——完整成功时序、前端 chunk 逆向方法、
  `edit` 正误 body 对照、`batchOps` 四类 opsType、发布错误码表、1688 采集/授权坑、token 抓取原理、浏览器通道边界、发布前自检清单；
  **第十章 = 2026-11-12 第二批 10 品复盘；第十三章 = 2026-11-13 VN20 路线、图片回读、单件 SKU 与写操作纪律**。
- `references/selection-strategy.md`：选品模块核心（九章）——一件代发三条铁律、履约链路与城市判据、成本结构与采购价上限矩阵、数据源分层与可信度分级、红线与供应链门槛、四维评分、TH/VN差异、破零节奏与数据回流、输出格式。Step 0 前必读。
- `references/linkfox-ip-detection-coverage.md`：睿观 IP 检测对 TH/VN 覆盖实测——**只有外观专利支持 TH**，商标/实用新型不支持，版权噪声大；brand_ip 红线仍靠人工。涉及 IP 检测前必读。
- `references/tiktok-api.md`：TikTok接口契约（已实测+待补）
- 图片闸门（两通道扫描 → 多模态整图判定 → description 重建 → 发布前硬闸）见上方 Step 4.3；
  执行脚本 `scripts/image_gate.py`、参数 `config/image_gate.yaml`
- `config/image_gate.yaml`：B2B 词表（**仅文本残留兜底，不是判定规则**）与发布前硬闸阈值
  （`min_clean_main_images` / `max_cjk_chars_in_description` / `require_no_template_residue`）；
  **描述质量阈值 `description_min_plain_chars: 500` / `description_min_paragraphs: 2`**
  （2026-11-12 后台回执，软提示；纯文本口径为保守实现，见 pitfalls 第十一章）
- `config/pricing.yaml`：站点定价参数（一件代发全成本、两档 markup、价格带）
- `config/selection.yaml`：选品参数（履约红线/门槛/四维权重/需求过滤/竞争与内容力口径/单量分层/铺货节奏）
- `scripts/pricing_calc.py`：售价计算与价格带校验（`--cost` 算售价 / `--limits` 反推采购价上限 / `--check` 自检）。纯标准库，配置改完必跑 `--check`。
- `scripts/kdzs_fastmoss.py`：FastMoss 选品数据（**免 token**，ERP 后端透传；`--categories` / `--live-fx`）
- `scripts/linkfox_sourcing.py`（2026-11-18 新增）：LinkFox **1688 找货**取数**本地加工器**（不联网）——
  `screen` 找货预筛（代发/48h/起订量/体验分）、`same-source` 同款跨供应商（`--enrich` 补公司）。
  吃 MCP 落盘 JSON（见 `erp_data/linkfox/README.md`），产出仍须过 `select_gate.py`。
  ⛔ `spec-terms`（EchoTik 竞品规格名）已于 2026-11-20 停用，代码留档但不再调用——TikTok 数据统一走 ERP 透传。
- `scripts/erp_client.py`：采集/认领/删除/发布/日志 主客户端（`ttl-publish` 支持 `--weight/--dims/--dry-run`；`ttl-save` 已标禁用）
- `scripts/erp_token_grab.py`：从 Chrome LevelDB 自动捞 JWT（`--save --check`），不用手工复制
- `erp_data/README.md`：**落盘目录约定**——顶层只放有效资产（`case_category_tree.json` 查类目 ID、`tiktok_shops.json` 拿 sellerId），
  跑批产物按 `_run/<日期_用途>/` 隔离，历史快照进 `_archive/<日期_用途>/` + `MANIFEST.md`。
  ⚠️ 脚本输出是**相对路径**：在根目录跑落 `erp_data/`，在 `scripts/` 下跑落 `scripts/erp_data/`（11-11 成功发布的数据在后者），读之前先确认在哪。
- `scripts/set_price.py`：**批量公式改价**（`batchOps opsType:2`），自带改前/改后完整性校验
- `scripts/fix_sku.py`：**修 SKU 规格名/库存**（官方 `edit` 正确 body 实现，发布前置校验修复主力）
- `scripts/batch_prep.py`：**批量预处理编排**（破带重定价 + 库存收敛 + 复检，默认 dry-run）。
  `--site MY [--ids ...] --save`。只动破带商品、已在带内的一律不碰；售价为空/缺重量/类目未匹配的一律转人工不猜。
  按每天 3 小时可投入，不批量化就铺不完 150 款
- `scripts/select_gate.py`：**collect 前选品硬闸（2026-11-12 新增，纯本地只读）**——
  校验候选 CSV 的干净主图≥3、详情可重建、库存≥200、MOQ=1、快递≤1 天、48h 书面确认、**真包邮证据**、
  实重/抛重≤400g、pieces=1、红线 pass、类目属家居树，并按最高 SKU 采购价现场重算价格带
  （不信任人工 in_band）。`--init-template` 出模板；退出码 1 = 禁止 collect。
  脚本不联网不看图，只认人工/多模态回填的证据列，专治「门槛写在文档里但没执行」（第二批白跑 6 品的根因）
- `scripts/route_gate.py`：**货代路线复核（2026-11-13 新增，纯本地只读）**——校验 `ship_city`、`forwarder_city`、
  `ship_transit_days`，仓库必须在已绑定列表内；缺发货城市、缺仓、缺到仓天数或 >1 天即退出码 1。
- `scripts/listing_quality_gate.py`：**商品卡质量闸（2026-11-18 新增，P0，纯本地只读）**——读本地
  `ttl_detail_*.json`，校验标题 ≥40 字符且命中 ≥3 个当地热搜词根、主图 ≥5、属性 ≥5、≥1 条视频。
  `--all` / `--ids` / `--site` / `--strict` / `--json` / `--summary`（落 `erp_data/card_quality.csv`）；
  `--keywords-template --site MY` 出热搜词表骨架（`config/keywords/{my,th,vn}.txt`，需填真实后台词）。
  只保证「能被搜到、有人点」，与 `image_gate`（干净）/ `prepublish_guard`（能发布）互补，三者都要过。
- `config/keywords/{my,th,vn}.txt`：当地热搜词根表——拉丁语系按词边界、泰语按子串匹配，<3 字符的词不参与（误命中）。
  `my.txt` 首版含 7 个官方 VERIFIED 词根（2026-11-19 商品机会→热门关键词 tab 实测）+ 种子词，逐周回后台扩充。
- `scripts/opportunity_keywords.py`：**热门关键词收割器（2026-11-20 新增）**——浏览器抄录的机会页关键词 CSV →
  一次同步**两张表**：`config/keywords/my.txt`（词根，供 listing_quality_gate 标题校验）+
  `erp_data/keywords_my.txt`（官方原词整串，供 pool）。修饰词表 `config/keywords/modifiers_my.txt` 排除
  tahan lasak/murah 类修饰语不拆根；计量单位（pcs/50）自动过滤；`--apply` 幂等；`--audit` 查两表覆盖缺口。
  周常动作（P0）：抄词 → dry-run → apply → git diff 复核 → 重跑 listing_quality_gate。
- `scripts/report_ready_check.py`：**提报前置检查（2026-11-23 新增，纯本地只读）**——进「绑定现有商品」GUI 之前
  扫在售品标题两类弹窗硬拦：竞品平台词（词表 `config/banned_platform_words.txt`，去非字母子串匹配，
  抓 laris Amazon/连写变体）+ 汉字残留。输出「可提报 ✓ / 被拦 ✗」清单，弹窗里只勾 ✓ 品；
  ✗ 品先改标题再提报。`--json` 供编排。治 11-20 靠 6 次截图试错才定位 "Amazon" 被拦的教训。
- `scripts/opportunity_intake.py`：**商品机会判读闸门（2026-11-19 新增，纯本地只读）**——吃机会卡片抄录 CSV，
  按品牌红线/价格带/类目一致/站内数据四项判 reject/direction_only/candidate。规则配置 `config/opportunity_intake.yaml`
  （known_brands / generic_terms / 参考价币种 / 需求下限，含数据口径警示注释）。`--title ... --site MY` 单品快查。
  candidate ≠ 可采集，仍需 select_gate；专治「照单铺机会品踩品牌/低价坑」。
- `scripts/opportunity_to_sourcing.py`：**机会卡片 → 1688 候选编排（2026-11-19 新增；2026-11-20 三通道：bailian|linkfox|aibuy）**——内部复用 intake 判读，
  对非 reject 行检索 1688：**默认 `--channel linkfox`**（`--linkfox plan` 出 `erp_data/linkfox/sourcing_tasks.json` 取数清单 →
  Agent 经 MCP proxy_call 原样落盘 `erp_data/linkfox/mcp/*.json` → `--linkfox read` 产候选，脚本自身零网络，守 LinkFox"只能经 MCP"硬约束）；
  `--channel aibuy` 为免积分 fallback（子进程调 aibuy_rank_client.py，复用限速与 mtop 握手）。
  offerId 去重，linkfox 默认保留检索原序（`--rank-by sales` 才按 salesQuantity 重排），aibuy 按 monthSold 降序 topN；
  输出新增 `image_url / ship_city_raw / consign_price_cny / data_source` 列（seed 侧 selection_intake 消费：
  详情页城市缺失时用 ship_city_raw 归一化补 ship_city 并在 reason 注明来源口径）。`--dry-run` 只看计划。
  同款判定归人工；检索全失败退出码 3（linkfox=落盘缺失，aibuy=mtop 被风控），失败行不自动跨通道补位。
- `scripts/backflow.py` 的 `index` / `index-due` / `index-stats`：**入池体检（2026-11-18 新增，P0）**——
  上架满 48h 强制做一次「陌生账号搜词 + 7 天曝光」判定，逾期 `index-due` 退出码 1 催办，
  结果落 `erp_data/index_check.csv`（与 `backflow.csv` 的 T+7 数据回流分开）。判据从出单改为入池率。
- `scripts/price_gate.py`：**发布前价格/库存硬闸**（读 `ttl-list` 落盘 CSV，逐条判价格带/库存上限/类目异常，
  退出码 1 = 禁止发布）。另有 `--hint --cost <采购价> --pieces <1|2>` 反推建议挂价与本币价格带。
  **任何 `ttl-publish` 之前必须先跑它**——ERP 认领自动价（≈采购价×7.574+25.24）不区分重量与类目费率，
  低价品必然破带，这是实测 19/20 条不合格的根因。
  **全量跑会被店铺历史脏草稿拖成退出码 1** → 用 `--csv` 传本批子集隔离验证（本轮 9 品子集退出码 0、全量 1）
- `scripts/prepublish_guard.py`：**发布前最后一道自检（2026-11-12 新增，纯本地只读）**——
  递归扫 `productCreateRequestV2` 全字段汉字（补 `image_gate` 扫不到 `product_attributes` 的洞）、
  SKU 规格/库存/类目/重复认领，**以及描述质量（纯文本 ≥500 字符 / ≥2 段）**。
  `--refresh --ids ...` 强制重新落盘再查，退出码 1 = 禁止发布；
  `--allow-short-desc` 把描述质量降级为提醒（需用户明确同意）。
  实测（加描述检查前）：4 品刷新后全 PASS；注入中文 `product_attributes` 立即 FAIL。
  ⚠️ 第三批起新增描述检查，本批 10 个本地快照有 9 个纯文本 <500 字符、8 个不足 2 段，
  历史品跑 guard 会 FAIL 属预期
- `scripts/listing_payload_audit.py`：**本地上架负载审计（2026-11-13 新增，纯本地只读）**——
  核目标站点、公网图数量、当地语言标题/描述、单件 SKU、库存/价格、源 SKU 序号、重量；尺寸缺失给提醒。
  本地通过不替代 ERP 真实详情闸门。
- `scripts/site_goods.py`：**站点商品（平台侧在售）客户端（2026-11-12 新增）**——
  `list` / `detail` / `log` 只读；`update-desc` 默认 dry-run、`--save` 才写，自动删
  `replicatedProducts` 并回读对比。⚠️ 回读有数分钟缓存延迟，别立刻判失败。
  上/下架 `ops` 未实测，**脚本刻意不实现**。接口契约见 `tiktok-api.md` 末章
- `scripts/prep_publish.py`：铺货前置一体化（按 SKU 梯度改价 + 补重量 + 回读验证 + 图片替换）
- `scripts/image_gate.py`：**详情页图片闸门**（`scan` 三通道扫描出待判定清单：主图 / 详情长图 / **SKU 规格图** /
  `apply` 按 L0–L3 重建并可选 `--save` 写回 / `verify` 发布前硬闸，退出码 1 = 拦截，
  **`--review` 为硬要求**，缺判定报 `NO_REVIEW`）。纯标准库，scan/apply(dry-run)/verify 全程离线；
  **脚本不判图**，判定由多模态模型回填 review.json。
- `scripts/i18n.py`：**译名表查询（2026-11-13 新增）**——`config/i18n/<lang>.csv` 是标题/描述/规格名译名的唯一来源，
  `get()` 未命中返回 `None`（不猜、不套模板）；`--list / --get / --missing-check` 三个子命令做查表与缺项体检。
- `scripts/verify_public_images.py`：**公网商品图回读核验（2026-11-13 新增）**——按商品逐张下载 URL，
  **先按 sha256 反查本地图**（命中即证明这张就是本地某张，并能指出错配到哪一张），未命中再按同下标比像素 RMS，
  在 `--rms-threshold`（默认 3.0）内视为重编码通过；下载失败或内容已变才退出码 1。禁止复用并发上传时错配的 path/短链映射。
- `scripts/erp_upload.py`：**ERP 官方图片上传通道（2026-11-19 破解）**——`getUploadPolicy` 正确 body
  `{"fileUploadTypeEnum":"PIC"}`（旧"curl 直调 500 只有浏览器能通"= 缺该参数的 NPE，见 pitfalls 第十四章）
  → OSS 表单直传 → 官方公网直链，自带 GET 回读 SHA-256 核验。发布用途的图一律用它，不再依赖 aka 短链。
- `scripts/aidge_http.py`：**阿里云 Aidge 纯标准库客户端（2026-11-19）**——POP V3 签名（ACS3-HMAC-SHA256），
  零第三方依赖（SDK 安装被代理+Py3.14 轮子问题拖死后的替代方案，实测比 SDK 路线快 1 小时）。
  真实 Action 名与产品页标签不同：抠图=`ImageMatting`、消除=`ImageRemove`、识别=`ImageRecognition`、
  图翻=`ImageTranslationPro`（全表可拉 `api.aliyun.com/meta/v1/products/Aidge/versions/2026-04-28/api-docs.json`）。
  凭证 `.env` 的 `ALIBABA_CLOUD_ACCESS_KEY_ID/SECRET`（RAM 用户 aidge-image-fix）。
- `scripts/image_fix.py`：**L1/L2 修图管道（2026-11-19）**——probe（元素识别补 seen_text 证据+过杀预警）→
  fix（ImageRemove 元素模式/Position 补刀、ImageTranslationPro、ImageMatting → 下载 → erp_upload 转存 → uri_map）→
  finalize（复检校验）。用法与五条款见 Step 4.3。实测围裙 3 张过杀主图救回（成本 ¥0.16 内，免费额度覆盖）。
- `scripts/category_template.py`：**类目模板表工具（2026-11-20 P0-1，对标店小秘「产品模板」）**——
  `config/category_templates.yaml` 存 类目→ERP类目ID/树/重量档/尺寸/货代城市/描述模板文件；
  `match`（候选标题词根命中）/ `record`（**从已发布成功 ttl_id 自动沉淀模板**，类目树 DFS 反查祖先链+AVAILABLE 校验，
  非 AVAILABLE 拒绝入库）/ `render`（描述占位填充，**未填占位=拒绝**，≥500字符/≥2段质量闸内置）。
  消灭每品重复的「查类目/定重量档/写描述/定城市」≈12 分钟。
- `scripts/ttl_edit.py`：**待发布记录一次 edit 到位（2026-11-20 P0，固化当日 4 次手写 edit）**——
  模板套用+SKU 款型精简（--keep-prefix seller_sku 前缀）+按各自 source_price 公式定价（出带即拒）+
  主图（**非 cbu01 域直接拒绝**，713 实测 kdzs/dashscope TikTok 拉不到）+描述渲染，默认 dry-run --save 才写。
  **2026-11-24 扩四参数（并进当日一次性脚本 edit_my5.py，全确定性）**：
  `--title` 消费者向当地语标题（含汉字拒）；`--attr-names "颜色=Warna,尺寸=Saiz,规格=Saiz"` 改属性名
  （ERP 认领不译 name，本批 5 品全中招）；`--sku-map <json>` 按 seller_sku 重写规格 value_name（英文/当地语）；
  `--sku-code erp_data/sku_code_map.json` 汉字货号换 ASCII 码（oauth apply 自动产出）。
  提交前内置 **PAYLOAD_CJK 终检**：扫整个上传 payload 的汉字，有则拒绝提交——prepublish 的 [12052250]
  不再可能第一次撞在发布端。
- `scripts/batch_review.py`（2026-11-24 新增）：contact sheet 目视结论 → review.json 批量回填
  （`--clean 6,7,8 --dirty 1,2,3 --flag 17` 编号=main_NN；同 URL 的 SKU 通道自动继承），消灭逐张手工编辑
  verdict。**verify 恢复硬要求（--review 必传，禁止 --no-require-review——本批用过是过渡态，下次不允许）**。
  顺序仍守既有铁律：`ttl_edit --save`（PAYLOAD_CJK 终检）→ `image_gate scan` → sheet 目视 + 本工具回填
  → `apply --save` → `verify`——scan 在 edit 之后，判定天然不会被 edit 作废。
- `scripts/hero_main_images.py`：**款型级主图供给预检（2026-11-20 P0-2）**——选品期按款型统计可用主图
  （通用干净 main 图 + 本款专属 SKU 图，**款型专属图不得计入他款通用供给**——针线盒中途换款 8 分钟教训）；
  供给<5 的款型 FAIL 并给处置顺序（换货源>剔除，禁止修图换链凑数）。针线盒反事实验证：方款 6 PASS、椭圆/圆款 0 FAIL。
- `scripts/linkfox_oauth.py`：**1688 OAuth sku 取数加工器（2026-11-20 新增，纯本地不联网）**——把详情页抓取降级为
  「只补重量/主图组/发货地」的单点操作。两段式：`scan --batch prefill.json` 列出缺价格/库存字段的 offer →
  Agent 走 MCP `alibaba1688/sku`（先决 authorizedStores=ACTIVE）原样落盘 `erp_data/linkfox/mcp/sku_<oid>.json` →
  `apply --batch prefill.json` 回填。纪律：**缺才填、不覆盖详情页值、MOQ 禁止猜（sku 无此字段）、
  offerId 与落盘不符直接拒绝、重量不填**；顺带解析规格值里的尺寸文案（"长25宽18高14cm"→dims_parsed，
  seed 自动进 dims_cm 并在 reason 注明来源）、收集规格图（不混入主图组）。数据=用户本人账号 OAuth 官方回包（VERIFIED 级）。
  ⚠ sku 单次扣费 UNKNOWN，从消耗记录页逐笔核对；授权过期需重走 authorizeUrl（必带 accountName）。
- `scripts/failure_router.py`：**失败路由器（2026-11-25 新增，Tier 1 确定性规则，纯本地不联网）**——把发布 `fail` 原因原文路由到已验证规则（诊断 + 修复命令 + 证据），一条报错含多个错误码时返回全部命中规则。
  规则表 `config/failure_rules.json`（15 条，全部来自实测案例，不收推测规则）；golden set `tests/fixtures/failure_golden_set.json`（23 案例 = 日志 20 条去重原因 + 文档记录 3 条）。
  子命令：`classify --text`（单条）/ `scan`（glob 全部 tiktok_logs.csv 的 fail 行）/ `backtest`（回测，2026-11-25 实测 23/23 命中、304 条 fail 行 100% 路由）/ `apply --ids`（修复计划/受控执行）。
  `apply` 安全边界：仅对点名清单执行、单次 ≤10 货源、默认 dry-run 只出计划；`--execute` 只执行规则表带 `auto_exec` 的规则（当前仅 stock_overflow=fix_sku 收库存、silent_skip=先查 site_goods status 再 --force 重发），tier=manual 与无 auto_exec 的规则一律只出命令不执行；每条写命令先 dry 变体、失败即停；ttl_id/weight/dims 从 `erp_data/_run/pipeline/<offer>.json` 解析，缺参数降级为计划；journal 落盘 `erp_data/_run/failure_router_apply/`；**执行完 ≠ 修复成功，必须人工跑四路验证**。
  退出码：0=全部路由；1=存在未路由失败 → **Tier 2 转 LLM 读 pitfalls/replay 全量案例诊断，确诊后回填规则表与 golden set**（数据飞轮）；Tier 3（embedding 检索）仅在案例库规模超上下文时启用。
- `tests/`：**离线测试套件（2026-11-13 新增，持续增长）**——`python3 tests/run.py` 一条命令跑完（220 项，约 5 秒，不联网、不需要 token；2026-11-25 实测）。
  覆盖 i18n 迁移等价性、图片三通道/过期判定/覆盖率硬闸、SKU 身份校验、公网图哈希匹配、已删子命令回归。
- `scripts/selection_intake.py`：**选品取证自动化（2026-11-12 新增）**——`pool` 多关键词批量拉 FastMoss 并按出单率分档、
  `image` 取 1688 详情页字段（成本/最高 SKU 价/库存/起订量/重量/发货城市）并拼 contact sheet、
  `seed` 按 `select_gate.TEMPLATE_HEADER` 生成候选骨架。**只抄事实，判断列一律留空给人工/多模态**。
  ⚠️ 1688 详情页有 IP 级 baxia 风控：首连可通、连打会锁，降级为检索通道单图（linkfox|aibuy 按 data_source 如实标注，Cookie 放 `scripts/ali_cookie.txt`）；
  2026-11-20 起 `--from-csv` 消费 opportunity_sourcing.csv 的新列（image_url/ship_city_raw/company/data_source）：
  详情页城市缺失时用 ship_city_raw 经 city_from_location 归一化补 ship_city，来源口径写进 reason
- `scripts/backflow.py`：**数据回流闭环（2026-11-12 新增）**——`sync` 自动排 T+2/T+7/T+20、`due` 报到期待填（退出码 1 = 有逾期）、
  `mark` 抄数值、`judge` 出 kill/培养/放大、`stats` 出分布与清单。数据仍要人工从后台抄，但**不用再记哪天看哪款**
- `scripts/health_check.py`：**接口契约 smoke test（2026-11-12 新增，只读）**——跨 skill 依赖哈希与上游漂移、
  ERP 鉴权、FastMoss 免鉴权、铺货配额可读性、**前端版本指纹（变版即 WARN）**、定价自洽、数据列契约。
  `--skip-net` 可离线跑。**开工前先跑它**，比在铺了半批货之后才发现接口变版便宜得多
- `scripts/bailian_sourcing.py`：**百炼「1688智能选品」官方 MCP 客户端（2026-11-20 新增）**——提供方=阿里巴巴(中国)，
  工具 `find_product`（query 文本搜 / imageUrl 图搜同款；**勿把商品ID当 query 传**，实测走文本召回返回无关品）。
  脚本直连 Streamable HTTP（凭证=skill `.env` 的 DASHSCOPE_API_KEY；LinkFox 的"只能经 MCP"约束**不适用**于此）。
  计费 ¥0.058/次 + 免费包 1000 credits。核心产出=官方履约标签：`is_late_ship_compensate`（晚发必赔）、
  `lgt_3m_24h_avg`（24h揽收率，0.0=UNKNOWN）、`pay_ord_cnt_30d`、库存/起批量/复购/开店年数。
  `probe` 子命令做连通自检；`search`/`search-image` 落 JSON+CSV 到 `erp_data/bailian/`。
- `scripts/aibuy_rank_client.py` + `config/dependency_contract.json`：**跨 skill 依赖内聚**——遨虾客户端已复制进本 skill
  （不再运行时依赖 shopee-light-listing），哈希与权威源登记在 contract 文件；`health_check.py` 负责发现漂移。
  **2026-11-20 角色调整**：找货检索降级为 fallback（免积分备选），主通道切 LinkFox MCP；opprank 榜单（Step 0.2③ 方向信号）仍用它。
  mtop token 过期/风控报错在预期内——报错看原文、勿重试风暴，找货改走 `--channel linkfox` 两段式即可。
