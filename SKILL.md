---
name: tiktok-light-listing
description: "TikTok Shop 跨境店铺货（目标跨境 ERP SaaS）：选品(官方商品机会中心/关键词榜单浏览器取候选→1688找同款→五维评分+红线剔除出清单)→1688链接采集→公共采集箱→认领到TikTok站点店铺(TH/VN)→TikTok采集箱二次编辑(标题翻译/图片擦除广告字+翻译功能字/首图白底/定价)→发布→记录统计。基于实测接口，复用同一 ERP 客户端的 token/collect/logs/distribute 约定。仅适用于单一 ERP 实例 + YOUR_SHOP_TH/VN 两个店铺，店铺主营类目为家居用品(选品仅限该类目)。不做其他ERP/其他平台。"
---

# TikTok Shop 跨境铺货（选品 → 1688采集 → 认领 → 编辑图片/翻译/定价 → 发布）

## Overview

在目标跨境 ERP（下文简称 ERP）内，把1688货源铺到 TikTok Shop 东南亚店（先做 TH 泰国 + VN 越南），店铺主营类目为**家居用品**。

链路分6段：
0. **选品**：官方后台工具（商品机会中心/关键词榜单）浏览器取候选 → 1688找同款offer → 五维评分+红线剔除 → 出候选清单
1. **采集**：1688 offerId → ERP公共采集箱（复用旧接口）
2. **认领**：公共采集箱 → 认领到指定TikTok站点店铺（TH/VN）
3. **编辑**：TikTok采集箱里逐品二次编辑——标题已自动翻译，图片要处理（擦广告字/翻译功能字/首图白底），价格按定价模板算
4. **发布**：提交到TikTok发布队列
5. **统计**：发布记录回查

## 适用范围

- **适用**：your-erp-host.example.com + YOUR_SHOP_TH(<TH_SELLER_ID>) + YOUR_SHOP_VN(<VN_SELLER_ID>)
- **不适用**：其他ERP、TikTok自运营店、其他站点(PH/SG/MY后续再加)、Shopee/Lazada

## 动作分层

- **AI自动**：官方工具取候选、1688找同款、五维评分、红线自动剔除、采集、认领、列表对账、价格计算、图片OCR分类、擦除广告字、翻译功能字、首图白底、发布提交、记录统计
- **硬红线自动阻断**：品牌侵权/IP联名、液体化工、带电、食品保健医疗、宗教、易碎、活体、多规格价差超限、平台限售需认证、跨类目（复用shopee selection-rules + TikTok限售/类目约束）
- **人工接管**：ERP登录/短信验证码、图片处理结果抽检（前10个人工过一眼）、候选清单最终确认（写操作前）

## 执行前约定

- 登录态：复用浏览器JWT，`qnquerystring`头 + `appType:3`，token存 `scripts/erp_token.txt`
- 接口为未公开内部接口，报错即回退浏览器操作
- 所有写操作(claim/publish)仅对用户明确点名的清单执行

## 工作流

### Step 0 选品（登录检查 → 取候选 → 1688匹配 → AI评分 → 产出offerId清单）
先读 `references/selection-strategy.md` 与 `config/selection.yaml`，按其中规则执行：

0. **登录检查（人工接管）**：导航ERP，若`blocked=auth`则请用户接管登录。登录后导出token：
   ```js
   sessionStorage.getItem('qnquerystring')
   ```
   存到 `scripts/erp_token.txt`。官方后台 `seller.tiktokshopglobalselling.com` 登录态同样由用户接管。
1. **取候选（优先级：遨虾接口 > 官方后台浏览器）**：
   - **遨虾接口（默认，免登录）**：调用外部的榜单/搜索客户端（可选依赖，脚本不在本仓库；也可用任意等价数据源替换），`--platform TikTok_XS --site TH/VN` 拉热销/飙升/新品榜（类目选 Home Supplies 等家居类），产出字段含下游价/同款数/同款中位价/三级类目，直接支撑评分。
   - **官方后台（浏览器，补充验证）**：商品机会中心 `/product/opportunity?tab=product`（"无竞争对手"标签、销量）、关键词榜单 `/compass/search-analytics/keyword-rank`（稀缺/热门/飙升词，兼作标题词库）。
2. **1688找同款（遨虾 `search --keyword` / `search-image`，免登录直接拿offerId）**：接口失效时浏览器进1688跨境专供搜索（需用户接管登录）。筛"跨境专供+一件代发+48小时发货"。
3. **AI 自动筛选与评分（规则见 selection-strategy.md 第三/四节）**：红线剔除 → 五维打分 → 同款去重 → 按综合分排序。
4. **出候选清单**：落盘 `erp_data/selection_candidates.csv`（字段：offer_id/offer_url/title_zh/category_dir/price_cny/region/score/score_detail/reason/redline_check）。
5. **清单确认（人工接管）**：输出建议清单给用户确认（目标150款），确认后 offerId 进入 Step1。写操作仅对用户点名的清单执行。

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
   "sellerId":"<TH_SELLER_ID>","sellerNick":"YOUR_SHOP_TH"}]}
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
按 `config/pricing.yaml` 算售价：
```
售价(本币) = (采购价 + 国内运费1元 + 物流藏价10元) × 汇率 × 加价系数
```
- TH加价系数≈1.76（扣点8%+3.21%+退款7%+利润25%）
- VN加价系数≈2.08（扣点14%+6%+退款7%+利润25%）
- 算完先输出建议售价表给用户确认，确认后批量填回skus[].price

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
   "targetPlatform":"tiktok","sourceSellerId":os.environ.get("ERP_SOURCE_SELLER_ID", ""),"targetSellerId":"<sellerId>",
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

### Step 6 发布记录统计
```bash
python3 scripts/erp_client.py logs --months 1
```
status: success/fail/processing/examine。

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
- `references/selection-strategy.md`：选品模块核心——数据源(TikTok官方后台/1688产品开发工具)、硬红线、五维评分、TH/VN差异、破零节奏(150款/20天)、候选清单格式。Step 0 选品前必读。
- `references/tiktok-api.md`：TikTok接口契约（已实测+待补）
- `config/pricing.yaml`：站点定价参数
- `config/selection.yaml`：选品参数（红线开关/五维权重/站点价格区间/竞争度阈值/破零节奏）
