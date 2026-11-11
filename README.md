# tiktok-light-listing

> 一个面向 AI Agent 的 **TikTok Shop 跨境店群铺货技能包**：把「选品 → 1688 采集 → 认领 → 图片本地化 → 定价 → 发布 → 回查」六段人工链路，收敛成一条有状态、可断点续跑、写操作需授权的自动化流水线。

![format](https://img.shields.io/badge/format-Agent%20Skill-6E4AFF)
![python](https://img.shields.io/badge/python-3.8%2B-3776AB)
![deps](https://img.shields.io/badge/dependencies-stdlib%20only-success)
![license](https://img.shields.io/badge/license-MIT-blue)

---

## 1. 它解决什么问题

跨境小卖家「铺货」不是上架一个动作，是六个动作的串联，每个都要人工点几十下：

| 环节 | 人工做法 | 单商品耗时 |
|---|---|---|
| 选品 | 后台翻榜单 → 截图 → 去 1688 手动搜同款 → 心算利润 | 3–5 min |
| 采集 | 1688 链接一条条粘进 ERP 采集插件 | 10 s |
| 认领 | 公共采集箱勾选 → 批量认领 → 下拉选平台 → 弹窗选店铺 | 30 s |
| 图片本地化 | 主图里全是中文「源头工厂/包邮/日产 5 万件」→ 逐张 PS 擦字、翻译、做白底 | **5–8 min** ← 真正的大头 |
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
│    └─ config/*.yaml                     权重/阈值/费率（外置） │
└───────────────────────────┬──────────────────────────────────┘
                            │ CLI 调用（唯一契约）
┌───────────────────────────▼──────────────────────────────────┐
│  scripts/erp_client.py（执行层，纯标准库，无三方依赖）          │
│    collect · items · ttl-list · ttl-detail · ttl-save         │
│    ttl-claim · ttl-del · ttl-publish · logs · shops · token   │
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
| **AI 自动** | 错了没损失，或损失可回滚 | 取候选、搜同款、五维打分、采集、认领、对账、算价、图片 OCR 分类、擦广告字、翻译、白底、提交发布、查记录 |
| **硬红线自动阻断** | 错了有罚金/下架风险，但判定规则确定 | 品牌侵权、IP 联名、液体化工、带电、食品保健医疗、宗教、易碎、活体、多规格价差、限售需认证、跨类目 |
| **人工接管** | 错了钱直接没了，且规则无法穷举 | ERP 登录/短信验证码、候选清单最终确认、图片处理结果抽检（前 10 个人工过一眼） |

为什么红线要「自动阻断」而不是「提示确认」？因为**规则能穷举的风险，问人等于把责任推给人**——而人会疲劳、会连点确认。规则穷举不了的（比如「这张图的泰文有没有幻觉字」），才交给人。

### 原则二：所有阈值外置到 YAML，代码只做执行

费率会变、汇率会变、什么样的同款数算红海会变。改一个数字不该改代码。`config/selection.yaml` 里改一行即时生效：

```yaml
weights:            # 五维权重，合计 1.0
  demand: 0.25      # 需求：关键词热度/榜单销量
  competition: 0.25 # 竞争：同款数/「无竞争对手」/「稀缺」标签
  margin: 0.20      # 利润：售价÷采购价倍数
  logistics: 0.15   # 物流：重量/尺寸/易碎
  breakzero: 0.15   # 破零：可搜性/需求稳定性/复购属性
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
Step 4  编辑        → ttl-detail → 图片本地化 → 定价 → ttl-save
Step 5  发布        → erp_client.py ttl-publish --ids <ttl_id,...>
Step 6  回查        → erp_client.py logs --months 1
```

**Step 0 选品**是整个链路的漏斗口，规则见 [`references/selection-strategy.md`](references/selection-strategy.md)：两条路径（榜单驱动 / 关键词驱动，新人优先后者抢空白供给）→ 硬红线剔除 → 五维评分 → 同款去重 → 按分排序。

**Step 4 图片本地化**是本 Skill 真正的差异化部分，也是最难的：主图里塞满中文营销信息（源头工厂、日产 XX、包退、一件代发、1688），直发等于放弃本地化。处理流水线：

```
下载原图 → OCR 识别所有文字块（位置 + 内容）→ LLM 三分类
   ├─ 营销/批发/承诺词  → 全擦（inpaint）
   ├─ 产品功能词        → 擦原文，原位写目标语言
   └─ 拿不准的          → 擦（宁可错删）
→ 第 1 张额外做抠图换白底（平台硬要求）→ 上传 → 回填 main_images[].uri
```

**AI 生成小语种文字的高发缺陷**（真实 case 实测，必须逐张校验）：

- **幻觉字**：模型会在金属反光、玻璃、空白处注入伪文字甚至反向英文。原图没有的元素一律不能信。修正 prompt 必须写明「仅保留指定文字区域，其他区域不得出现任何文字」，必要时二次、三次修正。
- **OCR 不可信**：内置 OCR 对泰文小字多次读出互相矛盾的结果。判定正确性的唯一办法是**裁剪单字符放大到原始 10 倍逐字目视**。
- **验收五条**：① 指定译文完整拼写正确 ② 无中文残留 ③ 无杂散伪字 ④ 无网址残留 ⑤ 首图白底。

---

## 6. 快速开始

**前置**：Python 3.8+（无需 pip install，全标准库）、一个已开通 TikTok Shop 跨境店的 ERP 账号。

```bash
git clone https://github.com/<your-account>/tiktok-light-listing.git
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
| `ttl-save --id [--price --weight --images --category]` | **W** | 保存编辑；`--images` 数量必须等于主图数，否则中止 |
| `ttl-claim --ids --region` | **W** | 认领到指定站点店铺 |
| `ttl-del --ids` | **W** | 删除采集箱记录（`batchOps` + `opsType:3`） |
| `ttl-publish --ids` | **W** | 提交发布队列，4 个/批 |
| `logs --months` | R | 发布记录，`success/fail/processing/examine` |
| `shops` | R | 店铺列表与授权状态 |

所有读命令默认落盘 `erp_data/*.csv` + `*.json`（已在 `.gitignore` 中排除，不入库）。

---

## 8. 工程笔记：四个反直觉的坑

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

---

## 9. 配置

| 文件 | 内容 |
|---|---|
| `config/selection.yaml` | 破零目标（150 款 / 20 天）、站点价格与成本区间、五维权重、一票否决红线、竞争度阈值、铺货节奏 |
| `config/pricing.yaml` | 平台佣金、交易手续费、退款预留、目标利润率、汇率、物流藏价 → 综合加价系数 |

**定价公式**（第一性原理，不拍脑袋）：

```
售价(本币) = (采购价 + 国内运费 + 物流藏价) × 汇率 × 加价系数
加价系数   = 1 / (1 - 平台佣金 - 交易手续费 - 退款预留 - 目标利润率)
```

TH：`1/(1-0.08-0.0321-0.07-0.25) ≈ 1.76`　　VN：`1/(1-0.14-0.06-0.07-0.25) ≈ 2.08`

算完先输出建议售价表给人确认，确认后才批量回填 `skus[].price`。

---

## 10. 目录结构

```
tiktok-light-listing/
├── SKILL.md                          # Agent 技能定义：动作分层 + 六步执行规程
├── README.md
├── .env.example                      # 环境变量模板
├── config/
│   ├── selection.yaml                # 选品规则（权重/红线/阈值/节奏）
│   └── pricing.yaml                  # 定价参数（费率/汇率/加价系数）
├── references/
│   ├── selection-strategy.md         # 选品方法论：两条路径、十类红线、五维评分细则
│   └── tiktok-api.md                 # 接口契约（已实测 12 个 + 待补）
├── scripts/
│   ├── erp_client.py                 # CLI，纯标准库
│   └── erp_token.example.txt         # token 文件模板（真实 token 已被 .gitignore 排除）
└── samples/
    ├── selection_candidates.sample.csv
    └── tiktok_list.sample.csv
```

运行时产物 `erp_data/`（采集箱快照、发布日志、图片处理中间件）不入库。

---

## 11. 局限与已知风险

诚实列出来，比藏起来强。

- **依赖未公开内部接口**：无契约、无版本号、字段随时可能变。任何报错的第一处置是回退浏览器自动化，不是重试。
- **登录态会过期**：`qnquerystring` 是浏览器会话 JWT，过期后所有命令返回 `703`。设计中「登录」永远是人工步骤，不做自动续期。
- **不适合无值守生产**：图片本地化的验收标准是目视，模型生成的泰文/越南文有幻觉字风险。这是设计上的取舍，不是缺陷。
- **类目自动匹配不可靠**：两次重认领后类目均为空，`CATEGORY_NOT_MATCH` 不会自愈，必须显式指定类目。
- **发布成功率受店铺侧限制**：如「该地区没有设置开启的默认销售仓库」属于 TikTok 后台配置问题，脚本无法代解。
- **`ttl-translate` 是骨架**：body 待确认，当前只打印 TODO（实际上认领时 ERP 已自动翻译标题，此命令优先级低）。

---

## 12. 合规声明

本项目是**方法论与工程实践的教学样本**，不是通用作弊工具：

- 仅用于**操作者自己拥有或已获书面授权**的 ERP 账号与店铺。
- 不含、不要求、也不接受任何绕过平台风控的手段（验证码识别、指纹伪装、代理池等）。
- 所有接口行为来自**操作者自身浏览器内的正常业务操作**的观测，未做越权访问。
- 面向未公开接口的自动化存在违反服务条款的风险，**使用前请自行确认目标平台条款**。作者不对使用者行为负责。
- 仓库中的域名、店铺 ID、sellerId、token、商品与记录 ID 均为占位符或脱敏值。

---

## License

MIT —— 见 [LICENSE](LICENSE)。
