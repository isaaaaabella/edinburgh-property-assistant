# 安装

[English](INSTALL.md) · **中文**

## 系统要求

- macOS 或 Linux（Windows 没测过）
- Python 3.9+
- [Claude Code](https://www.anthropic.com/claude-code) CLI 或 Desktop
- `poppler`（PDF 工具，`parse_home_report.py` 用到）
  - macOS: `brew install poppler`
  - Ubuntu: `sudo apt-get install poppler-utils`

## 步骤

### 1. clone + 安装依赖

```bash
git clone <repo-url> ~/.claude/property_assistant
cd ~/.claude/property_assistant
pip install -r requirements.txt
```

### 2. 配置 .env（零配置即可）

```bash
cp .env.example .env
```

默认 `.env` 只有 `STORAGE_BACKEND=local`，不需要任何 token。数据会存到 `~/.property_data/`。

**同时复制 preferences 模板**（含你的买房偏好和评分权重）：
```bash
cp preferences.example.json preferences.json
```

打开 `preferences.json` 改顶部 5 个字段：
- `bedrooms_preferred` / `floor_min` / `gas_required` — 硬性筛选条件
- `target_schools` — 你看重的学区列表
- `commute.user_label` / `user_workplace` / `partner_label` / `partner_workplace` — 通勤地（带 postcode）

如果不改，scoring 会自动 fallback 到 `preferences.example.json` 的默认值（适合 Edinburgh 南区买家）。`preferences.json` 在 `.gitignore` 里，不会上传到 GitHub。

### 3. 注册 SKILL 到 Claude commands 目录

```bash
mkdir -p ~/.claude/commands
# 如果你的 repo 把 SKILL.md 放在 skills/ 目录：
ln -s ~/.claude/property_assistant/skills/home-report.md ~/.claude/commands/home-report.md
ln -s ~/.claude/property_assistant/skills/property.md    ~/.claude/commands/property.md
```

（这个 repo 当前布局下，SKILL.md 已经在 `~/.claude/commands/` 里，跳过这步。）

### 4. 验证

```bash
# 跑全套测试
./run_tests.sh

# Health check
python -m property_assistant.orchestrator.router health
# 应该看到: ✅ backend=local · writable: ~/.property_data
```

### 5. 第一次跑

在 Claude Code 里：

```
/home-report ~/Downloads/some_home_report.pdf
```

Claude 会按 [`commands/home-report.md`](../../commands/home-report.md) 的步骤：
1. 调 `parse_home_report.py` 把 PDF 抽成 JSON
2. 扮演 RICS 评估师生成 `SurveyorOpinion` JSON
3. CLI validate，失败重试 1 次
4. 跑 pipeline → 评分 → 渲染 HTML → 入库（local 或 notion）

输出 HTML 路径会在末尾打印。

## 升级到完整工作流

零配置版只能用 `/home-report path.pdf`。要用 `/property`（含邮件同步、看房准备、复盘、对比），还需要：

- **Notion**（多设备同步） → [`INTEGRATIONS.md`](INTEGRATIONS.md#notion)
- **Gmail**（自动抓房产邮件） → [`INTEGRATIONS.md`](INTEGRATIONS.md#gmail)
- **Google Maps**（精确通勤时间，否则用 postcode 距离粗估） → [`INTEGRATIONS.md`](INTEGRATIONS.md#google-maps)

## 常见问题

### `pdftotext: command not found`
poppler 没装。`brew install poppler`（mac）或 `apt-get install poppler-utils`（ubuntu）。

### `/property` 报 `NotionAPIError`
`.env` 里 `STORAGE_BACKEND=notion` 但 token 没填。要么切回 `local`，要么按 INTEGRATIONS.md 配 Notion。

### `intake failed: fetch_emails.py error`
Gmail 没配。`/home-report` 不受影响；想用 `/property emails` 需要在 `.env` 加 `GMAIL_USER` + `GMAIL_APP_PASSWORD`。

### `SurveyorOpinion validation failed`
LLM 生成的 JSON 不符合 schema。Claude 会自动读 errors 重试一次，仍失败则 print 详细 errors。常见原因：
- `cat_notes_contradictions` 没被 `score_corrections` 覆盖（每条 contradiction 都要点名）
- `fact` 类 Finding 漏了 `evidence_page`
- 整篇 `judgment` 少于 3 条（变成事实堆砌）

修：人工编辑 `/tmp/opinion_$$.json`，rerun pipeline `python -m property_assistant.pipelines.home_report run <pdf> --opinion /tmp/opinion_$$.json`。

### 测试 fail
跑 `./run_tests.sh` 看 stderr。最常见原因是 `STORAGE_BACKEND` env 污染（前一次跑剩下的）。`unset STORAGE_BACKEND PROPERTY_DATA_DIR` 再重试。
