# 快递助手ERP跨境版 · 接口逆向与踩坑实录

> 本文件是「怎么在 ERP 内部接口上活下来」的实战笔记。凡是标 ✅ 的都来自本机实测（有时间点），
> 标 ⚠️ 的是尚未证实、不得当结论用的。执行 Step 3–Step 5 出问题先查本文件。
> 最后更新：2026-11-12（第二批 10 品复盘见**第九章**；首单压缩袋闭环见〇章）。
> 第九章含本轮新增的 `[12052250]` 根因、写操作时序、参数表读重量、三套默认重量与验证能力边界。

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
B="https://your-erp-web-frontend.oss-cn-<region>.aliyuncs.com/erpg/0.0.66/js"
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
| `[12052250] 不允许使用汉字。修改产品属性的属性值名称: 悬挂` | **`productCreateRequestV2.product_attributes` 残留中文**（✅ 2026-11-12 #3 沥水篮实测，`悬挂`/`塑料`）。`image_gate verify` 只扫 description 与图片，**不扫这个字段**；`price_gate` 也不扫 → 两个闸门全过仍 fail | 清空 `product_attributes=[]` 后重发即 success（#1 空数组发布成功可证安全）。发布前跑 `prepublish_guard.py --refresh --ids ...` 递归扫 v2 全部汉字 |
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

⚠️ **2026-11-12 第二批实测的能力边界**：后两路（配额 / 后台在售数）在 `erp_client.py`
与现有脚本里**都没有实现**，只能人工确认——`quotaQuery` 这里只是记录过路径，未封装成命令。
本轮 4 品发布后只验证到前两路，如实写「ERP 日志 success + 采集箱状态 1」，**不要写成四路全过**。

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
- [ ] **重新落盘**：`edit` 之后不重新 `ttl-detail` 会读到旧快照（本轮 #3 清空属性后本地文件仍含中文，guard 误报 FAIL）
- [ ] `description` 长度 > 0（不是纯空 HTML）
- [ ] **`description` 去标签后纯文本 ≥500 字符、≥2 个含文字段落**（2026-11-12 后台回执，见第十一章）；
      长尾档用可复用的分段当地语言模板，不要写「短 description」
- [ ] 类目三件套齐全，且属于**家居用品**树（跨类目会被下架）
- [ ] 每个 SKU：`price` 落在价格带内、`inventory` ≤ 999999（建议统一 1000）
- [ ] 每个 SKU 规格名：**与 1688 原始 `skus` 逐品对齐 + 当地语言**，无「自/最/第一/包退/工厂」等词。
  ⚠️ 不是一律「纯尺寸」——真实规格可能是图案款（蓝猫纹）、颜色（白/绿）、数量款（1把）、尺寸+颜色（小号米色）。
  本轮整批套「粉/蓝/灰」导致 4 品全错配，`seller_sku` 有时用 `outerId` 而非 `skuId`，对齐前先确认键
- [ ] 主图张数与 `main_images` 一致，首图白底，无中文残留
- [ ] **整个 `productCreateRequestV2` 递归扫汉字**，不只 description/图片（`[12052250]` 的根因）
- [ ] `prepublish_guard.py --refresh --ids ...` 退出码 0；`price_gate.py --csv <本批子集>` 退出码 0
      （全量会被店铺历史脏草稿拖成 1）；`image_gate verify` 退出码 0
- [ ] 包裹重量/尺寸按**真实值**传，`publishConfig` 是整批统一参数 → **重量尺寸不同必须拆单品提交**，
      禁用脚本默认 1kg / 10×10×10；推算值标 `INFERRED`
- [ ] `--dry-run` 先看一遍 publishConfig 与类目树

---

---

## 九、第二批 10 品复盘（2026-11-12，10 品 → 4 品 ERP 日志 success）

> 与〇章首单闭环不同的新增事实。✅ = 本机实测；⚠️ = 能力边界/推断，不得当结论。

### 9.1 效率：既有门槛没执行，6 个品全流程白跑
本轮跑完 采集 → 认领 → 编辑 → 图片闸门 才发现：5 品（除尘纸/纳米海绵/按压器/晾衣夹/湿巾）**0 张干净主图**
（#2 的 20 张全是「一件代发／16年实力工厂／店铺推荐带批发价」B2B 招商素材；#5 主图是中文营销字+车间照+纸箱仓库；
#7 混进别的产品和品牌图），1 品（冰格模具）唯一匹配类目是 `NON_MAIN_CATEGORY`。**6 个品全流程白跑**。

**门槛本来就存在**：`selection.yaml → supplier_gates.clean_consumer_images_min: 3`、strategy 5.2「无中文干净消费者向主图 ≥3 张」。
失效原因是选品只用了遨虾 `aibuy_rank_client.py search` 的结果（26 个字段，**不返回任何图片**），
没拉 1688 主图目视就把 offerId 写进了清单。→ **Step 0 取到 offerId 后必须先拉主图过闸再进 Step 1**。
另外别把两个阈值混了：选品门槛是 **3 张**（`clean_consumer_images_min`），发布闸门是 **1 张**
（`image_gate.yaml → min_clean_main_images`）；本轮 4 个过闸品里 3 个只有 1 张，按选品口径本就不该进清单。

其余三项一并在 Step 0 前置：类目权限（`case_category_tree.json`）／按尺寸估抛重后重算挂价／
多规格货源核对**最高** SKU 采购价（遨虾起批价 ≠ 最高 SKU 价，#7 4.8–20.9 元、#6 3.8 但含 14 元 SKU）。

图片判定本身可提速：先拼 contact sheet 做整图分类，**「删除不需要精判，保留才调原图放大」**，
152 张判定只需约 15 张放大。缩略图会骗人——#8 看着干净的产品图放大后有半透明中文水印「锐锋百货」，
#1 的 main_03 放大后见「买菜好帮手」。不做 AI 擦字就把带中文的图一律 `L0_drop`，不留 pending。

### 9.2 写操作时序（本轮最贵的错）
| 现象 | 事实 | 动作 |
|---|---|---|
| `ttl-claim` 一次提交 10 个 | 请求超时，但**服务端异步写入成功** | 每批 ≤3；超时后**先等异步落库再回查**，绝不盲目重试。本轮回查太早判失败→重试→重复 20 条，靠 `ttl-del` 删先写入那组 |
| `edit` 回执 | 恒 `{}`，但改动真实生效 | 必须回读 `getDetail` 确认；不重新落盘会读到旧快照 |
| `ttl-publish` 回执 `success:true` | 只代表**进队列** | #10 先 `processing` 后 `success`、#3 首次 `fail`；以前端日志 `status` 为准 |
| `publishConfig` | **整批统一参数** | 重量尺寸不同必须拆单品提交（本轮 4 次） |
| 连查 `queryErpItemListPage` | ✅ 报 707，要求等 10s | 串行 + 间隔；`erp_client.py items` 把响应裁成 7 字段，要原始 `itemInfo` 必须直调 |

### 9.3 数据获取：三处新事实
- ✅ **1688 授权字段不可信**：`tokenExpireTime` 显示 06:45 过期，10:24 `collect` 实测 8/8 成功。
  浏览器 1688 网页登录 ≠ ERP 内 OAuth 授权，但也不必按字段判死 → **用 1 个真实 offerId 探测**。
- ✅ **产品净重/尺寸藏在被判 `L0_drop` 的详情参数表里**：整图删（中文排版）≠ 不能读数字。
  多模态放大转录得到 #1 净重 45.5g／展开 38.5×60cm、#3 约 100g／25.5×15×9.1cm、#4 长 30cm、
  #10 大号 38.5×25cm。四路数据源里 `itemWeight` 卖家全填 `0.000/0.0` 或空、属性表无尺寸
  → **包裹实重与包装尺寸仍 UNKNOWN**，申报值按产品尺寸加余量推算并标 `INFERRED`。
- ✅ **三套默认重量打架**：`ttl-publish` 默认 1kg/10×10×10、`pricing.yaml default_weight_g:150`
  （**脚本里根本没被引用**）、本轮定价上界 400g。且 `detail.package_weight` 只是定价模型参数，
  **平台申报值走 `publishConfig`**，两者不是一回事。一律显式按品传。

### 9.4 抛重会吃掉利润
#3 沥水篮 25.5×15×9.1cm ÷6000 ≈ 580g，加缓冲 693g > 400g 定价上界 → ฿154 亏约 47 THB/单，
提价到 ฿201（$5.97，仍在带内）才覆盖。反例 #1 购物袋真实 56g 算出应挂 ฿100，反而**跌破 $3.5 下限**
→ 价格带下限是硬约束，多出来的算安全垫。立体件不能只按净重定价。

### 9.5 能力边界（别写成「四路全过」）
- ⚠️ 铺货配额 `quotaQuery` 与 TikTok 后台在售数在 `erp_client.py`／现有脚本里**没有实现**，只能人工确认。
  本轮 4 品只验证到「ERP 日志 success + 采集箱状态 1 + 异常标签空」。
- ⚠️ 价格闸门全量退出码 1 是店铺历史 15 条脏草稿所致 → 用本批子集 `--csv` 隔离（本轮 9 品子集退出码 0）。
- ⚠️ **`image_gate verify` 不扫 `product_attributes`**，补洞脚本 `scripts/prepublish_guard.py`（见 4.2 / SKILL 5.1）。

---

## 十、仍未证实（不得写成结论）

1. **抛重是否计费**：目前按 `max(实重, 抛重)` 保守估算，未找到官方计费原文
2. **头程公式时效**：`1 THB + 每 10g 1 THB` 出自 2022-09 版价卡（妙手引用），官方要求以最新物流价卡为准
3. **泰国进口税 17.7%**（关税 10% + VAT 7%，2026-01-01 取消 1500 THB 免税）：未建模，若价格带指「买家到手价」则采购价上限还要降约 30%
4. **`opsType:1` 改类目** 未实测（本次类目由自动匹配 + edit 写入，未走 batchOps）
5. **一件代发「1 SKU 对应多件采购」的捆绑发货能力**：未验证 → 在此之前禁止按 2 件套定价/宣称 2 件装（SKILL 4.4 / strategy 3.3）
6. **申报重量尺寸的合规口径**：本轮按产品尺寸加余量推算（`INFERRED`），非实测包裹值；供应商 48h 发货书面确认、货代 TIKTOK 标签覆盖仍为人工接管项
7. **TikTok 后台「描述格式 / 描述质量」的计数口径与权重影响**：见第十一章

---

## 十一、TikTok 后台编辑回执：描述优化项（2026-11-12，一手截图）

**来源 `VERIFIED`**：用户 13:41 提交的 `seller.tiktokshopglobalselling.com/product/edit/1700000000000000001`
（Thailand / YOUR_SHOP，状态「在售」）编辑页截图；原图存
`~/clipboard-images/<screenshot>.png`。
弹窗标题「2 个问题可能需要优化」，两行原文：

| 发品详情 | 建议（原文） |
|---|---|
| 描述格式 | 将你的描述分成段落，提高可读性从而影响购买决策。 |
| 描述质量 | 撰写至少含 500 个字符的描述，以吸引更多客户。 |

**性质 `VERIFIED`**：弹窗有「返回编辑 / **提交**」两个按钮，且商品已在售 → 这是**发布后的质量优化建议**，
不是第四章 `12052xxx` 那类发布阻断错误。不要写进错误码表，也不要当接口 fail 去重试。
同屏另有「基本信息 2个问题」下的「图片 · 商品图片少于 5 张」「品牌 · 缺少品牌信息」，
「商品详情 2个问题」即上表两项。是否影响曝光/权重 → `UNKNOWN`，无官方口径，不得推断成「会限流」。

**与本 Skill 的冲突（最值钱的一条）**：
- Step 4.2b 长尾档原写「模板化当地语言**短** description」；Step 4.3 硬规则 1 与第八章自检
  只要求 `description` **长度 > 0**；`image_gate.py apply` 又把整段 `localized_text` 包进**一个 `<p>`**。
  三者叠加 → 必然触发上面两条提示。**不是平台挑刺，是我们自己的流程产物不达标。**
- 本地快照佐证（2026-11-12，`erp_data/ttl_detail_*.json` 共 10 品，✅ 按
  `prepublish_guard.py` 正式口径「去标签 + 压缩空白」统计）：
  纯文本长度 0 / 0 / 0 / 88 / 134 / 144 / 153 / 160 / 240 / 560，**9/10 < 500 字符**；
  含文字段落数 0 / 0 / 0 / 1 / 1 / 1 / 1 / 1 / 15 / 27，**8/10 不足 2 段**
  （段落数 15 / 27 的两个品是 1688 招商话术与参数碎片、还混着中文，不是可读商品描述）。
  截图商品的泰文标题（大容量新款防水折叠便携购物袋）与 `ttl_detail_1500000000000000011.json`
  一致，该快照 `纯文本 153 字符 / 1 段 / 3 SKU`，与两条提示吻合。
  ⚠️ **此对应关系为 `INFERRED`**：后台商品 ID 与 ERP ttl_id 无已验证映射，ERP 发布还可能改写描述，
  **不能断言后台实际字符数就是 153**。

**落地改动（下次不返工）**：
1. `config/image_gate.yaml` 新增 `description_min_plain_chars: 500` / `description_min_paragraphs: 2`
   （`image_gate.py apply` 与 `prepublish_guard.py` 共用）。
2. `image_gate.py apply` 的 `localized_text` 按**空行拆多段**逐段包 `<p>`，单段时打印警告。
3. `prepublish_guard.py` 发布前查「纯文本字符数 + 含文字段落数」，默认阻断，
   `--allow-short-desc` 才降级为 warn。历史快照多不达标属预期（10 品里 9 品 <500 字符）。
4. Step 4.2b 长尾档改为「≥500 字符、≥2 段的**可复用**当地语言模板」，仍可 ≤3 分钟；
   建议四段：材质尺寸 / 使用场景 / 规格与内含物 / 保养与发货。
5. 「图片少于 5 张」「缺少品牌信息」只记录、不改硬闸：现有 `min_clean_main_images: 1`
   是发布最低线而非质量线；品牌一律「无品牌」（红线：不碰品牌货）。
   把主图线提到 5 张会让本轮过闸品里至少 3 品不合规（实测这 3 品只有 1 张干净主图），且收益未证实。

**仍未证实（不得写成结论）**：平台「500 个字符」按 HTML 源码、纯文本还是富文本节点计数；
泰文/越南文连写是否按 1 字符计；两项是否影响搜索权重/曝光；补齐后提示是否自动消失（需回后台复核）。
本 Skill 按「去标签 + 压缩空白后的纯文本」保守实现，偏严但无害。

---

## 十二、站点商品模块（2026-11-12 补，纠正「只有采集箱」的认知）

**触发**：用户截图指出 ERP 菜单「商品 → TikTok → 站点商品」有「编辑」按钮。
此前本 Skill 只逆向 `/item/crossBorder/platformCollect/*`（**发布前草稿采集箱**），
据此得出的「ERP 没有下架 / 重新上架能力」结论**只对采集箱成立**。
站点商品页前端有批量项 `editGoods(编辑商品信息)` / `goodsUpDn(商品上/下架)`，
主包 API 定义里也确有 `/item/item/itemBatchUpDownShelf`。**该结论已作废，勿再复用。**
完整接口契约见 `tiktok-api.md` 末章；本节只记逆向过程中的坑。

### 12.1 前端版本变了，旧方法论的路径要更新
- 2026-11-12 实测 `index.html` 指向 `erp/1.5.1/js/app.7cc506d6.js`（第一章记的 `erpg/0.0.66` 已 404）。
  版本与文件名以 `index.html` 现查为准，别背版本号。
- OSS 返回的 `.js` 是 **gzip**（magic `1f8b`），必须 `gunzip -c` 再搜。
- webpack chunk 文件名 = `js/<name>.<hash>.js`，但主包里有**三张长得很像的 `数字:"值"` 表**：
  ① 路由组件名表（3401 区）② `jsonpScriptSrc` 的**名字表**（9702–13729）
  ③ **hash 表**（13737–16482，另有一张 CSS hash 表在 5918）。
  **别把 CSS hash 表当成 JS hash 表**——本轮第一次就拼错文件名，拿到 404 NoSuchKey。
  正确做法：从 `js/"+(` 开始，按 `}[a]||a)+"."+{` 切出名字表与 hash 表再配对。

### 12.2 接口不在页面分包里，在主包 API 总表
`CrossShopeeSiteGoods.*.js` 里只有 6 个 URL 字面量，编辑/保存接口一个都没有；
真正定义在 `app.*.js` 的 API 总表（`function ItemItemQueryPlatformItemDetailApi(a){return d.a.post(...)}`），
编辑弹窗逻辑在 `CrossPlatformGoods.*.js`。→ **按页面找分包 + 按函数名回主包找 URL**，两头对齐。

### 12.3 保存接口的两个坑
1. 全量回传 `tiktokDetailInfo` 时**必须删 `replicatedProducts`**（多国家售卖副本 `skus` 为空），
   否则 `Missing SKU details in replicated_products`。前端关闭「多国家同步」时就是这么提交的。
2. **保存后立刻回读会骗人，两个方向都会误判**：本轮提交后
   `itemSuccess/skuSuccess/stockSuccess/settlementPriceSuccess` 全 true，日志查到
   `logOperateType:8 / enableStatus:1`，但 **+2 秒回读仍是 153 字符 / 1 段**；
   **数分钟后回读变成 1011 字符 / 4 段，确认已生效**。
   → `VERIFIED` = ERP 详情接口存在**数分钟缓存/异步延迟**；
   先别判「失败」，也别判「平台已生效」。正确动作：等数分钟重跑 `site_goods.py detail`；
   TikTok 后台是否同步、提示是否消失 = `UNKNOWN`，人工复核。
   （与第四章 `publish success:true 只代表进队列` 是同一类错误，但方向相反：一个是过早报成功，一个是过早报失败。）

### 12.4 本次操作结果（2026-11-12 全部完成，✅ 逐项对比备份核验）
- 4 个在售品**只改 `description` 一个字段**（原有第一段 + 3 段通用泰文；#10 原有 2 张详情图保留）。
  最终回读：1011 / 1002 / 992 / 1018 纯文本字符、**全部 4 段**。
  价格 ฿156 / 201 / 156 / 157、SKU 数、类目、`ACTIVATE/在售` 状态**全部未变**。
- **未删采集箱记录、未重新认领、未重新发布、未执行上下架**。
- 删 `replicatedProducts` 提交**不会丢副本**：#1 的 4 个 DRAFT 副本（MY/VN/SG/PH）保存前后一致，
  其余 3 品原本就 0 副本 → 该字段语义是「本次不同步修改」，不是「删除副本」。
- 平台侧详情备份在 `erp_data/_backup/2026-11-12_1404_before_relist/site_details/`。
- 上/下架接口只探明必填 `ops` / `list`，取值与元素结构 `UNKNOWN`，本轮故意没调用。
- 泰文文案由 AI 生成、未经母语者复核；**TikTok 后台两条提示是否消失 = `UNKNOWN`，需人工复核**。
