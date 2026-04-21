# Technical Reference

_Last updated: 2026-04-21_

This document is the engineering reference for the `marketing-claude-honeycomb` repository. It describes architecture, data model, APIs, deployment, and key implementation details. For a higher-level overview see [STATE_REPORT.md](./STATE_REPORT.md).

> **Maintenance rule:** This document must be updated in the same PR as any change that affects architecture, data model, APIs, deployment, or significant function contracts. See [CLAUDE.md](../CLAUDE.md) for the docs-update rule.

---

## 1. Architecture Overview

The system has three layers: a data/compute layer (Google Apps Script + Google Sheets), a presentation layer (a single-file React dashboard), and integration surfaces (Meta, HubSpot, Slack, Anthropic, GitHub). Google Sheets is the system of record — no separate database.

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
```

**Key properties:**

- **No build step.** The dashboard loads React, Recharts, Tailwind, and Babel from CDNs and uses in-browser JSX transpilation. There's no `package.json` or `npm install`.
- **No separate database.** All state lives in the Google Sheet or in Apps Script's `PropertiesService` (Script Properties).
- **Deployments are git-native.** Merging to `main` triggers GitHub Actions that push Apps Script via `clasp` and publish the dashboard to GitHub Pages. Nobody edits the Apps Script web editor directly.
- **Two execution contexts for the Apps Script layer:** (1) time-based triggers run on Google's schedule, (2) HTTP GET/POST to the published Web App `/exec` URL drives the dashboard and Slack approval links.

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
├── .github/workflows/
│   ├── deploy-apps-script.yml  # Push Code.js via clasp on merge to main
│   ├── deploy-webapp.yml       # Publish dashboard to GitHub Pages on merge to main
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

- **Writer:** `syncCampaignMappings_()` (Code.js:232). Auto-discovery from Meta ads API + manual edits allowed.
- **Discovery flow:** Reads `/ads?fields=creative{url_tags}` to extract utm_campaign; reads `/adsets?fields=promoted_object` to find custom conversion IDs; resolves custom conversion IDs to event names via `/{id}?fields=name`.
- **Read by:** `buildCampaignUTMMap_()` (Code.js:586) returns `{campaignId: utm}` lookup. `getICConversionMap_()` (Code.js:653) returns `{icCampaignIds, customConversionIds}` for IC-attribution matching.

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
| `IC_CONVERSION_EVENT_PATTERN` | `'investment_crowdfunding'` | Custom conversion event name pattern |

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
- **Model:** `claude-sonnet-4-6` — **hardcoded in 3 places** (narrative, budget commentary, chat). Technical debt; should be a single constant.
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
- Calls Anthropic with `claude-sonnet-4-6`, 1000 max tokens.
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
- Calls Anthropic with `claude-sonnet-4-6`, 1500 max tokens, full context block prepended to user message.
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
| Claude model hardcoded in 3 places | Code.js:~1463, ~2775, ~3700 | Model upgrades require 3 edits; should be a constant |
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

