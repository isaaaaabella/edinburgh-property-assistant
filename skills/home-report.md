# /home-report — Scottish Home Report 分析（零配置入口）

## 用途

给一个 Home Report PDF 路径，输出 HTML 分析报告（含评估师专业意见的三层卡片）+ 入库（Notion 或 Local JSON）。

零配置体验：不需要 Notion / Gmail 也能跑（自动 fallback 到 LocalJSONStorage，HTML 存 `~/.property_data/reports/<slug>/`）。

## 触发

```
/home-report <PDF_PATH>
/home-report <PDF_PATH> --out <HTML_PATH>
/home-report <PDF_PATH> --listing <RIGHTMOVE_URL>
/home-report <PDF_PATH> --viewing "YYYY-MM-DD HH:MM"
```

可组合：`--listing` + `--viewing` 同时给即可一次性填齐。

- `--listing` 触发 Rightmove 抓取（og:image 作为 Notion 封面 + asking price → 「挂牌价(£)」+ listing URL 写入）
- `--viewing` 接受 wall-clock Europe/London 时间（自动算 BST/GMT 偏移），写入 PropertyRecord.viewing_date + Notion「Viewing时间」full datetime
- pipeline 默认会自动跑 `fetch_area_data.py` → 学区命中通过 Edinburgh Council ArcGIS catchment API 写入 PropertyRecord.school_zone（落到 Notion「学区」multi_select）— 加 `--skip-area` 跳过

## 第一步：解析 PDF

跑确定性 Python 解析器（不要用 Read 工具自己抽 —— `parse_home_report.py` 已经覆盖 Quest/Allied/DM Hall/Graham Sibbald 4 个 surveyor 模板）：

```bash
PDF="<PDF_PATH>"
SLUG="$(basename "$PDF" .pdf)"
PARSED="$(dirname "$PDF")/${SLUG}_parsed.json"
OPINION="$(dirname "$PDF")/${SLUG}_opinion.json"

# 幂等：parsed 缓存存在就 skip parse（pipeline 也会维护同一份缓存）
if [ ! -f "$PARSED" ]; then
  python ~/.claude/property_assistant/parse_home_report.py "$PDF" > "$PARSED"
fi
```

读 `$PARSED` 理解房子：
- `regex_extracted`：22 个字段（address/postcode/hr_valuation/bedrooms/floor/era/epc_rating 等），每个带 `value` + `page` + `source`
- `condition_table`：18-24 行 Repair Category（cat=1/2/3 + notes 原文 + 页码）
- `derived`：`category2_count` / `category3_count` / `roof_issue` / **`cat_notes_contradictions`**（关键）
- `warnings`：解析器警告（说明哪些字段提取失败/不确定）

## 第二步：生成 SurveyorOpinion JSON

### 幂等检查（先做）

- `"$OPINION"` 已存在 → 跳过本步，直接进 Step 3（让 validate 决定是否复用）
- 用户说「重新分析」/「重生成 opinion」/ 显式 `--regen-opinion` → `rm -f "$OPINION"` 强制重生成

否则按下面流程生成。

### Inject 角色 prompt + 风格 example

```bash
cat ~/.claude/property_assistant/prompts/surveyor_role.md  # 角色 + 约束 + cat_notes 处理 + schema 单一来源
```

读 `$PARSED.regex_extracted` 的 `building_type` + `era` 选 1 个 example `cat` 进上下文（学 voice，不要照抄事实）：

| 房型 | example 文件 |
|---|---|
| Pre-1919 Victorian / Tenement | `prompts/examples/marchmont_tenement.json` |
| Interwar (1919-1939) Semi / Terrace | `prompts/examples/interwar_semi.json` |
| Post-war / Modern flat / 现代公寓 | `prompts/examples/modern_flat.json` |

按 `surveyor_role.md` 里的约束写 `"$OPINION"`（用 Write 工具，不要 echo）。

## 第三步：校验 SurveyorOpinion

```bash
python -m property_assistant.analysis.surveyor_opinion validate \
    --parsed "$PARSED" --opinion "$OPINION"
```

- **退出 0** → 进 Step 4
- **退出 1** → 读 stderr errors 列表，修 `"$OPINION"` 再跑 validate（**最多重试 2 次**）
- **三次都失败** → 停下报告："SurveyorOpinion 验证失败 3 次，errors: [...]。请人工编辑 `$OPINION` 或检查 parsed.json 是否异常"

常见 errors → 修法：
- `"未覆盖矛盾项: X_pN"` → 在 `score_corrections` 加 `contradiction_id="X_pN"` 的 Finding
- `"viewing_priorities 必须 1-5 条"` → 条数不对，删/加
- `"fact 缺 evidence_page"` → 找到对应 fact 加 `evidence_page`
- `"judgment 类 Finding 不足 3 条"` → 把 fact 改成 judgment，或补判断

## 第四步：跑 pipeline（评分 + 渲染 + 入库）

```bash
python -m property_assistant.pipelines.home_report run \
    "$PDF" \
    --opinion "$OPINION" \
    --parsed "$PARSED" \
    ${OUT:+--out "$OUT"} \
    ${LISTING:+--listing "$LISTING"} \
    ${VIEWING:+--viewing "$VIEWING"}
```

pipeline 内部自动：学区 catchment 查询 → Rightmove 抓取（如 `--listing`）→ 解析 viewing datetime（如 `--viewing`）→ 7 维度评分 → 二次 validate opinion → 渲染分层卡片 HTML → upsert 到 storage → attach HTML 链接到 Notion + cover image post-patch。

## 第五步：终端摘要

```
✓ HTML: /Users/.../<slug>_analysis.html
  📍 <address> (<postcode>)
  ⭐ <recommendation> · 总分 <total>/100
  💷 HR £<hr_valuation> · 挂牌 £<asking_price>
  🏠 <bedrooms>BR <floor_area>m² · <floor> · <building_type>
  ⚡ <N> 个 cat_notes_contradictions [若有]
  🗄️ <storage_backend> id=<storage_id>
```

如果检测到 `cat_notes_contradictions`，加一行：
```
  📝 注意：[N] 个 Cat 2/3 行的 notes 实际写"无问题"，看房时核对；评估师已在 ② 评分校正 给出建议
```

## 错误处理

- **解析器失败**：`parse_home_report.py` 返回非 0 → 报告 stderr 给用户，建议人工检查 PDF（可能是非标准模板）
- **validate 三次失败**：见 Step 3
- **storage 失败**：HTML 已生成在 PDF 同目录，不要因为 Notion API 错就失败整个 run；warning + 继续

## 缓存策略

PDF 同目录的两份缓存**都不要删**：
- `<slug>_parsed.json` — pipeline / Step 1 共用，下次重跑省 5-15s 解析
- `<slug>_opinion.json` — Step 2 生成，下次重跑省一次 LLM 生成（几千 token）

强制重生成 opinion：删掉 `<slug>_opinion.json`，或用户显式说「重新分析」/「重生成 opinion」。
