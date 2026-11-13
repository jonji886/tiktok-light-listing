# TikTok Shop 铺货接口契约（实测 2026-10-28 起，2026-11-12 全链路闭环修订）

认证同 shopee-light-listing：`qnquerystring` 头 + `appType:3`。
⚠️ **2026-11-12 修订**：本文早期版本给出的"保存 body"与"保存与发布互斥"结论**均已被推翻**，
根因是 `itemModel` 传成了内层对象。以本版为准；完整踩坑见 `erp-pitfalls.md`。

## 已实测接口

| 用途 | 方法 | 路径 | body/参数 | 备注 |
|---|---|---|---|---|
| 1688采集 | POST | /item/center/batchPluginCollectItem | `{"itemIdList":[offerId...]}`（CLI 实测可用） | 数组元素是 1688 offerId，不是 ERP numIid |
| 公共采集箱列表 | POST | /item/center/queryErpItemListPage | `{pageNo,pageSize,platNumIidList?}` | 复用旧 |
| TikTok店铺列表 | POST | /index/platformShop/getPlatformShops | `{platform:"tiktok"}` | TH=7494877320827733079 VN=8686823118786298967 |
| **认领到TikTok** ✅ | POST | /item/crossBorder/commonCollect/batchClaimMultiShop | `{"numIids":["<numIid>"],"targetShops":[{"targetPlatform":"tiktok","sellerId":"<id>","sellerNick":"<nick>"}]}` | numIids=ERP内部ID数组、targetShops=嵌套对象数组；返回 `[{operationId,success}]`；CLI: ttl-claim |
| **TikTok采集箱列表** | POST | /item/crossBorder/platformCollect/queryListPage | `{pageNo,pageSize,toRegion?,status?}` | 返回id/标题/类目树/售价/SKU/异常标签 |
| **编辑详情** | POST | /item/crossBorder/platformCollect/getDetail | `{id: ttl_id}` | **仅"有异常标签"商品返回itemModel；异常清空后不再返回**（设计使然，不影响发布） |
| **保存编辑** ✅ | POST | /item/crossBorder/edit | **见下方"正确 body"（2026-11-12 修正）** | 逐字段校验：id→itemModel→title→类目 |
| **发布** ✅ | POST | /item/distribute/batchPublish | `{"publishConfig":{weight,weightSet,packageLength/Width/Height,packageSet,filterAlreadyPublish,forbiddenWordDetectMode,priceLimitEnable,priceLimitMultiple,useTiktokMultiCountryConfig,tiktokOverflowConfig},"distribItemVos":[{itemId,sourcePlatform,targetPlatform,sourceSellerId,targetSellerId,targetSellerNick,categoryIds,categoryNames}]}` | itemId=采集箱记录id（ttl_id）；sourceSellerId=1323965；categoryIds/categoryNames 为**逗号分隔字符串**（传真实类目树）；weight/dims **按真实包裹填**；CLI: ttl-publish |
| **批量操作** ✅ | POST | /item/crossBorder/platformCollect/batchOps | `{"idList":[ttl_id],"platform":"tiktok","opsType":N,...}` | **opsType: 1=类目 2=售价 3=删除 4=备注**；opsType=2 公式改价（CLI: set_price.py）、opsType=3 删除（CLI: ttl-del） |
| **FastMoss 单品搜索** ✅ | POST | /item/fastmoss/product/search（**erpg.kuaidizs.cn 域，免 token**） | `{keywords,filter:{region,shopType,priceRange,creatorCountRange},orderby:[{field,order}],page,pagesize}` | field 用 snake_case（day7_units_sold 等）；CLI: kdzs_fastmoss.py |
| **FastMoss 类目树** ✅ | POST | /item/fastmoss/category/list（**免 token**） | `{lang:"ZH_CN"}` | 同上 |
| 发布记录 | POST | /item/distribute/queryLogs | `{toPlatformList:["tiktok"],fromPlatformList:["1688"],startTime,endTime}` | 时间窗≤31天 |
| 类目树 | POST | /item/distribute/categoryTree | `{platform:"tiktok"}` | 编辑弹窗加载 |
| 品牌列表 | POST | /item/crossBorder/platformCollect/getBrandList | | |
| 图片上传凭证 | POST | /item/file/getUploadPolicy | 浏览器里自动调通，curl直调500 | **实际不需要**——外链URL可直接填main_images保存 |
| 发布额度 | GET | /trade/crossItemDistrib/quotaQuery | | 发布成功后会 +1，可作侧证 |
| 汇率 | POST | /item/exchangeRate/getList | | 2026-11-11：1 USD=6.67111 CNY=33.65 THB |

## 保存接口 body 结构（2026-11-12 修正，之前版本是错的）

```python
detail = post("/item/crossBorder/platformCollect/getDetail", {"id": ttl_id})
d       = detail                      # 注意：reqInfo 在响应顶层，不在 data 里
reqInfo = d["reqInfo"]
ttm     = reqInfo["tikTokItemModel"]  # 三键结构之一
v2      = ttm["productCreateRequestV2"]   # 改动写这里

body = {
    "id": d["id"],                       # 缺→"平台采集箱id不能为空"
    "platform": "tiktok",
    "sellerId": d["toSellerId"],         # 必须有 sellerId（不是只传 toSellerId）
    "currency": d.get("currency") or "THB",
    "toCategoryId": d["toCategoryId"],   # 无效→"当前类目获取失败"
    "toCategoryIdTree": d["toCategoryIdTree"],
    "toCategoryNameTree": d["toCategoryNameTree"],
    "title": d["title"],                 # 缺→"title is null"
    "itemModel": {                       # ★ 整个 reqInfo 结构（含三个平台键）
        **reqInfo,
        "tikTokItemModel": {**ttm, "productCreateRequestV2": v2},
    },
}
# ★ 绝对不要额外传顶层 "reqInfo"——传了会把 itemModel 写坏
post("/item/crossBorder/edit", body)   # → {"data":null,"success":true} 或 {}
```

**错误写法与症状（务必对照）**：

| 写法 | 症状 |
|---|---|
| `itemModel: ttm` + 顶层再传 `"reqInfo": reqInfo` | 列表变 `MISSING_ITEM_DATA`、`tikTokItemModel: null`、售价区间空 |
| 缺 `sellerId` | 保存后归属丢失，同上 |

写出问题后只能 `ttl-del` + `ttl-claim` 重建（会丢图片/类目/价格设置）。

**图片替换**：改动写 `itemModel.tikTokItemModel.productCreateRequestV2.main_images[].uri`，
用 aka.doubaocdn.com 外链直接填即可（无需 getUploadPolicy）。
CLI 用 `prep_publish.py --images "<url1>,<url2>,..."`（数量必须与主图数一致）；
`erp_client.py ttl-save` 的 body 仍是旧结构，**已标禁用**。

**保存成功判定 = 异常标签减少/清空 + 类目生效 + 回读描述长度/主图数/SKU 数未缩水**
（success:true 仅表示请求被接受）。

**getDetail 行为规律**：
- **itemModel 只在"待完善草稿"（带异常标签）的记录中返回**；保存（edit）或发布成功后 getDetail 不再返回
- 这是 ERP 的设计（getDetail 只服务待处理商品），**不是数据丢失**
- ✅ **并且不阻塞发布**：2026-11-11 实测，`fix_sku.py`（正确 body）保存后（异常仅剩 `MISSING_WEIGHT`）
  直接 `ttl-publish` → logs `success`、采集箱状态 1、配额 +1、TikTok 后台在售 +1
- 实测 4 例（2026-10-28）均符合此规律：牙刷架/排插/清洁抹布/沙漏
- ⚠️ 保存后浏览器"编辑"弹窗主图显示"已选 0 张"——页面读不到已保存图片，**图片语言必须在保存前本地逐张验完**

**历史结论更正**：2026-10-29 的两个 case（衣架、漏斗）报 `tikTokItemModel不能为空`，
当时归因为"接口保存与接口发布互斥"，**实为 body 结构写错把数据写坏了**。
2026-11-11 用正确结构复现后发布成功，该结论正式作废。

**注意**：getDetail 响应里 **reqInfo / tikTokItemModel 在响应顶层**（`d.reqInfo`），**不在 `data` 里**。


## 编辑详情数据结构（getDetail 返回）

```
顶层:
  id: ttl采集箱记录ID
  title: 泰文标题
  toRegion: "TH"
  toSellerNick: "JingShanShop-TH"
  toSellerId: "7494877320827733079"
  currency: "THB"
  toCategoryId / toCategoryIdTree / toCategoryNameTree
  reqInfo:                         ← 在顶层
    shopeeItemModel: null
    temuItemModel: null
    tikTokItemModel:
      productCreateRequestV2:
        title: 泰文标题
        category_id: "852744"
        main_images: [{uri:"https://cbu01.alicdn.com/..."}]   ← 外链可直接用
        description: "<div>...<img src=...>"                  ← 空则发布报"描述不能为空"
        package_weight / package_length 等
        skus: [{price:{amount:"173",currency:"THB"},
                inventory:[{quantity:1000}],
                sales_attributes:[{...规格名...}]}]
```


## 批量改价（2026-11-11 反编译确认，✅ 实测）

`POST /item/crossBorder/platformCollect/batchOps`，`opsType:2`。**改价不要用 `edit`**。

```json
{"idList":["<ttl_id>"],"platform":"tiktok","opsType":2,"priceType":2,
 "priceFormula":{"type":1,"multiplicationColumn":2.8,"additionColumn":39.26,
                 "reduceColumn":0,"decimalPlacesType":0,"tailAmount":null},
 "price":null}
```

| 字段 | 取值 |
|---|---|
| `priceType` | 1=固定价（配 `price`）、2=公式 |
| `priceFormula.type` | 1=**基于当前售价**、2=基于货源价 |
| `multiplicationColumn` | 倍数（UI 百分比 ÷ 100） |
| `additionColumn` | 加价，**单位=商品币种**（THB 填泰铢） |
| `decimalPlacesType` | 0=取整 |
| `tailAmount` | 尾数（如 .99），null=不处理 |

✅ 实测：47.81/57.05/32.05 → 173/199/129，描述 1578 字符 / 5 主图 / 3 SKU 全部保留。CLI：`set_price.py`。

## 发布失败的错误码（平台前置校验，全部是商品数据问题）

| 错误码 | 含义 | 修法 |
|---|---|---|
| `12052153` | SKU 规格名含平台敏感词（「自」这种单字也会命中） | 规格名改纯尺寸当地语言 |
| `12052055` | SKU 库存 > 999999 | 统一 1000 |
| `商品描述不能为空` | `description` HTML 长度 = 0 | 补描述 |
| `类目不能为空` | 类目三件套缺失（重认领后常见） | 显式写类目 |
| `tikTokItemModel不能为空` | 商品数据被错误 body 写坏 | 重写或删除重认领 |

## 待补

1. ~~认领接口body~~：**已破解 2026-10-29**（`numIids`+`targetShops` 嵌套结构）；破解通道=CDP document_start 注入 hook（JS 层拦截被前端原生引用绕过，故须页面脚本执行前注入）。**但现在更推荐读前端 chunk 逆向，成本低得多（见 erp-pitfalls 第一章）**
2. ~~getUploadPolicy/OSS上传~~：不需要——外链URL直接填main_images保存即可（已验证）
3. ~~translate接口~~：不需要——认领时ERP自动翻译标题，图片翻译用我们自己的流水线
4. ~~发布接口body~~：**已破解 2026-10-29**；2026-11-12 修正 weight/dims 与 categoryIds 口径
5. ~~删除接口~~：**已破解 2026-10-29**（batchOps + opsType:3）
6. ~~批量改价接口~~：**已破解 2026-11-11**（batchOps + opsType:2，公式模式）
7. ~~选品数据~~：**已打通 2026-11-11**（`/item/fastmoss/product/search`，免 token）
8. **待补**：`opsType:1`（改类目）未实测；货代管理无接口（只能人工）；TikTok 后台商品分析数据无接口（T+7/T+20 回填需人工或浏览器读）；**站点商品上/下架 `ops` 取值未实测**
9. ~~站点商品编辑/保存~~：**已破解 2026-11-12**（见下章）

---

## 站点商品（已发布 / 在售）编辑接口（2026-11-12 新增，✅ 实测）

⚠️ **与前面「采集箱」是两套东西**：`/item/crossBorder/platformCollect/*` 管**发布前草稿**；
本章管**平台侧已在售商品**（ERP 菜单「商品 → TikTok → 站点商品」`/cross/goods/tiktok-site`，
列表 `numIid` = TikTok 商品 ID、`outerId` = 1688 offerId）。此前 Skill 只逆向了采集箱，
据此得出的「ERP 没有下架 / 重新上架能力」**只对采集箱成立，对站点商品模块不成立**。
站点商品页前端明确有批量项 `editGoods(编辑商品信息)` 与 `goodsUpDn(商品上/下架)`。

| 用途 | 路径 | body | 备注 |
|---|---|---|---|
| 站点商品列表 | `/item/sysItem/queryPlatformItemListWithPage` | `{pageNo,pageSize}` | ✅ 实测。`data.list[]` 含 `numIid / outerId / sellerId / sellerNick / platform / platformItemSkuList`。**全平台混排**（实测 total=301，含 shopees），按 `platform=="tiktok"` 自行筛 |
| 编辑详情 | `/item/item/queryPlatformItemDetail` | `{sellerId, platform:"tiktok", numIid}` | ✅ 实测。缺 `platform` →「平台不能为空」；缺 `sellerId` →「店铺id不能为空」。返回 `data.tiktokDetailInfo` |
| 保存编辑 | `/item/item/updatePlatformItem` | `{sellerId, platform, numIid, tiktokDetailInfo:{...详情对象+改动字段}}` | ✅ 实测，两个坑见下 |
| 操作日志 | `/item/getPlatItemOperateLog` | `{pageNo,pageSize,numIid,viewType:1,logOperateTypeList:[8]}` | ✅ 实测。`logOperateType:8` = 编辑商品信息，`enableStatus:1` |
| 批量上/下架 | `/item/item/itemBatchUpDownShelf` | `{ops, list, ...}` | ⚠️ 路径由主包 API 定义确认，**body 未完整实测**：缺 `ops` →「ops is null」；缺/错 `list` →「list is empty」。`ops` 取值与 `list` 元素结构 = `UNKNOWN`，**禁止凭猜测调用** |

### 保存 body 的两个硬坑

1. **全量回传 `tiktokDetailInfo` 必须 `delete replicatedProducts`（及 `multiCountrySkuSalesConfig`）**。
   否则平台报 `tiktok编辑商品失败：Missing SKU details in replicated_products. You must complete the SKU details.`
   根因：多国家售卖副本的 `skus` 是空数组，全量提交触发 TikTok 校验。
   前端「多国家同步」开关关闭时正是 `delete y.replicatedProducts` 再提交（`CrossPlatformGoods` 分包）。
   ✅ 副作用核验（2026-11-12）：删除该字段提交后，原商品的 4 个 DRAFT 副本（MY/VN/SG/PH）
   保存前后**数量与状态一致，未被删除** → 该字段是「本次不同步修改」，不是「删除副本」。
2. **`success:true` ≠ 立刻回读得到新值**（2026-11-12 实测）。返回
   `{"data":{"itemSuccess":true,"skuSuccess":true,"stockSuccess":true,"settlementPriceSuccess":true,"success":true},"success":true}`，
   日志也记了 `logOperateType:8 / enableStatus:1`；但**保存后 2 秒回读 `description` 仍是旧值（153 字符 / 1 段）**，
   **数分钟后再回读 = 1011 字符 / 4 段，确认已生效**。
   → ERP 详情接口有**数分钟缓存/异步延迟**，禁止保存后立刻回读就判失败；
   但也别把 ERP 回执直接当成平台已生效：**TikTok 后台是否同步、两条提示是否消失仍 `UNKNOWN`，须人工复核**。

### `tiktokDetailInfo` 关键字段

`id / numIid`、`title`、`description`(HTML)、`mainImages[]`、`skus[].price.salePrice`、
`productStatus`(`ACTIVATE`=在售)、`productStatusName`("在售")、`categoryId` / `categoryChains`、
`packageWeight`、`packageDimensions`、`productAttributes`、`replicatedProducts[]`(多国家售卖副本)、`video`。

2026-11-12 实测本轮发布的 4 品：`productStatus` 全 `ACTIVATE/在售`；
`description` 纯文本 134 / 144 / 153 / 160 字符、**全部仅 1 段** → 与后台「描述格式 / 描述质量」
两条优化提示完全对应（`VERIFIED` 平台侧一手数据，比采集箱快照更硬）。

### 前端包版本（逆向用，2026-11-12）

前端已升到 **`erp/1.5.1`**（旧笔记 `erpg/0.0.66` 已过时）。
chunk 文件名解析：主包 `app.<hash>.js` 的 `jsonpScriptSrc` 里有两张表——
`({0:"SettlementSetting",...}[a]||a)` 给名字、`{0:"4ba86a77",...}[a]` 给 hash，拼成 `js/<name>.<hash>.js`。
站点商品页相关分包：`CrossShopeeSiteGoods.*.js`（列表 / 批量操作）、
`CrossPlatformGoods.*.js`（编辑弹窗 + 保存逻辑）。文件是 gzip，先 `gunzip -c`。

---

## 铺货配额查询（2026-11-12 实测，只读）

`POST /trade/crossItemDistrib/quotaQuery`，**body 传 `{}` 即可**，鉴权头同其它接口。

返回结构：

```json
{"success": true,
 "data": {"businessModule": "CROSS_ITEM_LIMIT", "consumedQuota": "8",
   "details": [{"platform": "tiktok", "sellerId": "7494877320827733079",
     "totalNum": "5000", "consumeNum": "8", "remainingNum": "4992",
     "lastResetTime": "2026-10-27 10:48:34", "nextResetTime": "2026-11-26 10:48:34"}]}}
```

要点：
- `details[]` **按 sellerId 分店铺**，TH 与 VN 各自独立计数（实测 TH 8/5000、VN 0/5000，月度窗口同步）；
- 平台字段是 `platform`（`tiktok` / `shopees`），不是 `targetPlatform`；
- 数值全是**字符串**，比较前要转 int；
- `nextResetTime` 是配额重置日，铺货排期跨这个点要重算剩余量。

CLI：`python3 scripts/erp_client.py quota --region TH`（`--region` 留空列全部，落 `erp_data/quota.csv`）。
这是发布后四路验证的第三路，**可以脚本验证**；第四路「TikTok 后台在售数」仍无接口，只能人工看商品管理页。
