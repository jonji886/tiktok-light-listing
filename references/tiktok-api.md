# TikTok Shop 铺货接口契约（实测 2026-10-28，2026-10-29 补充图片保存/发布闭环）

认证同 shopee-light-listing：`qnquerystring` 头 + `appType:3`。

## 已实测接口

| 用途 | 方法 | 路径 | body/参数 | 备注 |
|---|---|---|---|---|
| 1688采集 | POST | /item/center/batchPluginCollectItem | `{itemIdList:[offerId...]}` | 复用旧 |
| 公共采集箱列表 | POST | /item/center/queryErpItemListPage | `{pageNo,pageSize,platNumIidList?}` | 复用旧 |
| TikTok店铺列表 | POST | /index/platformShop/getPlatformShops | `{platform:"tiktok"}` | TH=<TH_SELLER_ID> VN=<VN_SELLER_ID> |
| **认领到TikTok** ✅ | POST | /item/crossBorder/commonCollect/batchClaimMultiShop | `{"numIids":["<numIid>"],"targetShops":[{"targetPlatform":"tiktok","sellerId":"<id>","sellerNick":"<nick>"}]}` | **body 2026-10-29 破解**（CDP document_start 注入抓真实请求）；numIids=ERP内部ID数组、targetShops=嵌套对象数组；返回 `[{operationId,success}]`；CLI: ttl-claim |
| **TikTok采集箱列表** | POST | /item/crossBorder/platformCollect/queryListPage | `{pageNo,pageSize,toRegion?,status?}` | 返回id/标题/类目树/售价/SKU |
| **编辑详情** | POST | /item/crossBorder/platformCollect/getDetail | `{id: ttl_id}` | **仅"有异常标签"商品返回itemModel；异常清空后不再返回**（设计使然） |
| **保存编辑** ✅ | POST | /item/crossBorder/edit | **见下方"正确body"** | 逐字段校验：id→itemModel→title→类目 |
| **发布** ✅ | POST | /item/distribute/batchPublish | `{"publishConfig":{...},"distribItemVos":[{itemId,sourcePlatform,targetPlatform,sourceSellerId,targetSellerId,targetSellerNick,categoryIds,categoryNames}]}` | **body 2026-10-29 破解**（document_start 注入抓真实请求）；itemId=采集箱记录id（ttl_id），不是itemCenterNumIid；sourceSellerId=<ERP_SOURCE_SELLER_ID>；categoryIds/categoryNames 为字符串；返回 success:true 进队列；CLI: ttl-publish |
| **删除采集箱记录** ✅ | POST | /item/crossBorder/platformCollect/batchOps | `{"idList":["<ttl_id>"],"platform":"tiktok","opsType":3}` | **2026-10-29 破解**；opsType=3 删除；返回 `{successCount,totalCount}`；CLI: ttl-del |
| 发布记录 | POST | /item/distribute/queryLogs | `{toPlatformList:["tiktok"],fromPlatformList:["1688"],startTime,endTime}` | 时间窗≤31天 |
| 类目树 | POST | /item/distribute/categoryTree | `{platform:"tiktok"}` | 编辑弹窗加载 |
| 品牌列表 | POST | /item/crossBorder/platformCollect/getBrandList | | |
| 图片上传凭证 | POST | /item/file/getUploadPolicy | 浏览器里自动调通，curl直调500 | **实际不需要**——外链URL可直接填main_images保存 |
| 发布额度 | GET | /trade/crossItemDistrib/quotaQuery | | 本月1/25000 |

## 保存接口 body 结构（2026-10-28 破解，逐字段校验）

```python
detail = post("/item/crossBorder/platformCollect/getDetail", {"id": ttl_id})
data = detail["data"]
ttm = data["reqInfo"]["tikTokItemModel"]          # 整个外层对象（含v2）
v2 = ttm["productCreateRequestV2"]                # 改动写这里

body = {
    "id": data["id"],                              # 缺→"平台采集箱id不能为空"
    "itemModel": ttm,                              # 整个外层；缺→"itemModel is null"
    "platform": "tiktok",
    "reqInfo": data["reqInfo"],
    "title": data["title"] or v2["title"],         # 缺→"title is null"
    "toSellerId": data.get("toSellerId"),
    "toSellerNick": data.get("toSellerNick"),
    "toCategoryId": data.get("toCategoryId"),      # 无效→"当前类目获取失败"
    "toCategoryIdTree": data.get("toCategoryIdTree"),
    "toCategoryNameTree": data.get("toCategoryNameTree"),
    "currency": data.get("currency"),
    "fromItemId": data.get("fromItemId"),
    "fromPlatform": data.get("fromPlatform"),
    "status": data.get("status"),
}
post("/item/crossBorder/edit", body)   # → {"data":null,"success":true}
```

**图片替换（2026-10-29 新增）**：改动写 `itemModel.productCreateRequestV2.main_images[].uri`，用 your-cdn.example.com 外链直接填即可（无需 getUploadPolicy）。CLI：`ttl-save --id <ttl_id> --images "<url1>,<url2>,..."`（数量必须与主图数一致，脚本会校验）。

**保存成功判定 = 列表 serviceTagList 清空 + 类目生效**（success:true 仅表示请求被接受）。

**getDetail 行为规律（2026-10-28 实测闭环，2026-10-29 图片保存 case 再确认）**：
- **itemModel 只在"待完善草稿"（带异常标签）的记录中返回**；接口保存（edit）或发布成功后 **itemModel 即被清空**（getDetail 不再返回）——**getDetail 只服务待处理商品，非数据丢失**
- 实测 4 例（2026-10-28）：牙刷架(IS_REQUIRED→发布前有/发布后无)、排插 IS_REQUIRED(本地快照有/实时无=已被保存)、清洁抹布(IS_REQUIRED 但已被保存→无)、沙漏(保存过→无)
- **实测闭环（2026-10-29 衣架 738241485567，图片翻译→保存→发布全链路）**：
  - 认领后带 IS_REQUIRED_FIELDS，getDetail 返回完整 itemModel（5主图/3SKU/泰文标题）
  - `ttl-save --images` 替换 5 张泰文图 → success=True，异常标签清空、类目生效
  - 保存后 getDetail 复查：标题空、主图 0 张、SKU 0 个（itemModel 清空复现）
  - **编辑弹窗表现**：浏览器"编辑"弹窗主图区显示"已选 0 张"——页面端同样读不到已保存图片
  - 浏览器发布"确定发布" → **报"tikTokItemModel不能为空"**，未进队列，logs 无记录
- **实测闭环 2（2026-10-29 漏斗 784293912606，接口发布同样受阻）**：
  - 认领→`ttl-save --images --category 853000` 保存成功（异常清空、类目"家居用品,家居收纳,收纳瓶罐"生效）
  - **接口发布**（`batchPublish`，body 已破解）→ **同样报"tikTokItemModel不能为空"**——即**接口保存与接口发布互斥**，不只是页面发布失败
  - 删除→重认领→itemModel 恢复可读（5主图/40SKU/描述3926字）→ 接口发布 → **"类目不能为空"**（重认领后类目回空，自动匹配失败）
  - 两次重认领均 CATEGORY_NOT_MATCH 未自动消除 → **类目必须显式 ttl-save --category 指定**（家居 AVAILABLE 类目见 case_category_tree.json；厨具600024为NON_MAIN_CATEGORY）
  - 编辑抽屉（浏览器）打开加载完整 itemModel（类目"请选择类目"），含图片翻译/白底/编辑原生按钮（翻译弹窗免费4张，目标含泰语）；**抽屉保存是否走独立通道未实测定论**（bu 视口521无法真实点击抽屉右侧区域，需真实浏览器/用户点击验证）
- **结论：任何"先保存完善再发布"都会失败**——保存（edit 接口语义=完善定型）清空 itemModel → 页面/接口发布都读不到 → 报"tikTokItemModel不能为空"（2026-10-29 已由两个真实 case 完整复现）
- **唯一可行路径**：认领后**不保存直接发布**（带异常 → 发布配置自动补全重量 → 成功），图片翻译/白底处理放到发布后的 TikTok 商家后台；或"删除重认领 → 在编辑弹窗/后台改图"（需注意公共采集箱认领标记残留陷阱；编辑抽屉保存通道待验证）
- **注意**：getDetail 响应结构里 **reqInfo/tikTokItemModel 在响应顶层**（`d.reqInfo`），**不在 `data` 里**——脚本已按此修正（2026-10-28）

## 编辑详情数据结构（getDetail返回）

```
data:
  id: ttl采集箱记录ID
  title: 泰文标题
  toRegion: "TH"
  toSellerNick: "YOUR_SHOP_TH"
  toCategoryNameTree: "家居用品,家居收纳,挂钩"
  reqInfo.tikTokItemModel.productCreateRequestV2:
    title: 泰文标题
    category_id: "853128"
    main_images: [{uri:"https://cbu01.alicdn.com/..."}]  ← 主图数组，替换uri即可（外链可直接用）
    description: "<div>...<img src=...>"  ← 详情图HTML
    skus: [{price:{amount:"185",currency:"THB"},
            inventory:[{quantity:984}], ...}]
```

## 待补

1. ~~认领接口body~~：**已破解 2026-10-29**（`numIids`+`targetShops` 嵌套结构，见上表与SKILL Step3）；破解通道=CDP Page.addScriptToEvaluateOnNewDocument document_start 注入 hook XHR/fetch（JS层拦截被前端原生引用绕过，故须页面脚本执行前注入）
2. ~~getUploadPolicy/OSS上传~~：不需要——外链URL直接填main_images保存即可（已验证）
3. ~~translate接口~~：不需要——认领时ERP自动翻译标题，图片翻译用我们自己的流水线
4. ~~发布接口body~~：**已破解 2026-10-29**（publishConfig+distribItemVos 结构，见上表与SKILL Step5）；此前"未找到该平台采集记录"为旧body结构错误所致
5. ~~删除接口~~：**已破解 2026-10-29**（batchOps + opsType:3，见上表）
