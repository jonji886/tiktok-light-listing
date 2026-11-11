# 快递助手ERP跨境版 · 接口逆向与踩坑实录

> 本文件是「怎么在 ERP 内部接口上活下来」的实战笔记。凡是标 ✅ 的都来自本机实测（有时间点），
> 标 ⚠️ 的是尚未证实、不得当结论用的。执行 Step 3–Step 5 出问题先查本文件。
> 最后更新：2026-11-12（首单压缩袋闭环：采集 → 认领 → 改价 → 修SKU → 发布 → 店铺在售）

---

## 〇、一条已跑通的完整时序（可作为标准剧本）

```
公共采集箱(856997023844)
  → ttl-claim 认领到 YOUR_SHOP_TH          ✅ 标题自动泰文化、类目自动匹配
  → set_price.py --rate 2.8 --add 39.26       ✅ 32.05/47.81/57.05 → 129/173/199 THB，数据零丢失
  → ttl-publish --weight 0.3 --dims 30x20x5   ❌ 22:28:42 fail（两条平台校验）
  → fix_sku.py --stock 1000                   ✅ 规格名改纯尺寸泰文 + 库存收敛 1000
  → ttl-publish --weight 0.3 --dims 30x20x5   ✅ 22:34:37 success
  → 四路验证：ERP日志 success / 采集箱状态 1 / 铺货配额 3→4 / TikTok 后台在售 4 款
```

**这条时序的意义**：`edit`（正确 body）保存 → `batchPublish` 发布 **是同一条可行链路**，
不是互斥的两条路。SKILL.md 旧版「保存即清空 itemModel，发布必败」的结论**根因是 body 结构写错**，不是 ERP 设计如此。

---

## 一、接口逆向方法论（可复用，别再用 CDP 试）

ERP 前端是 Webpack 分包，接口名被混淆，但**路径字面量没混淆**。抓接口最低成本路径是拉前端包离线搜：

```bash
B="https://kdzs-erp-item-web-frontend.oss-cn-zhangjiakou.aliyuncs.com/erpg/0.0.66/js"
curl -sS "$B/CrossFastUploadTiktokCollection.3afa808d.js" -o chunk.gz
gunzip -c chunk.gz > chunk.js   # 这些 .js 是 gzip 的，必须先解
```

```python
import re
s = open('chunk.js', encoding='utf-8', errors='ignore').read()
# 1) 拿全部接口路径
paths = sorted(set(re.findall(r'"(/[a-zA-Z][a-zA-Z0-9_/-]{5,110})"', s)))
# 2) 中文文案已被转成 \uXXXX，要解码才能看
dec = lambda t: re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), t)
# 3) 定位按钮文案附近的请求构造逻辑
i = s.find('\u8bbe\u7f6e\u552e\u4ef7')   # 「设置售价」
print(dec(s[i-600:i+600]))
```

要点：
- **按页面找分包**，不要全站搜。TikTok 采集箱页 = `CrossFastUploadTiktokCollection.*.js`；
  搬移页 = `MovingPlatformGoods.*.js`；跨平台商品 = `CrossPlatformGoods.*.js`；API 定义总表在 `app.dec.js`
- 版本号 `erpg/0.0.66/` 会随发版变；`0.0.66` 是 2026-11 的实测值，若 404 去 `index.html` 里找真实版本
- CDP `Page.addScriptToEvaluateOnNewDocument` 抓包是最后手段（认领接口当初是这么破的），**成本远高于读前端包**

---

## 二、`/item/crossBorder/edit`：唯一必须记住的 body 结构

### 2.1 正确结构（✅ 2026-11-11 实测，发布成功）

```json
{
  "id": "<ttl_id>",
  "platform": "tiktok",
  "sellerId": "<toSellerId>",
  "currency": "THB",
  "toCategoryId": "852744",
  "toCategoryIdTree": "600001,851848,852744",
  "toCategoryNameTree": "家居用品,家居收纳,收纳包",
  "title": "<标题>",
  "itemModel": {
    "shopeeItemModel": null,
    "temuItemModel": null,
    "tikTokItemModel": { "...": "原 ttm", "productCreateRequestV2": { "...改后的 v2" } }
  }
}
```

三件套规律（来源：前端 `buildTKRequest` 返回值）：
1. `itemModel` = **整个 reqInfo 结构**（含 shopee / temu / tikTok 三个键），不是内层 `tikTokItemModel`
2. **不能**传顶层 `reqInfo`（传了反而会覆盖成空）
3. 必须有 `sellerId`（不是 `toSellerId` 作顶层键名）

### 2.2 错误结构的症状（别再踩）

| 写法 | 症状 | 后果 |
|---|---|---|
| `itemModel` 传内层 ttm + 顶层再传 `reqInfo` | 接口可能返回 success 或 `{}` | 列表变 `MISSING_ITEM_DATA`、`tikTokItemModel: null`、售价区间空 |
| 缺 `sellerId` | 保存后归属丢失 | 同上，需 `ttl-del` + `ttl-claim` 重建 |

**恢复方式**：`ttl-del --ids <ttl_id>` → `ttl-claim --ids <货源ID> --region TH`。
⚠️ 重认领会**丢设置**（图片回到 1688 原图、类目回到未匹配、价格回到原始自动值），只有标题保留当地语言。所以别把它当日常手段用。

### 2.3 「保存后 getDetail 不返回 itemModel」不是数据丢失

- 现象：保存成功后列表的异常标签清空，再调 `getDetail` → `reqInfo.tikTokItemModel` 不见了
- 解释：ERP 的 getDetail 只服务「待完善草稿」
- **关键**：这不影响发布。✅ 2026-11-11 实测：`fix_sku.py` 保存后（异常只剩 `MISSING_WEIGHT`）直接 `ttl-publish` **成功**
- 推论被推翻：旧版 SKILL 说「保存→发布必败，只能走删除重认领/后台改图」，实为**旧 body 结构把数据写坏了**导致

---

## 三、`/item/crossBorder/platformCollect/batchOps`：批量操作(万能口)

同一个接口复用，靠 `opsType` 区分（来源：前端枚举，✅ 实测 2/3 可用）：

| opsType | 语义 | 额外参数 |
|---|---|---|
| 1 | 改类目 | `categoryId` / 类目树 |
| **2** | **改售价** | `priceType` + `priceFormula` / `price` |
| **3** | **删除** | — |
| 4 | 改备注 | `remark` |

### 3.1 公式改价（推荐，保留 SKU 价格梯度）

```json
{"idList":["<ttl_id>"],"platform":"tiktok","opsType":2,"priceType":2,
 "priceFormula":{"type":1,"multiplicationColumn":2.8,"additionColumn":39.26,
                 "reduceColumn":0,"decimalPlacesType":0,"tailAmount":null},
 "price":null}
```

- 公式：`新价 = 基准价 × multiplicationColumn + additionColumn - reduceColumn`
- `type`：1 = 基于**当前售价**、2 = 基于**货源价**
- `multiplicationColumn` = UI 上的百分比 ÷ 100；`additionColumn` 单位 = **商品币种**（THB 就填泰铢）
- `decimalPlacesType`：0 = 取整；`tailAmount` = 尾数（如 .99），null 即不处理
- ✅ 实测：47.81 / 57.05 / 32.05 → **173 / 199 / 129**，描述 1578 字符、5 张主图、3 个 SKU 全部保留
- CLI：`python3 scripts/set_price.py --id <ttl_id> --rate 2.8 --add 39.26 --save`

**怎么从目标价反解倍数**：`rate = (目标高价 - 目标低价) / (当前高价 - 当前低价)`，
再 `add = 目标高价 - 当前高价 × rate`。目标价来自 `pricing_calc.py`，不是拍脑袋。

---

## 四、发布：`POST /item/distribute/batchPublish`

### 4.1 body 要点（✅ 实测 success）

```json
{"publishConfig":{"weight":0.3,"weightSet":1,
   "packageLength":30,"packageWidth":20,"packageHeight":5,"packageSet":1,
   "filterAlreadyPublish":true,"forbiddenWordDetectMode":0,
   "priceLimitEnable":false,"priceLimitMultiple":"1","useTiktokMultiCountryConfig":false,
   "tiktokOverflowConfig":{"detailEnabled":true,"titleEnabled":true,"titleLimit":255,
      "titleTruncateType":"END","skuNameEnabled":true,"skuNameLimit":20,
      "skuNameTruncateType":"END","skuValueEnabled":true,"skuValueLimit":50,
      "skuValueTruncateType":"END","skuTotalEnabled":false}},
 "distribItemVos":[{"itemId":"<ttl_id>","sourcePlatform":"kdzs_erp",
   "targetPlatform":"tiktok","sourceSellerId":"<ERP_SOURCE_SELLER_ID>","targetSellerId":"<sellerId>",
   "targetSellerNick":"YOUR_SHOP_TH",
   "categoryIds":"600001,851848,852744","categoryNames":"家居用品,家居收纳,收纳包"}]}
```

2026-11-11 相对旧版的三处修正：
1. `weight` / `packageLength|Width|Height` **按真实包裹填**（旧版固定 1kg / 10×10×10，对轻小件高估头程）
2. `categoryIds` / `categoryNames` 传**真实类目树**（旧版传空串；虽然空串也能过，但类目校验信息会丢）
3. `itemId` 必须是 **ttl_id**（TikTok 采集箱记录 id），不是 itemCenterNumIid

CLI：`python3 scripts/erp_client.py ttl-publish --ids <ttl_id> --weight 0.3 --dims 30x20x5 [--dry-run]`

### 4.2 平台侧校验错误码（发布返回 fail 时逐条对）

| 报错 | 含义 | 修法 |
|---|---|---|
| `[12052153] 产品可变选项…内容词 (自)` | SKU 规格名含平台敏感/违禁词。本例「立体打印**自**透气」的「自」被判违规 | 规格名改**纯尺寸**当地语言，如 `ขนาด 50x70x30 ซม.`，不带任何功能描述 |
| `[12052055] SKU inventory quantity … must be between 0 and 999999` | 库存沿用 1688 原值（如 9,981,384）超上限 | 统一收敛到 **1000/SKU**（`fix_sku.py --stock 1000`） |
| `商品描述不能为空` | `description` HTML 长度 = 0 | 编辑侧补描述（有图的商品一般 1500+ 字符不会中招） |
| `类目不能为空` | 重认领后类目回空、自动匹配失败 | `fix_sku.py`/`prep_publish.py` 显式写类目三件套 |
| `tikTokItemModel不能为空` | `edit` body 结构错，商品数据被写坏 | 见第二章，用正确结构重写或删除重认领 |
| `TikTok店铺此地区没有设置开启的默认销售仓库` | TikTok 后台未建默认仓 | 去 `seller.tiktokshopglobalselling.com` 后台建仓（人工） |

**规律总结**：`fail` 都是**平台前置校验**（商品数据不合法），不是接口问题。看到 fail 先读 `失败原因` 原文，
按上表定位；**接口返回 `success:true` 只代表进了队列**，最终以前端日志 `success` 为准。

### 4.3 发布后必须做的四路验证（单一信号会骗人）

| 信号 | 命令/入口 | 通过值 |
|---|---|---|
| ERP 发布日志 | `erp_client.py logs --months 1` | `status = success` |
| 采集箱状态 | `erp_client.py ttl-list --region TH` | `状态 = 1`（已发布） |
| 铺货配额 | `POST /trade/crossItemDistrib/quotaQuery` | 已用数 +1 |
| **TikTok 后台在售数** | 商品管理 `在售 N` | N +1（最权威） |

`processing` 状态最长要等几分钟，别在 `processing` 时就宣布成功。

---

## 五、1688 采集与授权

| 坑 | 事实 | 正确做法 |
|---|---|---|
| 采集参数 | 采集端点 `/item/center/batchPluginCollectItem` 的数组字段是 **`itemIdList`**，元素为 **1688 offerId** | 用 `erp_client.py collect --ids <offerId,...>`；误填 ERP numIid 会报「1688商品ID不能为空」 |
| 重新授权 | `reauth/url` 要求字段名 **`authId`**，值取 `auth/list` 返回的 `id` | 传 `id` 字段会校验失败 |
| 授权有效期 | ✅ 实测 `tokenExpireTime` 仅 **约 10 小时** | 每次批量采集前先 `auth/list` 看剩余时间，别等到采集中途过期 |
| 登录态不共享 | `auth.1688.com`（OAuth 授权页）与 `www.1688.com` **不共享登录态** | 需在 OAuth 页单独登录一次并勾选协议 |
| 搜索编码 | 关键词必须 **GBK percent-encoding** | UTF-8 编码会搜出丝袜等无关品 |
| 页面滚动 | 带「深度思考」AI 模块的 1688 搜索结果页**滚动必超时** | 换更长更具体的词（「衣物压缩袋」优于「压缩袋」） |
| iframe 操作 | 阿里牛顿页在 iframe 内，**DOM 不可点击** | 不点页面，直接拿 offerId 调采集接口 |

---

## 六、Token 获取（WorkBuddy 浏览器通道没有 execute_js，只能本地捞）

ERP 把 JWT 放在 `sessionStorage.qnquerystring`，接口鉴权头同名。

- **不要**指望在 WorkBuddy 浏览器里 `execute_js` 读 sessionStorage——该通道**没有这个能力**
- 可行路径：Chrome 会把 sessionStorage 持久化到 `Session Storage/` 的 LevelDB，
  但值用 **UTF-16LE** 编码 + **snappy 压缩块**，直接 grep 必失败
- 用 `scripts/erp_token_grab.py --save --check`：解析 LevelDB block（含 snappy 解压）取明文并顺手验鉴权
- token 过期：用户重新登录 ERP 一次（不用复制粘贴），再跑脚本即可

---

## 七、WorkBuddy 浏览器通道的能力边界（别浪费时间试）

| 能力 | 实际情况 | 替代方案 |
|---|---|---|
| 执行 JS | ❌ 无 execute_js | 走本地 LevelDB / 直接调接口 |
| `tab_goto` | 仅接受 http(s) URL | 站内路由用页面导航点击 |
| iframe 内 DOM | ❌ 不可点击 | 绕过 UI，拿 ID 调接口 |
| 坐标点击 | 受页面缩放影响，实测落点偏到侧栏 | 优先 `dom_cua_click`（拿 node_id），坐标点击最后用 |
| 视口高度 | 受控浏览器视口小（实测 ~521px），抽屉右侧区域点不到 | 关键写操作全部接口化 |

**结论**：本 skill 的定位就是「能用接口的全部接口化」，浏览器只用于
① 用户登录接管 ② 读官方后台数据（商品机会中心/关键词榜） ③ 最终人工确认。

---

## 八、发布前自检清单（照着勾，能省掉 90% 的 fail）

- [ ] `ttl-detail` 能读到 `tikTokItemModel.productCreateRequestV2`（读不到说明数据被写坏）
- [ ] `description` 长度 > 0（不是纯空 HTML）
- [ ] 类目三件套齐全，且属于**家居用品**树（跨类目会被下架）
- [ ] 每个 SKU：`price` 落在价格带内、`inventory` ≤ 999999（建议统一 1000）
- [ ] 每个 SKU 规格名：**纯尺寸 + 当地语言**，无「自/最/第一/包退/工厂」等词
- [ ] 主图张数与 `main_images` 一致，首图白底，无中文残留
- [ ] 包裹重量/尺寸按**真实值**传（影响头程与平台物流校验）
- [ ] `--dry-run` 先看一遍 publishConfig 与类目树

---

## 九、仍未证实（不得写成结论）

1. **抛重是否计费**：目前按 `max(实重, 抛重)` 保守估算，未找到官方计费原文
2. **头程公式时效**：`1 THB + 每 10g 1 THB` 出自 2022-09 版价卡（妙手引用），官方要求以最新物流价卡为准
3. **泰国进口税 17.7%**（关税 10% + VAT 7%，2026-01-01 取消 1500 THB 免税）：未建模，若价格带指「买家到手价」则采购价上限还要降约 30%
4. **`opsType:1` 改类目** 未实测（本次类目由自动匹配 + edit 写入，未走 batchOps）
