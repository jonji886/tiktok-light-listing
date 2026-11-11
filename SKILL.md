---
name: tiktok-light-listing
description: "TikTok Shop 跨境店铺货（快递助手ERP跨境版，一件代发模式）：选品(先定货代省份→官方商品机会中心/关键词榜单取需求信号→1688找货→红线与供应链门槛→履约/内容力/实净利/站内竞争四维评分+价格带校验出清单)→1688链接采集→公共采集箱→认领到TikTok站点店铺(TH/VN)→TikTok采集箱二次编辑(标题翻译/图片擦除广告字+翻译功能字/首图白底/定价)→发布→记录统计→T+7/T+20数据回流。基于实测接口，复用 shopee-light-listing 的 token/collect/logs/distribute 基础。仅适用于 <your-erp-host> + YOUR_SHOP_TH/VN 两个店铺，店铺主营类目为家居用品(选品仅限该类目)。不做其他ERP/其他平台。"
---

# TikTok Shop 跨境铺货（选品 → 1688采集 → 认领 → 编辑图片/翻译/定价 → 发布）

## Overview

在快递助手ERP跨境版内，把1688货源铺到 TikTok Shop 东南亚店（先做 TH 泰国 + VN 越南），店铺主营类目为**家居用品**。

链路分6段：
0. **选品**：官方后台取需求信号 → 1688 找货 → 红线+供应链门槛 → 四维评分+价格带校验 → **按发货城市聚簇 + 查货代覆盖（TIKTOK 标签）** → 出候选清单
1. **采集**：1688 offerId → ERP公共采集箱（复用旧接口）
2. **认领**：公共采集箱 → 认领到指定TikTok站点店铺（TH/VN）
3. **编辑**：TikTok采集箱里逐品二次编辑——标题已自动翻译，图片要处理（擦广告字/翻译功能字/首图白底），价格按定价模板算
4. **发布**：提交到TikTok发布队列
5. **统计**：发布记录回查 + T+7/T+20 真实曝光/点击/履约数据回流

**本版本的模型前提（2026-11-11 重构，与前版不同）**：新人 · 无自有货源 · 1688/拼多多单件下单 → 国内货代 → TikTok 中转仓。
由此产生的三条硬结论，写在 `references/selection-strategy.md` 第一至三章，执行前必读：
- 官方时效为 **48h 打包 + 3 个自然日到仓扫描**（不是 96 小时）→ **判据是「发货地到货代仓的快递天数」，不是「同省」**：≤1 天绿档、2 天卡死、≥3 天必超（见 Step 0.0）
- 国内段成本是 **单件快递 5 元 + 货代 2 元**（不是批量均摊 1 元），采购价上限由价格带反推，用 `scripts/pricing_calc.py` 校验
- **暂不开达人佣金**（2026-11-11 决定），只走货架场；`creator` 档在配置里 `enabled: false` 备查

## 适用范围

- **适用**：<your-erp-host> + YOUR_SHOP_TH(<TH_SELLER_ID>) + YOUR_SHOP_VN(<VN_SELLER_ID>)
- **不适用**：其他ERP、TikTok自运营店、其他站点(PH/SG/MY后续再加)、Shopee/Lazada

## 动作分层

- **AI自动**：官方工具取需求信号、1688找货、四维评分、价格带校验（`pricing_calc.py`）、红线与供应链门槛自动剔除、采集、认领、列表对账、价格计算、图片OCR分类、擦除广告字、翻译功能字、首图白底、发布提交、记录统计
- **硬红线自动阻断**：品牌侵权/IP联名、液体化工、带电、食品保健医疗、宗教、易碎、活体、多规格价差超限、平台限售需认证、跨类目（复用shopee selection-rules + TikTok限售/类目约束）
- **人工接管**：ERP登录/短信验证码、**货代覆盖确认（ERP「货代管理」按城市查、筛 TIKTOK 标签，无接口）**、供应商 48h 发货的书面确认、图片处理结果抽检（前10个人工过一眼）、候选清单最终确认（写操作前）

## 执行前约定

- 登录态：复用浏览器JWT，`qnquerystring`头 + `appType:3`，token存 `scripts/erp_token.txt`
- 接口为未公开内部接口，报错即回退浏览器操作
- 所有写操作(claim/publish)仅对用户明确点名的清单执行

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
导航ERP，若`blocked=auth`则请用户接管登录。登录后导出token：
```js
sessionStorage.getItem('qnquerystring')
```
存到 `scripts/erp_token.txt`。官方后台 `seller.tiktokshopglobalselling.com` 登录态同样由用户接管。

#### 0.2 取需求信号（L1 官方后台，第一手，免费）
**优先级：官方后台 > 第三方单品数据(如可用) > 1688 供给侧**。三层分工见 strategy 第四章。
- **官方后台（浏览器）**：商品机会中心 `/product/opportunity?tab=product`（"无竞争对手"标签、销量近似值）、`?tab=trending_keywords`（未满足需求/热搜商品）、`?tab=low_competition_category`（在售0-5款+有搜索量）；关键词榜单 `/compass/search-analytics/keyword-rank`（稀缺/热门词，兼作标题词库）。抄录当地语言词 → 翻回中文品类词。
- **遨虾榜单（免登录，方向信号）**：复用 shopee-light-listing 的 `scripts/aibuy_rank_client.py`，`--platform TikTok_XS --site TH/VN` 拉热销/飙升/新品榜（类目选 Home Supplies 等）。
  - ⚠️ **`opprank` 榜单只有 14 个字段（标题/下游价/1688同款数/同款中位价/类目/图/rankId），没有销量、没有评价数、没有达人数**。它只能给方向，不能支撑需求打分。
- **L2 单品数据（FastMoss/EchoTik）**：仅用于"内容力"维度（`totalIflCnt`/`totalVideoCnt`/`totalLiveCnt`）。
  - 现状：LinkFox 通道实测 402（账户额度问题）、FastMoss 连接器未绑定。**不可用时按 `content.proxy_mode` 降级打分，上限 6 分，并标注 `content_source: proxy`**。不得因缺数据给假高分。

#### 0.3 1688 找货（按站点成本边界筛，不是无脑搜）
- 用遨虾 `search --keyword <中文品类词>` 或 `search-image` 拿 offerId；接口失效时浏览器进 1688 跨境专供（需用户接管登录）。
- **先算成本再挑品**：`python3 scripts/pricing_calc.py --cost <采购价> --scheme shelf --pieces <1|2> --site TH`，返回 `in_band`。
- 采购价上限（150g / shelf，`--limits` 实算）：TH 单件 **22.4** / 2件套单件等效 **9.4**；VN 单件 **16.7** / 2件套 **6.6**。重量上去上限迅速下降（TH 400g 单件仅 17.4）。**超上限 = 换品，不要硬调 markup。**
- 供应链门槛全部要过（见 strategy 5.2）：跨境专供 / 一件代发 / **无隐形起订量** / 库存≥200 / **承诺48h发货（要客服书面确认）** / 同省发货 / 非预售非定制 / **同店≥2个可搭配 SKU** / 主图可复用 / ≤150g 且 ≤25×20×10cm。
- **1688 双轨定价**：同一件货有"包邮价（1件起）"和"批发价（2件起）"，把两个含邮总价并列取低者。买1件看包邮价，买2件看批发价。

#### 0.4 AI 评分（四维，规则见 strategy 第六章）
红线剔除 → 供应链门槛 → **需求过滤（不打分）** → 四维打分：

| 维度 | 权重 | 数据来源 |
|---|---|---|
| 履约确定性 fulfillment | 0.30 | **发货城市→货代仓快递天数**/承诺时效/库存/起订量（<6 分剔除） |
| 内容力 content | 0.25 | 达人/视频/直播数；无数据时按可拍性判，上限 6 |
| 实净利 margin | 0.25 | `pricing_calc` 全成本净利率（<8% 剔除） |
| 站内竞争 competition | 0.20 | TikTok 站内在售款数/官方标签（**不是 1688 同款数**） |

1688 同款数只作"跟卖时间窗"修正（<10 款 +1；>300 款 −2），不作为竞争度打分。

#### 0.5 出候选清单
落盘 `erp_data/selection_candidates.csv`，字段见 strategy 第九章（必须含 `landed_cny`/`price_local`/`in_band`/`pieces`/`content_source`/`competition_source`/`fulfill_check`）。

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

**接口删除（2026-10-29 破解，写操作）**：`POST /item/crossBorder/platformCollect/batchOps`，body=`{"idList":["<ttl_id>"],"platform":"tiktok","opsType":3}`（opsType=3 删除；同一接口复用通用批量操作）。CLI：
```bash
python3 scripts/erp_client.py ttl-del --ids <ttl_id逗号分隔>
```
- 返回 `{"failCount":0,"successCount":1,"totalCount":1}`；实测删除后 ttl-list total 立即减 1（2026-10-29：Y105→19条、Y103→18条均验证）
- 配合"删除重认领"路线A：`ttl-del --ids <ttl_id>` 删除 → `ttl-claim --ids <货源ID> --region TH` 重新认领（恢复草稿态 itemModel 可读）

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
- `main_images[]`：主图URL数组，每项 `{uri: "..."}`
- `skus[]`：SKU价格/库存
- `category_id`：类目（ERP已自动填）

#### 4.3 图片处理（本Skill核心）
对每张主图执行：
1. 下载原图
2. OCR识别图上所有中文文字块（位置+内容）
3. LLM分类：
   - **营销/批发/承诺词**（源头工厂/日产XX/包退/包邮/发货时间/赠品/促销/月销/好评/OEM/一件代发/1688/工厂直供）→ AI inpaint擦除
   - **产品功能词**（吸水/不掉毛/加厚/防滑/食品级等）→ 擦除原文，同位置写泰文/越南文
   - **拿不准的** → 擦除（宁可错删）
4. **第1张主图**：额外做AI抠图换白底（首图必须白底，TikTok要求）
5. 上传处理后的图到ERP文件服务，拿新URL
6. 替换 `main_images[i].uri`

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
| VN | **2.08** ✅ 净利 25% | 2.63 ⏸ 停用 |

采购价上限（150g、shelf、`pricing_calc.py --limits` 实算，**不是手填**）：

| 站点 | 单件 | 2 件套（单件等效） |
|---|---|---|
| TH | 22.4 元 | 9.4 元 |
| VN | 16.7 元 | 6.6 元 |

**三条硬规则**：
- 售价折美元必须落在价格带内（TH $5–8 / VN $5–8.5），脚本会给 `✓ 带内 / ✗ 超带`。**超带就换品，不要调 markup。**
- **开 20% 达人佣金必须同时把净利降到 15%**。若一边开邀请佣金一边要求 25% 净利，该组合无解。
- **2 件套窗口很窄**：单件等效上限 TH ≤9.4 元、VN ≤6.6 元（150g）；重量到 400g 只剩 4.5 / 1.7 元 → 泡货别做组合装。

算完先输出建议售价表给用户确认，确认后批量填回 `skus[].price`。

#### 4.5 保存编辑（接口已破解 2026-10-28）
接口：**`POST /item/crossBorder/edit`**（不是 platformCollect/save）。

**正确 body（后端逐字段校验，缺一报错）**：
- `id`（ttl_id）→ 缺则"平台采集箱id不能为空"
- `itemModel` = **整个外层 tikTokItemModel**（含 productCreateRequestV2）→ 缺则"itemModel is null"；传内层 v2 会 success 但详情不可读
- `platform` = "tiktok"
- `reqInfo` = getDetail 的 reqInfo 原样
- `title` → 缺则"title is null"
- 类目三件套：`toCategoryId` / `toCategoryIdTree` / `toCategoryNameTree` → 无效则"当前类目获取失败"
- `toSellerId` / `toSellerNick` / `currency` / `fromItemId` / `fromPlatform` / `status`

改动写进 `itemModel.productCreateRequestV2`：类目 `category_id`、重量 `package_weight`、主图 `main_images[].uri`、价格 `skus[].price`。

**保存成功判定 = 列表 serviceTagList（异常标签）清空 + 类目生效**（接口 success:true 仅表示请求被接受）。

**getDetail 行为规律（关键）**：**有异常标签**的商品 getDetail 返回 `reqInfo.tikTokItemModel`；**异常清空后不再返回**（ERP 设计：getDetail 只服务待完善商品）——**不是数据丢失**，发布时商品走其他读取通道。

**图片处理捷径**：擦字/白底图**直接用外链 URL 填 `main_images[].uri` 保存即可**，无需 getUploadPolicy/OSS 上传（your-cdn.example.com 外链实测有效）。

**ttl-save 已支持图片替换（2026-10-29 新增）**：
```bash
python3 scripts/erp_client.py ttl-save --id <ttl_id> --images "<url1>,<url2>,...,<urlN>"
```
- `--images` 按逗号分隔、**数量必须与主图数一致**（不一致脚本中止，防错位）
- 内部按顺序替换 `main_images[].uri`，其余 body 结构同上方"正确 body"
- 实测（2026-10-29 衣架 case）：替换 5 张图保存 → success=True，异常标签清空、类目生效

**保存后编辑弹窗实测表现（2026-10-29，重要）**：
- 接口保存成功后，**打开浏览器"编辑"弹窗，主图区显示"已选 0 张"**——页面端读取通道（getDetail）已被"异常清空"关闭，**已保存的泰文图无法在 ERP 页面编辑弹窗中看到/复核**
- 这不是数据丢失（接口确实写入成功），是 ERP 的 getDetail 只服务"待完善草稿"的设计所致
- **含义**：图片语言正确性必须在保存前（本地文件层面）逐张验证完毕，保存后无法再通过 ERP 复核

### Step 5 发布（写操作）
**接口 body 已破解（2026-10-29）**：`POST /item/distribute/batchPublish`。此前"对 TikTok 报'未找到该平台采集记录'"的根因是**旧 body 结构错误**（缺 publishConfig、itemId 不是 itemCenterNumIid、缺 sourceSellerId、categoryIds 类型错误）。正确 body 用同一接口即可发布。

**破解的发布接口 body（ground truth）**：
```json
{"publishConfig":{"weight":1,"weightSet":1,"packageLength":10,"packageWidth":10,
  "packageHeight":10,"packageSet":1,"filterAlreadyPublish":true,"forbiddenWordDetectMode":0,
  "priceLimitEnable":false,"priceLimitMultiple":"1","useTiktokMultiCountryConfig":false,
  "tiktokOverflowConfig":{"detailEnabled":true,"titleEnabled":true,"titleLimit":255,
    "titleTruncateType":"END","skuNameEnabled":true,"skuNameLimit":20,"skuNameTruncateType":"END",
    "skuValueEnabled":true,"skuValueLimit":50,"skuValueTruncateType":"END","skuTotalEnabled":false}},
 "distribItemVos":[{"itemId":"<itemCenterNumIid>","sourcePlatform":"kdzs_erp",
   "targetPlatform":"tiktok","sourceSellerId":"<ERP_SOURCE_SELLER_ID>","targetSellerId":"<sellerId>",
   "targetSellerNick":"YOUR_SHOP_TH","categoryIds":"","categoryNames":""}]}
```
**要点**：`itemId`=**TikTok采集箱记录 id（ttl_id）**，不是 itemCenterNumIid（2026-10-29 实测：ttl_id→受理进队列，center ID→"未找到该平台采集记录"）；`sourceSellerId` 必填（实测=<ERP_SOURCE_SELLER_ID>）；`categoryIds/categoryNames` 是**字符串**（可空），不是数组；商品缺类目→"类目不能为空"、缺描述→"商品描述不能为空"（商品数据问题，需先在编辑侧完善）。

**接口发布（已可用，替代浏览器路径）**：
```bash
python3 scripts/erp_client.py ttl-publish --ids <ttl_id逗号分隔>
```
- 实测：带类目商品提交返回 `{"data":null,"success":true}` 进入发布队列；logs 回查可看到该商品记录（processing→success/fail）
- 2026-10-29 实测：Y100（手动创建，缺描述）发布 → 队列受理、后端校验报"商品描述不能为空"（商品数据问题非接口问题）；Y104（缺类目）→ "类目不能为空"

**发布最佳实践（浏览器路径，保留备选）**：
1. 采集箱勾选商品 → 批量发布
2. "发布提示"弹窗：有异常商品显示异常原因，点"继续全部发布"
3. "发布产品"弹窗（配置）：**包裹重量默认 1.000KG + "对未设置重量产品生效"已勾选、尺寸 10×10×10**，防重复/截断/违禁词默认即可 → 点"确定发布"
4. 结果弹窗"商品已成功加入发布任务" → 发布记录状态 processing → 最终 success/fail

**关键结论（勿踩）**：
- **带异常（如缺重量）的商品可直接发布**（发布配置自动补全重量），**不要**先用接口保存"完善"商品——完善后（异常清空）getDetail 不再返回 itemModel，页面发布会报 **"tikTokItemModel不能为空"**！
- **完整复现记录（2026-10-29 衣架 case 实测闭环）**：接口保存泰文图（ttl-save success=True、异常清空、类目生效）→ 打开编辑弹窗主图"已选 0 张" → 浏览器发布点"确定发布" → **报错"tikTokItemModel不能为空"**（红色提示，弹窗内两个 error 条）→ 未进发布队列，logs 无记录。**至此"接口保存→页面发布"不可行已由真实 case 全链路验证，不是推测**
- 若商品已被接口保存完善 → 删除重认领，或保留异常直接发布（**删除/重认领均可用接口：ttl-del + ttl-claim**）
- 发布失败常见原因："TikTok店铺此地区没有设置开启的默认销售仓库"（需 TikTok Shop 后台建默认销售仓库）
- 发布按钮可能在视口外（页面高 721 时 y≈750），需先滚动弹窗到可见再点击；发布前清理残留弹窗避免 no-modal

**图片翻译后的发布路径决策（2026-10-29 实测后明确，2026-10-29 晚更新）**：
- **前提**：图片翻译走接口保存（Step 4.5）必然清空异常 → 之后 ERP 页面发布必失败。两条可行路线：
  - **路线A（推荐，图片先翻译）**：认领 → getDetail 取 itemModel → 本地图片翻译并逐张验证 → **删除该记录 → 重新认领**（带 IS_REQUIRED_FIELDS 异常，itemModel 恢复可读；**删除/重认领均接口化：`ttl-del --ids <ttl_id>` + `ttl-claim --ids <货源ID> --region TH`**）→ 用重新认领记录的 itemModel 构造保存 body（图片仍是原图）**或**直接在编辑弹窗内处理 → 发布。⚠️ 注意重新认领陷阱：公共采集箱认领标记残留，已删除记录重新认领可能不产生新记录（需先按 numIid 在公共采集箱定位）
  - **路线B（发布后处理图片）**：认领 → **不保存直接发布**（带异常直接发，发布配置自动补全重量）→ 发布成功后，**图片翻译放到 TikTok 商家后台二次编辑**（seller.tiktokshopglobalselling.com 商品编辑里替换图片）
- **图片语言验证时机**：必须在接口保存前完成（本地文件逐张校验），因为保存后 ERP 复核通道关闭（见 Step 4.5 实测表现）

**发布点击方式（2026-10-28 二次实测，牙刷架 1043101456623 发布成功进队列）**：
- 行内"发布"按钮 JS rect ≈ (886,360)px → bu 千分比 **(774,499)**；行内"编辑"在 (854,360)px
- **"确定发布"用 `bu.find("确定发布")` 拿 ref 后 `bu.click(ref)` 最可靠**（n2079 成功）；JS/坐标点击在弹窗有遮罩残留时可能无效
- 成功弹窗："商品已成功加入发布任务！请前往【发布记录】查看发布进度及结果"
- 发布记录回查 `logs --months 1`：刚提交显示 processing，随后 success/fail

**公共采集箱定位（2026-10-28 实测）**：
- 公共采集箱"货源ID"输入框匹配的是 **ERP内部ID（numIid）**，不是 1688 offerId——用 `python3 scripts/erp_client.py items --ids <offerId>` 先查 numIid，再填浏览器
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
| T+7 | 曝光、点击、CTR、加购 | 有点击无加购 → 换主图/调价；零曝光 → 等 T+20 |
| T+20 | 曝光、点击、成交单量、成交额、退款率、**履约率/延迟发货率** | 决定 kill / 培养 / 放大 |

判定规则（`selection.yaml` → `cadence`）：T+20 仍无曝光无转化 → 下架换品；有成交 → 进第二批放大的方向池。
**履约率是链路信号不是选品信号**：延迟发货率上升 → 先换货代或转小批量备货（>30 单/日），不要靠继续换品掩盖。

### Step 7 全链路可行性结论（2026-10-29 真实 case 验证）

**用真实 case（1688 offer 738241485567 不锈钢衣架，TH）跑通全链路的结论**：

| 环节 | 结果 | 说明 |
|---|---|---|
| 选品→采集→认领 | ✅ | **认领接口已破解（2026-10-29）**：ttl-claim 接口直认领成功（单条+批量实测），标题自动泰文化、类目自动匹配、带 IS_REQUIRED_FIELDS 异常 |
| 图片翻译/擦字/白底 | ✅ | 4张有字图全部泰文（幻觉字两轮修正后干净），图片语言验证通过 |
| 接口保存（ttl-save --images） | ✅ | success=True，异常清空、类目生效，main_images 替换成功 |
| 保存后 ERP 复核 | ❌ | 编辑弹窗主图"已选 0 张"，getDetail 不再返回 itemModel |
| 保存后页面发布 | ❌ | 报"tikTokItemModel不能为空"，未进发布队列 |

**认领接口破解（2026-10-29 补充）**：`batchClaimMultiShop` body=`{"numIids":[<numIid>],"targetShops":[{"targetPlatform":"tiktok","sellerId":..,"sellerNick":..}]}`——CDP document_start 注入 hook 抓真实请求破解（详见 Step 3）。**认领已从"浏览器必选"升级为"接口直认领可用"**。

**发布/删除接口破解（2026-10-29 补充）**：
- **发布**：`batchPublish` 正确 body 已破解（publishConfig+distribItemVos，见 Step 5）——此前"未找到该平台采集记录"是旧 body 结构错误。独立调用返回 `{"data":null,"success":true}` 进发布队列；logs 回查可见记录（processing→success/fail）。CLI：`ttl-publish --ids <ttl_id>`
- **删除**：`batchOps` + `opsType:3`（见 Step 3）——CLI：`ttl-del --ids <ttl_id>`，实测删除生效（total 立即-1）
- **至此认领/删除/发布三个写操作接口全部可用**，配合 ttl-claim/ttl-save/ttl-list/logs，**采集→认领→删除→重认领→发布→回查统计全链路无浏览器人工点**（图片翻译/擦字/白底仍需 AI 流水线，保存编辑用 ttl-save）

**核心结论**：图片翻译本身可行且质量可验证；**"接口保存→页面发布"链路在 ERP 端不可行**（保存即清空草稿态，发布读不到 itemModel）。生产上按 Step 5"图片翻译后的发布路径决策"走**路线A（删除重认领后在编辑/后台改图）**或**路线B（带异常直接发布→后台二次改图）**。认领/删除/发布环节现在可全接口化（配合批量采集，选品→采集→认领→发布→统计已无人工点，图片语言仍需本地验证）。

### Step 7b 第二轮真实 case（2026-10-29 offer 784293912606 硅胶折叠漏斗 → TH）

**目的**：用第二个真实 case 复现全链路，尤其验证"图片是否真的改成对应国家语言（泰文）"。

| 环节 | 结果 | 说明 |
|---|---|---|
| 选品 | ✅ | aibuy search "硅胶折叠漏斗" → offer 784293912606（0.50元起批，厨房小工具，轻小件，无红线） |
| 采集 | ✅ | ttl collect 成功（1/1） |
| 认领（接口） | ✅ | ttl-claim 成功，标题自动泰文化，带 IS_REQUIRED_FIELDS,CATEGORY_NOT_MATCH |
| 图片分析 | ✅ | 5张主图：4张无字（含1张白底首图），**图3含中文公司名+网址（营销信息）+尺寸数字** |
| 图片处理（本case语言点） | ✅ | 图3用 image_edit 擦除公司名+网址，**保留全部尺寸数字**（14.5cm等非语言内容），OCR 复查干净。**本case无中文功能词需翻译成泰文**——"图片语言"体现为**移除中文营销信息**，4张无字图无需处理 |
| 接口保存（ttl-save --images --category） | ✅ | 类目选**收纳瓶罐853000**（厨具600024是NON_MAIN_CATEGORY不可用）；保存后异常清空、类目生效"家居用品,家居收纳,收纳瓶罐" |
| **发布（接口）** | ❌ | 保存后发布报**"tikTokItemModel不能为空"**——itemModel 被清空（getDetail 不再返回），**接口发布同样依赖 itemModel**（不是只有页面发布依赖） |
| 发布（重认领后） | ❌ | 删除→重认领恢复 itemModel（5图40SKU描述完整）→ 发布报**"类目不能为空"**（重认领后类目回空，自动匹配失败） |
| 编辑抽屉（浏览器） | 🔶 | 抽屉打开加载完整 itemModel（getDetail），**类目显示"请选择类目"（未自动匹配）**；含"图片翻译/图片白底/图片编辑"原生按钮 + 翻译弹窗（免费4张，目标语言含泰语/越南语）；**保存通道未实测定论**（bu 视口521无法真实点击抽屉右侧1200区域，需真实浏览器/用户点击验证是否走独立通道不破坏 itemModel） |

**本轮新结论（对 SKILL.md 的修正/补充）**：
1. **接口发布依赖 itemModel 可读**：`batchPublish` 对"已保存（ttl-save 完善）"的商品报 `tikTokItemModel不能为空`——即**接口保存和接口发布互斥**，与页面发布一致。此前 Step 5 只记录了"页面发布"失败，本轮确认**接口发布同样失败**。
2. **类目自动匹配不可靠**：两次重认领后类目均为空/自动匹配中，最终 CATEGORY_NOT_MATCH 未消除。**必须显式用 ttl-save --category 指定**（家居类目 AVAILABLE 表：收纳瓶罐853000 等，见 case_category_tree.json）。
3. **重认领 = 恢复草稿态但丢设置**：图片/类目回到 1688 原始值，仅标题保留泰文化。改图+设类目后若要发布，必须在**编辑抽屉（浏览器）**内完成——但抽屉保存是否走独立通道（不破坏 itemModel）**未实测定论**，需真实浏览器点击"开始翻译"/"保存"后抓包确认。
4. **图片语言验证的真实含义**：本类商品图片通常无中文功能词（不同于衣架 case 的功能词翻译），主要工作是**擦除中文营销信息（公司名/网址/广告语）**。功能词翻译需求取决于具体商品图——流水线应先 OCR 分类（营销词擦除/功能词翻译）再处理，无字图跳过。

**当前生产路径（本case验证后的推荐）**：
- 认领后**不要 ttl-save 保存**（会清空 itemModel → 发布必败）
- 在**编辑抽屉**内：改图（图片翻译/白底/编辑）→ 选类目 → 保存 → 发布
- 若抽屉保存同样清空 itemModel（待验证），则回到 Step5 路线B：**带异常直接发布**（发布配置自动补全重量），图片翻译放 TikTok 商家后台二次处理

## References
- `references/selection-strategy.md`：选品模块核心（九章）——一件代发三条铁律、履约链路与同省原则、成本结构与采购价上限矩阵、数据源分层与可信度分级、红线与供应链门槛、四维评分、TH/VN差异、破零节奏与数据回流、输出格式。Step 0 前必读。
- `references/tiktok-api.md`：TikTok接口契约（已实测+待补）
- 图片处理流水线（OCR分类 → 擦营销词 → 功能词翻译 → 首图白底 → 逐张验收）见上方 Step 4.3，无独立文件
- `config/pricing.yaml`：站点定价参数（一件代发全成本、两档 markup、价格带）
- `config/selection.yaml`：选品参数（履约红线/门槛/四维权重/需求过滤/竞争与内容力口径/单量分层/铺货节奏）
- `scripts/pricing_calc.py`：售价计算与价格带校验（`--cost` 算售价 / `--limits` 反推采购价上限 / `--check` 自检）。纯标准库，配置改完必跑 `--check`。
