# LinkFox 睿观 IP 检测对 TH/VN 的覆盖实测（2026-11-18）

**结论先行**：睿观 5 个检测工具里，**只有「外观专利」覆盖 TH**（且 VN 不支持），
**「文本商标 / 图形商标 / 实用新型」均不支持 TH 和 VN**，
**「版权检测」无区域参数、召回全球 Redbubble 图库但相似度噪声大**。
所以本 skill 的 brand_ip 红线**不能靠睿观自动化**，主判据仍是"标题大写英文词 + 主图目视"。

所有结论均为 **VERIFIED（当轮 MCP 直调）**，但检测库本身是第三方数据（`THIRD_PARTY_ONLY`），
判定结果（尤其 radarResult/similarity）只作线索，不构成法律意见。

## 实测矩阵（2026-11-18，MCP `proxy_call` 直调）

| 工具 toolName | 测试 region | 结果 | 判定 |
|---|---|---|---|
| `ruiguan/textTrademarkDetection` | VN | `400 第三方异常：不支持的region` | ❌ 不支持 VN |
| `ruiguan/textTrademarkDetection` | TH | `400 第三方异常：不支持的region` | ❌ 不支持 TH |
| `ruiguan/trademarkGraphicDetection` | TH | `400 Bad Request`（网关层） | ❌ 不支持 TH |
| `ruiguan/utilityPatentDetection` | — | schema 明写 `当前支持 US` | ❌ 不支持 TH/VN |
| `ruiguan/detectionPatentDesign` | **TH** | `errcode:200`，10 条泰国外观专利（含申请人泰文名、`registrationOfficeCode:TH`、相似度、`radarResult.same`） | ✅ **支持 TH** |
| `ruiguan/detectionPatentDesign` | VN | `400 Bad Request` | ❌ 不支持 VN |
| `ruiguan/detectionPatentDesign` | 不传（默认） | `errcode:200`，但返回 **US 专利** | ⚠️ 默认 US，铺 TH/VN 必须显式传 `regions:TH` |
| `ruiguan/copyrightDetection` | 无 region 参数 | `errcode:200`，10 条 Redbubble 版权图，`similarity≈0.70` | ⚠️ 全球图库、非站点定向 |

## 各工具支持区域（来自 schema，与实测一致）

| 工具 | 支持区域 | 本 skill 可用性 |
|---|---|---|
| 文本商标 | AU,BX,CA,DE,EM,ES,FR,GB,IT,JP,MX,TR,US,WO,CN | 无 TH/VN ❌ |
| 图形商标 | US,WO,ES,GB,DE,IT,CA,MX,EM,AU,FR,JP,TR,BX,CN | 无 TH/VN ❌ |
| 实用新型/发明 | `US`（仅） | ❌ |
| 外观专利 | SE,EU,CH,IE,BR,MX,US,WO,GB,IL,JP,IN,DK,DE,AU,IT,NZ,AT,CA,BX,FI,FR,CN,KR,**TH** | **TH 可用 ✅**（东南亚仅 TH） |
| 版权 | 无区域参数 | 全球图库，需人工判 ⚠️ |

## 外观专利 TH 的可用判据（实测字段）

以压缩袋主图实测，`regions:"TH"` 返回字段可直接支撑红线判断：

| 字段 | 用途 | 判据 |
|---|---|---|
| `similarity` | 图像相似度（0-1） | 高相似 ≠ 侵权，需结合下项 |
| `radarResult.same` | 雷达判定是否同款 | `true` = 高度疑似，转人工复核 |
| `radarResult.exp` | 英文理由 | 读它理解为何判同/不同 |
| `troCase` / `troHolder` | 是否 TRO 权利人或维权史 | `true` = 直接否决 |
| `patentValidity` | `ACT`=有效 | 失效专利风险低 |
| `registrationOfficeCode` | 受理局 | 须为 `TH`，否则不是泰国专利 |

本轮 10 条泰国专利 `radarResult.same` **全为 false**、`troCase` 全 false——压缩袋主图与
泰国现存外观专利无同款命中，但相似度普遍 0.55-0.69，**不能只看 similarity 下结论**。

## 对本 skill 的落地建议

1. **brand_ip 红线主判据不变**：标题大写英文词 + 主图目视（Step 0.x 现有逻辑），
   睿观不可作为东南亚自动化红线。
2. **TH 站点可选加一道外观专利预检**：`ruiguan/detectionPatentDesign` + `regions:"TH"`，
   只看 `radarResult.same==true` 或 `troCase==true` 才拦截，其余只记线索。
   **禁止**用 similarity 阈值自动否决（实测通用包装图就有 0.69 虚高）。
3. **VN 站点无 IP 自动检测**：如实标注 UNKNOWN，转人工。
4. **版权检测慎用**：全球 Redbubble 图库对家居白底图普遍返回 0.70 相似，全靠人工看 `radarResult`，
   性价比低，不作为常规闸门。
5. 所有调用经 MCP `proxy_call`，**不得用 curl/urllib 直连网关**（LinkFox 硬约束）。

## 调用模板（Agent 侧，非脚本）

```json
// TH 外观专利预检（唯一可用于本 skill 的睿观检测）
{"toolName":"ruiguan/detectionPatentDesign",
 "arguments":{"imageUrl":"<1688主图URL>","regions":"TH","topNumber":10,"queryMode":"hybrid"}}
```
