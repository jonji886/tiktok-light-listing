# erp_data 目录约定

## 顶层只放「当前有效」的东西

| 项 | 用途 | 会被覆盖吗 |
|---|---|---|
| `case_category_tree.json` | TikTok 类目树快照（640KB）。查可用类目 ID / `AVAILABLE` 标记，如 家居收纳·收纳包、收纳瓶罐 `853000`、挂钩 `853128` | 否，需手动刷新 |
| `tiktok_shops.json` / `.csv` | 两店 sellerId：TH `7494877320827733079`、VN `8686823118786298967` | 否，店铺变更才改 |
| `_archive/` | 历史快照，带 `MANIFEST.md`。**别当当前状态读** | 否 |

## 跑批产物怎么落

- 脚本默认输出目录是**相对路径** `erp_data`：
  - 在 skill 根目录跑 → 落 `erp_data/`
  - 在 `scripts/` 下跑 → 落 `scripts/erp_data/` ← **11-11 成功发布那次的数据在这里**
- 会互相覆盖的固定名：`selection_candidates.csv`、`tiktok_list.*`、`tiktok_logs.*`、`shared_goods.*`
- **需要留证的跑批**（发布、改价、批量认领）先建 `_run/<YYYY-MM-DD_HHMM>_<用途>/`，用 `erp_client.py` 的 `--out` 指过去

## 归档规则

跑完一轮、进入下一轮前，把上一轮快照移进 `_archive/<日期>_<用途>/`，并在其中写 `MANIFEST.md` 说明：哪天的、什么操作、能证明哪条结论。
超出 30 天且无引用价值的归档目录可整体删除（需用户明确同意）。

## 当前归档

- `_archive/2026-10-29_全链路破解测试/` — 10-27~10-29 破解期 68 项快照（1.7 MB），见其 `MANIFEST.md`
