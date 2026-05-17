# Install

**English** · [中文](INSTALL.zh.md)

## Requirements

- macOS or Linux (Windows untested)
- Python 3.9+
- [Claude Code](https://www.anthropic.com/claude-code) CLI or Desktop
- `poppler` (PDF utilities, used by `parse_home_report.py`)
  - macOS: `brew install poppler`
  - Ubuntu: `sudo apt-get install poppler-utils`

## Steps

### 1. Clone + install dependencies

```bash
git clone https://github.com/isaaaaabella/edinburgh-property-assistant ~/.claude/property_assistant
cd ~/.claude/property_assistant
pip install -r requirements.txt
```

### 2. Configure `.env` (zero-config works out of the box)

```bash
cp .env.example .env
```

The default `.env` only has `STORAGE_BACKEND=local` and needs no tokens. Data will be stored in `~/.property_data/`.

**Also copy the preferences template** (your buying preferences + scoring weights):
```bash
cp preferences.example.json preferences.json
```

Open `preferences.json` and edit the top 5 fields:
- `bedrooms_preferred` / `floor_min` / `gas_required` — hard filter conditions
- `target_schools` — list of school catchments you care about
- `commute.user_label` / `user_workplace` / `partner_label` / `partner_workplace` — commute addresses (include postcode)

If you don't edit, scoring will auto-fallback to the defaults in `preferences.example.json` (tuned for Edinburgh South Side buyers). `preferences.json` is in `.gitignore` — it won't be uploaded to GitHub.

### 3. Register the SKILLs to Claude commands directory

```bash
mkdir -p ~/.claude/commands
ln -s ~/.claude/property_assistant/skills/home-report.md ~/.claude/commands/home-report.md
ln -s ~/.claude/property_assistant/skills/property.md    ~/.claude/commands/property.md
```

### 4. Verify

```bash
# Run the full test suite
./run_tests.sh

# Health check
python -m property_assistant.orchestrator.router health
# Expected: ✅ backend=local · writable: ~/.property_data
```

### 5. First run

In Claude Code:

```
/home-report ~/Downloads/some_home_report.pdf
```

Claude follows the steps in [`skills/home-report.md`](../skills/home-report.md):
1. Calls `parse_home_report.py` to extract PDF → JSON
2. Plays the RICS surveyor role and generates a `SurveyorOpinion` JSON
3. Runs the validator CLI; retries once on failure
4. Runs the pipeline → score → render HTML → store (local or notion)

The output HTML path is printed at the end.

## Upgrading to the full workflow

The zero-config version only supports `/home-report path.pdf`. To use `/property` (with email sync, viewing prep, review, comparison), you also need:

- **Notion** (multi-device sync) → [`INTEGRATIONS.md#notion`](INTEGRATIONS.md#notion)
- **Gmail** (auto-fetch property emails) → [`INTEGRATIONS.md#gmail`](INTEGRATIONS.md#gmail)
- **Google Maps** (precise commute times; otherwise falls back to postcode distance) → [`INTEGRATIONS.md#google-maps`](INTEGRATIONS.md#google-maps)

**Don't have Notion?** That's fine — see [`WITHOUT_NOTION.md`](WITHOUT_NOTION.md) for what's still possible with just LocalJSONStorage.

## Using with other AI coding tools

Codex CLI, Cursor, Continue.dev, Cline, or just curl + GPT-4 — see the main [README](../README.md#using-with-other-ai-coding-tools) for two paths.

## Common issues

### `pdftotext: command not found`
Poppler isn't installed. `brew install poppler` (macOS) or `apt-get install poppler-utils` (Ubuntu).

### `/property` errors with `NotionAPIError`
`.env` has `STORAGE_BACKEND=notion` but the token isn't set. Either switch back to `local`, or configure Notion per `INTEGRATIONS.md`.

### `intake failed: fetch_emails.py error`
Gmail isn't configured. `/home-report` is unaffected; using `/property emails` requires `GMAIL_USER` + `GMAIL_APP_PASSWORD` in `.env`.

### `SurveyorOpinion validation failed`
The LLM's JSON doesn't match the schema. Claude auto-reads the errors and retries once; if that still fails, the detailed errors are printed. Common causes:
- `cat_notes_contradictions` not covered by `score_corrections` (each contradiction must be cited)
- `fact`-kind Finding missing `evidence_page`
- Whole opinion has < 3 `judgment`-kind findings (becomes pure fact-dumping)

Fix: manually edit `/tmp/opinion_$$.json` and rerun `python -m property_assistant.pipelines.home_report run <pdf> --opinion /tmp/opinion_$$.json`.

### Tests fail
Run `./run_tests.sh` and check stderr. Most common cause: leftover `STORAGE_BACKEND` env from a previous run. Try `unset STORAGE_BACKEND PROPERTY_DATA_DIR` and rerun.
