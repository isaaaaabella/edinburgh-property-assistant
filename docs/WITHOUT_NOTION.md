# Without Notion

**English** · [中文](WITHOUT_NOTION.zh.md)

You don't need Notion. The default setup uses `LocalJSONStorage` — properties, communications, and HTML reports all live on your laptop under `~/.property_data/`. This document explains exactly what's possible and what isn't.

## What works without Notion

Everything except cross-device sync and the partner-in-Notion collaboration UI.

### ✅ `/home-report path.pdf` — single PDF analysis
Full HTML output (layered cards + surveyor opinion + score breakdown + Cat table + map). Saves the PropertyRecord JSON and a copy of the HTML report under `~/.property_data/`.

### ✅ `/property prep --addr X` — pre-viewing brief
Looks up the property in local storage, generates a `ViewingStrategy`, renders the brief HTML.

### ✅ `/property compare --addr A --addr B` — side-by-side comparison
Pulls multiple records from local storage, renders the comparison table.

### ✅ `/property review` — post-viewing reflection
Reads viewed properties from local storage, surfaces gaps (score vs subjective feel, partner disagreement), suggests shortlist.

### ✅ `/property emails` (if Gmail configured)
Pulls emails, classifies, matches to local records, dry-run by default. Gmail and Notion are independent — you can have one without the other.

### ⚠️ What you lose
1. **Cross-device sync**: data is one-laptop-only. If you want to read your property list from your phone, no can do.
2. **Partner collaboration**: no shared Notion page with your partner. Workaround: share the HTML report files via Dropbox / Drive / email.
3. **Free-form note-taking**: Notion's rich-text editing for `你的感受` / `伴侣的感受` is nicer than editing a JSON field directly.

## Inspecting your data

Everything is plain JSON:

```bash
~/.property_data/
├── index.json                              # one-line summary per property
├── properties/<slug>.json                  # full PropertyRecord
├── communications/<slug>.jsonl             # comm log (one entry per line)
└── reports/<slug>/<timestamp>_<kind>.html  # all generated HTML
```

To list all your properties:
```bash
cat ~/.property_data/index.json | python -m json.tool
```

To open the latest report for one property:
```bash
ls -t ~/.property_data/reports/<slug>/*.html | head -1 | xargs open  # macOS
```

## Editing subjective feelings without Notion

Notion users edit `你的感受` / `伴侣的感受` directly in the page UI. Local users edit JSON:

```bash
# Option 1: command-line
python -c "
from property_assistant.storage.local_json_storage import LocalJSONStorage
s = LocalJSONStorage()
rec = s.find_by_address('Marchmont')
s.set_subjective_feedback(rec.storage_id,
    self_feeling='明亮，朝南起居室很喜欢',
    partner_feeling='厨房太小')
"

# Option 2: open the JSON directly
$EDITOR ~/.property_data/properties/<slug>.json
# edit "self_feeling" and "partner_feeling" fields
```

After editing, regenerate the brief / review HTML to see the new feedback reflected.

## Sharing reports with your partner (or anyone)

The HTML reports are **self-contained** — no external CSS, no JS dependencies, embedded Google Maps via iframe. Send the `.html` file directly:

- Drop into Dropbox / Google Drive / email attachment
- Recipient opens in any browser (mobile or desktop)
- The map iframe needs internet, everything else works offline

## Migrating to Notion later

If you decide to switch:

1. Set up Notion per [`INTEGRATIONS.md`](INTEGRATIONS.md#notion)
2. Switch `.env`: `STORAGE_BACKEND=notion`
3. For each existing property, re-run `/home-report <pdf>` — pipeline will upsert into Notion
4. Your local JSON stays in `~/.property_data/` as a backup

The `legacy/add_property.py` script provides a starting point for bulk migration if you have many properties.

## Future plans (not built yet)

Possible future backends:
- **Airtable** — similar collaborative UX to Notion, simpler API
- **SQLite** — for users who want a local relational DB without service dependency
- **Markdown vault** — for Obsidian / LogSeq users (each property = one .md file)

None of these are on the roadmap right now — the `StorageBackend` ABC is small (8 methods), so anyone who needs one can implement it in ~150 LOC. PRs welcome.

## Why is Notion the "blessed" path then?

Honest answer: it's what the project author uses. The shared Notion DB lets the author and partner coordinate viewings without a custom UI. For a solo buyer, LocalJSONStorage is just as good — and lower-maintenance.
