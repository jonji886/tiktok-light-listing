---
name: tiktok-light-listing
description: "TikTok Shop 跨境店铺货（快递助手ERP跨境版，一件代发模式）：选品(免token拉FastMoss单品数据→官方商品机会中心/榜单取需求信号→1688找货→红线与供应链门槛→履约/内容力/实净利/站内竞争四维评分+价格带校验出清单)→1688链接采集→公共采集箱→认领到TikTok站点店铺(TH/VN)→二次编辑(批量公式改价/图片擦文字翻译/首图白底/修SKU规格名与库存)→发布→四路验证→记录统计→T+7/T+20数据回流。全链路接口化，仅人工接管登录与货代确认。已跑通真实闭环(2026-11-12 泰国店在售)。仅适用于 <your-erp-host> + YOUR_SHOP_TH/VN 两个店铺，店铺主营类目为家居用品(选品仅限该类目)。不做其他ERP/其他平台。"
---

# TikTok Shop 跨境铺货（选品 → 1688采集 → 认领 → 编辑图片/翻译/定价 → 发布）

## Overview

在快递助手ERP跨境版内，把1688货源铺到 TikTok Shop 东南亚店（店铺 TH + VN 均已开，**破零期主攻 VN**，理由见 strategy 第七章），店铺主营类目为**家居用品**。

链路分6段：
0. **选品**：FastMoss 单品数据（免token）+ 官方后台取需求信号 → 1688 找货 → 红线+供应链门槛 → 四维评分+价格带校验 → **按发货城市聚簇 + 查货代覆盖（TIKTOK 标签）** → 出候选清单
1. **采集**：1688 offerId → ERP公共采集箱（复用旧接口）
2. **认领**：公共采集箱 → 认领到指定TikTok站点店铺（TH/VN）
3. **编辑**：TikTok采集箱里逐品二次编辑——标题已自动翻译，图片要处理（擦广告字/翻译功能字/首图白底），SKU 规格名与库存要过平台校验，价格用**批量公式改价**
4. **发布**：提交到TikTok发布队列（附真实重量/尺寸）→ **四路验证**
5. **统计**：发布记录回查 + T+7/T+20 真实曝光/点击/履约数据回流

**本版本的模型前提（2026-11-11 重构，与前版不同）**：新人 · 无自有货源 · 1688/拼多多单件下单 → 国内货代 → TikTok 中转仓。
由此产生的三条硬结论，写在 `references/selection-strategy.md` 第一至三章，执行前必读：
- 官方时效为 **48h 打包 + 3 个自然日到仓扫描**（不是 96 小时）→ **判据是「发货地到货代仓的快递天数」，不是「同省」**：≤1 天绿档、2 天卡死、≥3 天必超（见 Step 0.0）
- 国内段成本是 **单件快递 5 元 + 货代 2 元**（不是批量均摊 1 元），采购价上限由价格带反推，用 `scripts/pricing_calc.py` 校验
- **暂不开达人佣金**（2026-11-11 决定），只走货架场；`creator` 档在配置里 `enabled: false` 备查

**本版已达成（2026-11-12 首单闭环，可作为标准剧本）**：真空压缩袋经全接口链路铺到 **YOUR_SHOP_TH**，
TikTok 后台确认为「在售」，฿129–199，3 SKU。链路 = 采集 → 认领 → `set_price` 公式改价 → `fix_sku` 修规格名/库存
→ `ttl-publish` → **四路验证**（ERP 日志 / 采集箱状态 / 铺货配额 / 店铺后台在售数）。
**凡是接口相关的坑，先查 `references/erp-pitfalls.md`**（body 结构、错误码、逆向方法、浏览器边界全在里面）。

## 适用范围

- **适用**：<your-erp-host> + YOUR_SHOP_TH(<TH_SELLER_ID>) + YOUR_SHOP_VN(<VN_SELLER_ID>)
- **不适用**：其他ERP、TikTok自运营店、其他站点(PH/SG/MY后续再加)、Shopee/Lazada

## 动作分层

- **AI自动**：FastMoss 单品数据（免token）、官方后台取需求信号、1688找货、四维评分、价格带校验（`pricing_calc.py`）、红线与供应链门槛自动剔除、采集、认领、列表对账、价格计算、**批量公式改价**、**修 SKU 规格名/库存/类目/主图**、**两通道图片扫描与整图判定（多模态目视）**、**description 重建**、**图片闸门 `verify`**、保留图的擦字/翻译功能字/首图白底、发布提交、**四路验证**、记录统计
- **硬红线自动阻断**：品牌侵权/IP联名、液体化工、带电、食品保健医疗、宗教、易碎、活体、多规格价差超限、平台限售需认证、跨类目（复用shopee selection-rules + TikTok限售/类目约束）
- **人工接管**：ERP登录/短信验证码、**1688 授权（凭证仅约 10 小时有效，批次前必查）**、**货代覆盖确认（ERP「货代管理」按城市查、筛 TIKTOK 标签，无接口）**、供应商 48h 发货的书面确认、**整图判定低置信度时转人工**、图片处理结果抽检（前10个人工过一眼）、候选清单最终确认（写操作前）

## 执行前约定

- 登录态：`scripts/erp_token_grab.py --save` 自动取 JWT → `scripts/erp_token.txt`；接口头 `qnquerystring` + `appType:3`
- 接口为未公开内部接口：**报错先读 `失败原因`/`errorMessage` 原文，按 `references/erp-pitfalls.md` 的码表定位**，
  不要用同一份数据重试；确认是接口不可用时再回退浏览器操作
- 所有写操作(claim/edit/publish/del)仅对用户明确点名的清单执行；改价、修 SKU 一律先 `--dry-run`
- **拿不到数据就写"拿不到"，不要用旧快照或推测值补位**
- **发布前 `image_gate.py verify` 退出码必须为 0**（退出码 1 = 图片闸门拦截，禁止 `ttl-publish`；
  详见 Step 4.3 / 5.1）。脚本不会看图，整图判定由多模态模型回填 `review.json`，未判定即拒绝 apply

## 第二批实测（2026-11-12，10 品 → 4 品发布）：下次先读这 10 条

> 只留「改变动作」的结论，完整复盘见 `references/erp-pitfalls.md` 第十章。

1. **既有的四道门槛必须在 Step 0 真正执行**：本轮 10 品白跑 6 个（5 品无干净主图、1 品跨类目）。
   ⚠️ 门槛不是没有——`selection.yaml → supplier_gates.clean_consumer_images_min: 3` 和 strategy 5.2 早就写了，
   是**选品时只用了遨虾搜索结果（只有 offerId/价格/月销，不返回图片），压根没拉主图目视**。
   ① 类目 `AVAILABLE` 且属主营家居树（`case_category_tree.json`）② **先拉 1688 主图目视**，整图删后干净消费者向主图
   ≥ `clean_consumer_images_min`（3 张）——注意它不是发布闸门的 `min_clean_main_images`（1 张），
   本轮 4 个"过闸"品里 3 个只有 1 张，按选品门槛本就不该进清单
   ③ 按产品尺寸估抛重后重算挂价 ④ 多规格货源核对**最高** SKU 采购价。不合格直接换货源，别走完整流程。
2. **一件代发禁止 2 件套**：认领后 SKU 是单件规格、代发只下 1 件，按 2 件成本定价 = 收 2 件钱发 1 件（履约事故）。
   $3.5 下限后单件 @250g/400g 就在带内，`bundle_pieces: 2` 仅是定价模型参数，见 4.4 / strategy 3.3。
3. **FastMoss 不用销量总榜**：`day7_units_sold` 会把低达人出单率的货架场品埋掉（2000 条全量里家居 $5–8 仅 35 条、
   出单率 ≤30% 只剩 1 条）；改用当地语言关键词长尾池（18 词 → 326 条家居、其中 279 条 0–5%）。
   `creator_order_rate` 取 `--json`，别用旧 CSV 快照。
4. **1688 授权字段不可信**：`tokenExpireTime` 显示过期但 `collect` 实测 8/8 成功 → 用 1 个真实 offerId 探测，
   不按字段判死；浏览器 1688 网页登录 ≠ ERP 内 OAuth 授权。
5. **写操作时序**：`ttl-claim` 每批 ≤3；超时后先等异步落库再回查，**绝不盲目重试**（本轮 10 品超时→重试→重复 20 条）；
   `edit` 回执恒 `{}` 但改动生效，必须回读；`success:true` 只代表进队列，最终看日志 `status`；
   `publishConfig` 整批统一，重量尺寸不同必须**拆单品提交**。
6. **发布前必跑 `prepublish_guard.py --refresh --ids ...`**：`image_gate verify` 不扫 `product_attributes`，
   本轮 #3 因残留中文「悬挂/塑料」被 `[12052250]` 拒发；清空为 `[]` 后重发 success。
7. **SKU 规格名逐品对齐 1688 原始 `skus`**，禁止整批套同一套颜色名（真实规格可能是图案款/颜色/数量款/尺寸+颜色，
   `seller_sku` 有时用 `outerId` 而非 `skuId`）。写错 = 买家选 A 收 B。
8. **重量尺寸**：`itemWeight` 卖家全填 0；产品净重/尺寸藏在被判 `L0_drop` 的 1688 详情参数表里（整图删 ≠ 不能读数字）。
   申报值走 `publishConfig`，`detail.package_weight` 只是定价参数。三套默认值打架（发布脚本 1kg/10×10×10、
   `default_weight_g:150`、定价上界 400g）→ 一律显式按品传，推算值标 `INFERRED`。
9. **闸门用本批子集**：全量 `price_gate` 会被店铺历史脏草稿拖成退出码 1，用 `--csv <本批子集>` 隔离；
   铺货配额与 TikTok 后台在售数当前**无脚本能力**，如实标「未验证」，不要写成四路全过。
10. **别承诺出单**：新店零权重、无投流无达人，官方口径 150 款才出首单，10 款差一个数量级。接单时先讲清边界。

## 第三批实测（2026-11-12，TikTok 后台编辑回执）：描述要长、要分段

> 来源：用户截图 `seller.tiktokshopglobalselling.com/product/edit/1700000000000000001`
> （TH 店、商品「在售」）。平台页面原文 = `VERIFIED`；推论与边界见 `references/erp-pitfalls.md` 第十一章。

后台弹窗「**2 个问题可能需要优化**」原文：

| 发品详情 | 建议 |
|---|---|
| 描述格式 | 将你的描述分成段落，提高可读性从而影响购买决策。 |
| 描述质量 | 撰写至少含 500 个字符的描述，以吸引更多客户。 |

1. **这是发布后的软提示，不是发布阻断**：弹窗仍可点「提交」，商品已「在售」。别去 5.2 错误码表找它，
   也别当接口 fail 重试。同屏「基本信息 2个问题」还有「商品图片少于 5 张 / 缺少品牌信息」，
   **是否影响流量 UNKNOWN**，只记录、不改现有硬闸（品牌一律无品牌，不碰品牌货）。
2. **根因在我们自己的流程**：4.2b 长尾档写「短 description」、4.3 只要求长度 >0、
   `image_gate.py apply` 把整段文本包进**一个 `<p>`** → 必然触发这两条。按 `prepublish_guard.py`
   口径统计本批 10 个 detail 快照：**9 个纯文本 <500 字符、8 个不足 2 段**（2026-11-12 本地实测）。
3. **下次执行标准（模板化，不逐品手写）**：description 纯文本 **≥500 字符**且 **≥2 个含文字段落**
   （`config/image_gate.yaml`：`description_min_plain_chars` / `description_min_paragraphs`）。
   `review.json` 的 `localized_text` 用**空行分段**，`apply` 逐段包 `<p>`；长尾档复用
   「材质尺寸 / 使用场景 / 规格与内含物 / 保养与发货」四段当地语言模板，仍可 ≤3 分钟。
4. **发布前兜底**：`prepublish_guard.py --refresh --ids ...` 默认把描述质量不足列为阻断项
   （`--allow-short-desc` 才降级为提醒，需用户明确同意）。历史快照多不达标属预期（10 品里 9 品 <500 字符），回填描述即可。
5. **口径 UNKNOWN**：平台按 HTML 还是纯文本计字符、是否影响曝光、补齐后提示是否消失，均未证实。
   本 Skill 按纯文本保守实现（偏严），补齐后须回后台复核，**不要宣称「已解决」**。

## 第四批实测（2026-11-12）：ERP「站点商品」是另一套模块

> 用户指出「商品 → TikTok → 站点商品」有编辑按钮。**此前「ERP 没有下架 / 重新上架能力」的结论作废**——
> 那句话只对**采集箱（发布前草稿）**成立。完整接口契约见 `references/tiktok-api.md` 末章，
> 逆向踩坑见 `references/erp-pitfalls.md` 第十二章。

1. **两套模块别混**：`/item/crossBorder/platformCollect/*` = 发布前草稿（本 Skill 主体链路）；
   `/item/sysItem/queryPlatformItemListWithPage` + `/item/item/queryPlatformItemDetail` +
   `/item/item/updatePlatformItem` = **平台侧已在售商品**（`numIid` 是 TikTok 商品 ID，不是 `ttl_id`）。
   站点商品页前端确有「编辑商品信息」和「商品上/下架」批量项，接口 `/item/item/itemBatchUpDownShelf`
   **存在但 body 未实测**（只探明必填 `ops` / `list`），**禁止凭猜测调用**。
2. **平台侧一手数据坐实了第三批那两条提示**：4 个在售品 `productStatus` 全 `ACTIVATE/在售`，
   `description` 纯文本 134 / 144 / 153 / 160 字符、**全部仅 1 段**。比采集箱快照更硬。
3. **保存 body 两个坑**：① 全量回传 `tiktokDetailInfo` 必须 `delete replicatedProducts`
   （否则 `Missing SKU details in replicated_products`）；② **保存后立刻回读是旧值**——
   本轮回执与日志都成功、+2 秒回读仍是 153 字符 / 1 段，**数分钟后回读变 1011 字符 / 4 段，确认生效**。
   ERP 详情接口有数分钟缓存延迟：别过早判失败，也别把 ERP 回执当平台已生效；
   TikTok 后台是否同步、提示是否消失仍 `UNKNOWN`，须人工复核。
4. **工具**：`scripts/site_goods.py`（`list` / `detail` / `log` 只读；`update-desc` 默认 dry-run，
   `--save` 才写）。执行任何站点商品写操作前，先用 `detail` 落盘备份。
5. **本轮结果（✅ 对比备份逐项核对）**：4 个在售品只改 `description`，最终回读
   1011 / 1002 / 992 / 1018 字符、均 4 段；价格、SKU、类目、`ACTIVATE/在售` 状态全未变；
   删 `replicatedProducts` 未导致多国家副本丢失（#1 的 MY/VN/SG/PH 四个 DRAFT 副本前后一致）。
   **没删采集箱记录、没重新认领、没重新发布、没执行上下架**。
   泰文文案由 AI 生成、未经母语者复核；TikTok 后台两条提示是否消失仍 `UNKNOWN`，需人工复核。

## 工作流

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
  - 接口：`POST https://<your-erp-host>/item/fastmoss/product/search`（`/item/fastmoss/category/list` 同样免鉴权）。
    返回 `day7UnitsSold` / `day28UnitsSold` / `videoCount` / `creatorCount` / `creatorOrderRate` / `floorPrice` / `isCrossBorder`。
  - **参数必须嵌在 `filter` 里**（`region`、`shopType:2`=跨境店、`priceRange:{min,max}`、`creatorCountRange:{max}`），
    分页用 `page`/`pagesize`，排序字段是 **snake_case**（`day7_units_sold`/`creator_count`/`day7_gmv`…），不是驼峰。
  - CLI：`python3 scripts/kdzs_fastmoss.py --region TH --usd-min 3 --usd-max 8 --max-creator 100 --min-video 10 --top 40`
    / `--categories` 看类目树 / `--live-fx` 拉实时汇率。
  - ⚠️ **不再需要**买 FastMoss 订阅或开 LinkFox 权限（`echotik/listProduct`、`fastmoss/productSearch` 直连均 402，ERP 后端可透传）。
  - **判读优先看 `creatorOrderRate`（达人出单率）而不是周销排行**：出单率 = 0 说明销量几乎全靠自然搜索/货架场，
    这正是「不开达人佣金」的新人最该抢的品；出单率 100% 说明全靠达人带货，你不开佣金就拿不到这个量（详见 strategy 4.7）。
  - 降级仍保留：接口不可用时按 `content.proxy_mode` 打分，上限 6 分，标注 `content_source: proxy`。**不得因缺数据给假高分。**

**② L1 官方后台（浏览器，深度复核需求真伪）**：商品机会中心 `/product/opportunity?tab=product`（"无竞争对手"标签、销量近似值）、`?tab=trending_keywords`（未满足需求/热搜商品）、`?tab=low_competition_category`（在售0-5款+有搜索量）；关键词榜单 `/compass/search-analytics/keyword-rank`（稀缺/热门词，兼作标题词库）。抄录当地语言词 → 翻回中文品类词。

**③ 遨虾榜单（免登录，仅方向信号）**：复用 shopee-light-listing 的 `scripts/aibuy_rank_client.py`，`--platform TikTok_XS --site TH/VN` 拉热销/飙升/新品榜（类目选 Home Supplies 等）。
  - ⚠️ **`opprank` 榜单只有 14 个字段（标题/下游价/1688同款数/同款中位价/类目/图/rankId），没有销量、没有评价数、没有达人数**。它只能给方向，不能支撑需求打分。

#### 0.3 1688 找货（按站点成本边界筛，不是无脑搜）
- 用遨虾 `search --keyword <中文品类词>` 或 `search-image` 拿 offerId；接口失效时浏览器进 1688 跨境专供（需用户接管登录）。
  - ⚠️ 原生 1688 搜索 URL 的关键词必须 **GBK percent-encoding**（UTF-8 会搜出完全无关的品）；带「深度思考」AI 模块的结果页滚动必超时，改用更长更具体的词（「衣物压缩袋」而非「压缩袋」）。详见 `references/erp-pitfalls.md` 第五章。
- **先算成本再挑品**：`python3 scripts/pricing_calc.py --cost <采购价> --scheme shelf --pieces 1 --site TH`，返回 `in_band`。
  ⚠️ **`--pieces` 一律填 1**（2026-11-12 第二批纠正，见 4.4）：一件代发不做 2 件套，下方「2 件套」数字仅定价模型备查。
  本节凡出现「组合装 / 2件套 / 同店可搭配 SKU」的条件均为 $5 下限时代残留，**不作选品依据**。
- 采购价上限（150g / shelf，`--limits` 实算）：TH 单件 **22.4** / 2件套单件等效 **9.4**；VN 单件 **16.7** / 2件套 **6.6**。重量上去上限迅速下降（TH 400g 单件仅 17.4）。**超上限 = 换品，不要硬调 markup。**
- 供应链门槛全部要过（见 strategy 5.2）：跨境专供 / 一件代发 / **无隐形起订量** / 库存≥200 / **承诺48h发货（要客服书面确认）** / **快递到货代仓 ≤1 天** / 非预售非定制 / 主图可复用 / **无中文干净消费者向主图 ≥3 张 + 详情图可重建**（`selection.yaml` → `supplier_gates.clean_consumer_images_min`；
  全脏的货源在 1688 找货阶段就换掉，别等铺进采集箱才发现） / **实重 ≤400g（组合装 ≤250g）且抛重 ≤400g**
  （对应体积 ≤2400cm³，约 20×15×8cm）。
  - 尺寸一律按**抛重**判：`抛重(g) = 长×宽×高(cm) ÷ 6000 × 1000`。实重轻不代表运费低（假花/收纳筐这类必算）。
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

#### 0.5 出候选清单
**落盘前先执行「第二批实测」第 1 条的四道门槛**（这些门槛本来就在 `selection.yaml`/strategy 5.2 里，
本轮是**没执行**而不是没有）：类目权限 / 拉 1688 主图目视后 ≥3 张干净图 / 抛重后挂价 / 最高 SKU 采购价。
不合格的货源在 Step 0 就换掉。本轮跳过后，5 品跑完采集→认领→编辑→图片闸门才发现 0 张干净主图，
1 品才发现只能落 `NON_MAIN_CATEGORY`，6 个品的全流程白跑。

落盘 `erp_data/selection_candidates.csv`，字段见 strategy 第九章（必须含 `landed_cny`/`price_local`/`in_band`/`pieces`/`content_source`/`creator_order_rate`/`competition_source`/`fulfill_check`/
`clean_main_images`/`desc_rebuildable`——后两项是图片闸门的源头门槛，缺了就等于把脏图判断推迟到发布前）。
`pieces` 一律填 1（见 4.4）。

#### 0.6 清单确认（人工接管）
输出建议清单给用户确认（目标150款），确认后 offerId 进入 Step1。写操作仅对用户点名的清单执行。
**产能提醒**：历史落盘候选仅 14 条，而目标是 150 款。若清单产量不足，先检查是不是评分环节又退回到人工读后台——任何依赖人工转录的维度都不能进自动化流程。

### Step 1 采集1688链接（复用）
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
{"numIids":["1500000000000000001"],
 "targetShops":[{"targetPlatform":"tiktok",
   "sellerId":"","sellerNick":"YOUR_SHOP_TH"}]}
```
- **字段要点**：`numIids` 是 **ERP内部ID（numIid）数组**（不是1688 offerId，不是扁平 itemIds）；`targetShops` 是**嵌套对象数组**（targetPlatform/sellerId/sellerNick 三件套）。此前 24 组扁平字段（itemIds/targetSellerIds）全失败正是因为缺这两点。
- 💡 **以后再遇到未收录接口，优先读前端 chunk 逆向**（拉 OSS 上的 `*.js`，用正则提取路径字面量与函数体），
  比 CDP 注入便宜一个数量级，且能直接看到参数枚举与默认值。方法见 `references/erp-pitfalls.md` 第一章。

**接口认领（已可用，替代浏览器路径）**：
```bash
python3 scripts/erp_client.py ttl-claim --ids <货源ID或numIid逗号分隔> --region TH
```
- 脚本自动：查询公共采集箱拿 numIid → 构造上述 body → 提交
- 成功返回：`[{"operationId":"Y101_<TH_SELLER_ID>_YOUR_SHOP_TH","success":true}, ...]`（每商品一条）
- 实测：单条/批量（4 个同发）均 success:true；认领后 ttl-list 立即可见，标题自动泰文化、类目自动匹配、带 IS_REQUIRED_FIELDS 异常（正常草稿态）

**浏览器认领（保留为备选路径，7+次验证稳定）**：
公共采集箱勾选 → 批量认领 → 下拉选 Tiktok → 弹窗选店铺 YOUR_SHOP_TH/VN → 确定 → 认领成功弹窗

**认领陷阱**：认领弹窗默认选中 YOUR_SHOP_PH，必须手动选 TH/VN；免责声明弹窗需先处理；已删除记录的货源重新认领可能不产生新记录（公共采集箱认领标记残留）。

**接口删除（2026-10-29 破解，写操作）**：`POST /item/crossBorder/platformCollect/batchOps`，body=`{"idList":["<ttl_id>"],"platform":"tiktok","opsType":3}`（**opsType 枚举：1=类目 / 2=售价 / 3=删除 / 4=备注**，同一接口复用）。CLI：
```bash
python3 scripts/erp_client.py ttl-del --ids <ttl_id逗号分隔>
```
- 返回 `{"failCount":0,"successCount":1,"totalCount":1}`；实测删除后 ttl-list total 立即减 1（2026-10-29：Y105→19条、Y103→18条均验证）
- **删除重认领是"修复手段"不是日常流程**：`ttl-del --ids <ttl_id>` → `ttl-claim --ids <货源ID> --region TH` 可恢复草稿态 itemModel，
  但**图片/类目/价格全部回到 1688 原始值**（只保留标题当地语言）。只在商品数据被错误 body 写坏时用（见 Step 4.5）。
- 同一接口 `opsType:2` 是批量改价（官方唯一正确的改价入口），见 Step 4.4。

### Step 4 TikTok采集箱二次编辑

#### 4.1 拉取采集箱列表
```bash
python3 scripts/erp_client.py ttl-list --region TH
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

#### 4.3 图片闸门（本Skill核心，2026-11-12 重写）

**先认清两条图片通道**（实测 9 个真实 `ttl_detail_*.json`）：

| 通道 | 字段 | 实测张数 | 旧流水线是否处理 |
|---|---|---|---|
| 主图 | `main_images[].uri` | 恒 5 张 | ✅ 旧 Step 4.3 只管这里 |
| 详情长图 | `description` 里的 `<img src>` | **8–25 张** | ❌ 一张都没碰 |

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
`image_gate.py` **不假装能看图**：`scan` 只负责下载两通道的图并出待判定清单，
判定由模型回填 `review.json` 的 `verdict`。`config/image_gate.yaml` 里的 B2B 词表
只是 `verify` 阶段的**文本残留兜底**，不是判定规则。

| 级别 | 判定 | 处置 |
|---|---|---|
| `L0_drop` | B2B 渠道标注、参数表、工厂/仓库/证书图、整版中文 | **整图删**（主图移除；描述图从 HTML 移除） |
| `L1_inpaint` | 干净消费者向商品图，只叠了一行营销字 | 保留，列入待擦除清单 |
| `L2_localize` | 产品功能词 | 保留，擦原文 + 原位写泰文/越南文 |
| `L3_keep` | 无中文无水印 | 原样保留 |
| `pending` | 未判定 | **apply 默认拒绝执行**（或显式 `--assume-pending drop` 保守全删） |

**执行（必须先 `ttl-detail` 落盘，全流程纯标准库）**：
```bash
python3 scripts/image_gate.py scan --detail erp_data/ttl_detail_<id>.json
# 【多模态模型】逐张打开 erp_data/image_gate/<id>/*.jpg，回填 review.json：
#   images[].verdict / seen_text / confidence / note
#   localized_text：当地语言纯文本段落（必填，否则重建后描述为空）
#   uri_map：L1/L2 处理完的新图 URL（旧 URL → 新 URL）
python3 scripts/image_gate.py apply --detail erp_data/ttl_detail_<id>.json \
    --review erp_data/image_gate/<id>/review.json          # dry-run，只出文件
python3 scripts/image_gate.py verify --detail erp_data/image_gate/<id>/cleaned_detail.json
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
3. **发布前必须 `verify` 且退出码 0**。检查：描述非空 / 主图 ≥1 / 文本与 `img alt` 无汉字 /
   B2B 词无残留 / 无模板残渣 / 被判 `L0_drop` 的 URL 未在两个通道残留。
   退出码 1 = 禁止 `ttl-publish`。⚠️ 文本扫描看不见图里的中文字，只能兜底，判定表不能省。

**L1/L2 保留图的擦字与翻译仍走原 AI 流水线**，但只作用于少量干净图，不再对整版招商素材做无用功：
1. 下载原图 → OCR 文字块（位置+内容）→ 分类 → inpaint 擦除 / 原位写泰文·越南文
2. 第 1 张主图额外 AI 抠图换白底（TikTok 硬要求）
3. 新 URL 填回 `review.json` 的 `uri_map`，再 `apply` 一次（换 URL 不改数量，避免错位）
4. 直通外链填 `main_images[].uri` 即可，无需 getUploadPolicy/OSS 上传

**AI 生成泰文/越南文的高发缺陷（2026-10-29 真实case实测，必须逐张校验）**：
- **幻觉字高发**：AI 会在无关区域（金属反光、玻璃、花盆、空白处）注入杂散伪泰文字符甚至反向英文（实测图2右侧火花区出现"ตกหก/ตาน"、图3玻璃区出现"ARIEL 反向字样"），**原图没有的元素不能信任生成结果**，生成后必须逐区域目视
- **修正方法**：发现幻觉字后重新 `image_edit`，prompt 明确"仅保留指定文字区域，其他区域不得出现任何文字"，必要时二次/三次修正
- **泰文 OCR 不可靠**：内置 Read OCR、macOS Vision OCR 对泰文小字均不稳定（同一行多次读出矛盾结果），**判定泰文正确性用"裁剪单字符超大放大目视"**（sips 裁剪 + 放大到字符≥原始10倍，逐字符确认形状）
- **逐张验收标准**：①指定翻译文字完整、拼写正确；②无中文残留；③无杂散伪字/幻觉字；④无 URL/网址残留（网址必擦）；⑤首图为白底

#### 4.4 定价
**不要手算，用脚本**（参数唯一来源 `config/pricing.yaml`）：
```bash
python3 scripts/pricing_calc.py --cost <采购价> --scheme shelf --pieces 1 --site TH
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
| TH | **1.62** ✅ 净利 12%（破零冲量；起量后回调 2.05 / 25%） | 2.58 ⏸ 停用 |
| VN | **1.515** ✅ 净利 12% + **免交易手续费窗口**（扣点 22%，非 28%） | 2.326 ⏸ 停用 |

> ⚠ VN 的 1.515 只在「入驻后 90 天免交易手续费」期内成立（本店约剩 60 天）。
> 窗口一结束扣点回到 28%，markup 必须改 1.75（仍 12% 净利）；到期前一天就要改，否则每单少算 6%。
> 到期日回填到 `pricing.yaml → VN.schemes.shelf.waiver_expires`。

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

**恢复被写坏的商品**：`ttl-del --ids <ttl_id>` → `ttl-claim --ids <货源ID> --region TH`。
注意重认领会**丢设置**（图片回原图、类目回未匹配、价格回原始值），只有标题保留当地语言——别把它当日常手段。

**图片处理捷径**：擦字/白底图**直接用外链 URL 填 `main_images[].uri` 保存即可**，无需 getUploadPolicy/OSS 上传（your-cdn.example.com 外链实测有效）。

**图片与 description 重建写回走 `image_gate.py apply --save`**（按官方 edit body 结构，自动回读验收）；
只改 SKU/库存用 `fix_sku.py`，只改价格用 `set_price.py`，补重量用 `prep_publish.py`。
**不要用 `erp_client.py ttl-save`**：body 仍是旧结构，docstring 已标「不要直接使用」。
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
   "targetPlatform":"tiktok","sourceSellerId":"<ERP_SOURCE_SELLER_ID>","targetSellerId":"<sellerId>",
   "targetSellerNick":"YOUR_SHOP_TH",
   "categoryIds":"600001,851848,852744","categoryNames":"家居用品,家居收纳,收纳包"}]}
```

**要点**：
- `itemId` = **ttl_id**（TikTok 采集箱记录 id），**不是** itemCenterNumIid（用 center ID 会报"未找到该平台采集记录"）
- `sourceSellerId` 必填（实测 <ERP_SOURCE_SELLER_ID>）；`targetSellerNick` 必须与 sellerId 对应（认领时不选店会默认 PH）
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
0b. **`python3 scripts/image_gate.py verify --detail <cleaned_detail.json>` 退出码 0**（2026-11-12）：
   两通道无中文/B2B 素材、description 已重建、被判删的图未残留。退出码 1 时**禁止发布**
0c. **`python3 scripts/prepublish_guard.py --refresh --ids <ttl_id,...>` 退出码 0**（2026-11-12 第二批新增）：
   递归扫整个 `productCreateRequestV2` 的汉字（`image_gate` 只扫 description/图片，**扫不到 `product_attributes`**）、
   规格名/库存/类目/重复认领，**并查描述质量（纯文本 ≥500 字符 / ≥2 段，第三批新增；
   不达标可用 `--allow-short-desc` 降级为提醒）**。`--refresh` 必加，否则读到 edit 之前的旧快照会误判。
   **本轮 #3 就是两个闸门全过、仍被 `[12052250] 不允许使用汉字` 拒发**（属性残留「悬挂/塑料」）。
1. `ttl-detail` 能读到 `reqInfo.tikTokItemModel.productCreateRequestV2`（读不到 = 数据被写坏）
2. `description` 长度 > 0，且**无中文文本节点 / 无 B2B 字样 / 无 1688 模板残渣**（由第 0 项保证）；
   **纯文本 ≥500 字符、≥2 段**（第三批实测的后台优化项，由第 0c 项拦截）
3. 类目三件套齐全且在**家居用品**树下
4. 每个 SKU：`price` 在价格带内、`inventory` ≤ **999999**（建议统一 1000）
5. 每个 SKU 规格名：**纯尺寸 + 当地语言**，不含「自/最/第一/包退/工厂/包邮」这类词
6. 主图数 = `main_images` 数（闸门删完仍须 ≥1），首图白底、无中文残留
7. `--dry-run` 输出的 publishConfig / 类目树 / 店铺都对

#### 5.2 平台前置校验错误码（fail 时逐条对，全部是商品数据问题不是接口问题）

| 错误码/原文 | 含义 | 修法 |
|---|---|---|
| `[12052250] 不允许使用汉字。修改产品属性的属性值名称: 悬挂` | **`product_attributes` 里残留中文**（2026-11-12 #3 沥水篮实测）。图片闸门不扫这个字段，前两个闸门全过也会 fail | 清空 `product_attributes=[]` 后重发即 success；发布前用 `prepublish_guard.py --refresh` 递归扫 v2 全部汉字 |
| `[12052153] 产品可变选项…内容词 (自)` | SKU 规格名含平台敏感词（「立体打印**自**透气」的「自」被判违规） | 规格名改纯尺寸当地语言 `ขนาด 50x70x30 ซม.`，去掉一切功能描述 |
| `[12052055] SKU inventory quantity … between 0 and 999999` | 库存沿用 1688 原值（9,981,384）超上限 | `fix_sku.py --stock 1000` 统一收敛 |
| `商品描述不能为空` | `description` HTML 长度 = 0 | 编辑侧补描述 |
| `类目不能为空` | 重认领后类目回空、自动匹配失败 | 显式写类目三件套 |
| `tikTokItemModel不能为空` | `edit` body 结构错，商品数据被写坏 | 见 Step 4.5 正确结构；或删除重认领 |
| `TikTok店铺此地区没有设置开启的默认销售仓库` | TikTok 后台未建默认仓 | 人工去 seller 后台建仓 |

详见 `references/erp-pitfalls.md` 第四章（含实测时序与四路验证）。

#### 5.3 发布后四路验证（单一信号会骗人，必须交叉）
| 信号 | 命令/入口 | 通过值 |
|---|---|---|
| ERP 发布日志 | `erp_client.py logs --months 1` | `status = success` |
| 采集箱状态 | `erp_client.py ttl-list --region TH` | `状态 = 1` |
| 铺货配额 | `POST /trade/crossItemDistrib/quotaQuery` | 已用数 +1 |
| **TikTok 后台在售数** | 商品管理页 `在售 N` | N +1（最权威） |

`processing` 可能持续数分钟，**不要在 processing 阶段宣布成功**。

⚠️ **能力边界（2026-11-12 第二批实测）**：铺货配额与后台在售数在 `erp_client.py` 及现有脚本里
**都没有对应实现**（`quotaQuery` 只在 pitfalls 记录过路径，未封装），只能人工确认。
发布成功时如实写「ERP 日志 success + 采集箱状态 1，配额/后台在售数未验证」，
**不要把这两路写成已验证**。同理 `success:true` 只代表进队列，#10 曾先 `processing` 后转 `success`。

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
- 若确实需要重建：`ttl-del --ids <ttl_id>` + `ttl-claim --ids <货源ID> --region TH`（会丢图片/类目/价格设置）

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

#### 6.1 T+7 / T+20 数据回流（必做，否则第二批无法"放大赢家"）
TikTok 商家后台商品分析 → 回填到 `erp_data/backflow.csv`：

| 时点 | 字段 | 用途 |
|---|---|---|
| **T+2（48h）** | **推荐/商城曝光** | **入池检查（新增，破零最关键）**：曝光 <100 = 未入池 → 换主图强制重收录；≥100 才算发布成功 |
| T+7 | 曝光、点击、CTR、加购 | 有点击无加购 → 换主图/调价；零曝光 → 等 T+20 |
| T+20 | 曝光、点击、成交单量、成交额、退款率、**履约率/延迟发货率** | 决定 kill / 培养 / 放大 |

**T+0～T+2 两条纪律（旧版完全没有，是"铺了没曝光"的常见原因）**：
1. **上架后 48 小时内不要改标题/主图**——改动会触发重新收录，等于把刚拿到的初始曝光清零。
2. **每 3 天回后台「商品 → 商品机会」提报关联**：把「热门搜索」和「精选搜索」里与你的品相关的词全部关联提报，
   平台推这些词时会顺带推你的商品。这是不开达人佣金时**唯一能主动拿到的免费推流**，
   而旧版只把商品机会中心当"选品信号源"读，从来没把它当"流量入口"用。

判定规则（`selection.yaml` → `cadence`）：T+20 仍无曝光无转化 → 下架换品；有成交 → 进第二批放大的方向池。
**履约率是链路信号不是选品信号**：延迟发货率上升 → 先换货代或转小批量备货（>30 单/日），不要靠继续换品掩盖。

### Step 7 真实 case 复盘（三个 case，结论按时间优先，**以最新为准**）

#### 7.1 Case A｜不锈钢衣架（2026-10-29，未成功收尾）
认领 → 图片泰化 → 保存 → 页面发布报 `tikTokItemModel不能为空`。
当时判断"接口保存与发布互斥"，**该判断已于 2026-11-11 被推翻**（根因是 body 结构错误，见 7.3）。

#### 7.2 Case B｜硅胶折叠漏斗（2026-10-29，未成功收尾）
认领 → 图片擦中文公司名/网址 → 保存（类目 收纳瓶罐 853000）→ 发布报 `tikTokItemModel不能为空`；
删除重认领后发布报 `类目不能为空`。

**本 case 仍然成立的三条经验**（与 body 结构无关）：
1. **类目自动匹配不可靠**：重认领后类目常回空、`CATEGORY_NOT_MATCH` 不会自动消除 → **必须显式写类目三件套**
2. **重认领 = 恢复草稿态但丢设置**：图片回 1688 原图、类目回未匹配、价格回原始值，只有标题保留当地语言
3. **「图片语言化」的真实工作分两类**：多数家居品图里没有功能词，主要工作是**擦除中文营销信息（公司名/网址/广告语）**；
   功能词翻译取决于具体图 → 流水线先 OCR 分类（营销词擦 / 功能词译 / 无字跳过），再处理，不要一刀切

#### 7.3 Case C｜真空压缩袋（2026-11-11 ✅ 全链路成功，可作为标准剧本）
商品 `1700000000000000002`，YOUR_SHOP_TH，**TikTok 后台确认在售**，฿129–199，3 SKU。

| 环节 | 结果 | 关键点 |
|---|---|---|
| 采集→认领 | ✅ | `ttl-claim` 直认领，标题自动泰文 |
| 改价 | ✅ | `set_price.py` 公式 `×2.8+39.26` → 129/173/199 THB，数据零丢失 |
| 首次发布 | ❌ | `12052153` 规格名含「自」+ `12052055` 库存 9,981,384 超上限 |
| 修 SKU | ✅ | `fix_sku.py --stock 1000`，规格名改 `ขนาด 50x70x30 ซม.`，描述/主图/价格全保留 |
| 重发 | ✅ | 22:34:37 `success`，四路验证一致 |
| 店铺侧 | ✅ | 商品管理「在售 4」（原 3），上架 1 分钟内可见 |

**由 Case C 确立/推翻的结论**：
- ✅ **`edit`（正确 body）→ `batchPublish` 是可行链路**，无需删除重认领
- ✅ **改价用 `batchOps opsType:2`**，不要用 `edit` 改价
- ✅ **getDetail 保存后不返回 itemModel ≠ 数据丢失，且不阻塞发布**
- ❌ **推翻**："保存完善后发布必败 / 只能带异常直接发 / 只能去卖家后台改图"——这些结论全部源于 body 结构写错
- ✅ **发布失败第一动作是读 `失败原因` 原文**，按 Step 5.2 错误码表逐条修，不要重试同一份数据

#### 7.4 全链路接口化程度（现状）

| 环节 | 自动化程度 |
|---|---|
| 选品取数（FastMoss 单品 + 官方后台） | ✅ 全自动（官方后台需浏览器读页面） |
| 采集 / 认领 / 删除 | ✅ 全接口 |
| 批量公式改价 | ✅ 全接口 |
| 修 SKU 名/库存/类目/图片 | ✅ 全接口（`fix_sku.py` / `prep_publish.py`） |
| 发布 | ✅ 全接口 |
| 发布结果验证 | ✅ 全接口（日志 + 采集箱 + 配额）；店铺侧需浏览器核对 |
| 两通道图片扫描 / 整图判定 / description 重建 / 发布前硬闸 | ✅ 脚本化；判定由多模态模型回填 review.json，低置信度转人工 |
| 保留图的 OCR 分类 / 擦字 / 功能词翻译 / 首图白底 | ⚠️ AI 流水线，需人工抽检前 10 个 |
| 货代覆盖确认 / ERP 登录 / 1688 授权 | ❌ 人工接管 |

## References

- `references/erp-pitfalls.md`：**接口逆向与踩坑实录（出问题先查这个）**——完整成功时序、前端 chunk 逆向方法、
  `edit` 正误 body 对照、`batchOps` 四类 opsType、发布错误码表、1688 采集/授权坑、token 抓取原理、浏览器通道边界、发布前自检清单；
  **第十章 = 2026-11-12 第二批 10 品复盘（写操作时序、参数表读重量、三套默认重量、限流、能力边界）**。
- `references/selection-strategy.md`：选品模块核心（九章）——一件代发三条铁律、履约链路与城市判据、成本结构与采购价上限矩阵、数据源分层与可信度分级、红线与供应链门槛、四维评分、TH/VN差异、破零节奏与数据回流、输出格式。Step 0 前必读。
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
- `scripts/erp_client.py`：采集/认领/删除/发布/日志 主客户端（`ttl-publish` 支持 `--weight/--dims/--dry-run`；`ttl-save` 已标禁用）
- `scripts/erp_token_grab.py`：从 Chrome LevelDB 自动捞 JWT（`--save --check`），不用手工复制
- `erp_data/README.md`：**落盘目录约定**——顶层只放有效资产（`case_category_tree.json` 查类目 ID、`tiktok_shops.json` 拿 sellerId），
  跑批产物按 `_run/<日期_用途>/` 隔离，历史快照进 `_archive/<日期_用途>/` + `MANIFEST.md`。
  ⚠️ 脚本输出是**相对路径**：在根目录跑落 `erp_data/`，在 `scripts/` 下跑落 `scripts/erp_data/`（11-11 成功发布的数据在后者），读之前先确认在哪。
- `scripts/set_price.py`：**批量公式改价**（`batchOps opsType:2`），自带改前/改后完整性校验
- `scripts/fix_sku.py`：**修 SKU 规格名/库存**（官方 `edit` 正确 body 实现，发布前置校验修复主力）
- `scripts/batch_prep.py`：**批量预处理编排**（破带重定价 + 库存收敛 + 复检，默认 dry-run）。
  `--site VN [--ids ...] --save`。只动破带商品、已在带内的一律不碰；售价为空/缺重量/类目未匹配的一律转人工不猜。
  按每天 3 小时可投入，不批量化就铺不完 150 款
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
- `scripts/site_goods.py`：**站点商品（平台侧在售）客户端（2026-11-12 新增）**——
  `list` / `detail` / `log` 只读；`update-desc` 默认 dry-run、`--save` 才写，自动删
  `replicatedProducts` 并回读对比。⚠️ 回读有数分钟缓存延迟，别立刻判失败。
  上/下架 `ops` 未实测，**脚本刻意不实现**。接口契约见 `tiktok-api.md` 末章
- `scripts/prep_publish.py`：铺货前置一体化（按 SKU 梯度改价 + 补重量 + 回读验证 + 图片替换）
- `scripts/image_gate.py`：**详情页图片闸门**（`scan` 两通道扫描出待判定清单 / `apply` 按 L0–L3 重建并可选 `--save` 写回 /
  `verify` 发布前硬闸，退出码 1 = 拦截）。纯标准库，scan/apply(dry-run)/verify 全程离线；**脚本不判图**，判定由多模态模型回填 review.json
