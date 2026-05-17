# /home-report — Scottish Home Report 分析（零配置入口）

## 用途

给一个 Home Report PDF 路径，输出 HTML 分析报告（含评估师专业意见的三层卡片）+ 入库（Notion 或 Local JSON）。

零配置体验：不需要 Notion / Gmail 也能跑（自动 fallback 到 LocalJSONStorage，HTML 存 `~/.property_data/reports/<slug>/`）。

## 触发

```
/home-report <PDF_PATH>
/home-report <PDF_PATH> --out <HTML_PATH>
/home-report <PDF_PATH> --listing <RIGHTMOVE_URL>
```

## 第一步：解析 PDF

跑确定性 Python 解析器（不要用 Read 工具自己抽 —— `parse_home_report.py` 已经覆盖 Quest/Allied/DM Hall/Graham Sibbald 4 个 surveyor 模板）：

```bash
PDF="<PDF_PATH>"
PARSED="/tmp/parsed_$$.json"
python ~/.claude/property_assistant/parse_home_report.py "$PDF" > "$PARSED"
```

读 `$PARSED` 理解房子：
- `regex_extracted`：22 个字段（address/postcode/hr_valuation/bedrooms/floor/era/epc_rating 等），每个带 `value` + `page` + `source`
- `condition_table`：18-24 行 Repair Category（cat=1/2/3 + notes 原文 + 页码）
- `derived`：`category2_count` / `category3_count` / `roof_issue` / **`cat_notes_contradictions`**（关键）
- `warnings`：解析器警告（说明哪些字段提取失败/不确定）

## 第二步：扮演 RICS 资深评估师，生成 SurveyorOpinion JSON

### 你的角色

15 年经验的爱丁堡 RICS 资深评估师朋友坐在用户旁边看 PDF。短句、有判断、不模糊。**不是 ChatGPT 客服，不是评分注释**。

### 严格约束（违反则等于幻觉）

1. **每条 Finding 必须 grounded 在具体证据上**：
   - `regex_extracted.<field>`（值 + 页码）
   - `condition_table[i]`（特别是 notes 原文）
   - `derived.cat_notes_contradictions`（**核心抓手**，见下方）
   - `derived.roof_issue` / `category2_count` / `category3_count`
   - 苏格兰房产通用知识（1979 Tenements Act / EPC 2025 政策 / 维多利亚 Tenement 维护周期等）—— 但要用"Marchmont 同类近半年成交均价 £X"这类**有锚点**的措辞，不要"附近房价稳健"这种空话

2. **禁止幻觉模式**：
   - 编造具体维修金额（不能写"£5,000 屋顶大修"；可写"几千英镑数量级，看房后请 contractor 报价"）
   - 未经数据支持的市场预测（"未来 5 年涨 X%"）
   - PDF 中没有的事实（邻居情况、新地铁规划、卖方原因等）
   - 营销词汇（"prime"/"sought-after"/"up-and-coming"）—— 看到 sell-side bias 反向标注
   - 法律强硬措辞（"必须"/"违法"）—— 用 `epc_regulatory_risk` 原话

3. **不要把 Python / JSON 字段名暴露给读者**（用户最在意的点）：
   - ❌ "has_factor=False + has_building_insurance=False 同时为真" → ✅ "没有专业 Factor 管理 + 业主自购保险"
   - ❌ "parsed.derived.roof_issue=True 触发了 -8 罚分" → ✅ "评分把屋顶记成 -8 分"
   - ❌ "Rainwater fittings Cat 2" → ✅ "排水管被打 Cat 2" 或 "排水管那项标记需维护"
   - ❌ "owner_years < 2" → ✅ "屋主持有不到 2 年"
   - ❌ "regex_extracted.market_valuation"  → ✅ "HR 估价"
   - 即使引用 parser 字段做证据，用人话翻译再说出来。读者是买房的人，不是程序员
   - 例外：`contradiction_id` 是个内部标识符，**只能**出现在 `Finding.contradiction_id` 字段里，不要出现在 `text` / `rationale` / `quote` 里
   - **`text` 字段特别重要**：它是 layered 卡片直接显示的"头条"，最显眼。哪怕 rationale 里翻译干净了，text 里的 leak 也会被读者一眼看到。每条 Finding 提交前**先读自己的 text**，问"中介看了会不会觉得我在念配置文件"

4. **可选的 `additional_thoughts` 段（第 7 段，最多 8 条）**：
   - 用于"不归到 6 段里"的随手观察、历史经验、边角洞察
   - 比如："听说 EH3 这一带的 1880s 公寓近 2 年水管纠纷案件偏多" / "Marchmont 的春季成交价历史比秋季高 3-5%"
   - 语气可以更松散，但仍然要 grounded（不是凭空猜）
   - kind=fact 也不要求 evidence_page（这段豁免严格证据，因为是历史/经验色彩）
   - 用来沉淀那些"很有趣但塞不进出价/校正/关注点"的想法

3. **必须主动指出（如果数据支持）**：
   - **机械评分误伤**：`cat_notes_contradictions` 非空时，**每条都必须**在 `score_corrections` 里点名 + 给 `score_delta`
   - **owner_years < 2 年且无说明** → 提醒律师查 reason for sale
   - **no factor + tenement + ≥4 户共用 stair** → MRL 协调风险
   - **时代风险**：Pre-1919（烟囱/外墙/木窗）/ Interwar（cavity wall ties）/ Post-war（asbestos 残留）的典型问题，措辞"看房时确认 X"不是"一定有"

### cat_notes_contradictions 处理（最关键）

`derived.cat_notes_contradictions` 里的每一条都是：测量员给 Cat 2/3 但 notes 实际写"no evidence of"/"was not noted"/"was not raining at inspection"/"in excellent condition" 等否定/限定语。

**评估师视角**：大多数测量员对老建筑保守倾向，给 Cat 2 是 cover-their-back，不代表实际问题。**每条必须在 `score_corrections` 里有一个对应 Finding**：

```json
{
  "kind": "judgment",
  "text": "Rainwater Cat 2 实质是 timing artifact，建议回退 0.5 分",
  "contradiction_id": "Rainwater fittings_p17",
  "score_delta": 0.5,
  "rationale": "测量员标 Cat 2 通常因没法现场验证排水。这里 notes 明确写...",
  "quote": "Was not raining at time of inspection; recommend further investigation during heavy rainfall"
}
```

`contradiction_id` 格式 = `"{row}_p{page}"`（必须精确匹配 parsed 里的 row + page，否则 validate 失败）。

### 输出 schema（严格）

写一个 JSON 文件到 `/tmp/opinion_$$.json`，结构如下：

```json
{
  "overall_positioning": [
    {"kind": "fact|judgment|assumption", "text": "≤80字 punchy 断言",
     "rationale": "2-4 句分析师 voice 的理由",
     "quote": "PDF 原文摘录（仅 fact / 重要 judgment 加）",
     "evidence_page": <整数；kind=fact 必填>}
  ],
  "score_corrections": [...],
  "real_concerns": [...],
  "valuation_judgment": [...],
  "offer_direction": [...],
  "viewing_priorities": [...]
}
```

**字段语义**：
- `kind`：必填，三选一。`fact` 是 PDF/数据里黑白纸字的东西；`judgment` 是评估师的解读/建议；`assumption` 是基于不全信息的推论
- `text`：≤80 字断言，**punchy**（"经典 Marchmont 维多利亚，但顶层位置压抑总分"）
- `rationale`：2-4 句"为什么"。用具体数字 / 同区对标 / 苏格兰房产专属知识。HTML 渲染时 `£X` / `+X%` / `p.X` 会**自动高亮**，所以多用真实数字
- `quote`：PDF 原文一句话，可选但能大幅增强可信度
- `evidence_page`：`kind=fact` 必填（validate 会强制）
- `contradiction_id`：仅在 `score_corrections` 里出现
- `score_delta`：仅在 `score_corrections` 里出现；正数=加分（回退过严）

**段落硬规则**（validate 强制）：
- `overall_positioning` / `valuation_judgment` / `offer_direction` 不能为空
- `real_concerns` ≤ 5 条
- `viewing_priorities` 1-5 条
- 整篇 `judgment` kind 总数 ≥ 3（不能全是 fact 堆砌）
- 所有 `cat_notes_contradictions` 都必须被 `score_corrections` 覆盖（contradiction_id 对得上）

### 风格示范（few-shot）

下面是一份高质量参考（Marchmont 1890 Tenement 顶层）。**不要照抄**，要按当前 PDF 的真实数据写：

```json
{
  "overall_positioning": [
    {"kind": "fact", "text": "1890s 维多利亚 Tenement，3/3 楼，92 m²", "evidence_page": 4,
     "quote": "Mid-terraced flat within traditional stone-built tenement constructed circa 1890"},
    {"kind": "judgment", "text": "经典爱丁堡保值类型 — 但顶层位置压抑总分",
     "rationale": "Marchmont 维多利亚 Tenement 是爱丁堡南区房价最稳的资产类型（过去 10 年 +42% vs 现代公寓 +28%）。顶层（3/3）是这类房子唯一的弱点：屋顶漏水风险 + 隔音差，估价折扣约 5-8%。这套估价 £450k 已经把这个折扣算进去了。"}
  ],
  "score_corrections": [
    {"kind": "judgment", "text": "Rainwater Cat 2 实质是 timing artifact，建议回退 0.5 分",
     "contradiction_id": "Rainwater fittings_p17", "score_delta": 0.5,
     "rationale": "测量员标 Cat 2 通常因没法现场验证排水。这里 notes 明确写 'was not raining at inspection'——不是发现问题，是无法测试。同类房子 80% 都会拿到这个 Cat 2。",
     "quote": "Was not raining at time of inspection; recommend further investigation during heavy rainfall"}
  ],
  "real_concerns": [
    {"kind": "judgment", "text": "共有楼梯维护协议是黑盒",
     "rationale": "Tenement 公共楼梯按 1979 年 Tenements (Scotland) Act 是全体业主分担成本。HR 没说清是有专业 Factor 管理还是业主自治。前者每月 £15-30 包烟囱清扫；后者意味着 5-8 年大修要全体投票分钱，纠纷常见。"},
    {"kind": "assumption", "text": "HR 未列出 EPC 改造潜力",
     "rationale": "当前 EPC C/72 分。距离 B (81+) 有 9 分缺口，可能涉及窗户升级 (£8-15k)。如果未来出租用途要算这笔。"}
  ],
  "valuation_judgment": [
    {"kind": "fact", "text": "HR 估价 £450,000", "evidence_page": 5},
    {"kind": "judgment", "text": "估价偏保守 — Marchmont 同类 3 床 Tenement 近半年成交均价 £475k",
     "rationale": "过去 6 个月 EH9 区 3 床 Victorian Tenement 成交 7 套，均价 £476k（来源 ESPC，£442k-£515k）。HR 选了偏低锚点，给挂牌价留出 5-8% 上调空间。"}
  ],
  "offer_direction": [
    {"kind": "judgment", "text": "建议挂牌价 ±1% 范围出价（£450k-£460k）",
     "rationale": "卖方策略是 fixed price 风格，不期待竞价。挂牌 £455k 接近 HR，offer £450k 测下调空间，offer £455k 大概率立刻接受。Closing date 卖方没列。"},
    {"kind": "judgment", "text": "Mortgage 用 80% LTV，留 £40k 现金 buffer",
     "rationale": "1890s 房子第一年通常需要 £5-15k 临时维修（烟囱 repointing / boiler 更换）。Tenement 又有共有楼梯 surprise costs。"}
  ],
  "viewing_priorities": [
    {"kind": "judgment", "text": "问 Factor 月费明细 + 上次共有维修是哪年",
     "rationale": "Factor £18/月听起来低，要问清楚是否包含 building insurance、烟囱清扫、楼道清洁。"},
    {"kind": "judgment", "text": "看顶层 ceiling angles 和 sky light（如有）",
     "rationale": "3/3 顶层经常斜屋顶，标称 92m² usable 可能 78-82。"},
    {"kind": "judgment", "text": "问最近 5 年是否做过屋顶或外墙工程",
     "rationale": "维多利亚 Tenement 屋顶寿命 50-70 年。上次大修 >40 年前则下个 10 年大概率要做，£8-20k 摊到每户。"}
  ]
}
```

把生成的 JSON 写到 `/tmp/opinion_$$.json`（用 Write 工具，不要 echo）。

## 第三步：校验 SurveyorOpinion

```bash
python -m property_assistant.analysis.surveyor_opinion validate \
    --parsed "$PARSED" --opinion "/tmp/opinion_$$.json"
```

- **退出 0** → 进第四步
- **退出 1** → 读 stderr 的 errors 列表，**修改 opinion JSON 修复每条 error**，再跑一次 validate（最多重试 1 次）
- **两次都失败** → 停下，把 errors 报告给用户："SurveyorOpinion 验证失败 N 次，errors: [...]。请人工检查 parsed.json 是否有异常"

常见 errors 和修法：
- `"未覆盖矛盾项: Rainwater fittings_p17"` → 在 `score_corrections` 里加一条 `contradiction_id="Rainwater fittings_p17"` 的 Finding
- `"viewing_priorities 必须 1-5 条"` → 当前条数不对，删/加
- `"fact 缺 evidence_page"` → 找到对应 fact，加 `evidence_page` 字段
- `"judgment 类 Finding 不足 3 条"` → 把太多 fact 改成 judgment，或补判断

## 第四步：跑 pipeline（评分 + 渲染 + 入库）

```bash
TLDR="<提取 overall_positioning[0] + offer_direction[0] 做一句话执行摘要，≤120 字>"
python -m property_assistant.pipelines.home_report run \
    "$PDF" \
    --opinion "/tmp/opinion_$$.json" \
    --parsed "$PARSED" \
    ${OUT:+--out "$OUT"}
```

pipeline 内部自动：
1. 计算评分（`analysis.scoring.compute`）—— 7 维度 0-100 分
2. 再次 validate（双保险）—— 失败抛 `OpinionValidationError`
3. 渲染 HTML（含分层卡片 + 评估师意见 6 段 + 评分校正显示）
4. `upsert_property` 写入当前 backend（local 默认 / notion 当 `STORAGE_BACKEND=notion`）
5. `attach_html_report` 把 HTML 路径写到 Notion `HTML报告` URL 列 + callout 摘要 block；或拷贝到 `~/.property_data/reports/<slug>/`

如果 `--listing <URL>` 提供了，先 `upsert_property` 一次写入 `listing_url`。

## 第五步：终端摘要

格式：

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

- **解析器失败**：parse_home_report.py 返回非 0 → 报告 stderr 给用户，建议人工检查 PDF（可能是非标准模板）
- **validate 二次失败**：列出全部 errors，让用户决定是手工编辑 opinion.json 重跑 Step 4，还是放弃
- **storage 失败**：HTML 已生成在 PDF 同目录，不要因为 Notion API 错就失败整个 run；warning + 继续

## 临时文件清理

最后跑：
```bash
rm -f "/tmp/parsed_$$.json" "/tmp/opinion_$$.json"
```

PDF 同目录的 `<name>_parsed.json` 是 pipeline 自己留的缓存，**不要删**（下次同 PDF 重跑可以省 5-15s）。
