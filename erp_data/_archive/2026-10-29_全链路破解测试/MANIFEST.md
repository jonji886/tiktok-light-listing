# 归档说明：2026-10-27 ~ 10-29 全链路破解期快照

**归档时间**：2026-11-12 01:20
**原位置**：`erp_data/` 顶层（68 项 / 104 个文件 / 1.7 MB）
**归档人**：Agent（用户确认「归档」后执行，纯移动，未删除任何文件）

## 为什么归档

这批文件是 10-27~10-29 破解 ERP 内部接口时逐步骤抓的快照，与 **11-11 全链路成功那次的现行数据（在 `scripts/erp_data/`）** 混在同一层，容易被误读成"当前状态"。
归档后 `erp_data/` 顶层只剩当前有效的参考资产。

## 内容分组

| 前缀 / 目录 | 是什么 | 对应接口 |
|---|---|---|
| `tiktok_list*` / `ttk_*` / `case_*` / `after_*` / `final_*` / `list_for_delete` / `verify_after_save.csv`(目录) | TikTok 采集箱列表快照，按操作前后各抓一次（认领后 / 删除后 / 保存后 / 重认领后…） | `platformCollect/queryListPage` |
| `logs_*` | 发布记录快照（processing → success/fail 的中间态） | `distribute/queryLogs` |
| `shared*` / `recon*` / `pre_case_items` | 公共采集箱列表快照（认领前货源池） | `center/queryErpItemListPage` |
| `collect_result*` / `case_collect` / `collect3` | 1688 采集结果返回 | `center/batchPluginCollectItem` |
| `ttl_detail_*` / `ttl_detail_after_save` / `ttl_new` / `ref_published` | 单品 `getDetail` 草稿快照——**这批就是"itemModel 只在草稿态返回"结论的原始证据** | `platformCollect/getDetail` |
| `ttk_shops2` / 顶层 `tiktok_shops*` | 店铺列表旧快照（TH/VN 的 sellerId 取自此处） | `getPlatformShops` |
| `ttk_category_tree.json` | 10-28 版类目树（已被顶层的 `case_category_tree.json` 覆盖，仅留档对比） | `distribute/categoryTree` |
| `claim_field_test.json` / `ttl_test_save.json` / `ttl_test_full.json` | 认领/保存 body 字段试探记录（24 组扁平字段全失败、嵌套结构成功的过程） | — |

## 从这里得出的结论（已在 SKILL.md / references 定稿，不需再翻这批文件）

- `edit` 的 `itemModel` 必须是整个 reqInfo、不能传顶层 `reqInfo`、需带 `sellerId`（`ttl_test_save.json` vs 成功 body 对照）
- `getDetail` 只在带异常标签的草稿态返回 itemModel（多份 `ttl_detail_*` 前后对比）
- 删除 → 重认领可恢复草稿态，但类目回空（`case_reclaim*` 系列）
- 家居可用类目：收纳瓶罐 `853000`、挂钩 `853128` 等（`case_category_tree.json`）

## 使用提醒

- **不要当当前状态读**：里面的库存、价格、在售数全部是 10 月底的
- 需要复现某次对比时再翻，平时让 Agent 现拉：`python3 scripts/erp_client.py ttl-list` / `logs --months 1`
- 确认无用后可整目录删除（用户未授权删除，此处仅归档）
