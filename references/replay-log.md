# 复盘日志（2026-11 四批实测 + 三个真实 case）

> 这是 `SKILL.md` 的复盘下沉区。主文件只保留**改变当前动作**的结论，
> 逐条原文与全部细节在这里，追溯结论来源时读本文件。
> 时间顺序：Case A/B（10-29）→ 第二批（11-12 上午）→ Case C（11-11）→ 第三批（11-12）→ 第四批（11-12 晚）。
> **结论冲突时以时间最新者为准**（主文件已按此收敛）。

---

## 第二批实测（2026-11-12，10 品 → 4 品发布）

> 完整 10 条原文。主文件保留精简版。

1. **既有的四道门槛必须在 Step 0 真正执行**：本轮 10 品白跑 6 个（5 品无干净主图、1 品跨类目）。
   ⚠️ 门槛不是没有——`selection.yaml → supplier_gates.clean_consumer_images_min: 3` 和 strategy 5.2 早就写了，
   是**选品时只用了遨虾搜索结果（只有 offerId/价格/月销，不返回图片），压根没拉主图目视**。
   ✅ **2026-11-12 已代码化兜底**：`scripts/select_gate.py` 在 collect 前强制校验全部门槛，退出码 1 禁止采集。
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

---

## 第三批实测（2026-11-12，TikTok 后台编辑回执）：描述要长、要分段

> 来源：用户截图 `seller.tiktokshopglobalselling.com/product/edit/1738510677889025111`
> （TH 店、商品「在售」）。平台页面原文 = `VERIFIED`；推论与边界见 `erp-pitfalls.md` 第十一章。

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

---

## 第四批实测（2026-11-12）：ERP「站点商品」是另一套模块

> 用户指出「商品 → TikTok → 站点商品」有编辑按钮。**此前「ERP 没有下架 / 重新上架能力」的结论作废**——
> 那句话只对**采集箱（发布前草稿）**成立。完整接口契约见 `tiktok-api.md` 末章，
> 逆向踩坑见 `erp-pitfalls.md` 第十二章。

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

---

## 真实 case 复盘（三个 case，按时间先后；结论以最新为准）

### Case A｜不锈钢衣架（2026-10-29，未成功收尾）

认领 → 图片泰化 → 保存 → 页面发布报 `tikTokItemModel不能为空`。
当时判断"接口保存与发布互斥"，**该判断已于 2026-11-11 被推翻**（根因是 body 结构错误，见 Case C）。

### Case B｜硅胶折叠漏斗（2026-10-29，未成功收尾）

认领 → 图片擦中文公司名/网址 → 保存（类目 收纳瓶罐 853000）→ 发布报 `tikTokItemModel不能为空`；
删除重认领后发布报 `类目不能为空`。

**本 case 仍然成立的三条经验**（与 body 结构无关）：

1. **类目自动匹配不可靠**：重认领后类目常回空、`CATEGORY_NOT_MATCH` 不会自动消除 → **必须显式写类目三件套**
2. **重认领 = 恢复草稿态但丢设置**：图片回 1688 原图、类目回未匹配、价格回原始值，只有标题保留当地语言
3. **「图片语言化」的真实工作分两类**：多数家居品图里没有功能词，主要工作是**擦除中文营销信息（公司名/网址/广告语）**；
   功能词翻译取决于具体图 → 流水线先 OCR 分类（营销词擦 / 功能词译 / 无字跳过），再处理，不要一刀切

### Case C｜真空压缩袋（2026-11-11 ✅ 全链路成功，标准剧本）

商品 `<ttl_id-1>`，TH 店（`<TH_SHOP_NICK>`），**TikTok 后台确认在售**，฿129–199，3 SKU。

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

### 全链路接口化程度（2026-11-12 第四批后）

| 环节 | 自动化程度 |
|---|---|
| 选品取数（FastMoss 单品 + 官方后台） | ✅ 全自动（官方后台需浏览器读页面） |
| 选品取证（关键词池 / 主图抓取 / 抄录成本库存 MOQ） | ✅ 脚本化（`selection_intake.py`，2026-11-12 新增） |
| 采集 / 认领 / 删除 | ✅ 全接口 |
| 批量公式改价 | ✅ 全接口 |
| 修 SKU 名/库存/类目/图片 | ✅ 全接口（`fix_sku.py` / `prep_publish.py`） |
| 发布 | ✅ 全接口 |
| 发布结果验证 | ✅ 全接口（日志 + 采集箱 + 配额）；店铺侧需浏览器核对 |
| 两通道图片扫描 / 整图判定 / description 重建 / 发布前硬闸 | ✅ 脚本化；判定由多模态模型回填 review.json，低置信度转人工 |
| 数据回流（T+2/T+7/T+20 排期与判定） | ✅ 脚本化（`backflow.py`，2026-11-12 新增），数据仍须人工从后台抄 |
| 保留图的 OCR 分类 / 擦字 / 功能词翻译 / 首图白底 | ⚠️ AI 流水线，需人工抽检前 10 个 |
| 货代覆盖确认 / ERP 登录 / 1688 授权 / 供应商 48h 书面确认 | ❌ 人工接管（1688 详情页直连还有 IP 级风控） |

## 第五批实测（2026-11-19，MY 首品闭环：机会中心 → 在售，全程 ~20 分钟）

**品**：MY 商品机会卡片「KATUN Premium half body Apron」→ 1688 offer 751364095317 →
ttl_id 1573021951366418432 → TikTok MY 后台**在售** RM10.00 / 库存 9329（四路验证全过）。

**本批改变动作的结论（8 条）**：

1. **机会中心路线跑通，但命中结构要认清**：今日 6 卡片 → 3 品牌跟卖 reject、1 价格带 reject、
   2 无检索输入、1 candidate 出 5 候选。判读已代码化（opportunity_intake.py），
   人工 5 分钟抄卡片 + 脚本秒判，替代了逐个点开弹窗的低效目测。
2. **「KATUN=品牌」是人工误判，词典核实翻案**：katun 是印尼/马来语"棉布"（KBBI 词典，荷兰语 katoen 借词），
   且官方发品要求明写"品牌：无需提供"。教训：**全大写词 ≠ 品牌**，进白名单前查词典；
   启发式只认全大写 + known_brands 名单兜 title-case（Winstar 型），已固化。
3. **ERP 马来语自动翻译 VERIFIED 可用，但质量不可控**：标题自动译出且混入 "laris Amazon"（畅销亚马逊）
   竞品平台词；SKU"颜色"自动译 "Warna"。动作：发布前目视标题，ms.csv 转"按需积累替换词条"模式。
4. **1688 授权过期不影响 collect/claim/publish**（实测授权显示已过期仍全链路成功）。
   只影响 selection_intake 抓详情页图片。别再为授权过期中断铺货批次。
5. **被风控拦掉的详情图救回一次关键数据**：968478794506 抄录 FAIL，但本品的中文规格表恰在
   description 图 desc_06 里（23×16×1cm / 95g）——**删图前先读图**，重量/尺寸红线数据常藏在
   "包装信息"图里，读完整图判定再 L0_drop。
6. **ERP 自动价破带是常态不是例外**：认领自动价 RM8.62 < 带下限 RM8.98。
   动作链固化：ttl-list → price_gate 判带 → batch_prep 公式改价 → 刷新 → 复检 → publish。
   不要手动逐 SKU 填价。
7. **backflow sync 的路径坑**：sync 读 `erp_data/tiktok_list.csv`（顶层），
   `ttl-list --out X` 落 `X/tiktok_list.csv`（目录内）→ 新发品"新增 0"假象。
   用默认路径或 sync 前 cp。（已记 pitfalls 补记）
8. **48h 书面证据拦截按用户指示关闭**（`seller_ships_48h_proof: off`），降级 WARN `48H_PROOF_SKIPPED`
   仍可见不静默。**风险提示在案**：DTS 超时是履约红线第一权重，出延迟发货事故立即改回
   `platform_compensation_or_chat`。

**流程耗时基准（下次铺货的效率锚点）**：
抄卡片 5min → intake 判读 秒级 → 词搜/图搜 1min → 挑短名单+抄录 2min（1 条被风控）→
多模态看图判 3min → 描述重建 2min → 改价+复检 2min → 发布+四路验证 3min ≈ **20min/品**。

**遗留（本批未闭环，勿当已完成）**：
- 颜色不一致（机会卡绿 / 货源白）→ "添加同款"流量奖励资格存疑，待找绿色货源或确认无奖励铺法
- T+0 三件套未做（新人折扣/加速任务/机会页绑定提报）——`backflow.py traffic-due` 会催
- T+2 入池检查 2026-11-21、T+7 2026-11-26 已排期
- MY 免佣到期日未核（定价地基）

### 第五批续：10 品批量实测（同日 17:40-19:45）——真实瓶颈是货源图片质量

**结果**：机会中心 MY 站 2 页 30 卡 → intake 判读 reject 15（品牌9/液体5/大件1）→ 8 方向检索 → 10 品进认领
→ **实际新增在售 3 个**（830 纸巾盒、674 太空舱卷纸盒、678 马桶刷套装）+ 713 审核驳回换源 674 顶上。

**淘汰原因分布（10 进 3 出，图片质量占淘汰 100%）**：
- 粘鼠板×2：主图 5 张全带中文横幅+包装印品牌标（驰天/零虫迹）→ 干净主图 0
- 地缝刷×2：主图 4/5 张带中文营销字 → 干净主图 1 < 5
- 泡腾片：主图带中文+包装印"九行" → 干净主图 1
- 小苏打：主图全"食品级小苏打"+包装"食用"字样 → 双重风险（图片+食品类误判）
- 713 洗脸巾盒：主图干净但**右下角 shop***.1688.com 水印** → TikTok 审核驳回

**改变动作的三条结论**：
1. **发布硬闸的真实拦点在货源侧图片**，不在流程侧。机会中心清洁消耗品方向普遍是"内贸爆款图"，
   符合 5 张干净主图的货源密度 <30%。**下次批量前先跑首图水印/中文预检**（方法：aibuy 结果的 imageUrl
   下载→裁右下 30%→多模态批量判，一次判 8-20 张，实测有效——674 就是这么选出来的）。
2. **Aidge 擦字能救图但救不了发布**：NonObjectRemoveElements 全量擦中文横幅实测干净无幻觉（¥0.025/张），
   但产物 dashscope 链 TikTok 拉不到（713 三次实测：VPC 域 404、dashscope 域"参数无效"）。
   **发布用图必须来自 cbu01.alicdn.com 原链**（TikTok 会转存）→ 擦字只用于 ERP 内部展示或等 ERP 官方转存通道。
3. **换源快于修图**：同方向在 aibuy 候选池里找"首图无水印+无中文"的货源，实测 20 个候选里能挑出 8 个干净首图，
   比逐张修图便宜且零风险。674/678 都是这么来的。

**效率**：本批 2 小时 10 品认领 → 3 品在售。瓶颈工序=逐品 scan+拼 sheet+多模态判图（约 8 分钟/品）。

## 2026-11-25 流水线化改造（复盘 2026-11-24 my10 批次后落地，全部脚本实测通过）

**复盘数据（VERIFIED，来自会话 jsonl 统计）**：467 次工具调用 / 394 分钟。分布：browser 抄卡 ~120 次（25%）、MCP JSON 转录 ~40 次（15%）、5 品链路重复 ~96 次（25%）、试错 ~30 次（10%）、图像双轨 ~25 次（8%）、sleep 24 次 320 秒。根因三条：①LLM 在干 for 循环的活（机械转录/固定编排占用对话轮次）；②编排胶水在对话里不在脚本里；③脚本沉默失败逼 agent 参数穷举。

**落地**：
1. `publish_one.py`（P0-1）：单品全链路状态机，start/next/approve/status，CP1=edit dry-run 确认、CP2=仅 probe 存疑图目视。冒烟实测：collect→claim→detail→edit_dry 四连推全自动，dry-run 摘要直出；**防呆闸**：start 前查平台在售，命中即拒（冒烟时真踩出重复草稿 1574946268677373952，已 ttl-del，防呆上线后复测拦截生效）。
2. `mcp_write.py`（P0-2）：瘦表 TSV→JSON 落盘，search/sku 两格式，与 read/apply 端到端兼容实测通过；plan/scan 提示词已改。
3. `opp_center.py`（P1-1）：**接口重放四重证据排除**（Keychain 沙箱 SIGTERM／browser-use 禁 CDP／页面壳 curl 302／TikTok 签名头），且弹窗正文不进 DOM 可访问树→抄卡弹窗环节物理上不可压缩。落地为 DOM 卡片骨架解析（预览按钮 node_id 替代坐标盲试）+ 结构化 record 写信号 CSV。
4. `image_review.py`（P1-2）：probe-first 并发判定，实测洗衣机罩 5 主图全自动 L3_keep 零目视（旧流程这 5 张要先目视拼 sheet 再 probe）。
5. ttl_edit 自解释（P2）：keep-prefix 0 命中打印实际 seller_sku 清单；汉字维度名未覆盖点名；描述 CJK 命中词打印。
6. `wait_for.py`（P2）：指数退避轮询，实测已发布品 0s 返回 success。
7. SKILL.md 工作流头部新增「⚡2026-11-25 流水线化改造」节 + 选品策略闸门（P3）：卡片=方向池非货源池（两批实测单卡命中≈1/6），执行顺序改 FastMoss 词池/高通过率品类优先→机会中心按价格带×品类先验筛着抄→图搜扩供兜底。

**预期**：同规模任务（含选品 10 品目标）从 6.5h 压到 1.5–2h；不可压缩项=弹窗抄卡与 CP 人工确认。

---

## VN20 批次规则（2026-11-13，存量站历史批次；工具仍现行）

1. **货代仓以已绑定行为准**：目录卡片写多仓不等于账号已绑该仓；`route_gate.py` 校验发货城市、已绑定仓、≤1 天到仓证据，缺证据禁止 collect。
2. **公网图必须回读核验**：除首图外保留展示图；`verify_public_images.py` 逐张下载并比对 SHA-256/像素，并发上传错配的短链不得进 payload。
3. **本地负载先审计**：`listing_payload_audit.py` 核站点、图片数、当地语言文本、单件 SKU、库存/价格、源序号和重量；本地通过后，真实 ERP 详情上仍须重跑既有闸门。
4. **旧数据不得复用**：排除多件套、液体胶水/免钉胶、无关杆件；按本批真实 SKU 重算成本与售价，旧 SKU 成本/客单价作废；页面运费不等于货代仓运费。
5. **写操作先查重再分批**：认领每批 ≤3，按 offerId 精确查重；回执为空必须回读；`success` 不等于平台在售。旧 `ttl-save` 继续禁用。

## MY batch12 硬规则（2026-11-25，10 品全部 ACTIVATE 在售）

1. **三个 ID 永远别混**——这是本轮最大的时间黑洞：
   - `offer_id` = 1688 货源 ID，= 铺货日志的 `itemId`、站点商品的 `outerId`
   - `itemCenterNumIid`（`1574...`）= ERP 内部商品中心 ID，**喂 `queryPlatformItemDetail` 必报 does not exist**
   - `numIid`（`1738...`）= 真正的 TikTok 商品 ID，来源是铺货日志的 `platformItemId`
   判「平台侧是否在售」只有一个可靠口径：**`productStatus == ACTIVATE`**。
   `INITIAL` = 已创建、待平台审核（几分钟到几小时后自己转 ACTIVATE），**不能算上架**。
   一条命令：`python3 scripts/site_goods.py status --offer <1688ID>`（0 在售 / 3 已建未激活 / 4 失败）。
2. **`[12052522] 变体缺少主图`**：变体图在 `skus[].sales_attributes[0].sku_img.uri`。
   `image_gate apply` 删掉带中文的 SKU 图后，若那几张正是某变体的唯一图 → 变体全变 0 图 → 平台拒发。
   **删图前先算好每个变体的替代图**，用 `ttl_edit --sku-img-seq`（按 skus 顺序的 URL 列表）一次补齐。
3. **`mains.txt` 必须先目视预筛**：直接把 1688 前 6 张主图塞进 mains，其中常夹整幅中文卖点图，
   会在 apply 阶段被删 → 主图不足/变体缺图 → 返工重跑 scan/review。正确顺序是
   **先出 contact sheet 目视剔除带中文的图 → 再写 mains.txt**，这样 review_img 常能全自动 `L3_keep` 零目视。
4. **重复 `seller_sku` 会让 dict 互相覆盖**：同货号多规格时 `sku_map.json` 只剩 1 条。
   用 `--sku-map-seq` / `--sku-code-seq`（`prep_one.py` 总会产出，重复组自动生成 `-2/-3` 后缀）。
5. **主图只有 4 张时**：`prepublish_guard` 要求 ≥5。凑不出干净图时在 state 写 `allow_few_mains=true` + 原因，
   `stage_guards` 会自动带 `--allow-few-mains` 降级留痕（**禁止靠脏图凑数**）；T+7 看曝光再补图换源。
6. **`wait_for` 终态判定**：`erp_client._post` 返回的是 `data` 本体（已解包），不可再 `d.get("data")`；
   判定用 `status` **精确值**（`processing/success/fail`），不对整行 JSON 子串匹配。
7. **重发必须 `--force`**：`filterAlreadyPublish` 会静默跳过已提交过的 ttl_id（计数不动、logs 无新行）。
   `stage_publish` 先查是否已 ACTIVATE（已售则跳过，避免双链接自我竞争），否则带 `--force`。
8. **verify4 的 `logs_success` 只看最新一条**：CSV 里同时留着上一轮的 `fail` 行，全文正则会把重发成功的品误判为失败。

## MY batch13 硬规则（2026-11-25 下午，7 品 ACTIVATE）

1. **Aidge 欠费时主图证据闸的诚实降级**：Aidge 按量付费账户欠费（`Service suspended due to overdue payment`）时
   `image_fix.py probe` 全线失败，`image_review.py` 把所有图判 pending，`image_gate apply` 因 L3_keep 主图缺
   `evidence.rec_text` 拒绝提交。→ 用 `scripts/evidence_local.py --review <review.json>` 调本地 macOS Vision OCR
   （`img_grade.py` 的 swift OCR，零成本）真实识别并回填 evidence，`evidence.evidence_source=local_vision_ocr` 留痕。
   **不是伪造证据**：本地 OCR 识别到中文照样被 image_gate 的 CJK 检查拦；只是换真实识别来源。
   顺序：`auto_verdict.py` 必须在 `evidence_local.py` 之前跑（前者定 verdict，后者只对 L3_keep 主图补证据）。
2. **`prep_one.py` 的 `sku_code_seq` 漏写 bug（2026-11-25 已修复）**：旧版重复 seller_sku 时代码 print「已产
   sku_code_seq.json」但实际没有 `json.dump`；现 dup 分支已真写文件，重复组自动产 `-2/-3` 后缀。
3. **SKU 翻译值 ≤40 字符否则 [12052251] 重复**：翻译值 53–60 字符被平台截断到 50 字符后撞车。
   → 翻译词表一步到位写短值，别等发布端拒了再返工。
4. **`publish_one approve --which images` 会绕过 `review_file` 赋值**：手工回填 verdict 后直接 approve images
   会把 stage 设成 apply，但 state 里 `review_file` 还没写，apply 报 `KeyError: 'review_file'`。
   → 手工 approve 前先确认 state 有 `review_file`，没有就补 `erp_data/image_gate/<ttl_id>/review.json`。
5. **低价品（¥2–3）默认 markup 会破带上界**：高于**上限**的品用 `--markup` 压回带内；注意与「低于下限的品
   提高 markup 进带」方向相反，别混。
6. **高价 SKU 无法定点剔除（当前能力缺口）**：`ttl_edit` 只有 `--keep-prefix`（按货号前缀），没有按 SKU ID 排除；
   严重破带的高价规格只能整品放弃。

## 2026-11-25 流程优化、耗时账本与 11 项提效（全部已落地并回归 220 通过/1 跳过）

### 流程优化工具表

实测账：178 搜索候选 → 57 送检 → 19 过闸 → 10 在售；三批 precheck 耗时 1360+1389+1752s=**75 分钟（占全程 62%）**，
38 个淘汰品里 23 个「干净图不足」+8 个「体积超标」= 81% 的信号在写 ERP 前就有。

| 工具 | 用途 | 实测 |
|---|---|---|
| `scripts/ocr_vision_batch.swift` + `img_grade.ocr_batch()` | **批量 OCR**：一个 swift 进程处理整批图，摊薄编译/框架冷启动 | 8 张 **10.75s vs 串行 54s（5x）**；判级结果与串行逐张比对**完全一致** |
| `scripts/auto_verdict.py --batch-size N` | C 段判级默认走批量；`--no-batch` 回退旧串行 | `--jobs` 线程并发**无效**（60s，比串行还慢），别再试 |
| `scripts/prefilter.py` | **图片判级左移 + IP 品牌前置**：collect 前用候选 image_url 本地 OCR，只硬杀品牌/IP 与 B2B 工厂行话 | 15 候选硬杀 2，软标 7 保留；**首图含中文只软标不硬杀** |
| `scripts/assemble_guard.py` | 前置校验三件套：SKU 值>40 字符、中文残留、重复 seller_sku、markup 反解 | 反解给出可行区间；recommend 取**贴下限**而非区间中点（避免价格竞争力流失） |
| `scripts/auto_assemble.py` | 一条命令替代 prep/描述/markup/args/set 六步手工 | 语义字段仍须人工给 `--title/--desc-fields` |
| `health_check.py --only aidge` | 开工即探 Aidge 欠费：空参调用 ImageMatting 返回 `MissingImageUrl` = 正常（**不计费**）；`suspended/overdue` = FAIL | 欠费时提示走 `evidence_local.py` 降级 |

关键教训：
- **并发 OCR 是错的优化方向**——瓶颈是 swift 冷启动（5–9s/张）不是 IO 等待，8 线程 60s 反而慢于串行 54s。批量单进程才对。
- **左移预筛必须高召回**：首图中文图可擦字救回，硬杀会错杀已验证能 ACTIVATE 的品。只杀不可逆红线（IP/B2B 工厂素材）。
- **markup 反解取贴下限**：区间中点会把低价品顶到价格带中位，丧失竞争力；取 `lo × 1.02`。
- **品牌词表同源**：`prefilter.BRAND_RE` 合并 `config/opportunity_intake.yaml known_brands`，别另建词表。

### 耗时账本（可观测性）

| 组件 | 作用 |
|---|---|
| `scripts/timing.py` | 账本核心：`span()` 上下文/装饰器、`rec()`、CLI `init/mark/report`。每条形如 `{run,module,parent,t0,t1,dur,meta}`，**append-only JSONL，并发安全** |
| `scripts/timing_report.py` | 汇总三表：顶层模块占比、子模块 self、**按商品透视**。另算 `script_busy`（区间并集）与 `agent_gap`（墙钟 − 脚本实占）= agent 思考/转录/等人工 |
| `TT_RUN` 环境变量 | 批次名，**子进程自动继承**（`TT_PARENT` 传递父子关系） |

口径纪律：墙钟 ≠ 脚本实占 + agent 空转以外的任何东西；`script_busy` 用区间**并集**，嵌套不重复计、并发不虚增；
批次产品数不同时看单次均值；`pipeline.X` 已含其所有 `sub.*`，汇总只用顶层。

首批实测（p5t，5 品全部 ACTIVATE）：端到端 34.3 min；脚本实占 21.5 min (63%) / agent 空转 12.7 min (37%)；
筛选段 42s/品；发布链路 2.2 min/品，其中 77% 是等平台。→ **脚本侧已不是瓶颈**，下一刀切编排（先全发再统一轮询 verify、筛选段品间并行 3 路）。

### 11 项提效/防再犯

**提效 6 项**
| # | 组件 | 用法 / 效果 |
|---|---|---|
| A1 | `verify_batch.py` + `batch_run.py` | **先全发再统一验**：`batch_run.py --offers` 推到 `verify4` 就停，最后 `verify_batch.py` 并发查一次。`--workers 3`（别超 4） |
| A2 | `prefilter.py` 输出 `prefilter_verdict` 列 + `precheck.py` 排序 | 首图判级信号**只排序不硬杀**：keep 优先、flag 排队尾，配合 `--target` 早停 |
| A3 | `precheck.py --target N --wave M` | 序贯筛选：凑够 N 个过闸品就停，M 默认 N×2 |
| A4 | `precheck.py --c-jobs 3` | C 段品间并行，3 路 1.51x、4 路 1.50x（Vision 占满 4 核，非线性）。不传则行为与改前一致 |
| A5 | `sku_dict.py --offer-id X` | 自动生成 ≤40 字符 SKU 短值词表，喂 `auto_assemble --extra-dict`；分量词未覆盖才标「待确认」，不机翻 |
| A6 | `title_pool.py --category ... --features ... --wrap ...` | 从已 ACTIVATE 标题沉淀「品类+特征+场景」模板，词根全部来自真实标题，不编造新词 |

**防再犯 5 项**
| # | 组件 | 修了什么 |
|---|---|---|
| B1 | `ip_risk.py` + precheck B 段接入 | 标题/SKU 命中形象词 → 自动拼 `ip_risk_sheet.png` 强制人工看 SKU 图。词表宽进 |
| B2 | `image_gate.py` apply 主图补位 | SKU 图被判 L0 删空后用干净主图轮换补位，report 增 `sku_images_filled_by_main` |
| B3 | `image_review.py` classify 自动降级 | Aidge 欠费时走 `evidence_source=local_vision_ocr` 本地证据判定，`--keep-english` 下不留 pending |
| B4 | `publish_one.py` stage_apply 前置断言 | apply 前硬断 `pending == 0` |
| B5 | `assemble_guard.py` problems 一次性收集 | check_detail 收集全部问题一次输出 |

**实测推翻、别再尝试的 3 条**：标题 B2B 词硬杀整册脏图（无区分度）；图片宽高比/文件大小预测干净率（两组区间重叠）；品内并发 OCR（更慢）。

**两个「静默失效」（检查器本身骗人，优先级高于任何提速）**：
- `health_check --only aidge` 只探 ImageMatting 空参 → 现同时探 ImageRecognition（非法 URL，不下载不计费）。
- `assemble_guard` 原按 detail 原文判 SKU 值长度 → 现按**映射后**的值判长度与维度内重复（跨维度重复合法，如统一尺码）。
