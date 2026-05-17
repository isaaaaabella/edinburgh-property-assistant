# Integrations

**English** · [中文](INTEGRATIONS.zh.md)

The zero-config setup (`STORAGE_BACKEND=local`, no Gmail, no Notion) supports `/home-report path.pdf`. The full workflow needs the integrations below — enable them as you need.

**No Notion at all?** See [`WITHOUT_NOTION.md`](WITHOUT_NOTION.md) — most of the workflow still works.

---

## Notion

**When you need it**: Want cross-device sync / want to collaborate with a partner in Notion / already have a Notion DB you'd like to plug in.

### Setup

1. Create a Notion database (or import the structure described in `SCHEMA_AUDIT.md`) with these properties:
   - `地址` / Address (title) **required**
   - `状态` / Status (select)
   - `HR估价(£)` / `挂牌价(£)` / `Factor月费(£)` (number, format=pound)
   - `卧室数` / `面积(m²)` / `建造年代` / `EPC分数` / `Category 2数量` / `Category 3数量` / `SIMD综合分位` / `业主持有年限` / `通勤-<your name> (min)` / `通勤-<partner name> (min)` / `你的评分` / `伴侣评分` (number)
     - ⚠️ The commute column names must exactly match `NOTION_FIELD_MAP`; demo defaults are `通勤-Duoduo (min)` / `通勤-Jingjun (min)`. Edit either side to match.
   - `楼层` / `建筑类型` / `EPC评级` / `Factor情况` / `洪水风险` / `区域` (select)
   - `学区` (multi_select)
   - `主门公寓` / `Gas供暖` / `公共建筑保险` / `屋顶问题` / `值得二看` (checkbox)
   - `Closing Date` / `Viewing时间` (date)
   - `Rightmove链接` / **`HTML报告`** (url)
   - `备注` (rich_text)

   ⚠️ The `HTML报告` column must be added manually (the pipeline writes report links here).

2. Create an internal integration at [Notion Integrations](https://www.notion.so/profile/integrations) and grab the `secret_xxx` token.

3. On your database page, top-right ⋯ → Connections → add the integration you just created.

4. In `.env`:
   ```bash
   STORAGE_BACKEND=notion
   NOTION_TOKEN=secret_xxx
   NOTION_PROPERTY_DB_ID=<32-char UUID>
   ```

   Find the DB ID in your browser URL: `notion.so/<workspace>/<DB_ID>?v=...` — the `<DB_ID>` segment.

5. Verify:
   ```bash
   python -m property_assistant.orchestrator.router health
   # ✅ backend=notion · db <DB_ID> reachable, schema cached
   ```

### Field names differ?

If your DB column names don't match (e.g., you use `房屋估价` instead of `HR估价(£)`), edit the right-hand strings in `NOTION_FIELD_MAP` in [`storage/notion_storage.py`](../storage/notion_storage.py):

```python
"hr_valuation": ("HR估价(£)", "number"),
                  ^^^^^^^^^^ change this
```

The `schema_audit` tool can scan your existing DB and reconcile against PropertyRecord fields (see `SCHEMA_AUDIT.md`).

---

## Gmail

**When you need it**: `/property emails` auto-syncs property emails (agent viewing confirmations, closing date notices, mortgage broker messages, etc.).

### Setup

Gmail can't use account passwords for IMAP — use an [App Password](https://myaccount.google.com/apppasswords):

1. Enable 2FA (if not already on)
2. App Passwords → generate a 16-character password (spaces are OK)
3. `.env`:
   ```bash
   GMAIL_USER=you@gmail.com
   GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
   ```

4. Verify:
   ```bash
   python -m property_assistant.pipelines.intake run --hours 24
   # Lists emails from the past 24 hours + classifications
   ```

### Email classification

`pipelines/intake.py` uses regex-based classification (Chinese + English keywords):
- `viewing_confirmed` — viewing time confirmation
- `closing_date` — Closing date notice
- `mortgage` — Mortgage / AIP
- `solicitor` — Solicitor / missive
- `home_report` — Has PDF attachment OR subject/body mentions "Home Report"
- `general` — everything else

Address matching prioritises postcode (`EH\d`), falls back to street-number patterns (`10 Marchmont Rd` etc.). On match, a `CommEntry` is appended to the matching PropertyRecord.

When misclassified, run `/property emails --json` to see classification output.

---

## Google Maps

**When you need it**: Precise public-transit commute times (home → office).

Without it, `fetch_area_data.py` falls back to postcode-distance heuristics — still usable.

### Setup

1. [Google Cloud Console](https://console.cloud.google.com/) → create project → enable Directions API
2. APIs & Services → Credentials → create API key
3. `.env`:
   ```bash
   GOOGLE_MAPS_API_KEY=AIza...
   ```

Note: Directions API has a free tier (~40k requests/month). Monitor billing for heavy use.

---

## Switching backends

Switch any time:

```bash
# Temporarily run home-report against local (without touching Notion)
STORAGE_BACKEND=local /home-report ~/Downloads/x.pdf

# Permanently switch back to Notion by editing .env
```

Switching does NOT migrate data — properties in Notion won't auto-appear in local JSON after switching. To migrate, see `legacy/add_property.py` or write a script.
