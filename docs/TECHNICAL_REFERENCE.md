# Technical Reference

_Last updated: 2026-05-05 (Creative Intelligence + ad-copy-generator shipped; workflow-schedule docs corrected; §2 tree + §11.6 skills table completed for all 5 skills; daily-data cron resolved in §10.4)_

This document is the engineering reference for the `marketing-claude-honeycomb` repository. It describes architecture, data model, APIs, deployment, and key implementation details. For a higher-level overview see [STATE_REPORT.md](./STATE_REPORT.md).

> **Maintenance rule:** This document must be updated in the same PR as any change that affects architecture, data model, APIs, deployment, or significant function contracts. See [CLAUDE.md](../CLAUDE.md) for the docs-update rule.

---

## 1. Architecture Overview

The system now has **two parallel data pipelines** plus a presentation layer and an agent layer:

1. **Campaign-level pipeline** (Apps Script + Google Sheets) — the existing system. Source of truth for the dashboard, weekly rollup, narrative generator, and budget approval flow.
2. **Ad-level pipeline** (Python + GitHub Actions, new 2026-05-02) — a separate, additive pipeline that pulls ad-set + ad-level insights and creative metadata from Meta and writes JSON snapshots into the repo. Powers the agent skills.
3. **Presentation layer** — a single-file React dashboard.
4. **Agent layer** — Claude Code reads `skills/<name>/SKILL.md` files at session start, then operates against snapshot files under `data/`.

Google Sheets is the system of record for the campaign-level pipeline. The repo itself (Git history of `data/snapshots/`) is the system of record for the ad-level pipeline.

```
┌───────────────────────────────────────────────────────────────┐
│                  Google Sheet (data layer)                     │
│  rolling_data │ hubspot_icps │ weekly_rollup │ intelligence_log│
│  campaign_mapping │ budget_queue                              │
└───────────────────────────────────────────────────────────────┘
                             ↑↓
┌───────────────────────────────────────────────────────────────┐
│    Apps Script (apps-script/Code.js, ~4,200 lines)             │
│  - Daily/weekly scheduled triggers (fetch, rollup, narrative)  │
│  - Budget automation (signal → propose → approve → execute)    │
│  - Web App: /exec?action=... for dashboard API                 │
│  - Chat backend: forward user msg to Claude with live context  │
│  - Audit snapshot export to GitHub                             │
└───────────────────────────────────────────────────────────────┘
                             ↑↓
┌───────────────────────────────────────────────────────────────┐
│  External APIs:  Meta Ads │ HubSpot │ Slack │ Anthropic │ GH   │
└───────────────────────────────────────────────────────────────┘
                             ↑↓
┌───────────────────────────────────────────────────────────────┐
│  Dashboard (webapp/index.html, single-file React SPA)          │
│  Hosted on GitHub Pages. Talks only to the Apps Script /exec.  │
└───────────────────────────────────────────────────────────────┘

— — — Ad-level pipeline (parallel, agent-facing) — — —

┌───────────────────────────────────────────────────────────────┐
│  GitHub Actions: daily-data.yml (workflow_dispatch — manual)   │
│   1. scripts/fetch_ad_data.py   →  data/snapshots/<date>/      │
│   2. scripts/compute_signals.py →  data/derived/               │
│   3. git commit + push                                          │
└───────────────────────────────────────────────────────────────┘
                             ↑                       ↓
                ┌──────────────────┐   ┌────────────────────────┐
                │  Meta Graph API  │   │  Repo (data/ as DB)    │
                └──────────────────┘   │  snapshots/, derived/, │
                                       │  creatives/, config/   │
                                       └────────────────────────┘
                                                   ↓
                                       ┌────────────────────────┐
                                       │ Claude Code agent loop │
                                       │  reads skills/*.md +   │
                                       │  data/* on session     │
                                       └────────────────────────┘
```

**Key properties:**

- **No build step.** The dashboard loads React, Recharts, Tailwind, and Babel from CDNs and uses in-browser JSX transpilation. There's no `package.json` or `npm install`.
- **Two systems of record.** The Google Sheet stores campaign-level data; the Git repo's `data/` directory stores ad-level data. The ad-level pipeline does not write to the Sheet, and the campaign-level pipeline does not write to `data/`.
- **Deployments are git-native.** Merging to `main` triggers GitHub Actions that push Apps Script via `clasp` and publish the dashboard to GitHub Pages. Nobody edits the Apps Script web editor directly.
- **Two execution contexts for the Apps Script layer:** (1) time-based triggers run on Google's schedule, (2) HTTP GET/POST to the published Web App `/exec` URL drives the dashboard and Slack approval links.
- **Ad-level pipeline is autonomous.** `.github/workflows/daily-data.yml` runs daily at 8 AM ET (UTC 12:00) on cron and commits each snapshot directly to main. Manual `workflow_dispatch` is preserved for backfills via the `start_date` / `end_date` inputs.

## 2. Repository Structure

```
marketing-claude-honeycomb/
├── apps-script/
│   ├── Code.js              # The full intelligence layer (~4,200 lines)
│   ├── appsscript.json      # Apps Script manifest (scopes, runtime, web app access)
│   └── .clasp.json          # clasp deployment config (script ID, file mappings)
├── webapp/
│   ├── index.html           # Single-file React dashboard
│   └── apps-script-api.gs   # Reference copy of the web API layer (docs only)
├── docs/
│   ├── STATE_REPORT.md      # Non-technical project state
│   └── TECHNICAL_REFERENCE.md  # This document
├── scripts/                 # NEW (2026-05-02) Ad-level Python pipeline
│   ├── fetch_ad_data.py     # Daily Meta ad-set + ad insights pull
│   ├── compute_signals.py   # Derived fatigue / winner-bleeder signals
│   └── run_daily.sh         # Orchestrator (fetch → compute)
├── skills/                  # Agent skill definitions (2026-05-02+)
│   ├── pipeline-health/SKILL.md
│   ├── daily-check/SKILL.md
│   ├── fatigue-monitor/SKILL.md
│   ├── creative-intelligence/   # Added 2026-05-05
│   │   ├── SKILL.md
│   │   ├── references/      # copy_angle.md + visual_style.md
│   │   └── scripts/         # build_creative_dataset.py, categorize_creative.py
│   └── ad-copy-generator/       # Added 2026-05-05
│       ├── SKILL.md
│       ├── references/      # voice_guide.md, compliance_rules.md
│       └── scripts/         # generate_drafts.py
├── data/                    # NEW (2026-05-02) Agent data repository
│   ├── config/benchmarks.json     # All thresholds (single source)
│   ├── snapshots/<YYYY-MM-DD>/    # Daily JSON snapshots from Meta
│   │   ├── campaigns.json
│   │   ├── adsets.json
│   │   ├── ads.json
│   │   ├── adset_insights.json
│   │   ├── ad_insights.json
│   │   └── _manifest.json
│   ├── creatives/creatives.json   # Accumulating creative metadata
│   └── derived/                   # Computed signals (regenerable)
│       ├── fatigue_signals.json
│       ├── winner_bleeder.json
│       └── summary.json
├── .github/workflows/
│   ├── deploy-apps-script.yml  # Push Code.js via clasp on merge to main
│   ├── deploy-webapp.yml       # Publish dashboard to GitHub Pages on merge to main
│   ├── daily-data.yml          # NEW (2026-05-02) Ad-level data pull (daily cron)
│   └── claude.yml              # @claude mentions in issues/PRs
├── ad-copy/          # (empty placeholder) Meta ad copy by vertical
├── workflows/        # (empty placeholder) Automation scripts
├── audiences/        # (empty placeholder) Audience segmentation — never commit PII
├── reports/          # (empty placeholder) Campaign performance reports
└── CLAUDE.md         # Project-level instructions for Claude
```

**Branches:**

- `main` — production. All merges deploy automatically via CI.
- `audit-snapshots` — data-only branch. Never merged to `main`. Populated by `exportAuditSnapshot()` in Apps Script. Contains JSON exports under `snapshots/`:
  - `snapshots/rolling_data.json` (last 90 days)
  - `snapshots/weekly_rollup.json` (all weeks)
  - `snapshots/intelligence_log.json` (all narratives)
  - `snapshots/campaign_mapping.json` (all mappings)
  - `snapshots/_manifest.json` (summary metadata)

**Critical single-file dependencies:**

- `apps-script/Code.js` is the only place to edit Apps Script code. Anything changed in the web editor directly will be silently overwritten by CI on the next push.
- `webapp/index.html` is the entire dashboard. No separate JS/CSS files.

## 3. Data Model (Google Sheets)

Six tabs in a single Google Spreadsheet. Constants in `Code.js:26-30` reference them by name.

### 3.1 `rolling_data` — Daily Meta campaign insights

**13 columns. One row per (date, campaign). Written daily, appended only.**

| # | Column | Type | Notes |
|---|---|---|---|
| 0 | Date | Date (YYYY-MM-DD) | Dedup key component |
| 1 | Month | String | e.g. "April" |
| 2 | Week | Integer | ISO week number |
| 3 | Campaign Name | String | From Meta |
| 4 | Campaign ID | String (`@` format) | Forced text to preserve 16-digit precision |
| 5 | Impressions | Integer | |
| 6 | Clicks | Integer | |
| 7 | Spend | Float | USD |
| 8 | Reach | Integer | |
| 9 | Conversions | Integer | Meta-reported lead conversions |
| 10 | Frequency | Float (2 dec) | impressions / reach |
| 11 | CPL | Float or `null` | spend / conversions; `null` when conversions = 0 |
| 12 | IC Conversions | Integer | Custom "investment_crowdfunding" conversion count |

- **Writer:** `collectMetaRows_()` (Code.js:841) via `fetchDataForDateRange_()` (Code.js:760).
- **Dedup key:** `date || campaign_id` held in a `Set` in memory per run. Zero-spend rows are skipped.
- **Retention:** Append-only. No cleanup.
- **Name normalization:** Historical `Campaign Name` values are rewritten to the current Meta name when a rename is detected in `syncCampaignMappings_`. The primary key for all joins is `Campaign ID` (column E) — names are purely display labels. The one-time `backfillCampaignIds_` migration also normalizes historical names on first run.

### 3.2 `hubspot_icps` — HubSpot contacts decisioned as investment_crowdfunding

**16 columns. One row per contact. Written daily, appended only.**

| # | Column | Type | Notes |
|---|---|---|---|
| 0 | hs_contact_id | String | Dedup key |
| 1 | prequal_submitted | Date | |
| 2 | prequal_decision | String | Always `'investment_crowdfunding'` for rows in this sheet |
| 3 | prequal_utm_source | String | |
| 4 | prequal_utm_medium | String | |
| 5 | prequal_utm_campaign | String | Joins to `campaign_mapping.utm_campaign` |
| 6 | prequal_industry | String | |
| 7 | prequal_industry_tier | String | |
| 8 | prequal_funding_need | Number | |
| 9 | prequal_monthly_revenue | Number | |
| 10 | prequal_pre_approval_amount | Number | |
| 11 | prequal_business_name | String | PII-adjacent — NEVER export outside trusted systems |
| 12 | prequal_credit_score | Number | |
| 13 | prequal_rejection_reasons | String | |
| 14 | week_number | Integer | ISO week of `prequal_submitted` |
| 15 | week_start | Date (YYYY-MM-DD) | Monday of `prequal_submitted` week |

- **Writer:** `fetchHubspotICPs()` (Code.js:902).
- **Dedup key:** `hs_contact_id` in a `Set` per run.
- **Not exported to audit-snapshots branch** (PII-adjacent).

### 3.3 `campaign_mapping` — Campaign → UTM → conversion event lookup

**4 columns. One row per Meta campaign.**

| # | Column | Type | Notes |
|---|---|---|---|
| 0 | campaign_name | String | Meta campaign name (exact) |
| 1 | utm_campaign | String | UTM tag extracted from ad destination URLs |
| 2 | conversion_event | String | Custom conversion event name (manual or auto-discovered) |
| 3 | custom_conversion_id | String (`@` format) | Meta custom conversion ID; forced text for precision |
| 4 | campaign_id | String (`@` format) | **Primary key.** Stable Meta campaign ID. Populated by `syncCampaignMappings_`. Enables rename resilience — when a campaign is renamed in Meta, the sync updates the `campaign_name` column in place and preserves all manually-set values (utm_campaign, conversion_event, custom_conversion_id). |

- **Writer:** `syncCampaignMappings_()` (Code.js:232). Auto-discovery from Meta ads API + manual edits allowed.
- **Primary key:** `campaign_id` (column E). When populated, existence checks and UTM/IC lookups prefer this over `campaign_name`. Legacy rows without an ID fall back to name-based matching. The one-time `backfillCampaignIds_()` migration fills column E for all existing rows and deduplicates rename-caused duplicates.
- **Discovery flow:** Reads `/ads?fields=creative{url_tags}` to extract utm_campaign; reads `/adsets?fields=promoted_object` to find custom conversion IDs; resolves custom conversion IDs to event names via `/{id}?fields=name`.
- **Rename handling:** If a campaign_id already has a mapping row and the name in Meta has changed, the sync updates the name in place, rewrites all historical `rolling_data` rows for that campaign_id to the new name (so downstream consumers see one canonical name), and posts a Slack notification. It does NOT overwrite manually-set columns B-D.
- **Read by:** `buildCampaignUTMMap_()` (Code.js:~633) builds `{campaignId: utm}` lookup (prefers column E, falls back to name). `getICConversionMap_()` (Code.js:~706) identifies IC campaigns (prefers column E, falls back to name→rolling_data join).

### 3.4 `weekly_rollup` — Aggregated weekly performance + hybrid attribution

**22 columns. One row per (week_start, campaign). Rebuilt from scratch on every run.**

| # | Column | Type | Notes |
|---|---|---|---|
| 0 | week_start | Date (YYYY-MM-DD) | Monday. Primary rollup key. |
| 1 | campaign_name | String | |
| 2 | utm_campaign | String | From `campaign_mapping` |
| 3 | spend | Float | |
| 4 | impressions | Integer | |
| 5 | clicks | Integer | |
| 6 | reach | Integer | |
| 7 | avg_frequency | Float (2 dec) | |
| 8 | ctr | Float (4 dec) | |
| 9 | meta_conversions | Integer | |
| 10 | ic_conversions | Integer | |
| 11 | icps_attributed | Integer | Hard UTM-matched ICP count |
| 12 | estimated_icps | Float (1 dec) | **Hybrid v3 attribution — primary volume metric** |
| 13 | attribution_rate | Float (1 dec) | ic_conversions / estimated_icps × 100 |
| 14 | cpl | Float or null | |
| 15 | cpicp_attributed | Float or null | spend / icps_attributed |
| 16 | cpicp_blended | Float or null | **spend / estimated_icps — primary efficiency metric** |
| 17 | cpicp_blended_prior_week | Float or null | |
| 18 | cpicp_blended_4wk_avg | Float or null | |
| 19 | cpicp_blended_wow_pct | Float (1 dec) or null | |
| 20 | cpicp_blended_vs_4wk_pct | Float (1 dec) or null | |
| 21 | icp_wow_delta | Float (1 dec) or null | |

- **Writer:** `buildWeeklyRollup()` (Code.js:1061).
- **Rebuild semantics:** Sheet is cleared and fully repopulated every run. Never append.
- **Hybrid attribution v3 formula** (per day, per campaign):
  ```
  dailyUnattributed = max(0, totalHubspotICPsOnDate − totalICConversionsOnDate)
  campaignShareOfUnattributed = (campaignMetaConvs / totalMetaConvsOnDate) × dailyUnattributed
  campaignDailyICPs = campaignICConversions + campaignShareOfUnattributed
  ```
  Campaign-week `estimated_icps` = sum of `campaignDailyICPs` over the week.

### 3.5 `intelligence_log` — Weekly AI-generated narratives

**7 columns. One row per completed week. Append with overwrite.**

| # | Column | Type | Notes |
|---|---|---|---|
| 0 | generated_at | ISO timestamp | |
| 1 | reporting_week | Date (YYYY-MM-DD) | Must be a Monday; validated in writer |
| 2 | total_spend | Float (2 dec) | Rounded after accumulation to avoid float residuals |
| 3 | total_icps | Float (1 dec) | Rounded after accumulation |
| 4 | overall_cpicp | String or `'N/A'` | `.toFixed(2)` of spend/icps |
| 5 | context_block | Text (multi-KB) | Full data context sent to Claude |
| 6 | narrative | Text | Claude Sonnet output |

- **Writer:** `generateNarrativeForWeek_()` (Code.js:1373). Scheduled entry point: `generateWeeklyNarrative()` (Code.js:1303).
- **Invariants:**
  - `reporting_week` must be a Monday (asserted by parsing YYYY-MM-DD component parts to avoid UTC timezone quirk).
  - At most one row per week (scheduled wrapper skips if row already exists; manual regeneration uses `overwrite: true`).
- **Backfill utility:** `backfillHistoricalNarratives()` (Code.js:1649) — one-time migration that deletes Sunday-convention rows and regenerates under Monday convention.

### 3.6 `budget_queue` — Pending and executed budget changes

**12 columns. One row per proposed change. Append only.**

| # | Column | Type | Notes |
|---|---|---|---|
| 0 | token | String (16-char hex) | Groups a batch of proposals |
| 1 | created_at | ISO timestamp | |
| 2 | analysis_date | Date | |
| 3 | execution_scheduled | Date | Tomorrow at 3 AM |
| 4 | campaign_id | String | |
| 5 | campaign_name | String | |
| 6 | current_budget_cents | Integer | |
| 7 | proposed_budget_cents | Integer | |
| 8 | change_cents | Integer | Can be negative |
| 9 | change_pct | Float | |
| 10 | signal_reasons | String | Pipe-separated reasons from the rules engine |
| 11 | status | Enum | `pending` → `approved` → `executed` / `failed`, or `pending` → `rejected` / `expired` |

- **Writer:** `writeToQueue_()` (Code.js:2619).
- **State machine driven by Script Properties:** `BUDGET_PENDING_TOKEN`, `BUDGET_APPROVED_TOKEN`, `BUDGET_REJECTED_TOKEN` — see §7 Budget Automation.

## 4. Configuration

### 4.1 Hardcoded constants (Code.js:14-52)

| Constant | Value | Purpose |
|---|---|---|
| `AD_ACCOUNT_ID` | `'act_1953544531525812'` | Meta ad account |
| `API_VERSION` | `'v21.0'` | Meta Graph API version |
| `META_SHEET` | `'rolling_data'` | Sheet name constants |
| `HS_SHEET` | `'hubspot_icps'` | |
| `MAPPING_SHEET` | `'campaign_mapping'` | |
| `ROLLUP_SHEET` | `'weekly_rollup'` | |
| `INTEL_SHEET` | `'intelligence_log'` | |
| `BUDGET_SHEET` | `'budget_queue'` | |
| `TARGET_WEEKLY_SPEND` | `10000` (USD) | Weekly budget target |
| `WEEKLY_SPEND_TOLERANCE` | `500` (USD) | ± tolerance band |
| `CAMPAIGN_DAILY_MIN_CENTS` | `2500` | Minimum $25/day floor |
| `MAX_CHANGE_PCT` | `0.02` | ±2% per optimization cycle |
| `MAX_REDUCTION_PCT` | `0.04` | Hard cap: max 4% cut |
| `LIFETIME_MIN_CONVERSIONS` | `10` | Eligibility gate for budget changes |
| `WEEKLY_ICP_TARGET` | `75` | Benchmark, informational only |
| `ROLLING_DAYS` | `14` | Signal window for budget decisions |
| `FREQ_WATCH_THRESHOLD` | `2.0` | Frequency flag |
| `FREQ_HIGH_THRESHOLD` | `3.0` | Frequency override (reduce) |
| `ANTHROPIC_MODEL` | `'claude-opus-4-7'` | Claude model for all Anthropic API calls (narrative, chat, budget commentary, daily digest). Change here to upgrade everywhere. |
| `IC_CONVERSION_EVENT_PATTERN` | `'investment crowdfunding'` | Substring match (case-insensitive) against `campaign_mapping.conversion_event`. Matches "Investment Crowdfunding Prequal Decision". Changed from `'investment_crowdfunding'` (underscore) on 2026-04-21 to fix an IC tracking outage that ran 4/15–4/20 — see the discontinuity comment in `Code.js`. |

### 4.2 Script Properties (secrets + runtime state)

Stored via `PropertiesService.getScriptProperties()` (`PROPS` in code). Set manually in Apps Script editor → Project Settings → Script Properties.

**Required secrets:**

| Key | Purpose | Used by |
|---|---|---|
| `META_ACCESS_TOKEN` | Meta Graph API OAuth token | All Meta API calls |
| `HUBSPOT_API_KEY` | HubSpot API bearer token | `fetchHubspotICPs()` |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook URL | `postToSlack_()` |
| `ANTHROPIC_API_KEY` | Anthropic API key | Narrative, budget commentary, chat |
| `WEB_APP_URL` | Deployed Web App `/exec` URL | Embedded in Slack approval links |
| `GITHUB_PAT` | Fine-grained GitHub PAT (Contents R/W) | `exportAuditSnapshot()` |

**Runtime state (managed by code, not user-set):**

| Key | Purpose |
|---|---|
| `BUDGET_PENDING_TOKEN` | Active proposal token (one at a time) |
| `BUDGET_APPROVED_TOKEN` | Set when someone approves in Slack |
| `BUDGET_REJECTED_TOKEN` | Set when someone rejects in Slack |
| `BUDGET_LAST_RUN_AT`, `BUDGET_LAST_APPROVED_BY`, `BUDGET_LAST_APPROVED_AT` | Audit trail |
| `SPEND_TARGET_PENDING_TOKEN`, `PENDING_SPEND_TARGET`, `PENDING_SPEND_TOLERANCE` | Spend-target override state machine |
| `DASHBOARD_TARGET_WEEKLY_SPEND`, `DASHBOARD_WEEKLY_SPEND_TOLERANCE` | Runtime overrides of the hardcoded constants |
| `SYNC_LAST_RUN_DATE` | Once-per-day guard for `syncCampaignMappings_` |
| `SYNC_SCOPE_WARNED` | Suppresses repeated scope warnings |
| `SYNC_WARNED_CAMPAIGNS` | Pipe-delimited list of campaigns already flagged as unmappable |

**Runtime override accessors:**

- `getTargetWeeklySpend_()` (Code.js:4152) — returns override if set, otherwise the hardcoded constant.
- `getWeeklySpendTolerance_()` (Code.js:4157) — same pattern.

### 4.3 Apps Script manifest (`appsscript.json`)

```json
{
  "timeZone": "America/New_York",
  "runtimeVersion": "V8",
  "exceptionLogging": "STACKDRIVER",
  "webapp": {
    "executeAs": "USER_DEPLOYING",
    "access": "ANYONE_ANONYMOUS"
  }
}
```

- `executeAs: USER_DEPLOYING` — script runs as the deployer's Google account; Meta/HubSpot tokens are theirs.
- `access: ANYONE_ANONYMOUS` — `/exec` URL is unauthenticated. Relies on URL obscurity.
- `timeZone: America/New_York` — all date formatting and trigger times use ET.

### 4.4 clasp configuration (`.clasp.json`)

- `scriptId`: fixed Apps Script project ID. Do not change.
- `rootDir: ""` — files at root of `apps-script/`.
- `scriptExtensions: [".js", ".gs"]` — clasp converts `.js` ↔ `.gs` on push/pull.

## 5. External API Integrations (Part 1: Meta + HubSpot)

### 5.1 Meta (Facebook) Graph API

- **Base URL:** `https://graph.facebook.com/v21.0`
- **Credential:** `META_ACCESS_TOKEN` (Script Property), passed as `access_token` query param or bearer
- **Retry wrapper:** `fetchWithRetry_()` (Code.js:158) — retries 5xx/network errors up to 3× with backoff; 4xx fail immediately.

**Endpoints used:**

| Endpoint | Method | Called by | Purpose |
|---|---|---|---|
| `/{AD_ACCOUNT_ID}/insights` | GET | `fetchDataForDateRange_()` | Daily campaign insights with `time_increment=1` |
| `/{AD_ACCOUNT_ID}/campaigns` | GET | `getCurrentMetaBudgets_()` | Current daily budgets + status |
| `/{CAMPAIGN_ID}` | POST | `applyBudgetChange_()` | Update daily_budget |
| `/{AD_ACCOUNT_ID}/ads` | GET | `syncCampaignMappings_()` | Extract destination URLs for UTM parsing |
| `/{AD_ACCOUNT_ID}/adsets` | GET | `syncCampaignMappings_()` | Find custom conversion IDs via promoted_object |
| `/{CUSTOM_CONVERSION_ID}` | GET | `syncCampaignMappings_()` | Resolve custom conversion ID → event name |

**Fields pulled in insights call:**
`campaign_name, campaign_id, impressions, clicks, spend, reach, actions, frequency, date_start, date_stop`

Pagination handled via `json.paging.next` follow-up fetches.

### 5.2 HubSpot CRM API

- **Base URL:** `https://api.hubapi.com`
- **Credential:** `HUBSPOT_API_KEY` (Script Property), sent as `Authorization: Bearer <key>`
- **Endpoint used:** `POST /crm/v3/objects/contacts/search`

**Filter:** `prequal_decision EQ 'investment_crowdfunding'`.

**Properties pulled:** `prequal_submitted`, `prequal_decision`, `prequal_utm_source`, `prequal_utm_medium`, `prequal_utm_campaign`, `prequal_industry`, `prequal_industry_tier`, `prequal_funding_need`, `prequal_monthly_revenue`, `prequal_pre_approval_amount`, `prequal_business_name`, `prequal_credit_score`, `prequal_rejection_reasons`.

Pagination: offset-based cursor via `json.paging.next.after`.

**Written to:** `hubspot_icps` sheet. Dedup by `hs_contact_id`.

## 6. External API Integrations (Part 2: Anthropic + Slack + GitHub)

### 6.1 Anthropic Claude API

- **Base URL:** `https://api.anthropic.com/v1/messages`
- **Credential:** `ANTHROPIC_API_KEY` (Script Property), sent as `x-api-key` header
- **Model:** Controlled by the `ANTHROPIC_MODEL` constant (Code.js:45). Currently set to `claude-opus-4-7`. All 5 call sites reference the constant.
- **Common headers:** `anthropic-version: 2023-06-01`, `Content-Type: application/json`

**Three call sites:**

| Caller | `max_tokens` | System prompt purpose |
|---|---|---|
| `generateNarrativeForWeek_()` (Code.js:~1557) | 1000 | Weekly Slack narrative in fixed format (OVERALL / SEGMENTS / WATCH / ACTION) |
| `postBudgetProposalToSlack_()` (Code.js:2665) | 350 | Commentary on proposed budget changes (SITUATION / CHANGES / WATCH) |
| `handleChatRequest_()` (Code.js:3643) | 1500 | "Hive Mind" interactive chat with live data context |

**Error handling (chat):** explicit branches for HTTP 401/403 (auth), 429 (rate limit), 400 (invalid/too-long history), 5xx (server), timeouts, DNS errors. Returns `{error: string}` to the client.

### 6.2 Slack

- **Credential:** `SLACK_WEBHOOK_URL` (Script Property) — incoming webhook URL
- **Wrapper:** `postToSlack_(text)` (Code.js:137). Catches exceptions, logs non-200 responses, never throws.

**Where Slack messages are posted from:**

- Daily pipeline completion (`runDailyPipeline`)
- New campaign mappings auto-detected (`syncCampaignMappings_`)
- Unresolvable campaigns (`syncCampaignMappings_`)
- Budget proposals with approve/reject links (`postBudgetProposalToSlack_`)
- Budget execution results (`postExecutionSummaryToSlack_`)
- Budget rejection/expiry (`executeBudgetChanges`)
- Weekly narrative (`postWeeklyNarrativeToSlack_`)
- Spend target change confirmations (`applyTargetDecision_`)

**Link-unfurling defense:** Approval links never directly mutate state. Clicking shows an HTML confirmation page (`showApprovalConfirmationPage_()`, `showTargetApprovalPage_()`) with a button; only the button click calls `applyApprovalDecision_()` / `applyTargetDecision_()`. This prevents Slack's bot from accidentally approving changes when it previews the link.

### 6.3 GitHub API (audit snapshot export)

- **Base URL:** `https://api.github.com`
- **Credential:** `GITHUB_PAT` (Script Property) — fine-grained PAT with Contents: Read/Write on this repo only
- **Wrapper:** `pushSnapshotToGitHub_()` (Code.js:~4067)
- **Repo/branch:** `tylerhoneycomb/marketing-claude-honeycomb` → `audit-snapshots`

**Git Data API flow** (single atomic commit):

1. `GET /repos/{owner}/{repo}/git/ref/heads/audit-snapshots` — check branch exists
2. If 404: `GET /git/ref/heads/main` → `POST /git/refs` to create branch from main
3. `GET /git/commits/{parentSha}` → get base tree SHA
4. For each file: `POST /git/blobs` with UTF-8 content → collect blob SHAs
5. `POST /git/trees` with `base_tree` and new entries
6. `POST /git/commits` with message + tree SHA + parent
7. `PATCH /git/refs/heads/audit-snapshots` to update ref

All failures are logged (HTTP code + first 200 chars of body) and abort the export. No retry logic — **known technical debt**.

## 7. Scheduled Pipelines

All triggers are set up via `createAllTriggers()` and `createBudgetTriggers()` (manual one-time calls). Apps Script time zone is `America/New_York`.

### 7.1 Trigger matrix

| Schedule | Function | Purpose |
|---|---|---|
| Daily, 7 AM | `runDailyPipeline()` (Code.js:1971) | Fetch Meta + HubSpot, rebuild weekly rollup, post daily digest |
| Mondays, 8 AM | `generateWeeklyNarrative()` (Code.js:1303) | Generate narrative for most-recent-completed week, post to Slack |
| Wed + Fri, 6 AM | `runBudgetAnalysis()` (Code.js:2135) | Compute signals, propose budget changes, post Slack approval |
| Thu + Sat, 3 AM | `executeBudgetChanges()` (Code.js:2836) | Apply approved changes to Meta, mark queue rows, post summary |

### 7.2 Daily pipeline (7 AM) — `runDailyPipeline()`

Sequential with 2-3 second sleeps between stages:

1. `fetchMetaAdsData()` — pulls yesterday's campaign insights into `rolling_data`. Skips zero-spend rows. Dedupes by `date||campaign_id`.
2. `fetchHubspotICPs()` — pulls all HubSpot contacts decisioned as `investment_crowdfunding` into `hubspot_icps`. Dedupes by `hs_contact_id`.
3. `buildWeeklyRollup()` — rebuilds `weekly_rollup` from scratch using hybrid v3 attribution.
4. `postDailyDigest()` — Slack message with yesterday + WTD + last 30 days + budget summary.

**Side effects inside the pipeline:**

- `buildWeeklyRollup()` calls `buildCampaignUTMMap_()` which calls `syncCampaignMappings_()` — guarded by `SYNC_LAST_RUN_DATE` to run at most once per day.
- Custom conversion discovery runs inside `syncCampaignMappings_()` and writes new rows to `campaign_mapping` if any are found.

### 7.3 Weekly narrative (Mon 8 AM) — `generateWeeklyNarrative()`

Thin wrapper:

1. Read `weekly_rollup`, build `allWeeks` list.
2. `getMostRecentCompletedWeek_(allWeeks)` — returns the newest week whose end date is before today.
3. Scan `intelligence_log` for existing row via `resolveReportingWeek_()` — if found, **skip** (idempotent guard).
4. Call `generateNarrativeForWeek_(targetWeek, { postToSlack: true, overwrite: false })`.

**Core function: `generateNarrativeForWeek_()` (Code.js:1373)**

- Validates `targetWeek` matches `/^\d{4}-\d{2}-\d{2}$/` (format guard).
- Validates `targetWeek` is a Monday by parsing YYYY-MM-DD component parts (avoids `new Date('2026-03-09')` UTC quirk).
- Aggregates spend / ICPs / conversions from `weekly_rollup` rows for that week.
- Rounds `totalSpend` (2 decimals), `totalICPs` / `totalAttrICPs` (1 decimal) to eliminate IEEE 754 residuals before write.
- Builds `contextBlock` with campaign breakdown, frequency alerts, CPICP spike alerts, zero-ICP warnings.
- Calls Anthropic with `ANTHROPIC_MODEL` (`claude-opus-4-7`), 1000 max tokens.
- On `overwrite: true`: deletes existing rows matching target Monday OR preceding Sunday (covers old pre-fix convention).
- Appends new row.
- Reconciliation check: independently re-reads rollup, sums spend for target week, warns if mismatch > $0.01.
- If `postToSlack: true`: calls `postWeeklyNarrativeToSlack_()`.

### 7.4 Key utility functions

- **`getWeekStart(date)`** (Code.js:92) — **Canonical week function.** Returns Monday as YYYY-MM-DD. The single source of truth for week bucketing. `buildWeeklyRollup` and `generateWeeklyNarrative` both depend on this. Never inline week math elsewhere.
- **`dateToYMD_(val)`** (Code.js:106) — normalizes Date / ISO string / YYYY-MM-DD to YYYY-MM-DD.
- **`resolveReportingWeek_(val)`** (Code.js:1349) — normalizes any `reporting_week` cell value (Date, YYYY-MM-DD, or `Date.toString()` format) to YYYY-MM-DD. Used by idempotency guards.
- **`fetchWithRetry_(url, options, maxRetries)`** (Code.js:158) — retries 5xx/network errors up to 3× with backoff.
- **`validateTokens_()`** (Code.js:59) — throws if any required Script Property is missing. Called at start of every public entry point.

## 8. Budget Automation System

### 8.1 State machine

Four Script Properties drive the approval state:

```
              (no pending)
                   │
                   ▼
       runBudgetAnalysis() runs Wed/Fri 6 AM
                   │
                   ▼
    BUDGET_PENDING_TOKEN = <uuid>
    Slack message posted with approve/reject links
                   │
         ┌─────────┴──────────┐
         ▼                     ▼
  User clicks approve    User clicks reject
         │                     │
         ▼                     ▼
  BUDGET_APPROVED_TOKEN  BUDGET_REJECTED_TOKEN
         │                     │
         ▼                     ▼
   executeBudgetChanges() runs Thu/Sat 3 AM
         │                     │
   Apply Meta budgets     Mark rows rejected
   Mark rows executed     Clear state
   Clear state
```

### 8.2 Signal computation — `computeBudgetSignals_()` (Code.js:2178)

Reads `rolling_data` and `hubspot_icps` for the last `ROLLING_DAYS` (14 days). For each campaign, computes:

- `spend`, `lifetimeConversions` — totals across the window
- `estimatedIcps` — using the same hybrid v3 formula as `buildWeeklyRollup` (**duplicated logic — technical debt**)
- `cpicp` — spend / estimatedIcps (null if zero ICPs)
- `avgFreq` — weighted average frequency
- `icpTrend` — recent 7 days ICPs minus prior 7 days (direction signal)

Returns `{campaignId: {cpicp, avgFreq, estimatedIcps, icpTrend, lifetimeConversions, ...}}`.

### 8.3 Recommendations — `computeRecommendations_()` (Code.js:2367)

**Eligibility gate:** campaigns with `lifetimeConversions < LIFETIME_MIN_CONVERSIONS` (10) are excluded from changes. Their current spend still counts toward portfolio total.

**Direction assignment (per eligible campaign):**

1. `avgFreq >= FREQ_HIGH_THRESHOLD` (3.0) → direction = −1 (reduce, audience saturation)
2. `cpicp == null` (zero ICPs in 14d) → direction = −1 (reduce dead spend)
3. Otherwise → ranked composite:
   - CPICP rank (lower = better) × 0.70
   - ICP trend rank (higher = better) × 0.30
   - Sort ascending; top quartile = +1 (increase unless `avgFreq >= 2.0` → hold); bottom quartile = −1; middle = 0

**Portfolio correction:** if `currentTotal > targetDaily + toleranceDaily`, apply a 1% knockdown to all eligible budgets as a correction pool.

**Change application:**

- Reductions: apply `MAX_CHANGE_PCT` (2%) cut, floor at `CAMPAIGN_DAILY_MIN_CENTS` ($25/day). Hard cap `MAX_REDUCTION_PCT` (4%).
- Increases: distribute freed budget proportionally, capped at `MAX_CHANGE_PCT` per campaign.
- Holds: no change.

Returns an array of changed campaigns with `changeCents`, `proposedDailyBudgetCents`, `reasons[]`, plus meta fields `_currentTotal`, `_proposedTotal`, `_poolWarning`.

### 8.4 Proposal + approval — `runBudgetAnalysis()` → `postBudgetProposalToSlack_()` → web app

1. `runBudgetAnalysis()` (Code.js:2135) orchestrates: fetch current budgets, compute signals, compute recommendations, write to queue, post to Slack.
2. `writeToQueue_()` (Code.js:2619) generates a 16-char hex token, writes one row per recommendation with `status='pending'`, sets `BUDGET_PENDING_TOKEN`.
3. `postBudgetProposalToSlack_()` (Code.js:2665) builds approve/reject URLs (`{WEB_APP_URL}?action=approve&token=<token>`), calls Anthropic for commentary (max 350 tokens), posts formatted Slack message with budget changes, reasons, AI commentary, and both action links.
4. User clicks link → `doGet(e)` (Code.js:3099) validates token → `showApprovalConfirmationPage_()` returns HTML form → user clicks button → `applyApprovalDecision_()` sets `BUDGET_APPROVED_TOKEN` or `BUDGET_REJECTED_TOKEN`.

### 8.5 Execution — `executeBudgetChanges()` (Code.js:2836)

Runs Thu/Sat 3 AM:

1. **Orphan expiry:** walk `budget_queue`, mark any `pending` row with a token different from `BUDGET_PENDING_TOKEN` as `expired`.
2. Check state:
   - If `BUDGET_APPROVED_TOKEN == BUDGET_PENDING_TOKEN`: iterate matching pending rows, call `applyBudgetChange_(campaignId, newBudgetCents)` for each. Sleep 300ms between calls. Mark each row `executed` or `failed`. Post execution summary to Slack.
   - If `BUDGET_REJECTED_TOKEN == BUDGET_PENDING_TOKEN`: mark matching rows as `rejected`, post Slack message.
   - If neither: mark as `expired`.
3. Clear `BUDGET_PENDING_TOKEN` and `BUDGET_APPROVED_TOKEN` properties.

### 8.6 Spend target override (separate mini state machine)

Dashboard can propose a new weekly spend target via `handleDashboardApi_` action `propose_spend_target`. This stages `PENDING_SPEND_TARGET` / `PENDING_SPEND_TOLERANCE` / `SPEND_TARGET_PENDING_TOKEN` and posts a Slack approval link. Approval flow mirrors the budget-change pattern: link → confirmation page → button click → writes `DASHBOARD_TARGET_WEEKLY_SPEND` / `DASHBOARD_WEEKLY_SPEND_TOLERANCE`.

`computeRecommendations_()` reads these overrides via `getTargetWeeklySpend_()` / `getWeeklySpendTolerance_()` so the dashboard can adjust budget goals without code changes.

## 9. Web App & Dashboard

### 9.1 Apps Script Web App entry points

- **`doGet(e)` (Code.js:3099)** — handles all GET requests.
  - Delegates dashboard actions to `handleDashboardApi_(e)`. Returns `null` when `handleDashboardApi_` doesn't handle the action, then falls through to legacy approve/reject handlers.
  - Legacy handlers: `approve`, `reject` (budget), `approve_target`, `reject_target`, `confirm_*` — all with token validation.
- **`doPost(e)` (Code.js:3632)** — routes `action=chat` to `handleChatRequest_()`.

### 9.2 Dashboard API endpoints (via `handleDashboardApi_`)

All return `ContentService.createTextOutput(JSON.stringify(payload))` with MIME type JSON.

| Action | Method | Params | Returns |
|---|---|---|---|
| `rollup` | GET | — | `weekly_rollup` as array of objects via `sheetToObjects_` |
| `daily` | GET | `start`, `end` (YYYY-MM-DD) | Filtered `rolling_data` via `getDailyData_()` |
| `mappings` | GET | — | `campaign_mapping` as array of objects |
| `narrative` | GET | — | Most recent `intelligence_log` row via `getLatestNarrative_()` |
| `summary` | GET | `start`, `end` | Aggregated totals via `getSummary_()` |
| `campaigns` | GET | — | Distinct campaigns + last_active date via `getCampaignList_()` |
| `chat` | POST | `message`, `history` (JSON) | `{reply: string}` or `{error: string}` |
| `run_budget_analysis` | GET | — | Triggers `runBudgetAnalysis()`, returns `{ok: true}` |
| `get_spend_goal` | GET | — | Current target + pending proposal |
| `get_campaign_budgets` | GET | — | Current Meta daily budgets |
| `propose_spend_target` | GET | `target`, `tolerance` | Stages change, sends Slack approval |
| `approve_target` / `reject_target` | GET | `token` | HTML confirmation page |
| `confirm_approve_target` / `confirm_reject_target` | GET | `token` | Applies decision |
| `rolling-latest-date` | GET | — | `{latest_date, total_rows}` from `rolling_data`. Used by `pipeline-health` skill. |
| `health-write` | POST or GET | JSON body `{rows:[…]}` or `rows=<json>` or `check`/`status`/`detail` | Appends to `pipeline_health` tab (auto-created). Header row: `date, check, status, detail, recorded_at`. |
| `daily-check-write` | POST or GET | JSON body `{row:{...}}` or query params (`date`, `pacing_status`, `total_spend`, `total_icps`, `portfolio_cpicp`, `fatigue_flag_count`) | Appends one summary row to `daily_check_log` tab (auto-created). Header row: `date, pacing_status, total_spend, total_icps, portfolio_cpicp, fatigue_flag_count, recorded_at`. |
| `budget-queue-read` | GET | `campaign_id` (optional) | Returns `{pending: [...], count: N}` from `budget_queue`. Each row: `token, created_at, analysis_date, execution_scheduled, campaign_id, campaign_name, current_budget_cents, proposed_budget_cents, change_cents, change_pct, direction (increase/decrease/flat), signal_reasons, status`. Used by `fatigue-monitor` to flag conflicts. |
| `fatigue-write` | POST or GET | JSON body `{rows:[...]}` or `rows=<json>` | Appends per-ad rows to `fatigue_log` tab (auto-created). Header row: `date, ad_id, ad_name, campaign, classification, ctr_baseline, ctr_current, ctr_decline_pct, frequency, cpc_baseline, cpc_current, days_active, baseline_type, budget_conflict, recorded_at`. |
| `creative-intelligence-write` | POST or GET | JSON body `{rows:[...]}` or `rows=<json>` | Appends per-vertical rows to `creative_intelligence_log` tab (auto-created). Used by `creative-intelligence` skill. Header row: `date, vertical, ad_count, median_cpicp, spend_total, ic_total, top_body_variant_id, top_body_text, top_body_cpicp, top_visual_hash, top_visual_style, bottom_decile_count, recorded_at`. |

### 9.3 `buildDashboardContext_()` (Code.js:3836)

Builds a compact text snapshot for the chat LLM. Sections:

1. **Weekly rollup** — most recent 40 rows, tab-separated.
2. **Daily performance (last 30 days)** — per-campaign rows with date, campaign, spend, impressions, clicks, conversions, ic_conversions.
3. **Daily portfolio summary (last 30 days)** — aggregated totals by date.
4. **Campaign mappings** — all rows.
5. **Latest narrative** — from `intelligence_log`.

### 9.4 Chat backend — `handleChatRequest_()` (Code.js:3643)

- Validates `ANTHROPIC_API_KEY`.
- Caps `message` at 4,000 chars; caps `history` at 30 turns (user/assistant only).
- Builds system prompt: "Hive Mind" persona, CPICP definition, hybrid v3 attribution explanation, secondary metric definitions, daily data disclaimer.
- Calls Anthropic with `ANTHROPIC_MODEL` (`claude-opus-4-7`), 1500 max tokens, full context block prepended to user message.
- Error handling: explicit branches for each HTTP error class (see §6.1). Returns friendly, actionable error messages to the client.

### 9.5 Dashboard (`webapp/index.html`)

**Stack:** React 18 + Recharts + Tailwind CSS + Babel standalone (all from CDN). No build step.

**Features:**

- **API URL config modal** — user pastes Apps Script `/exec` URL; saved to `localStorage`. Falls back to mock data if unset.
- **Date range bar** — presets (7/14/30 days, this month/quarter/year) + custom picker. Default: last 30 days.
- **CPICP alert card** — week-over-week trend indicator.
- **ICP summary cards** — total spend, estimated ICPs, overall CPICP, blended/attributed CPICP, attribution rate.
- **Leaderboards** — top 3 / bottom 3 campaigns, sortable by CPICP / ICPs / attribution / CPL / CTR / spend. "Mature only" toggle hides campaigns under 10 lifetime conversions.
- **Metric trend chart** — multi-select metric visualization, per-campaign or portfolio, daily/weekly granularity. Recharts line chart with weighted regression trendlines.
- **Goal tracker** — 7-day and 30-day ICP pace vs target; weekly spend vs $10K target with ±$500 tolerance.
- **Budget controls** — run-analysis button, spend goal editor (two-step Slack approval).
- **Campaign performance table** — sortable per-campaign weekly metrics, "IC-Optimized" and "Paused" badges, click-through to daily breakdown.
- **Narrative panel** — latest `intelligence_log` narrative, markdown-formatted.
- **Mappings table** — campaign_id → utm → conversion event reference.
- **"Hive Mind" chat** — unlocked by clicking the 🐝 logo 5 times. Natural-language interface to campaign data via Claude.

**Chart rendering notes:**

- **Daily granularity x-axis:** `allBuckets` enumerates every date between `rangeStart` and `rangeEnd` inclusive (via `enumerateDateRange()` helper, ~line 144). This ensures days with no data still appear on the axis. Week/month granularity derives buckets from `rollup` since those are comprehensive.
- **Per-campaign line breaks:** Per-campaign `<Line>` components use `connectNulls={false}` so paused campaigns render as line breaks, not straight-line bridges across the gap. Portfolio-mode lines keep `connectNulls={true}` since aggregate totals are continuous. Trendlines also keep `connectNulls={true}` since they're fully populated by design.

**Reference copy:** `webapp/apps-script-api.gs` is a documentation-only subset of `Code.js` showing the web API layer. **Not auto-generated** — maintained by hand. Diverges from `Code.js` in practice; only `Code.js` is the source of truth for deployed behavior.

---

## 10. Deployment, Audit Snapshots, and Technical Debt Index

### 10.1 CI/CD (`.github/workflows/`)

**`deploy-apps-script.yml`**

- Triggers: push to `main` affecting `apps-script/**` or the workflow file; manual dispatch.
- Steps: checkout → install Node 20 → install clasp → write `~/.clasprc.json` from `CLASPRC_JSON` secret → `clasp push -f` → `clasp deploy --deploymentId ${{ secrets.CLASP_DEPLOYMENT_ID }}`.
- **Critical:** the `--deploymentId` flag updates the existing Web App deployment in place. Without it, every run creates a phantom deployment with a new URL while the live URL goes stale.
- Secrets required: `CLASPRC_JSON` (OAuth creds), `CLASP_DEPLOYMENT_ID` (fixed deployment ID).
- Typical runtime: 30-60 seconds from merge to live.

**`deploy-webapp.yml`**

- Triggers: push to `main` affecting `webapp/**` or the workflow file; manual dispatch.
- Steps: checkout → setup Pages → upload `webapp/` as artifact → deploy to GitHub Pages.
- Output URL: `https://tylerhoneycomb.github.io/marketing-claude-honeycomb/`.

**`claude.yml`**

- Triggers: issue comments, PR review comments, issues, PR reviews containing `@claude`.
- Invokes Claude Code agent for automated assistance.
- Permissions: `contents:write`, `pull-requests:write`, `issues:write`, `id-token:write`.

**`agent-pipeline-health.yml`** _(added 2026-05-03)_

- Triggers: `workflow_dispatch` + active cron `0 13 * * *` (9 AM ET / UTC 13:00).
- Steps: checkout → setup Python 3.12 → `pip install requests==2.32.3` → `anthropics/claude-code-action@v1` with a fixed `prompt` instructing it to run the `pipeline-health` skill per `SKILL.md` → "Dump Claude execution log" step that cats `/tmp/claude-execution-output.json` (`if: always()`).
- Secrets required: `ANTHROPIC_API_KEY`, `META_ACCESS_TOKEN`. Optional: `SLACK_WEBHOOK_URL` — if absent, Claude skips the Slack post and only surfaces results in the workflow log.
- Permissions: `contents: read` (skill is read-only) + `id-token: write` (required by `claude-code-action@v1` for OIDC auth at startup).
- Action inputs: `show_full_output: "true"`, `display_report: "true"`, `claude_args: "--permission-mode bypassPermissions"`. The bypass is necessary because the action runs Claude in `permissionMode: "default"` by default and auto-denies every Bash command in CI (no human to click "approve").
- Concurrency group `agent-pipeline-health`.

**`agent-daily-check.yml`** _(added 2026-05-03)_

- Same template as `agent-pipeline-health.yml` (id-token, bypassPermissions, show_full_output, display_report, dump-log step).
- Triggers: `workflow_dispatch` + active cron `30 12 * * *` (8:30 AM ET / UTC 12:30).
- timeout-minutes: 25 (fetch + analyze + 5 Meta API calls).
- Prompt: run `fetch_daily_data.py > /tmp/daily_data.json` → `analyze_daily.py --input /tmp/daily_data.json` → compose sectioned summary (PACING, PORTFOLIO, WINNERS, BLEEDERS, FATIGUE WATCH, LEARNING, STALE).
- Concurrency group `agent-daily-check`.

**`agent-fatigue-monitor.yml`** _(added 2026-05-03)_

- Same template.
- Triggers: `workflow_dispatch` + active cron `30 13 * * 1,4` (Mon + Thu 9:30 AM ET / UTC 13:30) — twice-weekly because fatigue moves slowly and daily would over-query Meta.
- timeout-minutes: 30 (the longest skill: 14-day fetch + creative metadata + Path-B historical query + classification).
- Prompt: run the three scripts in sequence (fetch → baselines → classify) → compose summary grouped by severity, skip healthy ads, prominently surface budget conflicts.
- Concurrency group `agent-fatigue-monitor`.

**`agent-creative-intelligence.yml`** _(added 2026-05-05)_

- Departs from the other agent workflows' template. The other skills run their Python scripts inside `claude-code-action`'s Bash prompt; this skill runs the scripts as ordinary workflow steps BEFORE invoking `claude-code-action`. Reason: the first production run on 2026-05-05 hit `APIConnectionError` on 526/526 categorizer calls when the script ran inside the action's subprocess shell. Fatigue-monitor's Meta calls work fine from the same subprocess context, so it's specifically Anthropic SDK calls that fail — suspected cause is an inherited `ANTHROPIC_BASE_URL` or HTTP-proxy env var from the action that breaks direct SDK connections.
- Pipeline: `pip install requests==2.32.3 anthropic==0.98.1` → `build_creative_dataset.py` (refresh cache + emit dataset) → `categorize_creative.py` (LLM tagging, `continue-on-error: true` so a failure here doesn't kill the brief) → `build_creative_dataset.py` (re-emit with tags) → `claude-code-action@v1` whose prompt only composes the brief from `/tmp/creative_dataset.json`.
- The categorizer constructs its Anthropic client with explicit `base_url="https://api.anthropic.com"` to defeat any stray env-var override (belt-and-suspenders alongside the workflow restructure).
- Triggers: `workflow_dispatch` AND active cron `0 14 * * 1` (Mon 10 AM ET / 9 AM EST). Weekly cadence matches the corpus-aggregation attribution model.
- timeout-minutes: 45 (longest of any skill: ~$5 of Anthropic categorization on first run + 30-day snapshot aggregation + creative cache refresh + image downloads via /adimages resolution).
- Includes a `commit cache updates` step that pushes refreshed `data/creatives/` (creatives.json, images/, categorizations.json) back to main so subsequent runs skip the expensive first-time work.
- Concurrency group `agent-creative-intelligence`.

**`agent-ad-copy-generator.yml`** _(added 2026-05-05)_

- `workflow_dispatch` only — drafts are markdown for human review and never auto-published, so a schedule would just produce drafts nobody reads. Tyler invokes this after the Monday Creative Intelligence brief once he's decided which verticals warrant new drafts.
- Inputs: `vertical` (single-vertical mode if non-blank, else `--all-verticals`), `num_drafts` (default 5), `min_vertical_ads` (default 5), `model` (default `claude-sonnet-4-5`).
- Pipeline: `pip install requests==2.32.3 anthropic==0.98.1` → `build_creative_dataset.py --skip-meta` (re-emits the dataset from the locally-cached creatives.json — no Meta calls; uses the cache from the most recent `agent-creative-intelligence` commit) → `generate_drafts.py` with the dispatch inputs → "Compute status one-liner" step counts written drafts + flagged files → "Commit drafts" step pushes `data/drafts/<date>-<vertical>.md` back to main with the same 4-attempt retry pattern as `daily-data.yml` → status comment to issue #48.
- timeout-minutes: 20 (no Meta calls, ~$0.50-0.80 of Anthropic, fast).
- Permissions: `contents: write` (commit drafts) + `issues: write` (status comment). No `id-token: write` because this workflow doesn't use `claude-code-action` — the script calls Anthropic directly.
- Concurrency group `agent-ad-copy-generator`.

### 10.1.1 Apps Script fallback dispatch (added 2026-05-03)

GitHub Actions cron is best-effort. To make scheduled runs more reliable, Apps Script time-based triggers act as a fallback. The pattern lives in `apps-script/Code.js`:

| Function | Purpose |
|---|---|
| `triggerAgentWorkflow_(filename)` | POSTs to `/repos/.../actions/workflows/<filename>/dispatches` with `{ref: "main"}`. Reads `GITHUB_PAT` from Script Properties. |
| `workflowRanWithinHours_(filename, hours)` | GETs `/repos/.../actions/workflows/<filename>/runs?per_page=10`, returns true if any run within window has status `in_progress`/`queued`/`pending` or conclusion `success`. Failed runs do NOT count (so the fallback retries them). |
| `triggerAgentPipelineHealthIfNeeded` | Daily 12-1 PM ET. 18-hour lookback. |
| `triggerAgentDailyCheckIfNeeded` | Daily 12-1 PM ET. 18-hour lookback. |
| `triggerAgentFatigueMonitorIfNeeded` | Daily 1-2 PM ET, but early-outs unless ISO day-of-week is 1 (Mon) or 4 (Thu). 12-hour lookback. |
| `triggerAgentCreativeIntelligenceIfNeeded` | Daily 1-2 PM ET, but early-outs unless ISO day-of-week is 1 (Mon). 12-hour lookback. Weekly cadence matches the corpus-aggregation attribution model. |
| `testAgentDispatch` | Diagnostic: lists workflows via the API to verify the PAT has the right scope. Run before `createAllTriggers()` on first install. |

Setup is one-time:
1. Run `testAgentDispatch()` from the Apps Script editor — confirms `GITHUB_PAT` has Actions: Read+Write (classic PAT with `repo` works).
2. Run `createAllTriggers()` — installs the four agent-fallback triggers alongside the existing `runDailyPipeline` and `generateWeeklyNarrative` triggers.

Idempotency: `createAllTriggers()` deletes any existing triggers it owns before recreating them, so it's safe to re-run.

### 10.2 Audit snapshots — `exportAuditSnapshot()` (Code.js:~3980)

- Exports 4 sheets as JSON to the `audit-snapshots` branch via the GitHub Git Data API flow (see §6.3).
- `rolling_data` is filtered to the last 90 days; other sheets export fully.
- Writes 5 files: `snapshots/rolling_data.json`, `weekly_rollup.json`, `intelligence_log.json`, `campaign_mapping.json`, `_manifest.json`.
- Each per-sheet file includes `{sheet, exported_at, row_count, total_rows_in_sheet, columns, data}`.
- `hubspot_icps` is **deliberately excluded** (PII-adjacent — business names, contact IDs).
- **Manual trigger only** — no scheduled time trigger yet (recommended next step).

### 10.3 Function reference index (by file location)

Key functions you'll reach for most often:

| Function | Location | Purpose |
|---|---|---|
| `getWeekStart(date)` | Code.js:92 | **Canonical week function** (Monday-based) |
| `dateToYMD_(val)` | Code.js:106 | Normalize any date-like to YYYY-MM-DD |
| `getMostRecentCompletedWeek_` | Code.js:120 | Picks target week for narrative |
| `resolveReportingWeek_` | Code.js:1349 | Normalize any `reporting_week` cell value |
| `fetchWithRetry_` | Code.js:158 | HTTP retry wrapper |
| `validateTokens_` | Code.js:59 | Assert all secrets are set |
| `postToSlack_` | Code.js:137 | Slack webhook wrapper |
| `syncCampaignMappings_` | Code.js:232 | Auto-discover new campaigns + custom conversions |
| `buildCampaignUTMMap_` | Code.js:586 | `{campaignId: utm}` lookup |
| `getICConversionMap_` | Code.js:653 | Identify IC-optimized campaigns |
| `fetchMetaAdsData` | Code.js:713 | Daily Meta pull entry point |
| `collectMetaRows_` | Code.js:841 | Parse Meta insights into rollup rows |
| `fetchHubspotICPs` | Code.js:902 | Daily HubSpot pull |
| `buildWeeklyRollup` | Code.js:1061 | **Core aggregation + hybrid attribution** |
| `generateWeeklyNarrative` | Code.js:1303 | Scheduled narrative entry point |
| `generateNarrativeForWeek_` | Code.js:1373 | Core narrative generator (takes explicit week) |
| `backfillHistoricalNarratives` | Code.js:1649 | One-time data migration utility |
| `postDailyDigest` | Code.js:1784 | Daily Slack summary |
| `postWeeklyNarrativeToSlack_` | Code.js:~1673 | Weekly Slack summary |
| `runBudgetAnalysis` | Code.js:2135 | Scheduled budget-proposal entry point |
| `computeBudgetSignals_` | Code.js:2178 | 14-day rolling signals |
| `computeRecommendations_` | Code.js:2367 | Rules engine |
| `writeToQueue_` | Code.js:2619 | Write pending proposals |
| `postBudgetProposalToSlack_` | Code.js:2665 | Slack proposal with AI commentary |
| `executeBudgetChanges` | Code.js:2836 | Scheduled execution entry point |
| `applyBudgetChange_` | Code.js:2924 | Single Meta budget update |
| `doGet` | Code.js:3099 | Web App GET router |
| `doPost` | Code.js:3632 | Web App POST router (chat) |
| `handleDashboardApi_` | Code.js:3235 | Dashboard action router |
| `handleChatRequest_` | Code.js:3643 | Anthropic chat backend |
| `buildDashboardContext_` | Code.js:3836 | LLM context builder |
| `exportAuditSnapshot` | Code.js:~3980 | GitHub audit export entry point |
| `pushSnapshotToGitHub_` | Code.js:~4067 | GitHub Git Data API push |
| `getTargetWeeklySpend_` | Code.js:4152 | Read runtime spend override |

### 10.4 Technical debt index

Tracked so future contributors can see what's been consciously deferred. Each item has a location and an impact note.

| Issue | Location | Impact |
|---|---|---|
| ~~Claude model hardcoded in 3 places~~ | ~~Code.js~~ | **Resolved 2026-04-22.** Extracted to `ANTHROPIC_MODEL` constant (Code.js:45). All 5 call sites reference the constant. |
| Hybrid attribution math duplicated | Code.js:1166-1176 and 2178-2286 | Risk of drift between `buildWeeklyRollup` and `computeBudgetSignals_` |
| Rules engine is 200+ lines of nested logic | Code.js:2367 | Hard to test; decision table would help |
| Dashboard API inline in `handleDashboardApi_` | Code.js:3235 | Large switch; extract action handlers |
| `SYNC_WARNED_CAMPAIGNS` as pipe-delimited string | Code.js:494 | Fragile if names contain pipes |
| Slack digest duplicates WoW/4wk metric math | Code.js:~1676 | Shares logic with `generateNarrativeForWeek_` |
| `budget_queue` grows unbounded | No archival | Table never cleaned; add 90-day retention |
| No retry on GitHub API errors | `pushSnapshotToGitHub_` | Transient failures abort whole export |
| No rate limiting on chat endpoint | `handleChatRequest_` | Runaway client could burn Anthropic budget |
| History cap at 30 turns (hard) | `handleChatRequest_` | May truncate mid-conversation; consider token-based |
| `campaign_mapping.custom_conversion_id` not format-validated | `syncCampaignMappings_` | Malformed values cause silent IC tracking failure |
| No concurrent-edit lock on `campaign_mapping` | | Manual edits during sync could be overwritten |
| No circuit breaker on API failures | Daily pipeline | Extended outages produce noisy logs but no alerting escalation |
| Reference copy `webapp/apps-script-api.gs` drift | Manual maintenance | Can diverge silently from Code.js |
| Long-lived secrets in plaintext Script Properties | Apps Script | No rotation schedule or expiration warning |
| Meta token stored in two places | Apps Script Properties + GitHub Secrets | After ad-level pipeline added 2026-05-02, rotation must update both `META_ACCESS_TOKEN` locations |
| IC custom conversion ID hardcoded in `benchmarks.json` | `data/config/benchmarks.json` | Ad-level pipeline cannot read `campaign_mapping` (lives in Sheets) so it pins to a single `custom_conversion_id`. Will miss new IC conversions added later. |
| Ad-level fatigue logic duplicates 14-day-window concept from Apps Script budget | `scripts/compute_signals.py` vs `Code.js:computeBudgetSignals_` | Two implementations of "rolling-window-based health signal" can drift |
| ~~`daily-data.yml` cron disabled~~ | ~~`.github/workflows/daily-data.yml`~~ | **Resolved 2026-05-05.** Cron `0 12 * * *` (8 AM ET) is active. Verified through first production runs; the commit step uses a fetch+rebase retry loop to handle main moving during the run (documented in the workflow file). |

### 10.5 Testing & verification

- **`testMetaConnection()`** (Code.js:2016) — Meta API ping
- **`testHubspotConnection()`** (Code.js:2034) — HubSpot API ping
- **`testSlackWebhook()`** (Code.js:2056) — Slack webhook ping
- **`testAnthropicConnection()`** (Code.js:2074) — Anthropic API ping
- **`testBudgetSystem()`** (Code.js:3189) — full budget-system diagnostic
- **`runFullDiagnostic()`** (Code.js:2103) — runs all connection tests

To verify end-to-end after a change:

1. Push to branch → let CI deploy.
2. Open Apps Script editor → run the relevant test function → check execution log.
3. For data pipeline changes: run `exportAuditSnapshot()` and have Claude Code audit the resulting JSON files.
4. For dashboard changes: open the GitHub Pages URL and exercise the affected feature.
5. For budget automation: run `runBudgetAnalysis()` manually, inspect Slack message and `budget_queue` sheet, reject the proposal to avoid real Meta writes.
6. For ad-level pipeline changes: run `python3 scripts/fetch_ad_data.py --dry-run` first (no API calls), then trigger the `daily-data.yml` workflow via `workflow_dispatch` and inspect the committed snapshot.

---

## 11. Ad-Level Agent Pipeline (added 2026-05-02)

A parallel, additive pipeline that gives Claude Code per-ad-level data and pre-computed signals. Independent of the Apps Script pipeline. Runs as a GitHub Action; commits JSON snapshots to the repo.

### 11.1 Data flow

```
GitHub Action (daily-data.yml)
  └─ scripts/run_daily.sh
       ├─ fetch_ad_data.py      # Meta Graph API → data/snapshots/<date>/
       └─ compute_signals.py    # snapshots → data/derived/
  └─ git commit + push
```

### 11.2 Snapshot schema

Each daily directory `data/snapshots/<YYYY-MM-DD>/` contains:

| File | Schema (top-level array of objects unless noted) | Source |
|---|---|---|
| `campaigns.json` | `{campaign_id, campaign_name}` | Derived from union of insights rows |
| `adsets.json` | `{adset_id, adset_name, campaign_id, daily_budget_cents, lifetime_budget_cents, optimization_goal, effective_status, learning_stage_info, issues_info}` | Meta `/act_{id}/adsets` |
| `ads.json` | `{ad_id, ad_name, adset_id, campaign_id, effective_status, creative_id}` | Meta `/act_{id}/ads` |
| `adset_insights.json` | Per-(date, adset) row with `impressions, clicks, spend, reach, frequency, ctr, cpc, cpm, conversions, ic_conversions` | Meta `/insights?level=adset` |
| `ad_insights.json` | Per-(date, ad) row with the same metric set | Meta `/insights?level=ad` |
| `_manifest.json` | `{snapshot_date, exported_at, counts: {...}, files: [...]}` | Written by `fetch_ad_data.py` |

`data/creatives/creatives.json` is a single file accreted across runs. Schema reshaped 2026-05-05 to expose the asset_feed_spec variant arrays the Creative Intelligence skill needs (the previous scalar-only shape was losing ~95% of the copy data per ad):

```json
{
  "updated_at": "<iso>",
  "count": <n>,
  "creatives": [
    {"creative_id", "name", "thumbnail_url", "image_url",
     "effective_object_story_id",
     // Asset-feed variant arrays (raw text preserved end-to-end):
     "bodies": [...], "titles": [...], "descriptions": [...],
     "image_hashes": [...], "cta_types": [...], "link_urls": [...],
     // Backward-compat scalar aliases (index 0 of arrays for asset-
     // feed creatives; legacy fields for static link ads):
     "image_hash", "title", "body", "link_url", "call_to_action_type",
     "first_seen_date", "last_seen_date"}
  ]
}
```

`data/creatives/images/<image_hash>.jpg` — full-size creative images downloaded via `/adimages` resolution (1440px width on average). Cache key is the Meta image_hash. Idempotent download via `lib.meta.download_image` — existing files are skipped. Populated lazily by `build_creative_dataset.py`. The agent-creative-intelligence workflow commits new images back to main so subsequent runs skip the resolve+download.

`data/creatives/categorizations.json` — LLM tags produced by `skills/creative-intelligence/scripts/categorize_creative.py`:

```json
{
  "updated_at": "<iso>",
  "count": <n>,
  "categorizations": {
    "<variant_id>": {
      "kind": "copy",
      "variant_id": "<sha256-prefix-16>",
      "dimension": "body|title|description",
      "text": "...",
      "copy_angle": "owner_story|benefit_led|urgency|social_proof|question|product_feature|community_local",
      "rationale": "...",
      "categorized_at": "<iso>",
      "model": "claude-sonnet-4-5"
    },
    "<image_hash>": {
      "kind": "visual",
      "image_hash": "...",
      "image_path": "data/creatives/images/<hash>.jpg",
      "visual_style": "real_person|product_shot|lifestyle|storefront|graphic|text_heavy",
      "rationale": "...",
      "categorized_at": "<iso>",
      "model": "claude-sonnet-4-5"
    }
  }
}
```

Same file holds both text and image categorizations, namespaced via the `kind` field. Hash-deduped — the boilerplate "MCAs drain your margins…" body that appears in 50+ ads gets categorized once. Atomic incremental writes mean partial-run failures don't lose work.

### 11.3 Derived signals (`data/derived/`)

Computed by `scripts/compute_signals.py` from the most recent N snapshots (default: 7 days, configured via `snapshot_retention.rolling_window_days` in `benchmarks.json`).

**`fatigue_signals.json`** — per-ad fatigue evaluation:

```
{
  "computed_at": "<iso>", "window_days": 7,
  "rows": [
    {
      "ad_id", "ad_name", "adset_id", "adset_name",
      "campaign_id", "campaign_name", "creative_id", "creative_thumbnail_url",
      "days_active", "total_impressions", "total_clicks", "total_spend",
      "ctr_7d_rolling", "frequency_7d", "ctr_slope", "ctr_decline_pct",
      "first_date", "last_date",
      "flags": ["ctr_declining" | "frequency_warning" | "frequency_critical"
                | "below_min_days_active" | "below_min_impressions"
                | "adset_in_learning"],
      "severity": "critical" | "warning" | "ok",
      "actionable": true | false
    }
  ]
}
```

Severity rules:
- `critical` — `frequency_critical` OR (`ctr_declining` AND `frequency_warning`)
- `warning` — single fatigue flag
- `ok` — no flags or filtered by gates
- `actionable: false` is always set if days_active < 3 or impressions < 1,000 or ad set in learning phase

**`winner_bleeder.json`** — per-ad ranking inside its ad set:

```
{
  "computed_at", "window_days",
  "rows": [
    {"adset_id", "ad_id", "ad_name", "spend", "spend_share",
     "ctr", "ctr_vs_adset_avg",
     "label": "winner" | "bleeder" | null}
  ]
}
```

Label rules (gated on impressions ≥ `min_impressions_for_signal`):
- `winner` — `spend_share ≥ winner_spend_share_min` AND `ctr_vs_adset_avg ≥ 1.0`
- `bleeder` — `ctr_vs_adset_avg ≤ bleeder_ctr_vs_adset_avg`

**`summary.json`** — top-line counts for the daily-check skill:

```
{
  "computed_at", "window_days", "snapshot_dates",
  "ad_count", "adset_count", "campaign_count",
  "fatigue_severity_counts": {"critical", "warning", "ok"},
  "actionable_critical": [ad_id, …],
  "actionable_warning": [ad_id, …],
  "winners": [ad_id, …], "bleeders": [ad_id, …],
  "learning_phase_adsets_skipped": <n>
}
```

### 11.4 Configuration

All thresholds live in `data/config/benchmarks.json`. Scripts and skills MUST read from this file rather than hardcoding constants.

Top-level keys (current schema, 2026-05-03):
- `account.{id, name, meta_api_version, timezone}` — Meta account + Graph API version + display timezone
- `exec_endpoint` — Apps Script `/exec` URL skills hit for Sheet read/write
- `slack_webhook_secret_name` — name of the env var skills look up for Slack posting
- `ic_tracking.{custom_conversion_id, event_name, pattern}` — IC tracking constants. The action type is reconstructed in code as `offsite_conversion.custom.<custom_conversion_id>`.
- `pacing.{weekly_spend_target_dollars, pacing_tolerance_pct}` — used by daily-check skill
- `fatigue.*` — CTR decline thresholds (early/fatigued), frequency warnings, CPC inflation, baseline window, min impressions/days active, creative age warning
- `daily_check.*` — winner/bleeder definitions, early-fatigue thresholds for the daily briefing
- `pipeline_health.{token_warning_days, endpoint_timeout_seconds, data_freshness_max_gap_weekdays}` — used by pipeline-health skill
- `campaign_defaults.type` — `prospecting` vs `retargeting` (affects fatigue frequency thresholds)

### 11.5 IC conversion extraction

`fetch_ad_data.py:extract_conversions` mirrors `collectMetaRows_` in `apps-script/Code.js` (line ~1135-1188): for each `actions[]` array, take the first matching lead action type as `conversions`, sum any matches against `offsite_conversion.custom.<ic_tracking.custom_conversion_id>` as `ic_conversions`. This keeps daily ad-level totals reconcilable with the campaign-level `rolling_data` totals.

**Limitation:** Because the ad-level pipeline cannot read `campaign_mapping` (which lives in the Google Sheet), it cannot dynamically discover new IC custom conversion IDs. If marketing adds a second IC conversion in Meta, `benchmarks.json` must be updated by hand. See tech-debt index §10.4.

### 11.6 Skills (`/skills/`)

Skills are self-contained packages: a `SKILL.md` (with YAML frontmatter — `name`, `description`) plus a `scripts/` directory of Python scripts the skill runs via bash. Scripts emit structured JSON; the skill interprets the JSON and chooses what to send to Slack and what to write to the Sheet.

| Skill | Status | Purpose |
|---|---|---|
| `pipeline-health` | shipped 2026-05-03 | Four checks: data freshness, Meta token, IC conversion event, dashboard endpoint. Slack-silent on PASS. |
| `daily-check` | shipped 2026-05-03 | Morning briefing: pacing vs weekly target, portfolio CPICP rankings, top-3 winners + bleeders, early fatigue flags, learning-phase ad sets, stale creatives. Writes to `daily_check_log`. |
| `fatigue-monitor` | shipped 2026-05-03 | Three-script pipeline: 14-day fetch, baseline computation (Path A in-range / B historical-batched / C estimated), classification across 5 severity classes with budget-queue conflict cross-reference. Writes to `fatigue_log`. |
| `creative-intelligence` | shipped 2026-05-05 | Weekly brief on what creative copy and visual patterns are winning. Corpus-level text aggregation (not per-ad asset breakdown — see design rationale in `docs/CREATIVE_INTELLIGENCE_DESIGN.md`). Two-script pipeline: `categorize_creative.py` (Anthropic API, hash-deduped, ~$5 first run) + `build_creative_dataset.py` (snapshot join, image resolution, side-by-side pair finding). Writes to `creative_intelligence_log`. |
| `ad-copy-generator` | shipped 2026-05-05 | Drafts new (body, title, description) ad-copy variants for a target vertical. Reads `/tmp/creative_dataset.json` from the Creative Intelligence pipeline, splits dimensions at median CPICP (winners/losers always distinct even on small pools), forces `draft_ads` tool_use for structured output, runs compliance regex backstop, writes markdown to `data/drafts/<date>-<vertical>.md`. `workflow_dispatch`-only — **never auto-published**; every draft requires human review. |

`compute_signals.py`'s `data/derived/` outputs are an audit trail and historical lookback resource, not the canonical signal source — the skills query Meta live and compute their own canonical signals at run time.

### 11.7 Workflow (`.github/workflows/daily-data.yml`)

- **Trigger:** `workflow_dispatch` + active cron `0 12 * * *` UTC (8 AM ET).
- **Steps:** checkout → setup Python 3.12 → `pip install requests==2.32.3` → `python scripts/fetch_ad_data.py` → `python scripts/compute_signals.py` → commit `data/` and push to the current branch.
- **Secrets:** `META_ACCESS_TOKEN` (GitHub Secret on the repo, separate from the Apps Script Script Property of the same name). `META_AD_ACCOUNT_ID` is also read from env if set, falling back to `account.id` in `benchmarks.json`.
- **Permissions:** `contents: write` (needed to push the daily commit).
- **Concurrency:** group `daily-data`, no cancel — back-to-back runs queue rather than racing on the same files.
- **Inputs:**
  - `snapshot_date` — single-day mode (default: yesterday UTC).
  - `start_date` + `end_date` — backfill range mode (inclusive, idempotent — skips dates with an existing `_manifest.json`).
  - `sleep_between_calls` — min seconds between Meta API calls (default 1.0). Backfill uses exponential backoff to 60s on HTTP 429 / Meta error codes 1, 2, 4, 17, 32, 341, 613, 80000, 80004.

### 11.8 Function index — Python scripts

| Function | Location | Purpose |
|---|---|---|
| `MetaClient` | `scripts/fetch_ad_data.py` | Thin Graph API wrapper with paging + 4-retry exponential backoff |
| `MetaClient.insights(level, fields, date)` | `scripts/fetch_ad_data.py` | Single-day insights pull at `level=adset` or `level=ad` |
| `MetaClient.adsets()` / `ads()` / `creative(id)` | `scripts/fetch_ad_data.py` | Object-graph fetches |
| `extract_conversions` | `scripts/fetch_ad_data.py` | Mirrors `collectMetaRows_` IC + lead extraction |
| `merge_creatives` | `scripts/fetch_ad_data.py` | Accumulates creative metadata; preserves `first_seen_date` |
| `linear_trend_slope` | `scripts/compute_signals.py` | Best-fit slope for CTR-over-days |
| `compute_ad_metrics` | `scripts/compute_signals.py` | Per-ad rolling metrics |
| `evaluate_fatigue` | `scripts/compute_signals.py` | Apply thresholds → flags + severity + actionable |
| `compute_winner_bleeder` | `scripts/compute_signals.py` | Per-adset CTR/spend ranking |
| `expected_data_date` | `skills/pipeline-health/scripts/check_health.py` | Computes the date `rolling_data` should have, accounting for the 7 AM ET pull cutoff |
| `weekday_gap` | `skills/pipeline-health/scripts/check_health.py` | Counts business days missed between latest data and expected date |
| `check_data_freshness` / `check_meta_token` / `check_ic_conversion_event` / `check_dashboard_endpoint` | `skills/pipeline-health/scripts/check_health.py` | Four health checks; each returns `{name, status, detail}` |
| `compute_features(text)` | `scripts/lib/text_features.py` | Deterministic structural features per variant text (char/word/sentence count, opening word, syntactic markers). Pure Python, no LLM call. |
| `variant_id(text)` | `scripts/lib/text_features.py` | Whitespace-collapsed + lowercased SHA-256 prefix (16 hex chars). Stable join key between dataset builder and categorizer. |
| `MetaClient.resolve_image_hashes(hashes)` | `scripts/lib/meta.py` | Resolves `image_hash` values to full-size URLs via `/act_X/adimages?hashes=[...]`. Auto-chunks at 50 hashes per request. Returns `{hash: {url, width, height, ...}}`. |
| `download_image(creative_id_or_hash, url, dest_dir)` | `scripts/lib/meta.py` | Idempotent atomic download to `<dest_dir>/<key>.jpg`. Skips existing non-empty files; one retry on transient errors; logs and returns None on hard failure rather than aborting the caller. |
| `extract_vertical(campaign_name)` | `skills/creative-intelligence/scripts/build_creative_dataset.py` | Pulls vertical slug from `AD-/ICD-/Rev-<vertical>-Q<N>-<YYYY>` patterns (with optional `PAUSED -` prefix and legacy `Wineries / vineyards` fallback). Lowercased human-readable. |
| `aggregate_ad_performance(snapshot_dates)` | `skills/creative-intelligence/scripts/build_creative_dataset.py` | Sums per-ad impressions/spend/IC across the snapshot window. Tracks first/last active date and `days_active`. |
| `build_variant_corpus(ad_to_creative, cache)` | `skills/creative-intelligence/scripts/build_creative_dataset.py` | For each unique variant text, builds `{variant_id, dimension, text, structural, appears_in_ads}`. The corpus index is the spine of variant-level attribution. |
| `aggregate_variant_performance(variants, ad_performance)` | `skills/creative-intelligence/scripts/build_creative_dataset.py` | In-place: sums spend/impressions/IC across each variant's ad list. The corpus-aggregation attribution model in code form. |
| `find_side_by_side_pairs(ad_to_creative, cache, ad_performance)` | `skills/creative-intelligence/scripts/build_creative_dataset.py` | Finds ad pairs sharing an `image_hash` but differing on body text. The "same audience, same image, different copy" comparison. |
| `categorize_text` / `categorize_image` | `skills/creative-intelligence/scripts/categorize_creative.py` | One Anthropic API call per variant. Forced tool_use for structured JSON output; validates `tag` against the COPY_ANGLES / VISUAL_STYLES enum; one retry on transient errors. |
| `store_result(cache, key, entry)` | `skills/creative-intelligence/scripts/categorize_creative.py` | Thread-safe atomic incremental cache write under a `Lock`. Persists every successful categorization immediately so partial failures don't lose work. |
| `getRollingLatestDate_` | `apps-script/Code.js` | `?action=rolling-latest-date` handler — returns latest date in `rolling_data` |
| `handleHealthWrite_` | `apps-script/Code.js` | `?action=health-write` handler (GET or POST) — appends to `pipeline_health` tab, creates tab on first call |
| `handleCreativeIntelligenceWrite_` | `apps-script/Code.js` | `?action=creative-intelligence-write` handler — appends to `creative_intelligence_log` tab, creates tab on first call |

### 11.9 Tabs added by skills

| Tab | Created by | Header row |
|---|---|---|
| `pipeline_health` | `pipeline-health` skill via `?action=health-write` (auto-created in `handleHealthWrite_`) | `date, check, status, detail, recorded_at` |
| `daily_check_log` | `daily-check` skill via `?action=daily-check-write` (auto-created in `handleDailyCheckWrite_`) | `date, pacing_status, total_spend, total_icps, portfolio_cpicp, fatigue_flag_count, recorded_at` |
| `fatigue_log` | `fatigue-monitor` skill via `?action=fatigue-write` (auto-created in `handleFatigueWrite_`) | `date, ad_id, ad_name, campaign, classification, ctr_baseline, ctr_current, ctr_decline_pct, frequency, cpc_baseline, cpc_current, days_active, baseline_type, budget_conflict, recorded_at` |
| `creative_intelligence_log` | `creative-intelligence` skill via `?action=creative-intelligence-write` (auto-created in `handleCreativeIntelligenceWrite_`) | `date, vertical, ad_count, median_cpicp, spend_total, ic_total, top_body_variant_id, top_body_text, top_body_cpicp, top_visual_hash, top_visual_style, bottom_decile_count, recorded_at` |

### 11.10 Shared client (`scripts/lib/meta.py`, added 2026-05-03)

Single Meta Graph API client used by both the snapshot pipeline (`scripts/fetch_ad_data.py`) and skill scripts (`skills/<name>/scripts/`). Contains the `MetaClient` class plus normalization helpers and constants — extracted from `fetch_ad_data.py` so skill scripts don't duplicate the HTTP retry / throttle / paging logic. New skills that need Meta data should `from lib.meta import MetaClient` rather than reimplementing.

| Export | Purpose |
|---|---|
| `MetaClient` | Wrapper with `_request`, `_paginate`, `insights(level, fields, since, until=None, time_increment=1)`, `adsets(filtering=…)`, `ads(filtering=…)`, `creative(id)`. Per-call throttle, exponential backoff to 60s, retries on HTTP 429/5xx and Meta error codes 1, 2, 4, 17, 32, 341, 613, 80000, 80004. |
| `INSIGHTS_FIELDS_CAMPAIGN`, `INSIGHTS_FIELDS_ADSET`, `INSIGHTS_FIELDS_AD` | Field lists per insights level |
| `ADSET_OBJECT_FIELDS`, `AD_OBJECT_FIELDS`, `CREATIVE_FIELDS` | Object-graph field lists |
| `LEAD_ACTION_TYPES` | Standard Meta lead action types (mirrors `collectMetaRows_` in Code.js) |
| `extract_conversions(actions, ic_action_type, lead_action_types)` | Returns `(conversions, ic_conversions)` from a Meta `actions[]` array |
| `normalize_insights_row` / `normalize_adset` / `normalize_ad` / `normalize_creative` | Flatten Meta JSON into the project's row shape |
| `ic_action_type_from_config(config)` | Reconstructs `offsite_conversion.custom.<id>` from `benchmarks.json` |
| `load_config()` | Reads `data/config/benchmarks.json` |
| `yesterday_utc()` | Helper for snapshot pipeline default date |
