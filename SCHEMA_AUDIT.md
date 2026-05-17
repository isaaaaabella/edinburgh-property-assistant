# Notion DB Schema Audit

> 这份文档是 demo 用户在 2026-05-16 跑 schema_audit 后落档的字段映射决策。
> 你的 Notion DB 列名 / 类型可能跟这里列的不一样 —— 拿这份作为参考，按需改 `storage/notion_storage.py` 里的 `NOTION_FIELD_MAP`。

**DB**: `<YOUR_NOTION_DATABASE_ID>` "房源追踪"
**Source**: `mcp__notion__API-retrieve-a-database` 输出

## 字段映射状态

| PropertyRecord 字段 | 计划 Notion 名 | 实际 Notion 名 | 类型 | 状态 |
|---|---|---|---|---|
| `address` | 地址 | 地址 | title | ✓ |
| `status` | 状态 | 状态 | select | ✓（带 emoji 前缀） |
| `hr_valuation` | HR估价 | **HR估价(£)** | number(pound) | ⚠ 名字差 `(£)` 后缀 |
| `asking_price` | 挂牌价 | **挂牌价(£)** | number(pound) | ⚠ 名字差 `(£)` 后缀 |
| `bedrooms` | 卧室数 | 卧室数 | number | ✓ |
| `floor_area` | 面积 | **面积(m²)** | number | ⚠ 名字差 `(m²)` 后缀 |
| `floor` | 楼层 | 楼层 | **select**（不是 number/str） | ⚠ 类型注意：值如 "Ground ⚠️"/"2F ✅" |
| `is_main_door` | 主门公寓 | 主门公寓 | checkbox | ✓ |
| `building_type` | 建筑类型 | 建筑类型 | select | ✓ |
| `era` | 建造年代 | 建造年代 | number | ✓ |
| `epc_rating` | EPC评级 | EPC评级 | select(A-G) | ✓ |
| `epc_score` | EPC分数 | EPC分数 | number | ✓ |
| `cat2_count` | Category 2数量 | Category 2数量 | number | ✓ |
| `cat3_count` | Category 3数量 | Category 3数量 | number | ✓ |
| `roof_issue` | 屋顶问题 | 屋顶问题 | checkbox | ✓ |
| `gas_heating` | Gas供暖 | Gas供暖 | checkbox | ✓ |
| `building_insurance` | 公共建筑保险 | 公共建筑保险 | checkbox | ✓ |
| `factor_status` | Factor情况 | Factor情况 | select | ✓ |
| `factor_monthly` | Factor月费 | **Factor月费(£)** | number(pound) | ⚠ 名字差 `(£)` 后缀 |
| `ownership_years` | 业主持有年限 | 业主持有年限 | number | ✓ |
| `school_zone` | 学区 | 学区 | **multi_select**（不是 single） | ⚠ 类型注意 |
| `self_score` | 你的评分 | 你的评分 | number | ✓ |
| `closing_date` | Closing Date | Closing Date | date | ✓ |
| `simd_decile` | SIMD分位 | **SIMD综合分位** | number | ⚠ 名字差 `综合` |
| `flood_risk` | 洪水风险 | 洪水风险 | select | ✓ |
| `commute_user` | 通勤_user | **通勤-Duoduo (min)** | number | ⚠ 不同命名 |
| `commute_partner` | 通勤_partner | **通勤-Jingjun (min)** | number | ⚠ 不同命名 |
| `viewing_date` | Viewing时间 | Viewing时间 | date | ✓ |
| `html_report_url` | HTML报告 | — | — | **✗ 需要在 Notion 手动加 URL 列** |

## 计划外但 DB 已有的字段（决定保留进 PropertyRecord）

| 字段 | 类型 | 用途 | 加进 PropertyRecord？ |
|---|---|---|---|
| 备注 | rich_text | 自由备注 | ✓ `notes: str` |
| Place | place | Notion 内置位置类型 | ✗（用 address 代替） |
| 伴侣评分 | number | 伴侣评分 | ✓ `partner_score: float \| None` |
| Rightmove链接 | url | 房源 URL | ✓ `listing_url: str \| None` |
| 区域 | select | 邻里区域 | ✓ `area: str \| None` |
| 值得二看 | checkbox | 是否值得二看 | ✓ `worth_second_visit: bool` |

## 状态值（select options 全集，用于 PropertyRecord.status 校验）

`status`: 🔍 待看 / 👀 已看 / ⭐ 感兴趣 / 💰 已出价 / ✅ 已购入 / ❌ 已放弃 / 待看房

`floor`: Ground ⚠️ / 1F / 2F ✅ / 3F / 顶层 ⚠️ / 1F ✅

`building_type`: 维多利亚Tenement ✅ / 战间期 / 现代公寓 ⚠️ / 其他 / Tenement flat

`area`: Newington / Marchmont / Bruntsfield / Morningside / Shandon / Leith / Fountainbridge / Stockbridge / 其他

`flood_risk`: 无 ✅（目前只有 1 个 option）

`factor_status`: 专业Factor含保险 ✅ / 仅清洁 ⚠️ / 无 ❌

`epc_rating`: A-G

`school_zone` (multi): James Gillespie's ✅ / Boroughmuir ✅ / 其他 / 待确认 / James Gillespie's / Tynecastle / James Gillespie's High School / Broughton High School (待核实)

## 对 plan 的调整

1. **更新 `NOTION_FIELD_MAP`**：6 个字段名要按上表 ⚠ 调整
2. **PropertyRecord 新增 4 个字段**：`notes`, `partner_score`, `listing_url`, `area`, `worth_second_visit`
3. **`floor` 字段类型**：在 PropertyRecord 里用 `str | None`，写入 Notion 时按 select option 匹配；解析时从 PDF 抽出后做"normalize"（"Ground floor" → "Ground ⚠️"、"2nd floor" → "2F ✅" 等）
4. **`school_zone` 类型**：`list[str]` 而非 `str`，对应 multi_select
5. **手动操作（用户）**：在 Notion DB 加一列 `HTML报告`（URL 类型）。schema_audit CLI 启动时会检测并打印提示

## 旁支发现

- `parent.page_id` = `33c42146-2578-8009-9078-e735439c6046` 是承载 DB 的 page；同 ID 也出现在 `.env` 的 `NOTION_EMAIL_DB_ID` —— 这个名字会误导，实际是个 page 而不是 email DB。考虑 `.env.example` 里把 `NOTION_EMAIL_DB_ID` 标 deprecated 或重命名
- DB schema 没暴露 `data_source_id`，新 API（2025-09-03）的 query 用不上；现有 `parse_home_report.py`/`fetch_emails.py` 用 `Notion-Version: 2022-06-28` 直连 REST，NotionStorage 沿用这个版本即可
