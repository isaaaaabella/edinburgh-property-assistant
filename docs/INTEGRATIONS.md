# 集成

零配置版（`STORAGE_BACKEND=local`，无 Gmail / 无 Notion）能用 `/home-report path.pdf`。完整工作流需要按需开启下面几项。

---

## Notion

**何时需要**：想跨设备同步房产数据 / 想在 Notion 里和伴侣协作 / 已经有 Notion DB 想接管。

### 设置

1. 在 Notion 创建一个 database（或导入随包的 `docs/notion_database_template.md`），含以下 properties：
   - `地址` (title) **必需**
   - `状态` (select)
   - `HR估价(£)` / `挂牌价(£)` / `Factor月费(£)` (number, format=pound)
   - `卧室数` / `面积(m²)` / `建造年代` / `EPC分数` / `Category 2数量` / `Category 3数量` / `SIMD综合分位` / `业主持有年限` / `通勤-<你的名字> (min)` / `通勤-<伴侣名字> (min)` / `你的评分` / `伴侣评分` (number)
   - ⚠️ 通勤列名要跟 `NOTION_FIELD_MAP` 里的字符串完全一致；demo 仓库给的是 `通勤-Duoduo (min)` / `通勤-Jingjun (min)`。把这两个值改成你的名字（在 `storage/notion_storage.py`）或者你的 Notion 列名改成 demo 的名字。
   - `楼层` / `建筑类型` / `EPC评级` / `Factor情况` / `洪水风险` / `区域` (select)
   - `学区` (multi_select)
   - `主门公寓` / `Gas供暖` / `公共建筑保险` / `屋顶问题` / `值得二看` (checkbox)
   - `Closing Date` / `Viewing时间` (date)
   - `Rightmove链接` / **`HTML报告`** (url)
   - `备注` (rich_text)

   ⚠️ `HTML报告` 列必须手动加（pipeline 写报告链接到这里）。

2. 在 [Notion Integrations](https://www.notion.so/profile/integrations) 创建一个 internal integration，拿到 `secret_xxx` token。

3. 在你的 DB 页面右上角 ⋯ → Connections → 添加你刚创建的 integration。

4. 在 `.env`：
   ```bash
   STORAGE_BACKEND=notion
   NOTION_TOKEN=secret_xxx
   NOTION_PROPERTY_DB_ID=<32 字符 UUID>
   ```

   DB ID 在浏览器 URL 里找：`notion.so/<workspace>/<DB_ID>?v=...` —— DB_ID 那段就是。

5. 验证：
   ```bash
   python -m property_assistant.orchestrator.router health
   # ✅ backend=notion · db <DB_ID> reachable, schema cached
   ```

### Field name 不一样？

如果你的 DB 里某个 column 名称跟我假设的不一样（比如你用 `房屋估价` 而不是 `HR估价(£)`），改 [`storage/notion_storage.py`](../storage/notion_storage.py) 里的 `NOTION_FIELD_MAP` 那一行就行：

```python
"hr_valuation": ("HR估价(£)", "number"),
                  ^^^^^^^^^^ 改这里
```

`schema_audit` 工具可以扫现有 DB 跟 PropertyRecord 字段对账（参考 `SCHEMA_AUDIT.md`）。

---

## Gmail

**何时需要**：`/property emails` 自动同步房产邮件（中介看房确认、closing date 通知、贷款顾问消息等）。

### 设置

Gmail 不能用账号密码登 IMAP，需要 [App Password](https://myaccount.google.com/apppasswords)：

1. 开启 2FA（如果没开）
2. App Passwords → 生成一个 16 字符密码（带空格也行）
3. `.env`：
   ```bash
   GMAIL_USER=you@gmail.com
   GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
   ```

4. 验证：
   ```bash
   python -m property_assistant.pipelines.intake run --hours 24
   # 列出最近 24 小时邮件 + 分类
   ```

### 邮件分类

`pipelines/intake.py` 用正则做基础分类（中英文 keyword）：
- `viewing_confirmed` — 看房时间确认
- `closing_date` — Closing date 通知
- `mortgage` — 贷款 / AIP
- `solicitor` — 律师 / 公证
- `home_report` — 含 PDF 附件 OR 主题/正文有 "Home Report"
- `general` — 其他

地址匹配优先 postcode (`EH\d`)，fallback 街道编号 (`10 Marchmont Rd` 等模式)。匹配上则追加 `CommEntry` 到对应 PropertyRecord。

不准的时候手动跑 `/property emails --json` 看分类结果。

---

## Google Maps

**何时需要**：想要精确的公交通勤分钟数（家 → 工作地）。

不开启的话，`fetch_area_data.py` 会用 postcode 距离粗估，依然可用。

### 设置

1. [Google Cloud Console](https://console.cloud.google.com/) → 新建 project → enable Directions API
2. APIs & Services → Credentials → 新建 API key
3. `.env`：
   ```bash
   GOOGLE_MAPS_API_KEY=AIza...
   ```

注意 Directions API 有 free tier 限额（每月 ~40k requests 免费），重度使用要监控 billing。

---

## 切换 backend

随时可切：

```bash
# 临时切到 local 跑一次 home-report（不污染 Notion）
STORAGE_BACKEND=local /home-report ~/Downloads/x.pdf

# .env 里改 STORAGE_BACKEND=notion 永久切回
```

切换不会迁移数据 —— Notion 里的房子在切到 local 后不会自动出现在本地 JSON 里。如果想迁移，参考 `legacy/add_property.py` 或自己写 script。
