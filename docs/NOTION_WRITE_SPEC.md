# Notion 写入规范（/home-report 入库时到底写了什么）

> 目的：回答"每次 Notion 格式怎么不一样、到底有没有规范"。
> 结论：**结构是确定性的、幂等的**。看起来"不一样"通常是 (a) 后端被静默落到 local（见
> [[project_storage_backend_default]] 的修复）或 (b) 可选字段取决于输入。

每次 `/home-report <pdf>`（`STORAGE_BACKEND=notion`）对一条房源页面**固定产出下面四组东西**，
顺序无关、重复跑不累积（机器块按 marker 原地更新或删除重建）。

## 1. DB 属性（页面的数据库字段）

来源 `storage/notion_storage.py` 的 `NOTION_FIELD_MAP` → `_record_to_properties`。规则：

- `title` / `url` / `checkbox` 类型：**总是写**（即使空）。
- `number` / `date` / `select`：值为 None 时**跳过**（更新时不清空已有）。
- `multi_select`：空时**跳过**（不覆盖已有，例如「学区」）。

哪些字段被填**取决于输入**，这是"看起来不一样"的主因：

| 字段 | 何时被填 |
|---|---|
| 地址 / 评分 / 状态 / HR估价 等核心字段 | 每次都填（来自 PDF 解析 + 评分） |
| 挂牌价(£) / listing URL / 封面图 | 只有传 `--listing <rightmove_url>` 才填 |
| Viewing时间 | 只有传 `--viewing "YYYY-MM-DD HH:MM"` 才填 |
| 学区 | 只有 Edinburgh Council catchment 查询命中才填 |
| HTML报告 (url) | 每次都填（指向本地 `file://` HTML） |

> 注意：**Council Tax band 不是 DB 列**，只出现在评估师意见正文里。

## 2. 🎯 TL;DR callout（机器块，marker `[auto:tldr]`）

页面顶部一行执行摘要，由 `opinion.derive_tldr()` = `overall_positioning[0] + offer_direction[0]` 生成。
幂等：原地更新。

## 3. 🤖 HTML报告 callout（机器块，marker `[auto:html_report]`）

指向本地 HTML 的链接 + 一句说明。幂等：原地更新。

## 4. 🎓 评估师完整意见 callout（机器块，marker `[auto:opinion]`）

**2026-06-02 新增**（之前完整意见只在 HTML，Notion 只有 TL;DR）。一个 🤖 callout，children 是
完整 7 段意见，每段一个**加粗标题段落** + 每条 Finding 一个 **bulleted_list_item**：

```
【判断/事实/假设】<headline 加粗>  (p.X)
<rationale 灰色>
"<quote 灰色>"
```

段顺序与 HTML 模板 `_base.html.j2` 的 `opinion_detail` 完全一致：
① 整体定位 · ② 评分校正 · ③ 真正的关注点 · ④ 估值判断 · ⑤ 出价方向 · ⑥ 看房当日最关键问题 · 💭 额外想法。

**空段自动跳过**（例如没有 cat_notes_contradictions 时 ② 评分校正 不出现）——所以不同房源的段数会不同，
这是按内容裁剪，不是格式漂移。

幂等：**删除重建**。重跑 `/home-report` 先删掉旧的 opinion callout（连同子块），再写一份新的，
页面**永远只有一份**，不累积。已验证：连跑 3 次仍只有 1 个 opinion callout。

代码：`NotionStorage.set_opinion` / `_opinion_child_blocks` / `_finding_bullet`，
pipeline 在 `home_report.run` 里 `set_tldr` 之后调用。

## 沟通记录 bullets（不属于上面 4 组，但会累积）

`/property emails --apply` 走 `append_communication`，每封匹配邮件**追加**一条 bullet 到页面。
这部分**是累积的**（按时间增长），所以老房源沟通记录长、新房源短——属正常。

## 用户可自由编辑的区域

任何**非机器块**（普通段落 / 标题 / 列表，icon 不是 🤖）都是用户自己的，pipeline 不动它们；
读取「你的感受/伴侣的感受」时会跳过 🤖 机器块。

## 排查"格式不一样"

1. `python -m property_assistant.orchestrator.router health` → 确认 `backend=notion`（不是 local）。
2. 字段缺失通常是没传对应 flag（`--listing` / `--viewing`）或 catchment 没命中。
3. opinion 段数不同 = 按内容裁剪空段，正常。
