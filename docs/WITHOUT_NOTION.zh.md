# 没有 Notion 怎么办

[English](WITHOUT_NOTION.md) · **中文**

不需要 Notion。默认 `LocalJSONStorage` 把房源、沟通记录、HTML 报告都存在你笔记本本地 `~/.property_data/` 下。这份文档说清楚没有 Notion 能做什么、不能做什么。

## 没 Notion 能做什么

除了跨设备同步 + 伴侣在 Notion 协作的 UI，其他全能做。

### ✅ `/home-report path.pdf` 单 PDF 分析
完整 HTML 输出（三层卡片 + 评估师意见 + 评分详情 + Cat 表 + map）。PropertyRecord JSON 和 HTML 报告副本都存到 `~/.property_data/`。

### ✅ `/property prep --addr X` 看房前简报
从本地 storage 查这房，生成 `ViewingStrategy`，渲染 brief HTML。

### ✅ `/property compare --addr A --addr B` 横向对比
从本地 storage 拉多条记录，渲染对比表。

### ✅ `/property review` 复盘
读本地已看房源，做 gap 分析（评分 vs 主观感受、伴侣分歧），推 shortlist。

### ✅ `/property emails`（如果 Gmail 配了）
拉邮件、分类、匹配本地记录、默认 dry-run。Gmail 跟 Notion 独立 —— 任一可单独使用。

### ⚠️ 你失去的
1. **跨设备同步**：数据只在一台 laptop。手机想看房产列表？做不到。
2. **伴侣协作**：没有共享 Notion 页面。变通：HTML 报告文件用 Dropbox / Drive / 邮件发给伴侣。
3. **自由文本笔记**：Notion 编辑 `你的感受` / `伴侣的感受` 富文本，比直接编辑 JSON 字段顺手。

## 看自己的数据

全是 plain JSON：

```bash
~/.property_data/
├── index.json                              # 每套房一行摘要
├── properties/<slug>.json                  # 完整 PropertyRecord
├── communications/<slug>.jsonl             # 沟通日志（一行一条）
└── reports/<slug>/<timestamp>_<kind>.html  # 所有生成的 HTML
```

列出所有房源：
```bash
cat ~/.property_data/index.json | python -m json.tool
```

打开某房最新报告：
```bash
ls -t ~/.property_data/reports/<slug>/*.html | head -1 | xargs open  # macOS
```

## 没 Notion 时编辑主观感受

Notion 用户在页面 UI 里直接改 `你的感受` / `伴侣的感受`。本地用户改 JSON：

```bash
# 方式 1: 命令行
python -c "
from property_assistant.storage.local_json_storage import LocalJSONStorage
s = LocalJSONStorage()
rec = s.find_by_address('Marchmont')
s.set_subjective_feedback(rec.storage_id,
    self_feeling='明亮，朝南起居室很喜欢',
    partner_feeling='厨房太小')
"

# 方式 2: 直接打开 JSON
$EDITOR ~/.property_data/properties/<slug>.json
# 编辑 "self_feeling" 和 "partner_feeling" 字段
```

编辑完后重新生成 brief / review HTML 就能看到新反馈反映出来。

## 把报告分享给伴侣（或任何人）

HTML 报告**自包含** —— 没外部 CSS、没 JS 依赖、Google Maps 用 iframe 嵌入。直接发 `.html` 文件：

- 丢到 Dropbox / Google Drive / 邮件附件
- 对方在任何浏览器打开（手机或桌面）
- Map iframe 需要联网，其他全部离线可用

## 以后迁到 Notion

如果决定切换：

1. 按 [`INTEGRATIONS.zh.md`](INTEGRATIONS.zh.md#notion) 配 Notion
2. 改 `.env`：`STORAGE_BACKEND=notion`
3. 对每套已有房源，重跑 `/home-report <pdf>` —— pipeline 会 upsert 进 Notion
4. 本地 JSON 留在 `~/.property_data/` 作为备份

`legacy/add_property.py` 给批量迁移留了起点（如果你有很多房子）。

## 未来计划（还没做）

可能的未来 backend：
- **Airtable** —— 跟 Notion 类似的协作 UX，API 更简单
- **SQLite** —— 想要本地关系型 DB 不依赖外部服务的用户
- **Markdown vault** —— Obsidian / LogSeq 用户（每套房一个 .md 文件）

这些都不在当前 roadmap 上 —— `StorageBackend` ABC 只有 8 个方法，要实现一个新后端约 150 行代码。欢迎 PR。

## 那为什么 Notion 是"祝福路径"？

诚实答案：因为这是项目作者自己用的。共享 Notion DB 让作者和伴侣协调看房不需要自己写 UI。单人买家用 LocalJSONStorage 一样好用 —— 而且维护成本更低。
