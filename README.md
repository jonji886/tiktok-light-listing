# tiktok-light-listing

> 一个面向 AI Agent 的 **TikTok Shop 跨境店群铺货技能包**：把「选品 → 1688 采集 → 认领 → 图片本地化 → 定价 → 发布 → 回查」六段人工链路，收敛成一条有状态、可断点续跑、写操作需授权的自动化流水线。

![format](https://img.shields.io/badge/format-Agent%20Skill-6E4AFF)
![python](https://img.shields.io/badge/python-3.8%2B-3776AB)
![deps](https://img.shields.io/badge/dependencies-stdlib%20%2B%20optional%20cramjam-success)
![license](https://img.shields.io/badge/license-MIT-blue)

---

## 1. 它解决什么问题

跨境小卖家「铺货」不是上架一个动作，是六个动作的串联，每个都要人工点几十下：

| 环节 | 人工做法 | 单商品耗时 |
|---|---|---|
| 选品 | 后台翻榜单 → 截图 → 去 1688 手动搜同款 → 心算利润 | 3–5 min |
| 采集 | 1688 链接一条条粘进 ERP 采集插件 | 10 s |
| 认领 | 公共采集箱勾选 → 批量认领 → 下拉选平台 → 弹窗选店铺 | 30 s |
| 图片闸门 | 主图 + 详情长图满是中文「跨境电商专用/工厂直供/一件代发」→ 逐张判定、整图删、重建描述 | **5–8 min** ← 真正的大头 |
| 定价 | 汇率、平台扣点、交易手续费、退款预留、物流藏价各算一遍 | 2 min |
| 发布 + 回查 | 勾选 → 配置重量 → 提交 → 隔天翻记录看成功失败 | 1 min |

单店铺 150 款（TikTok 官方口径：多数商家上传 150 款后才出首单）≈ **40 小时纯体力活**。而这件事的正确解法不是「做个更快的后台」，是把**判断**和**点击**拆开：判断交给模型，点击交给脚本。

这个 Skill 就是把这条链路写成 Agent 可执行的规程 + 一个零依赖 CLI。

---

## 2. 边界：它做什么，不做什么

明确划界比堆功能重要。一个什么都做的 Skill 在 Agent 手里就是不可控。

| | 内容 |
|---|---|
| ✅ **做** | 单一跨境 ERP 实例内，1688 货源 → TikTok Shop 东南亚站（TH/VN）的选品、采集、认领、图片本地化、定价、发布、发布记录回查 |
| ✅ **做** | 硬红线自动剔除（品牌/IP、液体、带电、食品保健、宗教、易碎、活体、多规格价差、限售认证、跨类目） |
| ❌ **不做** | 其他 ERP、其他平台（Shopee/Lazada）、TikTok 自运营店（仅跨境店） |
| ❌ **不做** | TH/VN 以外的站点（PH/SG/MY 预留但未实现） |
| ❌ **不做** | 跨类目铺货——店铺类目是硬约束，跨类目发品会被下架甚至处罚 |
| ❌ **不做** | 无人值守的全自动跑量。写操作必须人工点名清单 |

> 一个 Skill 的价值不在于覆盖多少平台，而在于**在它声称的范围内不出错**。

---

## 3. 架构

```
┌──────────────────────────────────────────────────────────────┐
│  Agent（Claude / 豆包工作 / 任意支持 SKILL.md 的运行时）        │
│                                                              │
│  SKILL.md ── 动作分层与执行规程（判断层）                      │
│    ├─ references/selection-strategy.md  选品评分与红线规则     │
│    ├─ references/erp-pitfalls.md        接口坑与逆向方法备忘   │
│    └─ config/*.yaml         权重/阈值/费率/图片闸门词表（外置）│
└───────────────────────────┬──────────────────────────────────┘
                            │ CLI 调用（唯一契约）
┌───────────────────────────▼──────────────────────────────────┐
│  scripts/erp_client.py（执行层，纯标准库，无三方依赖）          │
│    collect · items · ttl-list · ttl-detail · ttl-claim        │
│    ttl-del · ttl-publish · logs · shops · token               │
│  scripts/set_price.py · fix_sku.py · prep_publish.py          │
│    （批量公式改价 / 修 SKU 规格名与库存 / 发布前准备与校验）    │
│  scripts/price_gate.py · image_gate.py · prepublish_guard.py   │
│    （价格库存闸门 / 图片闸门 / 发布前总闸，三道互补）           │
│  scripts/batch_prep.py · apply_listing_fix.py · site_goods.py  │
│    （批量编排 / 一次性清单修复 / 平台侧在售商品客户端）         │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP（qnquerystring: <JWT> + appType:3）
┌───────────────────────────▼──────────────────────────────────┐
│  目标跨境 ERP SaaS          +    TikTok Shop 卖家后台           │
└───────────────────────────────────────────────────────────────┘
```

**分层意图**：判断（选什么、改哪张图、定多少钱）必须能被模型改写；执行（发什么请求、怎么重试、怎么落盘）必须是确定性的、可测的。两者只通过 CLI 参数契约耦合——所以换模型、换运行时、换 ERP 都只动一层。

---

## 4. 三条设计原则

这三条是踩过坑之后才定下来的，不是抄的。

### 原则一：按「错了谁承担后果」决定自动化边界

不是按「能不能自动化」分层，而是按**责任归属**分层：

| 分层 | 判据 | 具体动作 |
|---|---|---|
| **AI 自动** | 错了没损失，或损失可回滚 | 取需求信号、搜同款、四维打分、价格带校验、采集、认领、对账、算价、**两通道图片扫描 + 多模态整图判定**、**description 重建**、**图片闸门校验**、保留图的擦字/翻译/白底、提交发布、查记录 |
| **硬红线自动阻断** | 错了有罚金/下架风险，但判定规则确定 | 品牌侵权、IP 联名、液体化工、带电、食品保健医疗、宗教、易碎、活体、多规格价差、限售需认证、跨类目 |
| **人工接管** | 错了钱直接没了，且规则无法穷举 | ERP 登录/短信验证码、候选清单最终确认、**整图判定低置信度时转人工**、图片处理结果抽检（前 10 个人工过一眼） |

为什么红线要「自动阻断」而不是「提示确认」？因为**规则能穷举的风险，问人等于把责任推给人**——而人会疲劳、会连点确认。规则穷举不了的（比如「这张图的泰文有没有幻觉字」），才交给人。

### 原则二：所有阈值外置到 YAML，代码只做执行

费率会变、汇率会变、什么样的同款数算红海会变。改一个数字不该改代码。`config/selection.yaml` 里改一行即时生效：

```yaml
weights:              # 四维权重，合计 1.0
  fulfillment: 0.30   # 履约确定性：发货地→货代仓的快递天数/库存/起订量
  content: 0.25       # 内容力：达人/视频/直播数（TikTok 是内容场）
  margin: 0.25        # 实净利：全成本口径（国内快递 5 + 货代 2 + 包材 0.6 + 跨境按重量 0.0198/g）
  competition: 0.20   # 站内竞争：TikTok 在售款数/「无竞争对手」标签
```

### 原则三：写操作只对「用户点名过的清单」执行

`claim`（认领）、`del`（删除）、`publish`（发布）三个动词会真实改变线上店铺状态，且部分不可逆。规程强制要求：候选清单先落盘给人确认，写操作只接受点名清单，脚本不做任何「顺手帮你把剩下的也发了」的推断。

---

## 5. 工作流

```
Step 0  选品        → samples/selection_candidates.csv（人工确认清单）
Step 1  采集        → erp_client.py collect --ids <offerId,...>
Step 2  对账        → erp_client.py items --size 50
Step 3  认领        → erp_client.py ttl-claim --ids <numIid> --region TH
Step 4  编辑        → ttl-detail → image_gate 图片闸门（两通道判定/重建/写回）→ 定价 → 修 SKU·重量
Step 5  发布        → 三闸门全过（price_gate / image_gate verify / prepublish_guard）
                     → erp_client.py ttl-publish --ids <ttl_id,...>
Step 6  回查        → erp_client.py logs --months 1；平台侧在售状态用 site_goods.py detail
```

**Step 0 选品**是整个链路的漏斗口，规则见 [`references/selection-strategy.md`](references/selection-strategy.md)（九章）：官方后台取需求信号 → 1688 找货 → 硬红线与供应链门槛剔除 → 四维评分（履约/内容力/实净利/站内竞争）→ 价格带校验 → **按发货城市聚簇 + 人工查货代覆盖（TIKTOK 标签）** → 出清单。

**Step 4 图片闸门**是本 Skill 真正的差异化部分。1688 详情图里塞满「跨境电商专用 / 工厂直供 /
一件代发 / 支持OEM」这类面向中国卖家的 B2B 素材，直发等于把招商广告贴到 TikTok 商品详情页。
而漏得最多的不是 5 张主图，是 `description` 里 **8–25 张详情长图**（实测见 §8.5）。

```
ttl-detail 落盘
  → image_gate.py scan     主图 + description 两通道全部下图，出 review.json（verdict=pending）
  → 多模态模型逐张看整图  回填 L0_drop / L1_inpaint / L2_localize / L3_keep（低置信度转人工）
  → image_gate.py apply    整图删 L0；丢弃 1688 模板残渣；重建 description
                           = 当地语言纯文本 + 保留的干净图；L1/L2 新 URL 走 uri_map 回填
  → image_gate.py verify   发布前硬闸，退出码 1 = 禁止 ttl-publish
  → apply --save           写回 ERP（按官方 edit body）→ 回读验收
```

**核心取舍：脚本不判图，整张删而不是擦**。这类图不是「干净商品图 + 一行字」，是整版为招商
设计的中文排版——擦完还剩工厂照片、参数表、批发承诺、二维码，AI 补图还会注入幻觉字。
判定单位是整张图，不是文字块；艺术字、竖排字、印章字 OCR 会漏，所以用多模态模型看整图，
而不是维护一张永远列不全的关键字表。`config/image_gate.yaml` 的 B2B 词表只用于**文本残留兜底**，
看不见图里的字，不能当判定规则。

**description 必须整段重建，不能删空**：剥掉所有 `<img>` / `usemap` / `styleType` / 中文文本节点，
只回填保留的干净图和一段泰文/越南文纯文本。删空会直接触发平台的「商品描述不能为空」。
`apply` 在没有当地语言文本时会中止，**不会替你编一段**。

**AI 生成小语种文字的高发缺陷**（只作用于 L1/L2 的少量保留图；真实 case 实测，必须逐张校验）：

- **幻觉字**：模型会在金属反光、玻璃、空白处注入伪文字甚至反向英文。原图没有的元素一律不能信。修正 prompt 必须写明「仅保留指定文字区域，其他区域不得出现任何文字」，必要时二次、三次修正。
- **OCR 不可信**：内置 OCR 对泰文小字多次读出互相矛盾的结果。判定正确性的唯一办法是**裁剪单字符放大到原始 10 倍逐字目视**。
- **验收五条**：① 指定译文完整拼写正确 ② 无中文残留 ③ 无杂散伪字 ④ 无网址残留 ⑤ 首图白底。

**发布前是三道闸门，不是一道**（2026-11-12 第二批实测补）。文字规则拦不住批量操作，
三个脚本各自读不同的数据、拦不同的失败：

| 闸门 | 读什么 | 拦什么 | 退出码 1 |
|---|---|---|---|
| `price_gate.py` | `erp_data/tiktok_list.csv` | ERP 认领时自动铺的线性公式价破价格带、库存 >999999、类目缺失 | 禁止发布 |
| `image_gate.py verify` | `ttl_detail_<id>.json` | description 与图片里的中文 / B2B 素材 / 模板残渣 / 描述质量 | 禁止发布 |
| `prepublish_guard.py` | 全 `productCreateRequestV2` 递归 | **属性名残留汉字**、SKU 规格名汉字、weight/dims 缺失、重复认领、描述 ≥500 字符 + ≥2 段 | 禁止发布 |

真实事故：某沥水篮 description 与主图全干净、前两道都过，仍被 TikTok 拒绝
`[12052250] 不允许使用汉字。修改产品属性的属性值名称: 悬挂` —— 根因是
`product_attributes` 里残留中文「悬挂 / 塑料」，只有第三道闸门扫这个字段。
清空 `product_attributes=[]` 后重发 success。`prepublish_guard.py` 读的是本地快照，
改完 ERP 必须加 `--refresh` 重新落盘，否则会拿旧值误报。

---

## 6. 快速开始

**前置**：Python 3.8+（无需 pip install，全标准库）、一个已开通 TikTok Shop 跨境店的 ERP 账号。

```bash
git clone https://github.com/jonji886/tiktok-light-listing.git
cd tiktok-light-listing

# 1) 配置站点（不写死进仓库）
cp .env.example .env
#    填 ERP_BASE_URL / TH_SELLER_ID / VN_SELLER_ID / ERP_SOURCE_SELLER_ID
set -a; source .env; set +a

# 2) 注入登录态：浏览器登录 ERP 后，控制台执行
#    sessionStorage.getItem('qnquerystring')
export ERP_TOKEN='<paste-jwt>'

# 3) 验证连通
python3 scripts/erp_client.py token --check
# → [erp] token OK, tiktok shops total=N

# 4) 校验定价配置自洽（改费率/汇率后必跑）
python3 scripts/pricing_calc.py --check
# → 配置自洽

# 5) 发布前三闸门自检（对已落盘的快照，不联网）
python3 scripts/price_gate.py
python3 scripts/image_gate.py verify --detail erp_data/ttl_detail_<id>.json
python3 scripts/prepublish_guard.py --ids <ttl_id,...> --refresh
# → 任一退出码 1 = 禁止发布（ttl-publish 前必须三道全过）
```

**第一条只读命令**（安全，不改任何东西）：

```bash
python3 scripts/erp_client.py shops --platform tiktok
python3 scripts/erp_client.py ttl-list --region TH --size 20
```

---

## 7. CLI 参考

| 命令 | 读写 | 作用 |
|---|---|---|
| `token set/check` | — / R | 保存或验证登录态 |
| `collect --ids` | R | 1688 offerId 批量进公共采集箱（500/批） |
| `items [--ids]` | R | 公共采集箱列表，offerId → ERP 内部 numIid 映射 |
| `ttl-list --region` | R | TikTok 采集箱列表（含异常标签，异常清空 = 已完善） |
| `ttl-detail --id` | R | 编辑详情，核心数据在 `reqInfo.tikTokItemModel.productCreateRequestV2` |
| `ttl-save --id [--price --weight --images --category]` | **W** | ⚠️ **已废弃**：body 是旧结构，会把数据清空成 `MISSING_ITEM_DATA`；改用 `image_gate.py apply --save` / `fix_sku.py` / `prep_publish.py` |
| `ttl-claim --ids --region` | **W** | 认领到指定站点店铺 |
| `ttl-del --ids` | **W** | 删除采集箱记录（`batchOps` + `opsType:3`） |
| `ttl-publish --ids` | **W** | 提交发布队列，4 个/批 |
| `logs --months` | R | 发布记录，`success/fail/processing/examine` |
| `shops` | R | 店铺列表与授权状态 |

所有读命令默认落盘 `erp_data/*.csv` + `*.json`（已在 `.gitignore` 中排除，不入库）。

**配套脚本**（编辑/发布环节，均纯标准库）：

| 脚本 | 读写 | 作用 |
|---|---|---|
| `set_price.py` | **W** | 官方「批量设置售价」接口（`batchOps opsType:2`，公式/固定价）改价，**不清空商品数据**——比 `ttl-save` 改价安全 |
| `fix_sku.py` | **W** | 修 SKU 规格名与库存（过 TikTok 发布前置校验）；body 必须是**整个 reqInfo 结构**，不能传顶层 reqInfo，否则清空成 `MISSING_ITEM_DATA` |
| `prep_publish.py` | **W** | 发布前置：按 SKU 梯度改价 + 补包裹重量/尺寸 + 回读验证 |
| `image_gate.py` | R / **W** | **详情页图片闸门**：`scan` 两通道扫描 / `apply` 按判定重建（`--save` 才写 ERP）/ `verify` 发布前硬闸（见 §8.5） |
| `pricing_calc.py` | R | 售价 / 采购价上限 / 配置自检（见 §9） |
| `kdzs_fastmoss.py` | R | 免登录选品数据源：销量 / 达人数 / 视频数（后端 FastMoss） |
| `erp_token_grab.py` | R | 从本机 Chrome LevelDB 提取登录态 JWT（可选，依赖 `pip install cramjam`） |
| `price_gate.py` | R | 价格/库存/类目闸门（读 `tiktok_list.csv`）；`--hint` 反推售价与价格带；退出码 1 禁止发布 |
| `prepublish_guard.py` | R | 发布前总闸：递归扫全 `productCreateRequestV2` 的汉字、属性名、SKU 规格名、重量/尺寸、重复认领与描述质量；`--refresh` 强制重新落盘 |
| `batch_prep.py` | **W** | 批量编排：破带重定价 + 库存收敛 + 复检，默认 dry-run，`--save` 才写；售价区间为空或重量缺失的品不猜、转人工 |
| `apply_listing_fix.py` | **W** | 一次性清单修复（单件 + 400g 保守计费重定价、精简多规格 SKU、类目修正），默认 dry-run；仓库版内的商品 ID 是占位示例 |
| `site_goods.py` | R / **W** | **平台侧在售商品**客户端（与采集箱两套数据）：`list` / `detail` / `log` / `update-desc`（默认 dry-run）；站点地址与 sellerId 走环境变量 |

`image_gate.py` 三条子命令（纯标准库，`scan` / `apply` 默认 dry-run / `verify` 全程离线）：

```bash
python3 scripts/image_gate.py scan --detail erp_data/ttl_detail_<id>.json
# → erp_data/image_gate/<id>/ 下图 + review.json（verdict 全为 pending）
# 【多模态模型/人工】逐张看图回填 verdict / localized_text / uri_map
python3 scripts/image_gate.py apply --detail erp_data/ttl_detail_<id>.json \
    --review erp_data/image_gate/<id>/review.json          # 只产 cleaned_detail.json + report.json
python3 scripts/image_gate.py verify --detail erp_data/image_gate/<id>/cleaned_detail.json
python3 scripts/image_gate.py apply --detail erp_data/ttl_detail_<id>.json \
    --review erp_data/image_gate/<id>/review.json --save   # 写回 ERP 并回读
```

四级判定：`L0_drop` 整图删 / `L1_inpaint` 擦一行营销字 / `L2_localize` 功能词原位翻译 /
`L3_keep` 原样保留。有 `pending` 未判定时 `apply` **默认拒绝执行**（可显式
`--assume-pending drop` 保守全删）。判定表还能交叉检查被删 URL 是否残留在两个通道。

三道闸门一起跑（顺序无关，任一失败即停止发布）：

```bash
python3 scripts/erp_client.py ttl-list --region TH        # 先落盘 tiktok_list.csv
python3 scripts/price_gate.py --site TH
python3 scripts/image_gate.py verify --detail erp_data/ttl_detail_<id>.json
python3 scripts/prepublish_guard.py --ids <ttl_id,...> --refresh
# 三道全过 → erp_client.py ttl-publish --ids <ttl_id,...>
```

平台侧在售商品的后续维护（发布成功之后，不是发布前）：

```bash
python3 scripts/site_goods.py list --platform tiktok --limit 10
python3 scripts/site_goods.py detail --num-iid <id>
python3 scripts/site_goods.py update-desc --num-iid <id> --text-file desc_th.txt          # dry-run
python3 scripts/site_goods.py update-desc --num-iid <id> --text-file desc_th.txt --save   # 写
```

---

## 8. 工程笔记：反直觉的坑

这部分是这个仓库里最值得读的东西——都是真实链路跑出来的，不是文档抄的。

### 8.1 `getDetail` 只在「草稿态」返回数据，不是数据丢失

ERP 的 `getDetail` 只服务**待完善商品**。商品一旦被保存完善（异常标签清空），`getDetail` 就**不再返回 `itemModel`**。

第一次遇到会以为数据被删了，反复重试。实际上这是设计：异常标签是「这个商品还需要处理」的标记，标记清空后读取通道就关了。

**推论**：图片语言正确性必须在**保存前**在本地文件层面逐张验收完毕。保存后 ERP 端无法再复核（浏览器编辑弹窗会显示「已选 0 张」）。

### 8.2 接口保存与接口发布互斥

保存（`edit`）的语义是「完善定型」，执行即清空草稿态 → 发布（`batchPublish`）读不到 `itemModel` → 报 `tikTokItemModel不能为空`。

这条在**页面发布和接口发布上都成立**，两个真实 case 完整复现过。所以「先用接口把图片和类目补好，再点发布」这条路根本不存在。

**两条可行路线**：

- **路线 A（推荐，图片先翻译）**：认领 → 取 `itemModel` → 本地图片本地化并逐张验收 → **删除该记录 → 重新认领**（草稿态恢复，`itemModel` 重新可读）→ 用新记录构造保存体 → 发布。注意重新认领有陷阱：公共采集箱认领标记会残留，可能不产生新记录，需先按 numIid 定位。
- **路线 B（发布后改图）**：认领 → **不保存直接发布**（带异常直接发，发布配置会自动补全重量）→ 发布成功后到 TikTok 商家后台二次替换图片。

### 8.3 抓内部请求：注入时机比拦截技术重要

破解 `batchClaimMultiShop` 的 body 之前，走了 4 条失败路径：

| 路径 | 失败原因 |
|---|---|
| 运行时替换 `XMLHttpRequest.prototype` | 前端缓存了原生引用，原型替换无效 |
| 用自动化框架的 `network_requests()` | 只暴露 url / method / status，**没有 body** |
| Webpack 产物里静态搜接口名 | 字符串混淆，0 命中 |
| 在页面加载后注入 hook | 时机晚于应用启动，请求已被发走 |

**突破口是时机不是技术**：用 CDP `Page.addScriptToEvaluateOnNewDocument` 在 `document_start`（任何页面脚本执行前）注入 hook，重写 `open/send` 与 `fetch`，落盘到 `window.__reqLog`，然后手动点一遍流程，回读真实 body。

结论很通用：**要抓首屏就发出的请求，唯一可靠的位置是 document_start。**

### 8.4 图片不必走 OSS 上传

`getUploadPolicy` 拿到的凭证 curl 直调报 500，看起来绕不过去。实测发现 `main_images[].uri` 直接填**任意可访问的外链 URL** 就能保存生效——ERP 不做来源校验。

少一个上传环节，整条流水线少一次失败点。

### 8.5 「跨境电商专用」漏进详情页，病根不在主图，在 `description`

旧流水线（原 Step 4.3）只处理 `main_images[].uri`；`prep_publish.py` 里也只是**打印**
`desc.count('<img')`，没有任何替换能力。实测 9 个真实 `ttl_detail_*.json`：

| 事实 | 实测结果 |
|---|---|
| 主图 | 恒 5 张 |
| `description` 里的详情长图 | **8–25 张** |
| 带中文纯文本节点的样本 | 3/9（133 / 156 个汉字），例如「产品注意事项…下载素材包」 |
| 模板残渣 | `usemap` / `styleType` / `offer-template`，以及中文 `alt`「预览状态下无法点击，发布后可点击跳转…」 |
| 抽看一张真实详情长图 | 整版「工厂直供 / 48 小时发货，一件代发 / 支持OEM定制，可贴牌 / 代发、批发、跨境 / 5000平大仓库」 |

所以「擦字」是错解：这张图整版都是给中国卖家看的招商素材，擦掉几处还剩工厂照片、参数表、
批发承诺、二维码，AI 补图还会注入幻觉字（§5）。**整图删，判定单位是整张图。**

由此多出两个必须同时处理的点：

1. **description 重建而不是删空**。发布自检要求长度 > 0（§9 配置 / SKILL.md 5.2 错误码表），
   所以 `apply` 剥掉 1688 模板后回填保留的干净图 + 一段当地语言纯文本；没有文本就中止。
2. **`fix_sku.py` 的回读断言会误报**。它原来把「description 长度不变 + 主图数不变」当作
   「数据没被清空」——闸门重建后这两个数本来就该变。已改为语义校验：描述非空、主图 ≥1、
   无中文/B2B 残留、SKU 数与价格未缩水。`prep_publish.py --show` 现在直接打印闸门结论。

还有一个容易忽略的边界：`getDetail` 保存后会关闭草稿通道（§8.1），此时回读不到
`itemModel`。这种情况**不能宣称图片已验收**，只能以发布日志 + TikTok 后台在售页为准；
`image_gate.py` 的 `--save` 回读分支和 `fix_sku.py` 都会明确打印这个限制。

---

## 9. 配置

| 文件 | 内容 |
|---|---|
| `config/selection.yaml` | 破零目标（150 款 / 20 天）、履约红线（48h 打包 / 3 自然日到仓）、地域收敛（城市级 + 货代 TIKTOK 标签）、站点价格带与采购价区间、四维权重、需求过滤、供应链门槛（含 **`clean_consumer_images_min: 3` / `desc_rebuildable`**）、红线、竞争与内容力口径、铺货节奏 |
| `config/pricing.yaml` | 平台佣金、交易手续费、**电商增长服务费**、退款预留、目标净利率、汇率、国内段成本、跨境段按重量物流 → 两档加价系数（shelf / creator） |
| `config/image_gate.yaml` | **B2B 词表（仅文本残留兜底，不是判定规则）**与图片闸门阈值：`min_clean_main_images` / `max_cjk_chars_in_description` / `require_no_template_residue` / `check_b2b_in_title`；另有描述质量线 `description_min_plain_chars: 500` / `description_min_paragraphs: 2`（TikTok 后台软提示，计数口径未证实，按纯文本保守实现） |

**定价公式**（2026-11-11b：跨境段物流由固定 10 元改为按计费重量线性计费）：

```
到岸成本(CNY) = 采购价×件数 + 国内快递(5，同城3.5/跨省6) + 货代(2) + 包材(0.6)
              + max(实重, 抛重) × ¥0.0198/g × 件数
售价(本币)    = 到岸成本 × 汇率 × markup
markup        = 1 / (1 - 佣金 - 交易手续费 - 增长服务费 - 退款预留 - 达人佣金 - 目标净利)
```

| 站点 | 货架场 shelf（当前启用） | 达人场 creator（停用） |
|---|---|---|
| TH | `1.62`（净利 12%，破零冲量） | `2.58` |
| VN | `2.08`（净利 25%） | `2.63` |

**不要手算**，用 `scripts/pricing_calc.py`：`--cost` 出售价与带内判定、`--limits` 反推采购价上限矩阵、`--check` 校验配置自洽（改费率/汇率后必跑）。算完先输出建议售价表给人确认，确认后才批量回填 `skus[].price`。

---

## 10. 目录结构

```
tiktok-light-listing/
├── SKILL.md                          # Agent 技能定义：动作分层 + 六步执行规程
├── README.md
├── .env.example                      # 环境变量模板
├── config/
│   ├── selection.yaml                # 选品规则（履约红线/四维权重/门槛/节奏/图片门槛）
│   ├── pricing.yaml                  # 定价参数（费率/汇率/成本项/两档 markup/价格带）
│   └── image_gate.yaml               # 图片闸门（B2B 词表兜底 + 硬闸阈值）
├── references/
│   ├── selection-strategy.md         # 选品方法论（九章）：履约链路、成本结构、数据源分层、红线与门槛、四维评分
│   ├── erp-pitfalls.md               # 接口坑备忘：body 结构、错误码、逆向方法、浏览器边界
│   └── tiktok-api.md                 # 接口契约（已实测 12 个 + 待补）
├── scripts/
│   ├── erp_client.py                 # 主 CLI（采集/认领/编辑/发布/回查），纯标准库
│   ├── set_price.py                  # 批量公式改价（batchOps opsType:2）
│   ├── fix_sku.py                    # 修 SKU 规格名与库存（官方 edit body 结构）
│   ├── prep_publish.py               # 发布前置：改价 + 补重量尺寸 + 回读验证
│   ├── image_gate.py                 # 详情页图片闸门（scan / apply / verify），纯标准库
│   ├── pricing_calc.py               # 定价与价格带校验（--cost / --limits / --check），纯标准库
│   ├── kdzs_fastmoss.py              # 选品数据源（后端 FastMoss 榜：销量/达人/视频数），免登录只读
│   ├── erp_token_grab.py             # 从本机 Chrome LevelDB 提取登录态 JWT（可选，依赖 cramjam）
│   ├── price_gate.py                 # 价格/库存/类目闸门（读 tiktok_list.csv）
│   ├── prepublish_guard.py           # 发布前总闸（全 reqInfo 递归扫汉字/属性/规格/描述质量）
│   ├── batch_prep.py                 # 批量编排：破带重定价 + 库存收敛 + 复检（默认 dry-run）
│   ├── apply_listing_fix.py          # 一次性清单修复（单件 + 400g 保守计费重定价）
│   ├── site_goods.py                 # 平台侧在售商品客户端（list/detail/log/update-desc）
│   └── erp_token.example.txt         # token 文件模板（真实 token 已被 .gitignore 排除）
└── samples/
    ├── selection_candidates.sample.csv
    └── tiktok_list.sample.csv
```

运行时产物 `erp_data/`（采集箱快照、发布日志、`image_gate/<id>/` 的下载图与 review.json、
cleaned_detail.json、report.json）不入库。

---

## 11. 局限与已知风险

诚实列出来，比藏起来强。

- **依赖未公开内部接口**：无契约、无版本号、字段随时可能变。任何报错的第一处置是回退浏览器自动化，不是重试。
- **登录态会过期**：`qnquerystring` 是浏览器会话 JWT，过期后所有命令返回 `703`。设计中「登录」永远是人工步骤，不做自动续期。
- **不适合无值守生产**：图片本地化的验收标准是目视，模型生成的泰文/越南文有幻觉字风险。这是设计上的取舍，不是缺陷。
- **图片闸门看不见图里的字**：`image_gate.py` 的文本扫描只能兜底 HTML 文本节点与属性，
  艺术字 / 竖排字 / 印章字 / 图内中文必须靠多模态模型逐张目视回填 `review.json`；
  判定表留 `pending` 时脚本会拒绝执行，不会静默放行。
- **类目自动匹配不可靠**：两次重认领后类目均为空，`CATEGORY_NOT_MATCH` 不会自愈，必须显式指定类目。
- **发布成功率受店铺侧限制**：如「该地区没有设置开启的默认销售仓库」属于 TikTok 后台配置问题，脚本无法代解。
- **`ttl-translate` 是骨架**：body 待确认，当前只打印 TODO（实际上认领时 ERP 已自动翻译标题，此命令优先级低）。
- **三道闸门不等于平台会收**：它们只覆盖本地快照里能看到的字段。TikTok 后台「描述优化」软提示的字符计数口径
  （HTML 源码 / 纯文本 / 富文本节点、泰文连写）`UNKNOWN`，是否影响曝光也未证实；本仓库按纯文本 ≥500 字符保守实现，偏严。
- **采集箱与站点商品是两套数据**：`erp_client.py` 管发布前草稿（`ttl_id`），`site_goods.py` 管平台侧在售商品
  （`numIid`）。发布成功不代表站点商品模块立刻可读，ERP 详情有数分钟缓存延迟，回读未更新时不要下结论。
- **一次性脚本里的清单是示例**：`apply_listing_fix.py` / `batch_prep.py` 用法里的商品 ID 在仓库版已换成占位示例，
  直接照抄不会命中任何真实商品；要复用先按自己的采集箱清单改。

---

## 12. 合规声明

本项目是**方法论与工程实践的教学样本**，不是通用作弊工具：

- 仅用于**操作者自己拥有或已获书面授权**的 ERP 账号与店铺。
- 不含、不要求、也不接受任何绕过平台风控的手段（验证码识别、指纹伪装、代理池等）。
- 所有接口行为来自**操作者自身浏览器内的正常业务操作**的观测，未做越权访问。
- 面向未公开接口的自动化存在违反服务条款的风险，**使用前请自行确认目标平台条款**。作者不对使用者行为负责。
- 仓库中的**私有 ERP 域名**、店铺 ID、sellerId、token、商品与记录 ID 均为占位符或脱敏值；
  公开平台域名（1688、TikTok 卖家后台、公共 CDN）与平台公开类目/文档 ID 保留原值以便核查。
- 脚本内的商品 ID、本地截图路径与用户名已替换为示例值；线上技能目录保留真实值，两者内容不同是刻意为之。

---

## 更新记录

**2026-11-12c（第二批/第三批实测：三闸门体系 + 批量编排 + 站点商品模块）**

- 新增三道发布前闸门，各自读不同数据、拦不同失败：`price_gate.py`（ERP 认领时自动铺价破价格带、
  库存 >999999）、`image_gate.py verify`（图片与 description）、`prepublish_guard.py`
  （全 `productCreateRequestV2` 递归扫汉字、`product_attributes`、SKU 规格名、重量尺寸、重复认领、
  描述 ≥500 字符 + ≥2 段）。真实事故 `[12052250] 不允许使用汉字…属性值名称: 悬挂` 证明前两道不够。
- 补上描述质量线：TikTok 后台编辑页「2 个问题可能需要优化」是**发布后软提示**，不是硬错误；
  平台字符计数口径 `UNKNOWN`，`config/image_gate.yaml` 按纯文本保守实现并显式标注。
- 新增 `batch_prep.py`（把逐条手工跑 4 个脚本压成一条命令，缺重量/售价区间时不猜、转人工）、
  `apply_listing_fix.py`（单件 + 400g 保守计费重定价，纠正「按 2 件套定价却只发 1 件」的履约风险）、
  `site_goods.py`（平台侧**在售**商品客户端，与采集箱两套数据；站点地址/sellerId 走环境变量）。
- `SKILL.md` 新增第二/三/四批实测章节；`references/erp-pitfalls.md` 补第九～十二章
  （10 品复盘、写操作时序、后台编辑回执、站点商品模块）；`references/tiktok-api.md` 补站点商品接口契约。
- 脱敏规则扩充：本轮新增的真实 ttl_id / TikTok 商品 ID / 本地截图路径 / 厂商前端 OSS 域名全部占位符化；
  同步脚本新增「未纳入同步清单的线上文件」自检，避免以后漏掉新增脚本。

**2026-11-12b（详情页图片闸门：B2B 中文素材整图删）**

- 定位病根：漏进 TikTok 详情页的主要是 `description` 里 8–25 张 1688 详情长图，
  不是 5 张主图；旧流水线只处理主图，且 `prep_publish.py` 只打印图数不替换（实测见 §8.5）
- 结论改为**整图删，不擦字**：整版招商素材擦不干净，判定单位是整张图；
  判定由多模态模型逐张目视回填，不用 OCR 关键字表（艺术字/竖排字/印章字会漏）
- 新增 `scripts/image_gate.py`：`scan` 两通道扫描出待判定清单 → `apply` 按 L0–L3 重建
  （description 整段重建、不留模板残渣、L1/L2 走 `uri_map`）→ `verify` 发布前硬闸
  （退出码 1 禁止发布）→ `--save` 按官方 edit body 写回并回读；纯标准库，离线可测
- 新增 `config/image_gate.yaml`：B2B 词表（文本兜底）与硬闸阈值
- 新增源头门槛 `supplier_gates.clean_consumer_images_min: 3` / `desc_rebuildable`，
  全脏货源在 1688 找货阶段就换掉，不等铺进采集箱
- `fix_sku.py` 回读断言改为**语义校验**（原「长度/图数不变」会把正常重建误报为「被清空」）；
  `prep_publish.py --show` 直接打印闸门结论
- `SKILL.md` Step 4.3 重写；Step 4.2 / 4.5 / 5.1 / 5.4 / 动作分层 / References 同步

**2026-11-12（首单真实闭环跑通）**

- 全链路接口化已用真实 case 验证：真空压缩袋经 采集 → 认领 → `set_price` 公式改价 → `fix_sku` 修规格名/库存 → `ttl-publish` 铺到 TH 店，TikTok 后台确认为「在售」
- 新增「四路验证」：ERP 日志 / 采集箱状态 / 铺货配额 / 店铺后台在售数
- 新增 `references/erp-pitfalls.md`：接口 body 结构、错误码、CDP 逆向方法、浏览器自动化边界的集中备忘
- 新增 `scripts/set_price.py`（官方批量改价，不清空数据）、`fix_sku.py`、`prep_publish.py`
- `ttl-publish` 支持真实重量/尺寸（`--weight` / `--dims`）与 `--dry-run`
- `ttl-save` 的 itemModel 结构坑定案：必须传整个 reqInfo 结构且不带顶层 reqInfo，否则清空成 `MISSING_ITEM_DATA`——旧用法已标注废弃，改用 `fix_sku.py`
- 发布的类目字段修正：传类目树（`toCategoryIdTree`），不再传空串
- 选品数据源升级：FastMoss 单品数据经 ERP 后端免 token 直连（`kdzs_fastmoss.py`）

**2026-11-11b（一件代发模式重构）**

- 选品从五维改**四维**：需求降为过滤条件，新增「内容力」；竞争度口径从「1688 同款数」改为「TikTok 站内在售款数」，同款数降级为跟卖时间窗修正项
- 履约判据改写：官方时效是 **48h 打包 + 3 个自然日到仓**（不是流传的 96 小时），判据是「发货地 → 货代仓的快递天数」（≤1 天绿 / 2 天黄 / ≥3 天红），不是「同省」
- 地域收敛按**城市**聚簇（ERP 货代页实测是城市级，不是省级），货代覆盖需人工核对 TIKTOK 标签
- 定价重算：国内段按单件快递 5 元 + 货代 2 元（非批量均摊 1 元）；跨境段由固定 10 元改为**按计费重量 ¥0.0198/g**；补入 TH「电商增长服务费 8.03%」；价格带下限抬到 $5
- 新增 `scripts/pricing_calc.py`（售价 / 采购价上限 / 配置自检）与 `scripts/kdzs_fastmoss.py`（销量·达人·视频数数据源）、`scripts/erp_token_grab.py`（登录态提取）
- 新增 T+7 / T+20 数据回流环节，替代事前预测

---

## License

MIT —— 见 [LICENSE](LICENSE)。
