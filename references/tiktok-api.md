# TikTok Shop 铺货接口契约（实测 2026-10-28 起，2026-11-12 全链路闭环修订）

认证同 shopee-light-listing：`qnquerystring` 头 + `appType:3`。
⚠️ **2026-11-12 修订**：本文早期版本给出的"保存 body"与"保存与发布互斥"结论**均已被推翻**，
根因是 `itemModel` 传成了内层对象。以本版为准；完整踩坑见 `erp-pitfalls.md`。

## 已实测接口

| 用途 | 方法 | 路径 | body/参数 | 备注 |
|---|---|---|---|---|
| 1688采集 | POST | /item/center/batchPluginCollectItem | `{"itemIdList":[offerId...]}`（CLI 实测可用） | 数组元素是 1688 offerId，不是 ERP numIid |
| 公共采集箱列表 | POST | /item/center/queryErpItemListPage | `{pageNo,pageSize,platNumIidList?}` | 复用旧 |
| TikTok店铺列表 | POST | /index/platformShop/getPlatformShops | `{platform:"tiktok"}` | TH=<TH_SELLER_ID> VN=<VN_SELLER_ID> |
| **认领到TikTok** ✅ | POST | /item/crossBorder/commonCollect/batchClaimMultiShop | `{"numIids":["<numIid>"],"targetShops":[{"targetPlatform":"tiktok","sellerId":"<id>","sellerNick":"<nick>"}]}` | numIids=ERP内部ID数组、targetShops=嵌套对象数组；返回 `[{operationId,success}]`；CLI: ttl-claim |
| **TikTok采集箱列表** | POST | /item/crossBorder/platformCollect/queryListPage | `{pageNo,pageSize,toRegion?,status?}` | 返回id/标题/类目树/售价/SKU/异常标签 |
| **编辑详情** | POST | /item/crossBorder/platformCollect/getDetail | `{id: ttl_id}` | **仅"有异常标签"商品返回itemModel；异常清空后不再返回**（设计使然，不影响发布） |
| **保存编辑** ✅ | POST | /item/crossBorder/edit | **见下方"正确 body"（2026-11-12 修正）** | 逐字段校验：id→itemModel→title→类目 |
| **发布** ✅ | POST | /item/distribute/batchPublish | `{"publishConfig":{weight,weightSet,packageLength/Width/Height,packageSet,filterAlreadyPublish,forbiddenWordDetectMode,priceLimitEnable,priceLimitMultiple,useTiktokMultiCountryConfig,tiktokOverflowConfig},"distribItemVos":[{itemId,sourcePlatform,targetPlatform,sourceSellerId,targetSellerId,targetSellerNick,categoryIds,categoryNames}]}` | itemId=采集箱记录id（ttl_id）；sourceSellerId=<ERP_SOURCE_SELLER_ID>；categoryIds/categoryNames 为**逗号分隔字符串**（传真实类目树）；weight/dims **按真实包裹填**；CLI: ttl-publish |
| **批量操作** ✅ | POST | /item/crossBorder/platformCollect/batchOps | `{"idList":[ttl_id],"platform":"tiktok","opsType":N,...}` | **opsType: 1=类目 2=售价 3=删除 4=备注**；opsType=2 公式改价（CLI: set_price.py）、opsType=3 删除（CLI: ttl-del） |
| **FastMoss 单品搜索** ✅ | POST | /item/fastmoss/product/search（**<your-erp-host> 域，免 token**） | `{keywords,filter:{region,shopType,priceRange,creatorCountRange},orderby:[{field,order}],page,pagesize}` | field 用 snake_case（day7_units_sold 等）；CLI: kdzs_fastmoss.py |
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
用 your-cdn.example.com 外链直接填即可（无需 getUploadPolicy）。
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
  toSellerNick: "YOUR_SHOP_TH"
  toSellerId: ""
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
8. **待补**：`opsType:1`（改类目）未实测；货代管理无接口（只能人工）；TikTok 后台商品分析数据无接口（T+7/T+20 回填需人工或浏览器读）
