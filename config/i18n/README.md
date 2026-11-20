# config/i18n — 商品文案 / 规格名译名表

`scripts/i18n.py` 的唯一数据源。加新品 = 加一行，不改代码。

## 文件

| 文件 | 对应站点 | 内容 |
|---|---|---|
| `vi.csv` | VN（JingShanShop-VN） | 20 品越南语标题 + 描述 + 69 条规格译名 |
| `th.csv` | TH（JingShanShop-TH） | 属性名译名（颜色/款式/型号/规格/尺寸） |
| `ms.csv` | MY（JingShanShop-MY，2026-11-19 主攻站） | **按需积累替换词条**。ERP 马来语自动翻译已 VERIFIED 可用（首品标题/规格名自动马来化），但质量不可控（混入 "laris Amazon" 竞品平台词）；发布前目视标题，需要修正的词按 `kind,key,src,tgt` 加行 |
| `_market_terms_vi.csv` | VN | **市场用词参考表**：EchoTik 竞品越南语规格原词（2026-11-18） |
| `_market_terms_th.csv` | TH | **市场用词参考表**：EchoTik 竞品泰语规格原词（2026-11-18） |

站点码 ≠ 语言码，`VN → vi`、`TH → th` 的映射在 `i18n.LANGS` 里，只此一处。
（市场表文件名也用语言码 vi/th，不是 vn/th——生成时别踩这个坑。）

## 市场用词参考表（与主译名表物理隔离）

2026-11-18 新增，来自 `echotik/batchProductDetail` 的 `salePropsInfo`，经
`scripts/linkfox_sourcing.py spec-terms` 提取。**两组表用途不同，禁止混用**。
⛔ 2026-11-20 起 `spec-terms` 已停用（TikTok 数据统一走 ERP 透传），这两张表**冻结留档、不再增量**；
已有词条仍可作人工选词参考，但不得再据此新增行。

| | 主译名表 `vi.csv`/`th.csv` | 市场参考表 `_market_terms_*.csv` |
|---|---|---|
| 方向 | 中文 src → 当地语 tgt | 当地语原词（竞品实际在用） |
| 用途 | 可直接用于 listing 文案 | **仅供人工选词参考**，不是译文 |
| 数据性质 | 已批准文案 | 第三方快照 `THIRD_PARTY_ONLY` |
| 进表门槛 | 人工确认 | 采集即入，中文提示需证据 |

字段：`site,kind,local_term,prop_name,cn_hint,evidence,product_id,source`。
`evidence` 纪律：
- `VERIFIED_BY_TABLE` = 该当地语词恰好命中主表某条已批准译文（`cn_hint` 可信）；
- `INFERRED_UNVERIFIED` = 无主表证据，**`cn_hint` 必须留空**，禁止机翻填中文。

查询（只查不猜）：
```bash
python3 scripts/i18n.py --site TH --market-list
python3 scripts/i18n.py --site TH --market-list --verified-only   # 只列有中文证据的
python3 scripts/i18n.py --site TH --market-lookup สี              # 查一个当地语词
```
把竞品原词升级为主表译文 = 人工核对语义 + 补进 `vi.csv`/`th.csv` 的 `src(中文)→tgt` 行，
**不能直接把 `local_term` 塞进主表 src 列**。

## 列

| 列 | 说明 |
|---|---|
| `kind` | `title` / `desc` / `spec` / `attr` |
| `key` | `title`/`desc` 填 offer_id；`spec`/`attr` 留空 |
| `src` | 原文（留痕核对用；`desc` 留空） |
| `tgt` | 译文 |

查询键：`title`/`desc` 按 `(kind, offer_id)`；`spec`/`attr` 按 `(kind, 归一化原文)`。
原文归一化只做「去首尾空白 + 压缩内部空白」，不做任何字符替换。

## 纪律

- **查不到就返回 `None`**，由调用方转人工或阻断。禁止用占位、序号模板、机翻补位。
  （2026-11-13 审查发现 `apply_listing_fix.py` 曾按 SKU 序号轮流贴「粉/蓝/灰/黑/白/绿」，
  与真实颜色无关，已删除。）
- 译文改动必须同时改 `src` 之外的 `tgt`；改 `src` 等于换键，旧行会失配。
- 平台因违禁词拒绝某个规格名（如 `[12052153] 内容词(自)`）时，正确做法是把该规格的
  合规译名写进表里，而不是统一改成一套通用颜色名。

## 命令

```bash
python3 scripts/i18n.py --site VN --list spec
python3 scripts/i18n.py --site VN --get spec --key "深灰大条纹>30*40*60"
python3 scripts/i18n.py --site VN --missing-check --csv erp_data/selection_candidates.csv
```
