# Technical Reference

_Last updated: 2026-09-10 (**Lead-first rewrite of every Slack-bound message and prompt.** `Code.js`: the daily digest and weekly narrative headline leads / CPL / spend with one secondary IC line, sort campaigns by CPL and key every alert on the new `TARGET_CPL_DOLLARS` / `CPL_*` constants (mirrored from `benchmarks.json`); `computeBudgetSignals_` / `computeRecommendations_` / `postBudgetProposalToSlack_` rank on CPL and lead trend (`computeWeeklyICPPace_` → `computeLeadPace_`, `PUMP_CPICP_CEILING` → `PUMP_CPL_CEILING`, HubSpot read dropped) while the `runBudgetAnalysis` / `executeBudgetChanges` guards stay and now clear the stale `BUDGET_*` tokens and `BUDGET_RANK_TIERS`; `buildBudgetWeeklySummary_(leads, cpl)` splits outcomes by `source` and reads `BUDGET_OPTIMIZER_PAUSED` for its footer; the `*_scaling` branches in `doGet` were moved above the `BUDGET_PENDING_TOKEN` gate that had made every Tuesday approve/reject link dead; strategic notices use status-aware lockout copy (`scalingLockoutStatusLine_`), execution summaries carry `signal_reasons`; the Hive Mind system prompt is lead-first. Skills: every SKILL.md + workflow prompt states the same standard (title says Leads, headline leads/CPL, CPL sorts, IC only as "of which N reached an IC decision"); fatigue-monitor adds leads/CPL to baselines and CPL inflation to the matrix; pipeline-health's `snapshot_volume` reports the lead total and Slack lines carry the check name; portfolio-scaling sums ACTIVE budgets only, buckets `LEADS-*` campaigns into their vertical, gates eligibility on lifetime leads and emits a 12-week portfolio headline; creative-intelligence and portfolio-scaling `claude-code-action` steps now receive `EXEC_SHARED_SECRET`. Wire contracts unchanged — legacy IC-named Sheet keys still sent; `scaling_log` still has no CPL column. Prior: 2026-09-09 **lead-first pivot of the ad-level pipeline, the six skills and the dashboard**, plus three follow-ups in `Code.js`. `ic_tracking` is replaced by a three-tier `conversions` spec read through `FunnelSpec`/`funnel_from_config()`; ranking, classification, reallocation weighting and creative deciles all move from CPICP to CPL. **`runBudgetAnalysis()` and `executeBudgetChanges()` are early-returned** — the optimizer ranked on CPICP, which fired once in the 30 days to 2026-09-08; `executeStrategicChanges()` is deliberately not guarded and stays live on the separate `SCALING_*` tokens. **A shared-secret gate (`PROTECTED_EXEC_ACTIONS` / `execAuthFailure_`) now covers every side-effecting `/exec` action** on both `doGet` and `doPost`, failing closed when `EXEC_SHARED_SECRET` is unset; read-only actions and the token-authenticated Slack approve/reject links are unaffected. Pacing target set to $2,100/week. Prior: 2026-06-23 folded pipeline-health into `daily-data.yml`; 2026-06-10 PAUSED `agent-fatigue-monitor.yml`)_

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
│    Apps Script (apps-script/Code.js, ~6,900 lines)             │
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
│  GitHub Actions: daily-data.yml (daily cron + dispatch)        │
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
- **Ad-level pipeline is autonomous.** `.github/workflows/daily-data.yml` runs daily at ~8:37 AM ET (UTC 12:37) on cron and commits each snapshot directly to main. (Cron minute moved off `:00` on 2026-06-23 — see §11.7 — because GitHub's top-of-hour queue contention was delaying the run 2-5 hours.) Manual `workflow_dispatch` is preserved for backfills via the `start_date` / `end_date` inputs.

## 2. Repository Structure

```
marketing-claude-honeycomb/
├── apps-script/
│   ├── Code.js              # The full intelligence layer (~6,900 lines)
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
├── skills/                  # NEW (2026-05-02) Agent skill definitions
│   ├── daily-check/SKILL.md
│   ├── fatigue-monitor/SKILL.md
│   ├── creative-intelligence/   # NEW (2026-05-05)
│   │   ├── SKILL.md
│   │   ├── references/      # copy_angle + visual_style markdown
│   │   └── scripts/         # build_creative_dataset.py, categorize_creative.py
│   ├── ad-copy-generator/       # NEW (2026-05-05) generate_drafts.py
│   ├── portfolio-scaling/       # NEW (2026-05-08) compute_scaling_profiles.py, compute_reallocation.py
│   └── pipeline-health/         # SKILL.md + scripts/check_health.py, report_health.py
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
│   ├── daily-data.yml          # NEW (2026-05-02) Ad-level data pull (daily cron) + pipeline-health
│   ├── agent-*.yml             # One per skill (see §10.1); only portfolio-scaling is on a cron
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
- **Lead columns vs legacy columns** _(2026-09-10)_: `meta_conversions` (col 9) is the lead count (traced from `rolling_data.Conversions`, the lead priority chain in `collectMetaRows_`) and `cpl` (col 14) is the primary cost metric. Cols 10-13 and 15-21 are the legacy IC / hybrid-ICP figures and their trend columns; they are still written so history stays comparable, but no Slack message or prompt headlines them. **The sheet has no CPL trend columns** — `generateNarrativeForWeek_` derives per-campaign CPL WoW / vs-4-week-average in memory from the earlier rollup rows (`cplTrend`) rather than widening the schema, so every positional read (`r[9]`, `r[14]`, `r[16]`…) stays valid.
- **Blank cells read as `''`, not `null`.** `setValues` with a JS `null` stores a blank, and `getValues()` returns `''` for it; `isBlankCell_()` (Code.js:186) is the guard every nullable column (`cpl`, `cpicp_blended`, trend pcts) must go through before arithmetic or sorting, otherwise `'' - n` coerces blanks to 0 and ranks zero-lead campaigns as "best".

### 3.5 `intelligence_log` — Weekly AI-generated narratives

**7 columns. One row per completed week. Append with overwrite.**

| # | Column | Type | Notes |
|---|---|---|---|
| 0 | generated_at | ISO timestamp | |
| 1 | reporting_week | Date (YYYY-MM-DD) | Must be a Monday; validated in writer |
| 2 | total_spend | Float (2 dec) | Rounded after accumulation to avoid float residuals |
| 3 | total_icps | Float (1 dec) | Rounded after accumulation. **Legacy** hybrid-ICP figure — still written positionally (read back by `getLatestNarrative_` and `buildDashboardContext_`); the week's leads and CPL live in `context_block` (`TOTAL LEADS`, `OVERALL CPL`) |
| 4 | overall_cpicp | String or `'N/A'` | `.toFixed(2)` of spend/icps. **Legacy** — see col 3 |
| 5 | context_block | Text (multi-KB) | Full data context sent to Claude (lead-first since 2026-09-10) |
| 6 | narrative | Text | Claude output (OVERALL / SEGMENTS / WATCH / ACTION on leads + CPL) |

- **Writer:** `generateNarrativeForWeek_()` (Code.js:1373). Scheduled entry point: `generateWeeklyNarrative()` (Code.js:1303).
- **Invariants:**
  - `reporting_week` must be a Monday (asserted by parsing YYYY-MM-DD component parts to avoid UTC timezone quirk).
  - At most one row per week (scheduled wrapper skips if row already exists; manual regeneration uses `overwrite: true`).
- **Backfill utility:** `backfillHistoricalNarratives()` (Code.js:1649) — one-time migration that deletes Sunday-convention rows and regenerates under Monday convention.

### 3.6 `budget_queue` — Pending and executed budget changes

**13 columns. One row per proposed change. Append only.** _(13th column added Session 2 of portfolio-scaling rollout, 2026-05-08)_

| # | Column | Type | Notes |
|---|---|---|---|
| 0 | token | String (16-char hex) | Groups a batch of proposals |
| 1 | created_at | ISO timestamp | |
| 2 | analysis_date | Date | |
| 3 | execution_scheduled | Date | Tomorrow at 3 AM (optimizer); next Wed 3 AM (strategic) |
| 4 | campaign_id | String | |
| 5 | campaign_name | String | |
| 6 | current_budget_cents | Integer | |
| 7 | proposed_budget_cents | Integer | |
| 8 | change_cents | Integer | Can be negative |
| 9 | change_pct | Float | Stored as a percent (e.g. `2.0` for 2%, NOT `0.02`) |
| 10 | signal_reasons | String | Pipe-separated reasons. `"1% portfolio knockdown"` substring identifies knockdown rows for headroom math. |
| 11 | status | Enum | `pending` → `approved` → `executed` / `failed`, or `pending` → `rejected` / `expired` |
| 12 | source | Enum | `optimizer` (default; daily budget optimizer) or `strategic` (weekly portfolio-scaling reallocation). Legacy rows from before column 12 existed are read as `optimizer`. |

- **Writers:** `writeToQueue_(recommendations, source)` (`source` defaults to `'optimizer'`) for the daily optimizer; `handleScalingQueueWrite_(e)` for the Tuesday strategic proposal.
- **Per-row execution helper:** `applyBudgetQueueRows_(qSheet, filterFn)` writes Meta API changes + status transitions. Both `executeBudgetChanges` and `executeStrategicChanges` delegate to it with their own filter callbacks.
- **State machine driven by Script Properties:**
  - Optimizer approval flow: `BUDGET_PENDING_TOKEN`, `BUDGET_APPROVED_TOKEN`, `BUDGET_REJECTED_TOKEN`. Plus identity-audit keys `BUDGET_LAST_APPROVED_BY`, `BUDGET_LAST_APPROVED_AT` (and the parallel `SCALING_*` keys on the strategic side).
  - Optimizer rank smoothing _(added 2026-05-11)_: `BUDGET_RANK_TIERS` — JSON map of `campaign_id → "top" | "middle" | "bottom"` from the prior cycle. Hysteresis on direction: a campaign must be in the same actionable tier for two consecutive cycles before the optimizer applies +1 / -1. JSON-parse-safe (try/except → empty dict on corruption).
  - Strategic: `SCALING_PENDING_TOKEN`, `SCALING_APPROVED_TOKEN`, `SCALING_REJECTED_TOKEN`. On strategic execution, `SCALING_PENDING_LOCKOUT_UNTIL` and `SCALING_PENDING_AFFECTED_IDS` are promoted to `SCALING_LOCKOUT_UNTIL` and `SCALING_AFFECTED_CAMPAIGN_IDS` (the live keys read by the daily optimizer's lockout filter).
- See §7 Budget Automation and §8 Portfolio Scaling.

### 3.7 `scaling_log` — Per-vertical scaling classifications

**16 columns. One row per vertical per Tuesday run. Append only.** _(added 2026-05-08)_

| # | Column | Type |
|---|---|---|
| 0 | date | Date (YYYY-MM-DD) |
| 1 | vertical | String (lowercase slug) |
| 2 | classification | Enum: `scalable` / `stable` / `saturating` / `over-invested` / `insufficient` |
| 3 | confidence | Enum: `confident` / `directional` / `insufficient` |
| 4 | elasticity_r | Float (Pearson, can be negative) |
| 5 | ic_rate | Float (IC / total conversions) |
| 6 | cpicp | Float (dollars) |
| 7 | spend_share_pct | Float |
| 8 | avg_frequency | Float (spend-weighted, last 4 weeks) |
| 9 | frequency_trend | Enum: `rising` / `flat` / `falling` |
| 10 | cpm_trend | Enum: `rising` / `flat` / `falling` |
| 11 | new_audience_needed | String `'TRUE'` / `'FALSE'` |
| 12 | weeks_with_conversions | Integer |
| 13 | contributed_to_pool | String `'TRUE'` / `'FALSE'` |
| 14 | received_from_pool | String `'TRUE'` / `'FALSE'` |
| 15 | recorded_at | ISO timestamp |

- **Writer:** `handleScalingWrite_(e)` (POST `?action=scaling-write`). Auto-creates the tab on first call.
- **Reader:** `getScalingLogRows_(params)` (GET `?action=scaling-log-read&since=YYYY-MM-DD&vertical=<slug>&limit=N`). Returns rows newest-first.
- **Booleans are stored as strings** (`'TRUE'`/`'FALSE'`) for predictable read-back from non-Sheets clients; no existing precedent for boolean cells in Code.js to be inconsistent with.
- **No CPL or lead column** _(known gap, 2026-09-10)_: the only cost columns are `ic_rate` / `cpicp` (cols 5-6). `compose_scaling_log_rows` sends `cpl` and `total_leads` in every row, but `handleScalingWrite_` reads keys by name and has no column for them, so they are dropped. Until the handler and header are extended (Code.js edit + redeploy), `scaling-log-read` must not be used as a cost source — the Tuesday brief computes week-over-week CPL and lead counts from `?action=rollup` (`spend` / `meta_conversions` per vertical for the two most recent `week_start` values) and uses the log only for classification, frequency trend and pool flags. Tracked in §10.4.

## 4. Configuration

### 4.1 Hardcoded constants (Code.js:14-84)

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
| `LIFETIME_MIN_CONVERSIONS` | `10` | Eligibility gate for budget changes — lifetime **leads** per campaign (sum of `rolling_data.Conversions`). Mirrored as `OPTIMIZER_LIFETIME_MIN_LEADS` in `compute_scaling_profiles.py`; change both. |
| `TARGET_CPL_DOLLARS` | `16` | _(2026-09-10)_ Lead-economics target. Mirrors `benchmarks.json:lead_economics.target_cpl_dollars` — dual-source like `SCALING_MAX_WEEKLY_PCT`; change both. Used by the digest, narrative, budget commentary, Hive Mind prompt and the Monday budget block. |
| `CPL_CRITICAL_MULTIPLE` | `2.0` | Mirrors `lead_economics.cpl_critical_multiple`. `TARGET × multiple` = $32 is the "CPL above target" alert line and the pump-up ceiling. |
| `CPL_SPIKE_WARNING_PCT` | `25` | Mirrors `fatigue.cpl_inflation_warning_pct`. Narrative "CPL SPIKE" alert: CPL more than this % above the campaign's 4-week average. |
| `CPL_FLAG_MIN_WEEKLY_LEADS` | `10` | Mirrors `scaling.min_weekly_conversions`. Weekly CPL alerts (above-target, spike) require this many leads in the week. |
| `CPL_FLAG_MIN_DAILY_LEADS` | `5` | Mirrors `daily_check.winner_min_conversions`. The digest's daily high-CPL watch requires this many leads yesterday. |
| `ZERO_LEAD_SPEND_WATCH_DOLLARS` | `50` | Daily spend with zero leads before the digest's watch line flags the campaign (was a hardcoded `> 50`). |
| `PUMP_CPL_CEILING` | `TARGET_CPL_DOLLARS × CPL_CRITICAL_MULTIPLE` (= $32) | Pump-up baseline skips campaigns with `cpl > ceiling` or `cpl === null` (0 leads in the 14-day window). Prevents the underspend pump from ramping bad performers. Knockdown is not affected. Replaced `PUMP_CPICP_CEILING` (175) on 2026-09-10; `WEEKLY_ICP_TARGET` (unused) was deleted at the same time. |
| `BUDGET_OPTIMIZER_PAUSED` | `true` | Status flag only — the pause itself is the early returns in `runBudgetAnalysis` / `executeBudgetChanges`. Drives the Monday budget block footer, the strategic lockout / reject copy (`scalingLockoutStatusLine_`, `scalingRejectStatusLine_`) and the dashboard's `run_budget_analysis` response. Flip it when the guards come out. |
| `ROLLING_DAYS` | `14` | Signal window for budget decisions |
| `FREQ_WATCH_THRESHOLD` | `2.0` | Frequency flag |
| `FREQ_HIGH_THRESHOLD` | `3.0` | Frequency override (reduce) |
| `ANTHROPIC_MODEL` | `'claude-opus-4-7'` | Claude model for all Anthropic API calls (narrative, chat, budget commentary, daily digest, connection test). Change here to upgrade everywhere. |
| `IC_CONVERSION_EVENT_PATTERN` | `'investment crowdfunding'` | Substring match (case-insensitive) against `campaign_mapping.conversion_event`. Matches "Investment Crowdfunding Prequal Decision". Changed from `'investment_crowdfunding'` (underscore) on 2026-04-21 to fix an IC tracking outage that ran 4/15–4/20 — see the discontinuity comment in `Code.js`. |
| `SCALING_MAX_WEEKLY_PCT` | `0.12` | Total \|change_pct\| per campaign per week, summed across optimizer + knockdown + strategic. Hard rail. **Intentionally dual-source with `data/config/benchmarks.json:scaling.max_weekly_total_change_pct`** — Python scripts read JSON, Apps Script reads this constant. Change both if you ever change one. |
| `SCALING_PROFILES_URL` | raw GitHub URL | `data/derived/scaling_profiles.json` on `main`. Read by `loadScalingProfiles_()` for the optimizer's classification tagging. |
| `SCALING_PROFILES_MAX_AGE_DAYS` | `14` | Stale-profile guard. Profiles older than this disable the optimizer's tagging overlay (the 12% cap and lockout still run unconditionally). |

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
| `GITHUB_PAT` | Fine-grained GitHub PAT (Contents R/W) | `exportAuditSnapshot()`, `triggerAgent*IfNeeded()` |
| `EXEC_SHARED_SECRET` | ≥16-char shared secret for side-effecting `/exec` actions (fails closed when unset) | `checkExecSecret_()` — see §9.1 |

**Runtime state (managed by code, not user-set):**

| Key | Purpose |
|---|---|
| `BUDGET_PENDING_TOKEN` | Active optimizer proposal token (one at a time). While the optimizer is paused, every 6 AM / 3 AM trigger run deletes it (plus the two below and `BUDGET_RANK_TIERS`) so a stale Slack approve link cannot post a confirmation and hysteresis restarts clean on re-enable. |
| `BUDGET_APPROVED_TOKEN` | Set when someone approves the optimizer proposal in Slack |
| `BUDGET_REJECTED_TOKEN` | Set when someone rejects in Slack |
| `BUDGET_LAST_RUN_AT`, `BUDGET_LAST_APPROVED_BY`, `BUDGET_LAST_APPROVED_AT` | Audit trail |
| `SCALING_PENDING_TOKEN` | Active strategic-reallocation proposal token (one at a time, separate from optimizer's token) |
| `SCALING_APPROVED_TOKEN`, `SCALING_REJECTED_TOKEN` | Strategic approve/reject state |
| `SCALING_PENDING_LOCKOUT_UNTIL`, `SCALING_PENDING_AFFECTED_IDS` | Lockout metadata stashed at queue-write time; promoted to live keys on successful execution |
| `SCALING_LOCKOUT_UNTIL` | ISO timestamp; daily optimizer skips affected campaigns until this passes |
| `SCALING_AFFECTED_CAMPAIGN_IDS` | Comma-separated list of locked-out campaign IDs |
| `SCALING_LAST_APPROVED_BY`, `SCALING_LAST_APPROVED_AT` | Audit trail |
| `SCALING_PROFILES_CACHE`, `SCALING_PROFILES_CACHED_AT` | In-memory cache of `data/derived/scaling_profiles.json` (1hr TTL before refetch from raw GitHub) |
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
- `access: ANYONE_ANONYMOUS` — `/exec` URL is reachable without a Google login. Read-only actions are open; every side-effecting action (`PROTECTED_EXEC_ACTIONS`) requires the `EXEC_SHARED_SECRET` Script Property, and the Slack approve/reject links authenticate with their own per-proposal token (see §9.1).
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
- **Model:** Controlled by the `ANTHROPIC_MODEL` constant (Code.js:84). Currently set to `claude-opus-4-7`. Every call site references the constant.
- **Common headers:** `anthropic-version: 2023-06-01`, `Content-Type: application/json`

**Four production call sites** (plus `testAnthropicConnection()`), all lead-first since 2026-09-10 — each prompt names leads and CPL as the metrics, states the `TARGET_CPL_DOLLARS` target, and is told that IC prequal decisions are a reported subtype that may only appear as "of which N reached an IC decision" and that ICPs / CPICP are not to be mentioned:

| Caller | `max_tokens` | System prompt purpose |
|---|---|---|
| `generateNarrativeForWeek_()` (Code.js:~1900-1945) | 1000 | Weekly Slack narrative in fixed format (OVERALL / SEGMENTS / WATCH / ACTION). SEGMENTS lines read `- [utm]: [N] leads, $[CPL] CPL` best-CPL-first plus a `- No leads:` line; WATCH flags are spend-without-leads / CPL above target / CPL spike / frequency. On failure inlines the error (HTTP code + body excerpt, exception message, or response-shape note) directly into the Slack fallback string instead of pointing readers at `intelligence_log` (improved 2026-05-11). |
| Commentary inside `postBudgetProposalToSlack_()` (Code.js:~3815-3870) | 800 | Commentary on proposed budget changes (SITUATION / CHANGES / WATCH). Context lists the lead pace, the IC count as "reported only, not a ranking input", and each change with `budgetCplLabel_` + signals. Bumped from 500 → 800 on 2026-05-09 after a CHANGES section truncated mid-word with 11 campaigns to summarize. Unreachable while the optimizer is paused. |
| Commentary inside `postDailyDigest()` (Code.js:~2320-2360) | 200 | Two-sentence "yesterday" verdict on leads / CPL vs target; context carries yesterday, WTD (with pacing vs the weekly target) and 30-day spend / leads / CPL plus the best/worst-CPL campaigns and the watch line. |
| `handleChatRequest_()` (Code.js:~6157) | 1500 | "Hive Mind" interactive chat with live data context. System prompt defines leads and CPL (target and 2× critical line), calls CPICP / attribution-rate / `estimated_icps` "legacy IC-cost columns, historical context only", and orders recommendations on CPL first (with enough leads to trust it), then lead-volume trend, then frequency. |

There is no `aiSummarizeRow_()` — an earlier revision of this table listed one for the portfolio-scaling brief, but that brief is composed by `claude-code-action` in the workflow, not by Code.js.

**Error handling (chat):** explicit branches for HTTP 401/403 (auth), 429 (rate limit), 400 (invalid/too-long history), 5xx (server), timeouts, DNS errors. Returns `{error: string}` to the client.

**Error handling (weekly narrative):** captures HTTP non-200 status code + body excerpt, JSON-parse errors, missing `content[0].text`, and exceptions into a `llmError` variable. When non-null, the fallback narrative reads `[LLM call failed: <error>]` and is included in the Slack post — visible without leaving Slack. The raw context block is also preserved in `intelligence_log.context_block` for post-mortem.

### 6.2 Slack

- **Credential:** `SLACK_WEBHOOK_URL` (Script Property) — incoming webhook URL
- **Wrapper:** `postToSlack_(text)` (Code.js:137). Catches exceptions, logs non-200 responses, never throws.

**Lead-first standard** _(applied 2026-09-10 to every message below and to every skill brief in §11.6)_: the headline of any Slack message is leads and CPL (and spend); titles say Leads; every ranking or ordering is by CPL (zero-lead rows last, or first where the point is "spend without leads"); every alert threshold is a CPL rule (`TARGET_CPL_DOLLARS × CPL_CRITICAL_MULTIPLE`, `CPL_SPIKE_WARNING_PCT`) gated on a minimum lead count (`CPL_FLAG_MIN_WEEKLY_LEADS` / `CPL_FLAG_MIN_DAILY_LEADS`); IC appears at most once, as a secondary `_of which N reached an IC decision_` line, never as a headline, sort key, threshold or title; and no message may describe the daily optimizer as running while `BUDGET_OPTIMIZER_PAUSED` is true.

**Where Slack messages are posted from (all via `postToSlack_` unless noted):**

| Message | Function | Trigger / status | Content (2026-09-10) |
|---|---|---|---|
| `*Honeycomb Leads — <EEE MMM d>*` daily digest | `postDailyDigest` (Code.js:~2187) | Daily 7 AM via `runDailyPipeline` — live | Yesterday / WTD / 30-day rows `Spend \| Leads \| CPL` (+ Freq on yesterday); `_Best CPL → … · Worst → …_` from `ydByCampaign`; WTD `WoW ±% (CPL)` vs prior rollup week and `Pacing → $X spend vs $T target \| N leads (d of 7 days)` with `T = getTargetWeeklySpend_()`; 30-day `(target $16)` and `Run rate → $X/week \| N leads/week`; one `_of which reached an IC decision: yesterday N · WTD N · 30-day N_` line (rolling_data col 12 / rollup col 10); watch line precedence: spend > `ZERO_LEAD_SPEND_WATCH_DOLLARS` with 0 leads → CPL > `PUMP_CPL_CEILING` on ≥ `CPL_FLAG_MIN_DAILY_LEADS` leads → freq > `FREQ_HIGH_THRESHOLD`; 2-sentence LLM commentary; source footer. The HubSpot `ydICPs` read was removed. |
| `*Honeycomb Leads — Week of <week>*` weekly post | `postWeeklyNarrativeToSlack_` (Code.js:~2089) | Mondays 8 AM via `generateWeeklyNarrative` — live | Yesterday / week / last-30-days rows `Spend \| Leads \| CPL`, week row shows `(target $16)` and `WoW ±% (prior $X CPL)`, 30-day row `vs prior 30` on CPL; one `_of which reached an IC decision: yesterday · week · last 30 days_` line; the narrative; then the Budget Activity block. Signature gained trailing `leads, icConversions` args (the older `icps, cpicp, attrICPs, attrRate` are still passed but no longer rendered). |
| Budget Activity block (embedded in the weekly post) | `buildBudgetWeeklySummary_(leads, cpl)` (Code.js:~4184) | Every Monday, non-null when `budget_queue` has rows | `Outcome this week: N leads at $X CPL (target $16)`; outcome counts split by `source` (`Daily optimizer: …` / `Strategic reallocation (portfolio-scaling): …`, with `expired` now counting rows marked `expired` as well as `failed` / `pending`); net changes per campaign tagged `_optimizer_` / `_strategic_`; anticipated spend from live Meta budgets; footer reads `BUDGET_OPTIMIZER_PAUSED` — `_Daily optimizer paused 2026-09-09 (IC ranking retired); strategic reallocation via portfolio-scaling remains active (Tuesdays)._` or `_Next CPL-ranked proposal: tomorrow morning_`. |
| `*Honeycomb Budget Proposal — <date>*` | `postBudgetProposalToSlack_(recs, token, leadPace, replacedPrior)` (Code.js:~3747) | 6 AM `runBudgetAnalysis` — **paused** (early return) | `*Leads — rolling 14 days*` `N leads \| CPL $X \| $S spend` + `_of which N reached an IC decision_`; Budget Pool; Reductions sorted worst-CPL-first (null CPL = 0 leads on top) and Increases best-CPL-first, each row `$cur → $prop/day (±%) \| CPL $X on N leads (14d)` (`budgetCplLabel_`) + `_reasons_`; AI commentary; approve/reject links. `allBudgets` param removed. |
| `*Honeycomb Budget Check — <date>*` no-change notice | `runBudgetAnalysis` (Code.js:~2919) | same — **paused** | `Leads (14d): N leads \| CPL $X \| $S spend` / `All campaigns within normal CPL range — no changes recommended this cycle.` + optional IC line. |
| `*Honeycomb Budget — <proposal date> proposal*` rejected / expired | `executeBudgetChanges` (Code.js:~4050) | 3 AM — **paused** | Status line plus `_N CPL-ranked changes, net ±$X/day_`. |
| `*Honeycomb Budget — Changes Applied (<date>)*` | `postExecutionSummaryToSlack_` (Code.js:~4154) | 3 AM — **paused** | Per campaign `↑/↓ name: $cur → $prop/day` followed by the row's `_signal_reasons_` (CPL rank text) via the `reasons` field `applyBudgetQueueRows_` now returns. |
| `*Honeycomb Budget* ✅ / ❌` approve / reject confirmations | `doGet` `confirm_approve` / `confirm_reject` | Human click — reachable only while a `BUDGET_PENDING_TOKEN` exists (cleared while paused) | Unchanged text. |
| `*Honeycomb Scaling* ✅ Strategic reallocation approved by …` | `doGet` `confirm_approve_scaling` (Code.js:~4509) | Human click on the Tuesday brief's link — live; **reachable since 2026-09-10** (see §9.1) | `Executes at the next 3:00 AM run (N campaigns).` (`countPendingQueueRows_(token, 'strategic')`) + `scalingLockoutStatusLine_(SCALING_PENDING_LOCKOUT_UNTIL)`. |
| `*Honeycomb Scaling* ❌ Strategic reallocation rejected by …` | `doGet` `confirm_reject_scaling` | same | `No reallocation this week.` (HTML page adds `scalingRejectStatusLine_()`). |
| `*Honeycomb Scaling — <date>*` rejected / expired | `executeStrategicChanges` (Code.js:~4860) | Daily 3 AM — live | `⏰ No approval received by the 3:00 AM run. Reallocation expired.` (no longer "Wednesday") + `_N campaigns, net ±$X/day_`. |
| `*Honeycomb Scaling — Strategic Reallocation Applied (<date>)*` | `postStrategicExecutionSummaryToSlack_` (Code.js:~4907) | Daily 3 AM after approval — live | Per campaign `↑/↓ name: $cur → $prop/day — <vertical>: <classification> \| CPL $X on N leads \| <reason>` (the queue row's `signal_reasons`), failures, then `scalingLockoutStatusLine_(lockoutUntil)`. |
| `*Honeycomb Spend Target Change*` proposal + ✅ / ❌ confirmations | `handleDashboardApi_` `propose_spend_target` (Code.js:~5150), `applyTargetDecision_` (Code.js:~6812) | Dashboard action (gated) + human click | Dollar headline unchanged; copy now says the target is used by pacing and the Tuesday strategic reallocation (and by the daily optimizer when re-enabled) and takes effect on the next pacing check. |
| Operational alerts: API failure (`fetchWithRetry_`), permission issue, new campaign mappings (`(optimizes for: <conversion name>)`), renames, unresolvable campaigns (`excluded from the weekly rollup, the daily/weekly lead digests and any budget tooling until mapped`), names reconciled | Code.js:~234, `syncCampaignMappings_` | Daily sync — live | Metric-neutral; two stale phrasings fixed 2026-09-10. |
| `Honeycomb Ads script: connection test successful.` | `testSlackWebhook` (raw `UrlFetchApp` POST, not `postToSlack_`) | Manual diagnostic | Unchanged. |

The skill briefs (daily-check, fatigue-monitor, creative-intelligence, portfolio-scaling, pipeline-health) are composed outside Code.js — see §11.6.

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
| Daily, 7 AM | `runDailyPipeline()` | Fetch Meta + HubSpot, rebuild weekly rollup, post the lead-first daily digest |
| Mondays, 8 AM | `generateWeeklyNarrative()` | Generate narrative for most-recent-completed week, post to Slack (lead-first) |
| Daily, 6 AM | `runBudgetAnalysis()` | **PAUSED 2026-09-09.** Trigger still installed; the function logs, deletes `BUDGET_PENDING_TOKEN` / `BUDGET_APPROVED_TOKEN` / `BUDGET_REJECTED_TOKEN` / `BUDGET_RANK_TIERS`, and returns. The CPL-ranked logic below the guard is ready (2026-09-10); remove the guard and flip `BUDGET_OPTIMIZER_PAUSED` to re-enable. |
| Daily, 3 AM | `executeBudgetChanges()` | **PAUSED 2026-09-09.** Same token cleanup, then returns. When live: apply approved optimizer changes to Meta, mark queue rows, post summary. |
| Daily, 3 AM | `executeStrategicChanges()` _(added 2026-05-08)_ | **Live** (deliberately not guarded). Apply approved strategic-reallocation rows; cheap no-op when no `SCALING_PENDING_TOKEN` exists. On execution, promotes `SCALING_PENDING_LOCKOUT_UNTIL` / `SCALING_PENDING_AFFECTED_IDS` to live keys, posts strategic execution summary to Slack. |
| Daily, 12-1 PM ET | `triggerAgentPipelineHealthIfNeeded()` | Apps Script fallback — re-dispatches `daily-data.yml` (which carries pipeline-health) if no successful run in 18 h. |
| Daily, 12-1 PM ET | `triggerAgentDailyCheckIfNeeded()` | **Early-returned (PAUSED 2026-06-08)** to match the commented-out cron. |
| Daily, 1-2 PM ET | `triggerAgentFatigueMonitorIfNeeded()` | **Early-returned (PAUSED 2026-06-10).** |
| Daily, 1-2 PM ET | `triggerAgentCreativeIntelligenceIfNeeded()` | **Early-returned (PAUSED 2026-06-08).** |
| Daily, 1 PM ET | `triggerAgentPortfolioScalingIfNeeded()` _(added 2026-05-08)_ | Apps Script fallback for `agent-portfolio-scaling.yml`. Early-outs unless ISO weekday is Tuesday; then dispatches `workflow_dispatch` only if no recent successful run in the last 12 hours. |

### 7.2 Daily pipeline (7 AM) — `runDailyPipeline()`

Sequential with 2-3 second sleeps between stages:

1. `fetchMetaAdsData()` — pulls yesterday's campaign insights into `rolling_data`. Skips zero-spend rows. Dedupes by `date||campaign_id`.
2. `fetchHubspotICPs()` — pulls all HubSpot contacts decisioned as `investment_crowdfunding` into `hubspot_icps`. Dedupes by `hs_contact_id`.
3. `buildWeeklyRollup()` — rebuilds `weekly_rollup` from scratch using hybrid v3 attribution.
4. `postDailyDigest()` — the `*Honeycomb Leads — <date>*` Slack message: yesterday + WTD + last 30 days as `Spend | Leads | CPL` rows, pacing vs the live weekly spend target, best/worst CPL yesterday, one secondary IC line, a CPL-keyed watch line and a two-sentence LLM verdict (full shape in §6.2). Reads `rolling_data` and `weekly_rollup` only — the HubSpot `hubspot_icps` read was dropped on 2026-09-10.

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
- Aggregates spend / leads (`r[9]`) / IC conversions (`r[10]`) — and, for the legacy `intelligence_log` columns only, hybrid ICPs — from `weekly_rollup` rows for that week.
- Rounds `totalSpend` (2 decimals), `totalICPs` / `totalAttrICPs` (1 decimal) to eliminate IEEE 754 residuals before write.
- Derives per-campaign CPL history in memory (`cplHistory` / `cplTrend`: prior-week CPL and mean of up to 4 prior non-blank weeks, keyed by campaign name) because the rollup only stores CPICP trend columns.
- Sorts campaigns by CPL ascending with blank-CPL (zero-lead) rows last, biggest spend first (`isBlankCell_` guard).
- Builds `contextBlock` _(lead-first, 2026-09-10)_: `TOTAL LEADS`, `OVERALL CPL: $X (target $16)`, `TOTAL SPEND`, one `IC PREQUAL DECISIONS (Meta custom conversion, reported only)` line; `CAMPAIGN BREAKDOWN (sorted by CPL, best first…)` with `spend | leads | CPL | WoW | vs 4wk avg | freq [| IC decisions N]` per line; then `CPL ABOVE TARGET (>$32 … campaigns with >=10 leads)`, `CPL SPIKE ALERTS (>25% above 4-week average; >=10 leads)`, `FREQUENCY ALERTS (>3)`, `SPEND WITHOUT LEADS`. All thresholds come from the `TARGET_CPL_DOLLARS` / `CPL_*` / `FREQ_HIGH_THRESHOLD` constants (§4.1), not literals.
- System prompt: community-investment-platform persona; "Primary metrics: leads, CPL, spend, frequency"; IC prequal decisions are a reported subtype — never the verdict, never a ranking key; "Never mention ICPs or CPICP"; SEGMENTS format `- [utm]: [N] leads, $[CPL] CPL` + `- No leads: …`.
- Calls Anthropic with `ANTHROPIC_MODEL` (`claude-opus-4-7`), 1000 max tokens.
- On `overwrite: true`: deletes existing rows matching target Monday OR preceding Sunday (covers old pre-fix convention).
- Appends new row.
- Reconciliation check: independently re-reads rollup, sums spend for target week, warns if mismatch > $0.01.
- If `postToSlack: true`: calls `postWeeklyNarrativeToSlack_(…, totalLeads, totalICConversions)` — the `*Honeycomb Leads — Week of …*` post described in §6.2, which in turn embeds `buildBudgetWeeklySummary_(leads, cpl)`.

### 7.4 Key utility functions

- **`getWeekStart(date)`** (Code.js:92) — **Canonical week function.** Returns Monday as YYYY-MM-DD. The single source of truth for week bucketing. `buildWeeklyRollup` and `generateWeeklyNarrative` both depend on this. Never inline week math elsewhere.
- **`dateToYMD_(val)`** (Code.js:106) — normalizes Date / ISO string / YYYY-MM-DD to YYYY-MM-DD.
- **`resolveReportingWeek_(val)`** (Code.js:1349) — normalizes any `reporting_week` cell value (Date, YYYY-MM-DD, or `Date.toString()` format) to YYYY-MM-DD. Used by idempotency guards.
- **`fetchWithRetry_(url, options, maxRetries)`** (Code.js:158) — retries 5xx/network errors up to 3× with backoff.
- **`validateTokens_()`** (Code.js:59) — throws if any required Script Property is missing. Called at start of every public entry point.
- **`isBlankCell_(v)`** (Code.js:186) — true for `null` / `undefined` / `''`. Rollup writers store "no value" as `null`, Sheets hands it back as `''`; use this before sorting or doing arithmetic on any nullable rollup column.

## 8. Budget Automation System

> **Status (2026-09-10): the daily optimizer is PAUSED.** `runBudgetAnalysis()` and `executeBudgetChanges()` early-return (since 2026-09-09) and, since 2026-09-10, clear the `BUDGET_*` tokens and `BUDGET_RANK_TIERS` on every run so nothing stale survives. The logic below the guards was converted from CPICP to leads / CPL on 2026-09-10 and is what this section documents; it has not run in production. `executeStrategicChanges()` and the Tuesday portfolio-scaling flow (§8.7) are live. Re-enable checklist: remove the two early returns, set `BUDGET_OPTIMIZER_PAUSED = false`, redeploy, then run `testBudgetSystem()`.

### 8.1 State machine

Four Script Properties drive the approval state:

```
              (no pending)
                   │
                   ▼
       runBudgetAnalysis() runs daily 6 AM
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
   executeBudgetChanges() runs daily 3 AM
         │                     │
   Apply Meta budgets     Mark rows rejected
   Mark rows executed     Clear state
   Clear state
```

### 8.2 Signal computation — `computeBudgetSignals_()` (Code.js:~2947)

_(Rewritten 2026-09-10.)_ Reads `rolling_data` only for the last `ROLLING_DAYS` (14 days) — the `hubspot_icps` read and the hybrid-attribution pass are gone, which also resolves the "duplicated attribution math" debt. For each campaign, computes:

- `spend` — total across the window; `lifetimeConversions` — all-time leads (col 9) for the eligibility gate
- `leads` — sum of `rolling_data` col 9 (the lead priority chain from `collectMetaRows_`)
- `cpl` — spend / leads (null when the window bought no leads)
- `leadTrend` — recent-half leads minus prior-half leads; null when the prior half had no leads so a just-launched campaign isn't ranked as a runaway improver
- `icConversions` — sum of col 12; **reported only**, never enters a sort or threshold
- `avgFreq` — weighted average frequency

Returns `{campaignId: {spend, leads, cpl, leadTrend, icConversions, avgFreq, lifetimeConversions, …}}`.

**`computeLeadPace_(signals)`** (Code.js:~3034) sums those signals into the portfolio `{leads, spend, cpl, icConversions, label}` used by the proposal headline and the no-change notice (label: `N leads | CPL $X | $S spend`). It replaced `computeWeeklyICPPace_`, which counted `hubspot_icps` rows.

### 8.3 Recommendations — `computeRecommendations_()` (Code.js:~3251)

**Eligibility gate:** campaigns with `lifetimeConversions < LIFETIME_MIN_CONVERSIONS` (10) are excluded from changes. Their current spend still counts toward portfolio total.

**Direction assignment (per eligible campaign):**

1. `avgFreq >= FREQ_HIGH_THRESHOLD` (3.0) → direction = −1 (reduce, audience saturation)
2. `cpl == null` (zero leads in 14d) → direction = −1; reason `0 leads in 14-day window ($S spend)` (the pre-2026-09-10 text said "7-day", which was wrong — the window has always been `ROLLING_DAYS`)
3. Otherwise → ranked composite:
   - CPL rank (lower = better) × 0.70
   - Lead-trend rank (higher = better; null trend takes the median rank) × 0.30
   - Reason text per campaign: `Composite rank i/n: CPL $X | N leads (14d) | ±T lead trend`
   - Sort ascending; tier-based assignment:
     - Top quartile → tier `"top"`. Applies +1 (increase) only if prior cycle's `BUDGET_RANK_TIERS[campaign_id]` was also `"top"` AND `avgFreq < 2.0`. Otherwise holds with a "first cycle" or "freq watch" note.
     - Bottom quartile → tier `"bottom"`. Applies −1 only if prior cycle was also `"bottom"`. Otherwise holds.
     - Middle → tier `"middle"`. Always holds.
   - Each cycle persists the current tier map to `PROPS.BUDGET_RANK_TIERS` (JSON) for next cycle's comparison. Hysteresis added 2026-05-11 to smooth day-to-day rank thrash; first run post-deploy holds everything because the prior-tiers map doesn't exist yet.

**Portfolio correction (symmetric):** the eligible-pool baseline is adjusted toward target before the rank-based redistribution runs, so under- and over-target are handled by the same mechanism:

- If `currentTotal > targetDaily + toleranceDaily` → **1% knockdown** to all eligible budgets. Reason string interpolated from `effectiveTarget + effectiveTolerance`.
- If `currentTotal < targetDaily - toleranceDaily` → **1% pump-up** to eligible budgets with `cpl <= PUMP_CPL_CEILING` ($32 = `TARGET_CPL_DOLLARS × CPL_CRITICAL_MULTIPLE`); campaigns above the ceiling, or with `cpl === null` (0 leads in the 14-day window — effectively worse than any finite CPL), keep their baseline at `currentDailyBudgetCents` and don't participate in the pump _(pump-up added 2026-05-27; ceiling added 2026-05-28 as `PUMP_CPICP_CEILING = 175`, converted to CPL 2026-09-10)_. Reason string interpolated from `effectiveTarget - effectiveTolerance`. Without the pump-up branch, increases were funded only by cuts to bottom-rank campaigns, so cycles with few/small cuts produced proposals that never closed an underspend gap — the portfolio could sit hundreds of dollars under target for weeks. The CPL ceiling prevents the pump from ramping bad performers toward the target even if it delays closing the gap; skipped campaigns can still receive reductions on the same cycle (the ceiling only blocks the upward baseline move, not the rank-based redistribution). The 12% weekly cap downstream is the upper guardrail; there is no per-campaign max-clamp here because no `CAMPAIGN_DAILY_MAX_CENTS` constant exists. The knockdown reason must keep the literal substring `portfolio knockdown` — `compute_scaling_profiles.py` attributes knockdown rows by matching it in `signal_reasons`.
- Otherwise → no baseline adjustment.

Baseline adjustment is independent of hysteresis-held direction — a held-by-hysteresis campaign still receives knockdown or pump-up.

**Change application** (on top of the adjusted baseline `knockdownBudgetCents`):

- Reductions: apply `MAX_CHANGE_PCT` (2%) cut from the adjusted baseline, floor at `CAMPAIGN_DAILY_MIN_CENTS` ($25/day). Hard cap `MAX_REDUCTION_PCT` (4%).
- Increases: distribute freed budget proportionally, capped at `MAX_CHANGE_PCT` per campaign.
- Holds: no change beyond the baseline adjustment.

Note that on a pump-up cycle, `toReduce` campaigns net to roughly `+1% pump − 2% cut = −1%` (softer cuts than the −2% on cycles without pump-up), `toIncrease` lands at `+1% + share of freed budget`, and `toHold` lands at `+1%`. This is the intentional symmetric counterpart to the knockdown cycle where everyone trends down by ~1% on top of rank-based moves.

Returns an array of changed campaigns with `changeCents`, `proposedDailyBudgetCents`, `reasons[]`, plus meta fields `_currentTotal`, `_proposedTotal`, `_poolWarning`.

### 8.4 Proposal + approval — `runBudgetAnalysis()` → `postBudgetProposalToSlack_()` → web app

1. `runBudgetAnalysis()` (Code.js:~2863) orchestrates: fetch current budgets (ACTIVE only), compute signals, `computeLeadPace_`, compute recommendations, write to queue, post to Slack. **Currently returns at the guard** (with token cleanup) before any of that. When there are no recommendations it posts the `*Honeycomb Budget Check*` notice with the lead-pace line instead of a proposal.
2. `writeToQueue_(recommendations, source)` (Code.js:~3678) generates a 16-char hex token, writes one row per recommendation with `status='pending'` and `source='optimizer'` (default), sets `BUDGET_PENDING_TOKEN`.
3. `postBudgetProposalToSlack_(recs, token, leadPace, replacedPrior)` (Code.js:~3747) builds approve/reject URLs (`{WEB_APP_URL}?action=approve&token=<token>`), sorts reductions worst-CPL-first and increases best-CPL-first (`recs` arrives in Meta's campaign order — this is the only ordering the reader sees; null CPL sorts as `Infinity`), calls Anthropic for commentary (`max_tokens: 800`, bumped from 500 on 2026-05-09 after a proposal CHANGES section truncated mid-word) with a lead-first context block, and posts the Slack message: `*Leads — rolling 14 days*` headline + optional `_of which N reached an IC decision_`, Budget Pool, per-campaign rows carrying `budgetCplLabel_(r)` (`CPL $X on N leads (14d)` / `no leads (14d)`), reasons, AI commentary, and both action links. Sub-dollar "reductions" (where rounded display dollars equal the previous value but cents-level changeCents is non-zero) render with `→` and `(held flat)` instead of `↓ (-0.1%)` so the per-row display doesn't contradict the AI commentary. The unused `allBudgets` parameter was removed.
4. User clicks link → `doGet(e)` validates token → `showBudgetConfirmationPage_()` (Code.js:~4177) returns an HTML **form** (replacing the previous click-only `<a>` link) with an optional `<input type="text" name="approver">` field plus the confirm button → user types name (or leaves blank) and submits → handler validates `token === BUDGET_PENDING_TOKEN` again (TOCTOU guard against a token rotation between page render and submit) → `resolveApprover_(e)` (Code.js:~6234) reads + sanitizes the typed name (strips Slack mrkdwn chars `<>*_~|\` + backticks) → sets `BUDGET_APPROVED_TOKEN` or `BUDGET_REJECTED_TOKEN` + writes `BUDGET_LAST_APPROVED_BY` + posts to Slack `Approved by <name>`. Blank submissions log as `"Slack approver (no identity captured)"` since `Session.getActiveUser().getEmail()` returns empty for cross-domain Slack clicks.

### 8.5 Execution — `executeBudgetChanges()` (Code.js:~3958)

Runs daily at 3 AM — **currently returns at the guard** after deleting `BUDGET_PENDING_TOKEN` / `BUDGET_APPROVED_TOKEN` / `BUDGET_REJECTED_TOKEN`. When live:

1. **Orphan expiry:** walk `budget_queue`, mark any `pending` row with a token different from `BUDGET_PENDING_TOKEN` as `expired`.
2. Check state:
   - If `BUDGET_APPROVED_TOKEN == BUDGET_PENDING_TOKEN`: delegate to `applyBudgetQueueRows_(qSheet, filterFn)` (the shared helper also used by `executeStrategicChanges`). It iterates matching pending rows, calls `applyBudgetChange_(campaignId, newBudgetCents)` for each, sleeps 300ms between calls, marks each row `executed` or `failed`, and posts an execution summary to Slack.
   - If `BUDGET_REJECTED_TOKEN == BUDGET_PENDING_TOKEN`: mark matching rows as `rejected`, post Slack message.
   - If neither: mark as `expired`. **Slack header is labeled with the proposal's `created_at` date** (read back from the queue row for the current pending token) rather than the executor's date — so a Sunday-3 AM expiry message says `Honeycomb Budget — Sat May 9 proposal` not `Honeycomb Budget — Sun May 10` (fixed 2026-05-11). The body still notes the executor wall-clock so both dates are visible, and (2026-09-10) appends `_N CPL-ranked changes, net ±$X/day_` summed from the rows just marked.
3. Clear `BUDGET_PENDING_TOKEN` and `BUDGET_APPROVED_TOKEN` properties.

### 8.6 Spend target override (separate mini state machine)

Dashboard can propose a new weekly spend target via `handleDashboardApi_` action `propose_spend_target` (gated by `EXEC_SHARED_SECRET`). This stages `PENDING_SPEND_TARGET` / `PENDING_SPEND_TOLERANCE` / `SPEND_TARGET_PENDING_TOKEN` and posts a Slack approval link. Approval flow mirrors the budget-change pattern: link → confirmation page → button click → writes `DASHBOARD_TARGET_WEEKLY_SPEND` / `DASHBOARD_WEEKLY_SPEND_TOLERANCE`. The Slack and HTML copy (2026-09-10) describes the target as the one used by pacing (the daily digest and `daily-check`) and the Tuesday strategic reallocation, "and by the daily optimizer when it is re-enabled" — it no longer says "the budget optimizer aims at" / "next optimization run".

`computeRecommendations_()` reads these overrides via `getTargetWeeklySpend_()` / `getWeeklySpendTolerance_()` so the dashboard can adjust budget goals without code changes.

#### Source of truth for the weekly spend goal + tolerance

**The runtime source of truth is the Apps Script Script Properties `DASHBOARD_TARGET_WEEKLY_SPEND` / `DASHBOARD_WEEKLY_SPEND_TOLERANCE`, read by every consumer through `/exec?action=get_spend_goal`** (Code.js:4607-4634). That handler returns `target_weekly_spend`, `weekly_spend_tolerance`, and a `source` field (`script_property_override` when an override is set, `hardcoded_default` otherwise).

Consumers and how they read it:

| Consumer | Access path |
|---|---|
| Apps Script optimizer | `getTargetWeeklySpend_()` / `getWeeklySpendTolerance_()` (same PROPS, in-process) |
| `portfolio-scaling/compute_scaling_profiles.py` | `lib.exec_api.get_spend_goal()` → `/exec?action=get_spend_goal` |
| `portfolio-scaling/compute_reallocation.py` | reads `scaling_profiles.json` (populated by the above) |
| `daily-check/analyze_daily.py` | `lib.exec_api.get_spend_goal()` → `/exec?action=get_spend_goal` |
| Webapp dashboard | `fetchAction(apiUrl, 'get_spend_goal')` |

The Apps Script constants `TARGET_WEEKLY_SPEND` (10000) / `WEEKLY_SPEND_TOLERANCE` (500) and `data/config/benchmarks.json:pacing.weekly_spend_target_dollars` are **fallback-only defaults** — the constants back the `get_spend_goal` handler when no override exists; the `benchmarks.json` value is used by `daily-check` only when `/exec` is unreachable (`get_spend_goal` returns `source: "fallback_unreachable"` in that case). `daily-check`'s `pacing_tolerance_pct` is a separate pacing-status sensitivity band, not the optimizer's dollar tolerance, and stays static config.

### 8.7 Portfolio scaling integration _(added 2026-05-08)_

The portfolio-scaling skill (`skills/portfolio-scaling/`) adds a structural overlay on top of the optimizer. Three behaviors are wired into `computeRecommendations_()`; all three degrade gracefully when `data/derived/scaling_profiles.json` is missing or stale.

**Inputs at the start of `computeRecommendations_()`:**

```js
var scalingProfiles = loadScalingProfiles_();  // raw GitHub fetch + 1hr cache
var lockoutSet      = getScalingLockoutSet_();  // {} when SCALING_LOCKOUT_UNTIL absent or past
var prevTue         = previousTuesdayUTC_();    // headroom window start
```

**1. Lockout filter (eligibility loop).** Campaigns whose IDs appear in `SCALING_AFFECTED_CAMPAIGN_IDS` while `SCALING_LOCKOUT_UNTIL` is in the future are excluded from `eligible[]` with a logged reason. The optimizer doesn't propose any change for them.

**2. 12% weekly cap (final pass).** After the 4% reduction cap has applied, each remaining proposal is checked against `getCampaignWeeklyConsumed_(campaignId, prevTue)`:

- Sums `|change_pct| / 100` (sheet stores `change_pct` as a percent value, e.g. `2.0` for 2%) across all `executed` rows in `budget_queue` since previous Tuesday for that campaign. Counts optimizer + knockdown + strategic movements.
- If `consumed + |proposed_pct| > SCALING_MAX_WEEKLY_PCT (0.12)`, the proposal is scaled down to `(SCALING_MAX_WEEKLY_PCT - consumed)` of `currentDailyBudgetCents`. If `consumed >= SCALING_MAX_WEEKLY_PCT` already, the proposal is suppressed (`changeCents = 0`).
- This pass runs **unconditionally** — even if `scalingProfiles` is null. The cap is a hard rail, not a feature gated on the data file.

**3. Classification tagging (informational, gated on profiles).** When `scalingProfiles` is non-null, each eligible campaign gets `c.scalingClassification`, `c.scalingConfidence`, `c.scalingNewAudienceNeeded`, `c.scalingVertical` populated, and a tag string appended to `c.reasons` (e.g. `"breweries: scalable + new-audience-needed"`). No logic change — the tag surfaces in the Slack proposal text only.

**Strategic execution path:**

- **Tuesday AM** — `agent-portfolio-scaling.yml` runs `compute_scaling_profiles.py` + `compute_reallocation.py`, commits derived JSON to main, then `claude-code-action` POSTs the proposal to `/exec?action=scaling-queue-write`.
- **`handleScalingQueueWrite_()`** — generates a token, writes pending rows to `budget_queue` with `source='strategic'`, sets `SCALING_PENDING_TOKEN` + `SCALING_PENDING_LOCKOUT_UNTIL` + `SCALING_PENDING_AFFECTED_IDS`. Returns `approve_url` + `reject_url` for the agent to embed in the Slack brief.
- **Tyler approves** — `doGet` action `approve_scaling` → `showScalingConfirmationPage_()` → `confirm_approve_scaling` → sets `SCALING_APPROVED_TOKEN`, posts `*Honeycomb Scaling* ✅ … Executes at the next 3:00 AM run (N campaigns).` (`countPendingQueueRows_(token, 'strategic')`) plus `scalingLockoutStatusLine_(SCALING_PENDING_LOCKOUT_UNTIL)`. **Until 2026-09-10 this path was unreachable**: `doGet` checked `BUDGET_PENDING_TOKEN` (and `token === pendingToken`) before it reached the `*_scaling` branches, and scaling tokens are minted independently by `handleScalingQueueWrite_`, so every link from the Tuesday brief returned "No pending budget proposal" / "Token mismatch" and every strategic proposal expired at the next 3 AM run. The scaling branches now sit above that gate (§9.1).
- **Next 3 AM run** (the trigger is daily, not Wednesday-only) — `executeStrategicChanges()` runs:
  1. Orphan expiry pass for stale strategic-pending rows.
  2. If `SCALING_APPROVED_TOKEN === SCALING_PENDING_TOKEN`: applies via `applyBudgetQueueRows_(qSheet, filterFn)` where `filterFn` matches `token + status='pending' + source='strategic'`.
  3. Promotes `SCALING_PENDING_LOCKOUT_UNTIL` → `SCALING_LOCKOUT_UNTIL` and `SCALING_PENDING_AFFECTED_IDS` → `SCALING_AFFECTED_CAMPAIGN_IDS`. The daily optimizer's lockout filter (behavior #1 above) consumes these once the optimizer is re-enabled; while it is paused the keys are still written and the Slack copy says the lockout is "recorded for when it resumes".
  4. Posts `postStrategicExecutionSummaryToSlack_(results, lockoutUntil)` — per campaign `↑/↓ name: $cur → $prop/day — <signal_reasons>` (the row's `<vertical>: <classification> | CPL $X on N leads | <reason>` text) + failures + `scalingLockoutStatusLine_(lockoutUntil)`.
  - Rejected / no approval: marks rows `rejected` / `expired` and posts `*Honeycomb Scaling — <date>*` with `⏰ No approval received by the 3:00 AM run. Reallocation expired.` + `_N campaigns, net ±$X/day_`.
- **Lockout expires** at `SCALING_LOCKOUT_UNTIL` (typically next Tuesday 00:00 UTC), unblocking the optimizer's Tuesday morning cycle.

**Shared helper:** `applyBudgetQueueRows_(qSheet, filterFn)` — extracted from `executeBudgetChanges` so both triggers share the per-row Meta API write + status transition + 300ms inter-row sleep. Filter callback decides which rows this run applies. Since 2026-09-10 each result object also carries `reasons` (`signal_reasons`, col 11) so both execution summaries can say why a campaign moved.

**Status-aware copy helpers:** `scalingLockoutStatusLine_(lockoutUntil)` (Code.js:~4662) and `scalingRejectStatusLine_()` (Code.js:~4673) read `BUDGET_OPTIMIZER_PAUSED` and return either the "optimizer locked out until <date>" sentence or the paused variant; every strategic approve / reject / execution message and confirmation page uses them so no message describes the optimizer as running while it is off.

**Hive Mind chat integration:** `handleChatRequest_()` detects scaling-related keywords in the user message (`scaling`, `scalable`, `saturating`, `over-invested`, `elasticity`, `headroom`, `capacity`, `classification`, `structural`, `reallocation`, `audience-needed`). When matched, fetches the latest 30 rows from `getScalingLogRows_()` and appends them to the context block before calling Anthropic. Per directive, this is keyword-gated rather than baked into every chat turn.

## 9. Web App & Dashboard

### 9.1 Apps Script Web App entry points

- **`doGet(e)` (Code.js:~4475)** — handles all GET requests, in this order:
  1. `execAuthFailure_(e, action)` — refuses any `PROTECTED_EXEC_ACTIONS` member without a valid `key` (`checkExecSecret_`, fails closed when `EXEC_SHARED_SECRET` is unset or under 16 chars). Read-only actions and the token-authenticated approve/reject links are never gated.
  2. Dashboard actions via `handleDashboardApi_(e)`; returns `null` for unknown actions and falls through.
  3. **Strategic-reallocation links** — `approve_scaling` / `reject_scaling` → `showScalingConfirmationPage_`, `confirm_approve_scaling` / `confirm_reject_scaling` → token check against `SCALING_PENDING_TOKEN`, property writes, Slack post. These sit **above** the optimizer gate as of 2026-09-10; before that they were unreachable because step 4 ran first and the scaling token never equals `BUDGET_PENDING_TOKEN`.
  4. Optimizer links — read `BUDGET_PENDING_TOKEN`; "No pending budget proposal" / "Token mismatch" pages; `approve` / `reject` → `showBudgetConfirmationPage_`; `confirm_approve` / `confirm_reject`.
  5. Spend-target links — `approve_target` / `reject_target` / `confirm_*_target`.
- **`doPost(e)` (Code.js:~6119)** — same auth gate, then routes `action=chat` to `handleChatRequest_()` and the bulk `*-write` / `scaling-queue-write` JSON handlers.

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
| `run_budget_analysis` | GET (gated) | — | While `BUDGET_OPTIMIZER_PAUSED` returns `{ok: false, message: "Daily optimizer paused 2026-09-09 — …"}` without calling anything; otherwise triggers `runBudgetAnalysis()` and returns `{ok: true}` |
| `get_spend_goal` | GET | — | Current target + pending proposal |
| `get_campaign_budgets` | GET | — | Current Meta daily budgets |
| `propose_spend_target` | GET | `target`, `tolerance` | Stages change, sends Slack approval |
| `approve_target` / `reject_target` | GET | `token` | HTML confirmation page |
| `confirm_approve_target` / `confirm_reject_target` | GET | `token` | Applies decision |
| `rolling-latest-date` | GET | — | `{latest_date, total_rows}` from `rolling_data`. Used by `pipeline-health` skill. |
| `health-write` | POST or GET | JSON body `{rows:[…]}` or `rows=<json>` or `check`/`status`/`detail` | Appends to `pipeline_health` tab (auto-created). Header row: `date, check, status, detail, recorded_at`. |
| `daily-check-write` | POST or GET | JSON body `{row:{...}}` or query params (`date`, `pacing_status`, `total_spend`, `total_icps`, `portfolio_cpicp`, `fatigue_flag_count`) | Appends one summary row to `daily_check_log` tab (auto-created). Header row: `date, pacing_status, total_spend, total_icps, portfolio_cpicp, fatigue_flag_count, recorded_at`. **Reads keys by name and appends only these seven columns** — the `total_leads` / `portfolio_cpl` keys the skill also sends are silently dropped (not blanked); the skill puts lead values under the legacy IC-named keys. |
| `budget-queue-read` | GET | `campaign_id` (optional) | Returns `{pending: [...], count: N}` from `budget_queue`. Each row: `token, created_at, analysis_date, execution_scheduled, campaign_id, campaign_name, current_budget_cents, proposed_budget_cents, change_cents, change_pct, direction (increase/decrease/flat), signal_reasons, status`. Used by `fatigue-monitor` to flag conflicts. |
| `fatigue-write` | POST or GET | JSON body `{rows:[...]}` or `rows=<json>` | Appends per-ad rows to `fatigue_log` tab (auto-created). Header row: `date, ad_id, ad_name, campaign, classification, ctr_baseline, ctr_current, ctr_decline_pct, frequency, cpc_baseline, cpc_current, days_active, baseline_type, budget_conflict, recorded_at`. The lead keys the skill sends additively (`spend_current`, `leads_current`, `cpl_current`, `leads_baseline`, `cpl_baseline`, `cpl_change_pct`) are dropped until the header gains those columns. |
| `creative-intelligence-write` | POST or GET | JSON body `{rows:[...]}` or `rows=<json>` | Appends per-vertical rows to `creative_intelligence_log` tab (auto-created). Used by `creative-intelligence` skill. Header row: `date, vertical, ad_count, median_cpicp, spend_total, ic_total, top_body_variant_id, top_body_text, top_body_cpicp, top_visual_hash, top_visual_style, bottom_decile_count, recorded_at`. Legacy-named, lead-valued: the skill sends `median_cpl` / `lead_total` / `top_body_cpl` (dropped) AND the same numbers under `median_cpicp` / `ic_total` / `top_body_cpicp` (stored). `top_body_*` is the lowest-CPL confident body. |
| `scaling-write` | POST or GET | JSON body `{rows:[...]}` or `rows=<json>` | Appends per-vertical rows to `scaling_log` tab (auto-created, see §3.7). Used by `portfolio-scaling` skill. Stores `ic_rate` / `cpicp`; drops the `cpl` / `total_leads` keys the script also sends. |
| `scaling-queue-read` | GET | `since` (YYYY-MM-DD) optional, `campaign_id` optional, `source` optional (`optimizer` / `strategic`) | Returns `{rows: [...], count: N, since}` from `budget_queue`. Unlike `budget-queue-read` (which filters to `pending` only), this returns **all statuses** so callers can sum executed rows for headroom math. Each row: same fields as `budget-queue-read` plus `source`. Used by `compute_scaling_profiles.py`. |
| `scaling-queue-write` | POST | JSON body `{rows: [{campaign_id, campaign_name, current_daily_cents, proposed_daily_cents, change_cents, change_pct, signal_reasons}, ...], lockout_until: "ISO", affected_campaign_ids: [...]}` | Writes pending strategic rows to `budget_queue` with `source='strategic'` and a fresh token. Stashes lockout metadata under `SCALING_PENDING_*` properties. Returns `{ok, token, written, approve_url, reject_url, lockout_until}`. Called by the Tuesday agent workflow. |
| `scaling-log-read` | GET | `since` (YYYY-MM-DD) optional, `vertical` (slug) optional, `limit` (default 200, max 1000) optional | Returns `{rows: [...], count: N}` from `scaling_log`, newest-first. Used by Hive Mind chat for scaling-keyword queries. |

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
- Builds system prompt _(lead-first since 2026-09-10)_: "Hive Mind" persona for a community investment platform; `PRIMARY METRICS — LEADS and CPL` with the `TARGET_CPL_DOLLARS` target and the `CPL_CRITICAL_MULTIPLE` critical line ("only trust a CPL backed by a reasonable lead count; rank campaigns by CPL, never by IC figures"); secondary metrics list IC conversions as a reported subtype ("mention as 'of which N reached an IC decision'; never rank or recommend on them") and CPICP / blended CPICP / attribution rate / `estimated_icps` as legacy IC-cost columns for historical context only; frequency threshold from `FREQ_HIGH_THRESHOLD`; daily-data and scaling-log disclaimers; "base recommendations on CPL first, then lead-volume trend, then frequency". The "quick Slack message" brevity rule is retained.
- Calls Anthropic with `ANTHROPIC_MODEL` (`claude-opus-4-7`), 1500 max tokens, full context block prepended to user message.
- Error handling: explicit branches for each HTTP error class (see §6.1). Returns friendly, actionable error messages to the client.

### 9.5 Dashboard (`webapp/index.html`)

**Stack:** React 18 + Recharts + Tailwind CSS + Babel standalone (all from CDN). No build step.

**Features:**

- **API URL config modal** — user pastes Apps Script `/exec` URL; saved to `localStorage`. Falls back to mock data if unset.
- **Date range bar** — presets (7/14/30 days, this month/quarter/year) + custom picker. Default: last 30 days.
- **View tabs** — `Leads` (default) and `Investment Crowdfunding`. Added 2026-09-09; IC is a reported subtype and no longer steers the primary view.
- **Lead summary cards** (Leads tab) — spend, leads, CPL, CTR.
- **CPICP alert card** (IC tab) — week-over-week trend indicator.
- **ICP summary cards** (IC tab) — total spend, estimated ICPs, overall CPICP, blended/attributed CPICP, attribution rate.
- **Leaderboards** — top 3 / bottom 3 campaigns, defaulting to CPL and sortable by CPL / CPICP / ICPs / attribution / CTR / spend. Row subtitles show lead counts. "Mature only" toggle hides campaigns under 10 lifetime conversions.
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

**`agent-pipeline-health.yml`** _(added 2026-05-03, **retired 2026-06-23 — merged into `daily-data.yml`**)_

- This was the v1 autonomous-agent workflow and established the template the other agents copied (`id-token: write`, `--permission-mode bypassPermissions`, `show_full_output`, `display_report`, the `if: always()` dump-log step). It ran daily on cron `0 13 * * *` and invoked `claude-code-action@v1` with a fixed prompt to run the `pipeline-health` skill.
- **Why retired:** `check_health.py` is fully deterministic — it runs the checks (four then, five now), writes the `pipeline_health` Sheet rows, and prints JSON. The LLM's only job was to reformat that JSON into a Slack string and `curl` it on WARN/FAIL — work that needs no model. Running `claude-code-action` daily for it was a per-day Anthropic spend, an extra scheduled workflow, and a duplicate checkout + Python setup one hour after `daily-data`.
- **Replacement:** two steps appended to the `daily-data.yml` job (see §11.7): `check_health.py` (unchanged) → `report_health.py` (new — terminal summary + Slack alert on WARN/FAIL + the issue-#48 one-liner to `/tmp/health_status.txt`). The check runs right after the morning data pull instead of via a separate 9 AM workflow. No LLM, no `id-token`/OIDC, no separate cron. The Apps Script fallback `triggerAgentPipelineHealthIfNeeded` was repointed to dispatch `daily-data.yml`.

**`agent-daily-check.yml`** _(added 2026-05-03)_

- Same template established by the (now-retired) `agent-pipeline-health.yml`: `id-token`, bypassPermissions, show_full_output, display_report, dump-log step.
- Triggers: `workflow_dispatch` only. The cron `30 12 * * *` (8:30 AM ET / UTC 12:30) has been **commented out since 2026-06-08** (Slack briefing paused at Tyler's request); the Apps Script fallback early-returns to match.
- timeout-minutes: 25 (fetch + analyze + 5 Meta API calls).
- Prompt _(lead-first 2026-09-10; it had still said "one line per campaign with IC conversions")_: run `fetch_daily_data.py > /tmp/daily_data.json` → `analyze_daily.py --input /tmp/daily_data.json` → compose the `📊 Daily Lead Check — <date>` summary: TOTALS line (`N leads · $X CPL · $Y spend · P prequal decisions (of which M reached an IC decision)`, parenthetical dropped at 0), PACING, PORTFOLIO in JSON order (best CPL first, `· IC n` only when > 0, `0 leads` explicit), WINNERS ranked by CPL (`$CPL, N leads`), BLEEDERS rendered by `reason`, FATIGUE WATCH, LEARNING, STALE. Status one-liner: `pacing=… spend=$… leads=… cpl=$… winners=… bleeders=… fatigue_flags=…` (optional trailing `ic=`).
- Concurrency group `agent-daily-check`.

**`agent-fatigue-monitor.yml`** _(added 2026-05-03)_

- Same template. The `claude-code-action` step receives `EXEC_SHARED_SECRET` for the `fatigue-write` POST.
- Triggers: `workflow_dispatch` only. The cron `30 13 * * 1,4` (Mon + Thu 9:30 AM ET / UTC 13:30 — twice-weekly because fatigue moves slowly and daily would over-query Meta) has been **commented out since 2026-06-10**; the Apps Script fallback early-returns to match.
- timeout-minutes: 30 (the longest skill: 14-day fetch + creative metadata + Path-B historical query + classification).
- Prompt _(lead-first 2026-09-10)_: run the three scripts in sequence (fetch → baselines → classify) → headline `🔥 Fatigue Monitor — <date> — <non_healthy> ads at risk · $<spend_7d> / <leads_7d> leads (CPL $<cpl_7d>) last 7d [· N budget conflicts]` → non-healthy ads in the pre-sorted `classifications` order (severity → conflicts → zero-lead spend → CPL desc; do not re-sort), lead line first (`Leads 7d: N (CPL $a → $b, ↑x%) | spend $s`, `· vs $<stats.target_cpl_dollars> target` when above), then CTR / freq / CPC diagnostics, days active + headline, `of which N reached an IC decision` only when `ic_current > 0`, `⚠️ budget_conflict` verbatim. All-healthy condition is `stats.non_healthy == 0` (the list still contains healthy ads) and posts a single lead-first line. Only the headline + non-healthy section go to Slack; the Sheet-log trailer, by-classification counts and any `sheet_write` WARN stay in the log.
- Concurrency group `agent-fatigue-monitor`.

**`agent-creative-intelligence.yml`** _(added 2026-05-05, validated 2026-05-05)_

- Departs from the other agent workflows' template — runs Python scripts as ordinary workflow steps BEFORE invoking `claude-code-action`, AND commits cache changes BEFORE the action runs too. Two distinct production-run findings forced this architecture:
  - **Run 1 (2026-05-05 morning)** — categorizer hit `APIConnectionError` on 526/526 calls when running inside the action's Bash subprocess. Fatigue-monitor's Meta calls work fine from the same subprocess context, so it's specifically Anthropic SDK calls that fail. Suspected cause: subprocess inheritance of an `ANTHROPIC_BASE_URL` or HTTP-proxy env var the action sets for its own runtime. Fix: move scripts to ordinary workflow steps + add explicit `base_url="https://api.anthropic.com"` belt-and-suspenders in the categorizer.
  - **Run 2 (2026-05-05 afternoon)** — categorize succeeded (with rate-limit issues — see below) but the cache `git push` failed all 4 retries with `Invalid username or token. Password authentication is not supported.` The persisted http extraheader credentials from `actions/checkout@v4` survive through Python script steps but get stripped or invalidated AFTER `claude-code-action@v1` runs. Daily-data.yml has no claude-code-action and commits successfully; this workflow had `Commit cache updates` AFTER the action and was getting auth-rejected. Fix: move `Commit cache updates` to BEFORE `claude-code-action`. Bonus: if Claude's brief composition fails for any reason, the $5 of categorization is preserved on main.
- Pipeline (final order): `pip install requests==2.32.3 anthropic==0.98.1` → `build_creative_dataset.py` (refresh cache + emit dataset) → `categorize_creative.py` (LLM tagging, `continue-on-error: true` so a failure here doesn't kill the brief) → `build_creative_dataset.py` (re-emit with tags) → **`Commit cache updates`** (pushes `data/creatives/` to main with `fetch+rebase+push` retry while credentials still valid) → `claude-code-action@v1` (brief composition only, reads `/tmp/creative_dataset.json`) → `Dump Claude execution log` → `Post status to tracking issue` (combines `/tmp/agent_status.txt` from Claude with `/tmp/cache_commit_status.txt` from the commit step).
- **Prompt caching:** the categorizer wraps its system message (≈5000 tokens of voice guide + compliance rules + definitions + enums) in `cache_control: {"type": "ephemeral"}`. Anthropic caches it after the first call and bills subsequent reads at ~10% of the normal rate. Production run 3 (2026-05-05 evening) confirmed: ~99% categorize success rate (vs 82% before caching), ~$1-2 cost (vs ~$5), well under the 30k tokens/min rate limit.
- The categorizer constructs its Anthropic client with explicit `base_url="https://api.anthropic.com"` to defeat any stray env-var override (belt-and-suspenders alongside the workflow restructure).
- Triggers: `workflow_dispatch` only. The cron `0 14 * * 1` (Mon 10 AM ET / 9 AM EST; weekly cadence matches the corpus-aggregation attribution model) has been **commented out since 2026-06-08**; the Apps Script fallback early-returns to match.
- Prompt _(lead-first 2026-09-10 — the lead pivot commit had not touched this YAML, so it still instructed `(4 ads, 18 IC, $42 CPICP)` examples and `≥25 IC` confidence floors while SKILL.md said leads)_: cite `ad_count`, `total_leads`, `cpl`; `cpl=null` → "no leads yet"; confidence from `benchmarks.json:creative_intelligence` (≥10 ads + ≥100 leads confident, ≥5 + ≥40 directional; only `confident` reaches Slack); first Slack line `🎯 Creative Intelligence — <until> — <N> leads at $<median CPL> median CPL across <M> ads (<since>–<until>)`; per-vertical findings CPL-ascending; side-by-sides compared on `ad_a_cpl` vs `ad_b_cpl`; at most one `of which N reached an IC decision` secondary line per finding and never IC / CPICP as headline, sort key, threshold or retire reason. The `claude-code-action` step now receives `EXEC_SHARED_SECRET` and the prompt sends it as `key` on the `creative-intelligence-write` POST (it had no secret at all after the 2026-09-09 gate, so the Sheet write was refused while the Slack post still shipped); the payload keeps the legacy `median_cpicp` / `ic_total` / `top_body_cpicp` keys carrying lead values plus the lead-named keys additively.
- timeout-minutes: 45 (longest of any skill: Anthropic categorization on first-ever run + 30-day snapshot aggregation + creative cache refresh + image downloads via /adimages resolution).
- Concurrency group `agent-creative-intelligence`. Validated end-to-end on 2026-05-05: cache_commit=ok, confident=4 portfolio findings, sheet_rows=15, github-actions[bot] commit `ea115069` landed on main with 525-entry categorizations.json.

**`agent-creative-preview.yml`** _(added 2026-05-05)_

- $0 alternative path. Same checkout + Meta call + cache-commit mechanics as `agent-creative-intelligence.yml`, but skips `categorize_creative.py` and `claude-code-action`. Runs `build_creative_dataset.py` (Meta only, free) → `preview_dataset.py` (pure Python, free) → commits the resulting Markdown brief to `data/previews/<date>.md`.
- Reason it exists: lets an operator validate the cache-commit path and the deterministic signal (raw text + numbers + structural patterns + side-by-side pairs) without spending Anthropic dollars. Used on 2026-05-05 to verify the auth fix in PR #68 worked before re-dispatching the LLM workflow.
- Triggers: `workflow_dispatch` only.
- Permissions: `contents: write` (commit) + `issues: write` (status comment). No `id-token: write` because no claude-code-action.
- timeout-minutes: 25.

**`agent-ad-copy-generator.yml`** _(added 2026-05-05, validated 2026-05-06)_

- `workflow_dispatch` only — drafts are markdown for human review and never auto-published, so a schedule would just produce drafts nobody reads. Tyler invokes this after the Monday Creative Intelligence brief once he's decided which verticals warrant new drafts.
- Inputs: `vertical` (single-vertical mode if non-blank, else `--all-verticals`), `num_drafts` (default 5), `min_vertical_ads` (default 5), `model` (default `claude-sonnet-4-5`).
- Pipeline: `pip install requests==2.32.3 anthropic==0.98.1` → `build_creative_dataset.py --skip-meta` (re-emits the dataset from the locally-cached creatives.json — no Meta calls; uses the cache from the most recent `agent-creative-intelligence` commit) → `generate_drafts.py` with the dispatch inputs → "Compute status one-liner" step counts written drafts + flagged files → "Commit drafts" step pushes `data/drafts/<date>-<vertical>.md` back to main with the `fetch+rebase+push` retry pattern → status comment to issue #48.
- timeout-minutes: 20 (no Meta calls, ~$0.10-0.80 of Anthropic, fast). Validated 2026-05-06: vertical=breweries produced 1 markdown file at `data/drafts/2026-05-06-breweries.md` with 5 drafts (1 flagged by the compliance regex backstop on "no personal guarantee" — a false positive the human reviewer adjudicates).
- Permissions: `contents: write` (commit drafts) + `issues: write` (status comment). No `id-token: write` because this workflow doesn't use `claude-code-action` — the script calls Anthropic directly.
- Concurrency group `agent-ad-copy-generator`.

**`agent-portfolio-scaling.yml`** _(added 2026-05-08)_ — the only agent workflow still on a schedule.

- Triggers: `workflow_dispatch` + active cron `43 13 * * 2` (Tuesdays ~9:43 AM ET; minute moved off `:30` on 2026-06-23). Apps Script fallback `triggerAgentPortfolioScalingIfNeeded` (Tuesdays ~1 PM ET) is live.
- Pipeline: `compute_scaling_profiles.py` → `compute_reallocation.py --write-log` (both receive `EXEC_SHARED_SECRET` for `get_spend_goal` / `scaling-write`) → commit `data/derived/scaling_profiles.json` + `reallocation.json` to main BEFORE the action → `claude-code-action` composes the brief → dump log → status comment. Since 2026-09-10 the action step also receives `EXEC_SHARED_SECRET`; the prompt sends it as `key` in the `scaling-queue-write` body and, on `{"error":"unauthorized"}`, posts the brief without approval links prefixed `⚠️ registration refused (EXEC_SHARED_SECRET missing)` rather than inventing URLs. `signal_reasons` is `<vertical>: <classification> | CPL $<cpl> on <total_leads> leads | <reason>` and must never contain the substring `portfolio knockdown`.
- Prompt _(lead-first 2026-09-10)_: title `*Honeycomb Scaling — <date>*`; open with `Portfolio (12w): N leads · $S spend · CPL $X · median vertical CPL $Y` from `portfolio.total_leads / total_spend / cpl / median_cpl`; scaling labels CPL-ascending within class with `cpl / total_conversions / total_spend` per vertical, `directional` tagged, `insufficient` skipped unless `active_campaign_count > 0` (then a `too new to classify — CPL $X on N leads (k weeks, j active campaigns)` one-liner), `CPL —` for null CPL (never substitute `cpicp`); strategic reallocation framed in lead terms with headroom per campaign, "active portfolio" totals (`pool.portfolio_current_daily_cents` is ACTIVE-only — do not add `paused_total_daily_cents`), `paused_saturating_verticals` and `increase_skip_reason` printed when relevant; audience actions lead with the `CPL $X on N leads (12w)` diagnosis; last week's evaluation only when `scaling-queue-read` shows executed strategic rows, with CPL / lead movement computed from `?action=rollup` (the `scaling_log` sheet has no CPL column) and `scaling-log-read` used only for classification / frequency / pool flags; IC exactly once as `_of which N reached an IC decision_` from `portfolio.total_ic_conversions`, and `ic_rate` / `cpicp` / `median_cpicp` / `median_ic_rate` / per-vertical IC counts never printed. Diagnostic-only path (empty pool) still carries the portfolio headline and the ACTIVE-but-insufficient verticals. Status one-liner opens `leads=<portfolio.total_leads> cpl=$<portfolio.cpl>`.
- Data fixes shipped with the pass (`compute_scaling_profiles.py`): `VERTICAL_RE` accepts the `LEADS-` prefix so `LEADS-Broad-Q3-2026` → `broad` and `LEADS-IFW-Broad-Q3-2026` → `ifw-broad` (before, the only two delivering campaigns fell through to one-campaign "verticals" with < 6 weeks of history and were skipped as `insufficient`); `lifetime_leads_per_campaign` (sum of `meta_conversions`) replaces `lifetime_ic_per_campaign` for `optimizer_eligible`, mirroring `LIFETIME_MIN_CONVERSIONS`; `portfolio.current_total_daily_cents`, the tolerance headroom and `optimizer_cycles_this_week` count ACTIVE campaigns only (the paused-inclusive total read ~$24k/week against a $2,100 target and scaled every increase to zero with `knockdown_risk` every week); verticals are emitted CPL-ascending; the portfolio block gains `total_leads / total_spend / cpl / total_ic_conversions / active_campaign_count / paused_total_daily_cents`; per-vertical `active_campaign_count`. `compute_reallocation.py`: proposal rows carry `cpl / total_leads / vertical_spend` (the mis-indented `cpicp` key was removed), `pool_skip_reasons` explains an empty side of the pool, audience diagnoses open with `CPL $X on N leads (12w)`, and `scaling_log` rows also send `total_leads` (dropped by the handler for now).
- timeout-minutes: 25. Concurrency group `agent-portfolio-scaling`.

### 10.1.0.5 Architectural pattern: scripts before claude-code-action _(established 2026-05-05)_

Two production-run findings established a recommended pattern for any new agent skill that involves either (a) heavy outbound HTTPS to non-Anthropic services or (b) committing artifacts back to main:

1. **Run Python scripts as ordinary workflow steps**, not inside `claude-code-action`'s Bash prompt. The action's subprocess shell appears to inherit env vars (suspected `ANTHROPIC_BASE_URL` or HTTP-proxy) that break the Anthropic SDK's direct connections from subprocesses. Verified: 526/526 APIConnectionError when scripts run inside the action's prompt; 0/526 when they run as separate workflow steps.
2. **Commit any cache/artifact changes BEFORE invoking claude-code-action**. The action strips or invalidates the http extraheader credentials that `actions/checkout@v4` persists. A `git push` AFTER the action fails with `Password authentication is not supported`; the same push BEFORE the action succeeds. Daily-data.yml works because it has no claude-code-action.

The daily-check and fatigue-monitor skills predate this finding. They run scripts inside the action's prompt and don't commit cache. They work fine because they don't trigger either failure mode (no Anthropic SDK subprocess calls; no commit-back). New skills with either dependency should follow the Creative Intelligence pattern. (pipeline-health was a third such skill until 2026-06-23, when it was made fully deterministic and merged into `daily-data.yml`, dropping `claude-code-action` entirely.)

### 10.1.1 Apps Script fallback dispatch (added 2026-05-03)

GitHub Actions cron is best-effort. To make scheduled runs more reliable, Apps Script time-based triggers act as a fallback. The pattern lives in `apps-script/Code.js`:

| Function | Purpose |
|---|---|
| `triggerAgentWorkflow_(filename)` | POSTs to `/repos/.../actions/workflows/<filename>/dispatches` with `{ref: "main"}`. Reads `GITHUB_PAT` from Script Properties. |
| `workflowRanWithinHours_(filename, hours)` | GETs `/repos/.../actions/workflows/<filename>/runs?per_page=10`, returns true if any run within window has status `in_progress`/`queued`/`pending` or conclusion `success`. Failed runs do NOT count (so the fallback retries them). |
| `triggerAgentPipelineHealthIfNeeded` | Daily 12-1 PM ET. 18-hour lookback. As of 2026-06-23 targets `daily-data.yml` (pipeline-health was merged into that workflow); function name kept so the installed trigger binding stays valid. Doubles as the daily-data fallback, which previously had none. |
| `triggerAgentDailyCheckIfNeeded` | Daily 12-1 PM ET. 18-hour lookback. **Early-returned since 2026-06-08** (PAUSED) — trigger stays installed, so unpausing needs only the guard removed. |
| `triggerAgentFatigueMonitorIfNeeded` | Daily 1-2 PM ET, but early-outs unless ISO day-of-week is 1 (Mon) or 4 (Thu). 12-hour lookback. **Early-returned since 2026-06-10** (PAUSED). |
| `triggerAgentCreativeIntelligenceIfNeeded` | Daily 1-2 PM ET, but early-outs unless ISO day-of-week is 1 (Mon). 12-hour lookback. Weekly cadence matches the corpus-aggregation attribution model. **Early-returned since 2026-06-08** (PAUSED). |
| `triggerAgentPortfolioScalingIfNeeded` | Daily ~1 PM ET, but early-outs unless ISO day-of-week is 2 (Tue). 12-hour lookback. Live. |
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
| `isBlankCell_` | Code.js:186 | `null` / `undefined` / `''` guard for nullable rollup cells (added 2026-09-10) |
| `generateWeeklyNarrative` | Code.js:~1645 | Scheduled narrative entry point |
| `generateNarrativeForWeek_` | Code.js:~1715 | Core narrative generator (takes explicit week). Lead-first context block + prompt; in-memory `cplTrend`; CPL sort; CPL-above-target / spike / frequency / spend-without-leads alerts. |
| `backfillHistoricalNarratives` | Code.js:~2060 | One-time data migration utility |
| `postWeeklyNarrativeToSlack_` | Code.js:~2089 | `*Honeycomb Leads — Week of …*` post. Trailing `leads, icConversions` args; WoW and vs-prior-30 on CPL; one IC line; embeds `buildBudgetWeeklySummary_(leads, cpl)`. |
| `postDailyDigest` | Code.js:~2187 | `*Honeycomb Leads — <date>*` digest: `Spend \| Leads \| CPL` rows, pacing vs target, best/worst CPL, one IC line, CPL-keyed watch line, lead-first LLM commentary. |
| `runBudgetAnalysis` | Code.js:~2863 | Scheduled budget-proposal entry point. **PAUSED** — early return that also deletes `BUDGET_*` tokens + `BUDGET_RANK_TIERS`. |
| `computeBudgetSignals_` | Code.js:~2947 | 14-day rolling signals per campaign: `spend, leads, cpl, leadTrend, icConversions (reported), avgFreq, lifetimeConversions`. `rolling_data` only — no HubSpot read. |
| `computeLeadPace_` | Code.js:~3034 | Portfolio `{leads, spend, cpl, icConversions, label}` summed from the signals; replaced `computeWeeklyICPPace_`. |
| `loadScalingProfiles_` | Code.js:~3064 | Fetch scaling_profiles.json from raw GitHub with Script Property cache (1hr TTL); returns `null` when stale or unavailable |
| `getScalingLockoutSet_` | Code.js:~3114 | `{campaign_id: true}` map of currently-locked-out campaigns (empty when lockout absent or expired) |
| `previousTuesdayUTC_` | Code.js:~3135 | UTC Tuesday strictly before now; mirrors Python `previous_tuesday` so headroom windows align |
| `getCampaignWeeklyConsumed_` | Code.js:~3149 | Sum `\|change_pct\|/100` across `executed` rows since previous Tuesday for one campaign |
| `getVerticalClassification_` | Code.js:~3186 | Reads a vertical's `classification / confidence / new_audience_needed` (+ `elasticity_r`, `spend_share_pct`, legacy `cpicp`) from the profiles for the optimizer tag |
| `computeRecommendations_` | Code.js:~3251 | Rules engine. Composite rank 70% CPL / 30% lead trend with hysteresis via `PROPS.BUDGET_RANK_TIERS` (added 2026-05-11); `0 leads in 14-day window` forced decrease; pump-up skips `cpl > PUMP_CPL_CEILING`. |
| `writeToQueue_` | Code.js:~3678 | Write pending proposals. `source` parameter defaults to `'optimizer'`. |
| `budgetCplLabel_` | Code.js:~3740 | `CPL $X on N leads (14d)` / `no leads (14d)` snippet shared by the proposal rows and the LLM context |
| `postBudgetProposalToSlack_` | Code.js:~3747 | `(recs, token, leadPace, replacedPrior)`. Lead-pace headline + optional IC line; reductions worst-CPL-first, increases best-CPL-first; AI commentary. Sub-dollar "reductions" render with `→ (held flat)` instead of `↓ (-0.1%)`. |
| `executeBudgetChanges` | Code.js:~3958 | Scheduled execution entry point. **PAUSED** (early return + token cleanup). Expiry Slack header labeled with proposal date, not executor date; expiry line adds count + net $/day. |
| `applyBudgetQueueRows_` | Code.js:~4085 | Shared per-row apply loop. Both `executeBudgetChanges` and `executeStrategicChanges` delegate here with their own filter callback. Results carry `name, current, proposed, reasons, success`. |
| `applyBudgetChange_` | Code.js:~4130 | Single Meta budget update |
| `postExecutionSummaryToSlack_` | Code.js:~4154 | `*Honeycomb Budget — Changes Applied*` with each row's `_signal_reasons_` |
| `buildBudgetWeeklySummary_` | Code.js:~4184 | `(leads, cpl)`. Monday Budget Activity block: outcome line, per-source counts (`expired` includes rows marked `expired`), net changes tagged by source, anticipated spend, `BUDGET_OPTIMIZER_PAUSED` footer. |
| `checkExecSecret_` / `execAuthFailure_` | Code.js:~4444 / ~4462 | Shared-secret gate for `PROTECTED_EXEC_ACTIONS` (fails closed) |
| `doGet` | Code.js:~4475 | Web App GET router. Order: auth gate → dashboard actions → `*_scaling` links → `BUDGET_PENDING_TOKEN` gate + optimizer links → target links (scaling moved above the gate 2026-09-10). |
| `showBudgetConfirmationPage_` | Code.js:~4598 | HTML form with optional approver text input + confirm button. Form submits via GET. |
| `scalingLockoutStatusLine_` / `scalingRejectStatusLine_` | Code.js:~4662 / ~4673 | Status-aware lockout / reject sentences driven by `BUDGET_OPTIMIZER_PAUSED` |
| `countPendingQueueRows_` | Code.js:~4681 | Pending `budget_queue` rows for a token (optional `source` filter) — the "N campaigns" in the scaling approval message |
| `showScalingConfirmationPage_` | Code.js:~4696 | Two-step approval HTML page for strategic reallocations |
| `executeStrategicChanges` | Code.js:~4804 | Strategic-reallocation execution entry point (daily 3 AM trigger, no-ops without pending strategic token). Live. |
| `postStrategicExecutionSummaryToSlack_` | Code.js:~4907 | `*Honeycomb Scaling — Strategic Reallocation Applied*` with per-row `signal_reasons` + status-aware lockout line |
| `handleDashboardApi_` | Code.js:~4985 | Dashboard action router (`run_budget_analysis` short-circuits while paused) |
| `exportAuditSnapshot` | Code.js:~5100 | GitHub audit export entry point |
| `pushSnapshotToGitHub_` | Code.js:~5180 | GitHub Git Data API push |
| `getRollingLatestDate_` | Code.js:~5285 | `?action=rolling-latest-date` |
| `handleHealthWrite_` / `handleDailyCheckWrite_` / `getBudgetQueuePending_` / `handleFatigueWrite_` / `handleCreativeIntelligenceWrite_` | Code.js:~5321 / ~5392 / ~5449 / ~5497 / ~5573 | Skill Sheet endpoints (§9.2). All read payload keys by name and drop unknown keys. |
| `handleScalingQueueWrite_` | Code.js:~5657 | POST /exec?action=scaling-queue-write — writes strategic proposal rows + token + lockout metadata |
| `handleScalingWrite_` | Code.js:~5749 | POST /exec?action=scaling-write — appends per-vertical scaling_log rows (`ic_rate` / `cpicp`; no CPL column) |
| `getScalingQueueRows_` | Code.js:~5825 | GET /exec?action=scaling-queue-read — all-status budget_queue rows since date, optional source/campaign_id filter |
| `getScalingLogRows_` | Code.js:~5890 | GET /exec?action=scaling-log-read — scaling_log rows newest-first, optional since/vertical filter |
| `doPost` | Code.js:~6119 | Web App POST router (chat + bulk JSON writes) |
| `handleChatRequest_` | Code.js:~6157 | Anthropic chat backend (lead-first system prompt) |
| `buildDashboardContext_` | Code.js:~6384 | LLM context builder (labels `intelligence_log` cols 3-4 as legacy IC columns) |
| `getTargetWeeklySpend_` | Code.js:~6702 | Read runtime spend override |
| `resolveApprover_` | Code.js:~6714 | URL-param `approver` (typed on confirmation page) > session email > "Slack approver" fallback. Strips Slack mrkdwn characters from typed input. Used by all three approval flows (budget, scaling, target). |
| `showTargetApprovalPage_` / `applyTargetDecision_` | Code.js:~6744 / ~6812 | Spend-target approval page + decision (copy references pacing + Tuesday reallocation) |
| `triggerAgentPortfolioScalingIfNeeded` | Code.js:~2687 | Apps Script fallback dispatch for `agent-portfolio-scaling.yml` (Tuesdays at ~1 PM ET). The other four `triggerAgent*IfNeeded` are at ~2590-2660. |

### 10.4 Technical debt index

Tracked so future contributors can see what's been consciously deferred. Each item has a location and an impact note.

| Issue | Location | Impact |
|---|---|---|
| `apps-script/` has no `.claspignore` | `apps-script/.clasp.json` | Added 2026-09-09. `skipSubdirectories: false` and no ignore file means `clasp push` uploads **every** `.js` under `apps-script/` into the live Apps Script project. A test or helper file placed there using `require()`/`module` would break the deployed script. The exec-auth test was moved to `scripts/tests/` for this reason. A `.claspignore` was not added because the deploy pipeline is unexercised since 2026-06-23 and a wrong pattern could exclude `Code.js` itself; fix alongside the next verified deploy. |
| Optimizer paused, awaiting sign-off to re-enable | `Code.js: runBudgetAnalysis` ~2863, `executeBudgetChanges` ~3958, `BUDGET_OPTIMIZER_PAUSED` | Added 2026-09-09; **ranking converted 2026-09-10.** The original blocker — `computeRecommendations_` ranked on CPICP with `PUMP_CPICP_CEILING` — is fixed: signals and rank are leads / CPL / lead trend, the pump ceiling is `PUMP_CPL_CEILING`, the proposal composer is lead-first. Both guards remain until Tyler signs off; they now also clear the `BUDGET_*` tokens and `BUDGET_RANK_TIERS` each run so re-enable starts clean. Re-enable = remove the two `return`s, set `BUDGET_OPTIMIZER_PAUSED = false`, redeploy, run `testBudgetSystem()`. Until then the account has no daily budget optimization, only the weekly strategic reallocation. |
| Sheet write handlers still use IC-named payload keys; unknown keys are dropped | `Code.js: handleDailyCheckWrite_` ~5392, `handleFatigueWrite_` ~5497, `handleCreativeIntelligenceWrite_` ~5573, `handleScalingWrite_` ~5749 | Added 2026-09-09, corrected 2026-09-10. The handlers read `total_icps`, `portfolio_cpicp`, `cpicp`, `ic_rate`, `median_cpicp`, `ic_total` and `top_body_cpicp` by name and append a fixed column list; a recognised key missing from the payload is written blank/0, and **a key the handler has no column for is silently dropped** (earlier wording said "written as blanks", which is wrong — nothing is written). The skills are lead-based, so they send lead values under the legacy names and add `total_leads` / `portfolio_cpl` / `cpl` / `median_cpl` / `lead_total` / `top_body_cpl` / the fatigue `*_current` / `*_baseline` keys additively, ready for the rename. Renaming requires the matching `Code.js` edit, a header migration on four existing tabs, and a redeploy. Each SKILL.md carries a note at the write-contract section. |
| `scaling_log` stores only `ic_rate` / `cpicp` — no CPL, no lead count | `Code.js: handleScalingWrite_` ~5749, `getScalingLogRows_` ~5890; `compute_reallocation.compose_scaling_log_rows` | Added 2026-09-10. Consequence of the row above: the only week-over-week cost series the Sheet keeps for verticals is CPICP. The Tuesday brief therefore computes last week's CPL and lead movement from `?action=rollup` (`spend` / `meta_conversions` per vertical, two most recent `week_start`s) and uses `scaling-log-read` only for classification, frequency trend and pool flags. Fix = add `cpl` + `total_leads` columns to the header, the write and the read, then a redeploy. |
| Apps Script pipeline still computes IC attribution under the hood | `Code.js: buildWeeklyRollup`, `IC_CONVERSION_EVENT_PATTERN`, `intelligence_log` cols 3-4 | Added 2026-09-09, narrowed 2026-09-10. Every Slack message, prompt and ranking in `Code.js` is now lead-first, so nothing acts on or headlines the IC numbers. But `buildWeeklyRollup` still runs the hybrid v3 attribution to fill rollup cols 10-13 / 15-21, `generateNarrativeForWeek_` still computes `totalICPs` / `overallCPICP` to keep `intelligence_log` cols 3-4 positionally stable for `getLatestNarrative_` / the dashboard, and the IC conversion event is still matched by fragile string pattern. Retiring those columns needs a header migration on both sheets plus dashboard changes. |
| `weekly_rollup` has no CPL trend columns | `Code.js: buildWeeklyRollup` history block; `generateNarrativeForWeek_` `cplTrend` | Added 2026-09-10. Cols 17-21 are all `cpicp_blended`-based. The narrative derives prior-week / 4-week-average CPL per campaign in memory from earlier rollup rows. Appending `cpl_prior_week` / `cpl_4wk_avg` / `cpl_wow_pct` / `cpl_vs_4wk_pct` after col 22 (never inserting — positional reads) would let the digests and dashboard share one series. |
| ~~`doGet` gate order made every strategic approve/reject link dead~~ | ~~`Code.js: doGet`~~ | **Resolved 2026-09-10.** Since the strategic flow shipped (2026-05-08) `doGet` returned "No pending budget proposal" / "Token mismatch" on `BUDGET_PENDING_TOKEN` before reaching the `*_scaling` branches, and scaling tokens never equal the optimizer token — so every Tuesday proposal expired unapproved at the next 3 AM run. The scaling branches now sit above the optimizer gate. No strategic reallocation has ever executed in production; the first real approval is the first end-to-end test. |
| ~~Portfolio-scaling portfolio total summed paused campaigns' budgets~~ | ~~`compute_scaling_profiles.py` portfolio block; `compute_reallocation.enforce_tolerance`~~ | **Resolved 2026-09-10.** `current_total_daily_cents`, the tolerance headroom and `optimizer_cycles_this_week` now count ACTIVE campaigns only (`paused_total_daily_cents` carried separately). The paused-inclusive total (345,324¢/day ≈ $24k/week vs a $2,100 target on 2026-09-08) had scaled every increase to zero and set `knockdown_risk` every week. The same pass added `LEADS-` to `VERTICAL_RE` and switched `optimizer_eligible` to lifetime leads. |
| ~~Paused optimizer left `BUDGET_PENDING_TOKEN` armed~~ | ~~`Code.js: runBudgetAnalysis` / `executeBudgetChanges` guards~~ | **Resolved 2026-09-10.** The 2026-09-09 early returns skipped the token cleanup at the bottom of `executeBudgetChanges`, so the last pre-pause proposal's token — and its Slack approve link — stayed valid indefinitely and could post "✅ Approved … will execute tonight" for a run that would never happen. Both guards now delete the three `BUDGET_*` tokens (and `BUDGET_RANK_TIERS`, which held CPICP-era tier history). |
| ~~Claude model hardcoded in 3 places~~ | ~~Code.js~~ | **Resolved 2026-04-22.** Extracted to `ANTHROPIC_MODEL` constant (Code.js:84). Every call site references the constant. |
| ~~Hybrid attribution math duplicated~~ | ~~Code.js: `buildWeeklyRollup` and `computeBudgetSignals_`~~ | **Resolved 2026-09-10.** `computeBudgetSignals_` no longer computes hybrid ICPs (it aggregates leads / CPL from `rolling_data`); only `buildWeeklyRollup` carries the attribution math. |
| Rules engine is 200+ lines of nested logic | Code.js: `computeRecommendations_` ~3251 | Hard to test; decision table would help. Hysteresis (2026-05-11) added another tier check. |
| Dashboard API inline in `handleDashboardApi_` | Code.js: `handleDashboardApi_` ~4985 | Large switch; extract action handlers |
| `SYNC_WARNED_CAMPAIGNS` as pipe-delimited string | Code.js:~520 | Fragile if names contain pipes |
| Week-over-week CPL math duplicated across three composers | Code.js: `postDailyDigest` ~2187, `postWeeklyNarrativeToSlack_` ~2089, `generateNarrativeForWeek_` `cplTrend` | Each computes prior-week / 4-week CPL comparisons from rollup rows independently (a consequence of the missing CPL trend columns). Extract one helper or add the columns. |
| `creative-intelligence-read` action does not exist | `compute_reallocation.fetch_creative_intelligence_log`; `Code.js: handleDashboardApi_` | Added 2026-09-10. The Tuesday brief's audience-action `creative_prescription` ("Top body: …") is sourced from a GET `?action=creative-intelligence-read` that no handler serves, so `fetch_json` fails and the line is always null. Add the read handler (mirror `getScalingLogRows_` over `creative_intelligence_log`) and carry `top_body_cpicp` (lead-valued) as the CPL figure until the column rename. |
| `scripts/preview_dataset.py` is CPICP-keyed and reads dead keys | `scripts/preview_dataset.py`, `.github/workflows/agent-creative-preview.yml` | Added 2026-09-10. The $0 preview path never touched Slack, so it was outside the lead-first pass, but it still sorts / splits on `cpicp` (null for nearly every ad since August) and reads `ad_a_cpicp` / `ad_b_cpicp`, which the dataset builder stopped emitting on 2026-09-09 (`ad_a_cpl` / `ad_b_cpl`). Its side-by-side section renders `n/a` for every pair. Rewrite to `cpl` before any future Slack hookup. |
| `pipeline_health.zero_lead_spend_floor_usd` not configured | `data/config/benchmarks.json`; `check_health.check_snapshot_volume` | Added 2026-09-10. `snapshot_volume` reports `N leads on $S spend` every day but only WARNs on zero leads when this optional floor is set; it is not, so a day of spend with no leads passes silently. Pick a floor (the account runs ~$300/day) and add it. |
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
| Funnel custom conversion IDs hardcoded in `benchmarks.json` | `data/config/benchmarks.json:conversions` | Ad-level pipeline cannot read `campaign_mapping` (lives in Sheets), so the quality tier and each subtype pin to a `custom_conversion_id` in config. New custom conversions must be added by hand (see §11.5); `pipeline-health` at least reports when a configured one is archived or stops firing. |
| Ad-level fatigue logic duplicates 14-day-window concept from Apps Script budget | `scripts/compute_signals.py` vs `Code.js:computeBudgetSignals_` | Two implementations of "rolling-window-based health signal" can drift |
| ~~`daily-data.yml` cron disabled~~ | ~~`.github/workflows/daily-data.yml`~~ | **Resolved 2026-05-04.** Cron `0 12 * * *` (daily 8 AM ET) active and validated; the bot's snapshot commits land on main every morning. |
| ~~pipeline-health ran an LLM daily to reformat deterministic JSON~~ | ~~`.github/workflows/agent-pipeline-health.yml`~~ | **Resolved 2026-06-23.** `check_health.py` already produced structured PASS/WARN/FAIL JSON and wrote the Sheet itself; `claude-code-action` only reformatted it for Slack. Replaced with deterministic `report_health.py` and folded into `daily-data.yml` (§11.7). Removed a daily Anthropic call and a scheduled workflow with no loss of outcomes. |
| `claude-code-action@v1` strips git credentials AND breaks Anthropic SDK in subprocesses | `.github/workflows/agent-creative-intelligence.yml` | The action's own internals are opaque; workaround is the architectural pattern documented in §10.1.0.5 (run scripts as workflow steps, commit before the action). If/when we ship Skill 6+ with Anthropic-subprocess or commit-back needs, this constrains the workflow shape. Worth revisiting when claude-code-action releases a new major version. |
| `build_creative_dataset.py` is invoked twice in `agent-creative-intelligence.yml` | `.github/workflows/agent-creative-intelligence.yml` | First invocation refreshes the cache + emits a baseline dataset (null tags); categorize then runs; second invocation re-emits with tags attached. The second run repeats all the snapshot aggregation + corpus building work to attach tags that could be merged in-place. ~5-10 second waste per Monday run. Could be optimized by adding a `--attach-tags-only` flag that skips snapshot reads. |
| Compliance regex backstop has known false positives | `skills/ad-copy-generator/scripts/generate_drafts.py` | E.g. "no personal guarantee" trips the `\bguarantee\b` pattern even though it's the OPPOSITE of a return-guarantee promise. The reviewer checklist catches these, but the ⚠️ flag is noisy. Could refine the regex to require return-context keywords nearby (`return`, `APY`, etc) instead of bare `\bguarantee\b`. |
| Categorizer's prompt caching is fragile to system-message drift | `skills/creative-intelligence/scripts/categorize_creative.py` | The 30k tokens/min Anthropic rate limit is only survivable because the 5000-token system message is identical across all 526 calls in a run, hitting Anthropic's prompt cache at ~10% effective cost. If a future change makes the system message vary per-call (e.g. injecting variant context), caching breaks and the skill regresses to ~$5/run + 18% rate-limit failures. Test by running the categorize step locally with the new prompt shape against `--max-new 50` BEFORE shipping such a change. |
| Daily Ads digest and Daily Check skill use different "yesterday spend" sources | `Code.js: postDailyDigest` reads `rolling_data` sheet (~7 AM snapshot) vs `skills/daily-check/scripts/fetch_daily_data.py` (fresh Meta call at ~11 AM) | Numbers can disagree by $100+ as Meta's attribution shifts between the two reads. Footers on each report now annotate the source (added 2026-05-11) so the discrepancy is transparent, but reconciling on a single source-of-truth would be the proper fix. Either delete-and-refetch yesterday's rolling_data row in `postDailyDigest`, or have Daily Check read the sheet via `/exec` to share the snapshot. |
| Composite-rank weighting is 70/30 CPL/lead-trend, not data-driven | `Code.js: computeRecommendations_` ~3340 | Weights chosen by intuition (carried over unchanged from the CPICP version on 2026-09-10). Could be calibrated by backtesting against historical CPL outcomes once enough budget-cycle history accumulates. Hysteresis (2026-05-11) addresses timing volatility but not the weighting question. |
| Anthropic categorizer retry is brittle | `skills/creative-intelligence/scripts/categorize_creative.py` ~194 + ~258 | 2 attempts × fixed 2-second sleep, no exponential backoff, no Retry-After header parsing, all `Exception` types treated alike. Prompt caching (PR #68) masks the underlying brittleness; as the variant pool grows past ~600 the 1% failure rate will likely regress. Fix is to distinguish 429 (parse Retry-After + exponential backoff) from validation errors (don't retry). |
| `MetaClient._paginate` and throttle backoff bounds are heuristic | `scripts/lib/meta.py: MAX_PAGES`, `MAX_BACKOFF_SECONDS` | Defensive caps (200 pages, 300 s) added 2026-05-11. The right fix for sustained Meta rate-limits is to parse `X-Business-Use-Case-Usage` headers and sleep the recommended duration — could be a major run-time saver during a true rate-limit event. |
| `compute_signals.evaluate_fatigue` severity bucket conflates "no signal" with "below floor" | `scripts/compute_signals.py` ~186 | An ad with `frequency_critical` flag but only 2 days of data lands in `severity_counts["ok"]` (since `actionable=false` short-circuits severity to ok). Misleading for downstream consumers reading the count. Separate `below_floor_with_flags` bucket would be clearer. |
| `daily-data.yml` backfill DETAIL line reads the wrong manifest | `.github/workflows/daily-data.yml` ~128 | In backfill mode, `DATE_LABEL` defaults to "yesterday UTC" so the manifest path resolved is yesterday's manifest, not the backfill range. HEADER is correct; DETAIL counts mismatch. Stitch a multi-date summary or skip DETAIL on backfill. |
| `fetch_ad_data.py` single-day path silently overwrites existing snapshots | `scripts/fetch_ad_data.py: run()` (single-day default) vs `run_range()` | `run_range` checks `has_snapshot()` and skips existing dates; the single-day default path doesn't. Manual workflow_dispatch re-runs for the same date silently overwrite. Concurrency-grouped daily cron is safe; this only matters for operator re-runs. |
| All agent cron times shift 1 hour in EST | `.github/workflows/agent-*.yml` + `daily-data.yml` | UTC cron expressions are tuned for EDT (summer). In EST (~Nov-Mar) every workflow runs an hour earlier than the documented ET times. Most files note this inline and CLAUDE.md carries the caveat centrally. Move to a DST-aware scheduler to fix permanently. |

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
5. For budget automation: while the optimizer is paused, `runBudgetAnalysis()` returns at the guard — run `testBudgetSystem()` instead (exercises `computeBudgetSignals_` and `computeLeadPace_` and logs the lead pace). Once re-enabled: run `runBudgetAnalysis()` manually, inspect the Slack proposal (lead-pace headline, CPL-sorted rows) and the `budget_queue` sheet, reject the proposal to avoid real Meta writes. For the strategic flow: dispatch `agent-portfolio-scaling.yml`, click the brief's approve link and confirm the `*Honeycomb Scaling* ✅ …` post names the campaign count.
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
| `adset_insights.json` | Per-(date, adset) row with `impressions, clicks, spend, reach, frequency, ctr, cpc, cpm` plus the funnel tiers `leads, prequal_decisions, ic_conversions, rewards_conversions` and the deprecated alias `conversions` (= `leads`) | Meta `/insights?level=adset` |
| `ad_insights.json` | Per-(date, ad) row with the same metric set. **Snapshots written before 2026-09-09 carry only `conversions` + `ic_conversions`** (no `leads` key) until the task-#5 backfill re-pulls them — every reader falls back `leads` → `conversions` | Meta `/insights?level=ad` |
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

Top-level keys (current schema, 2026-09-10):
- `account.{id, name, meta_api_version, timezone}` — Meta account + Graph API version + display timezone
- `exec_endpoint` — Apps Script `/exec` URL skills hit for Sheet read/write
- `slack_webhook_secret_name` — name of the env var skills look up for Slack posting
- `conversions.{primary, quality, subtypes}` — the three-tier funnel (§11.5). Replaced `ic_tracking` on 2026-09-09.
- `lead_economics.{target_cpl_dollars, cpl_warning_multiple, cpl_critical_multiple}` — $16 target, 1.5× / 2.0× lines. Mirrored in `Code.js` as `TARGET_CPL_DOLLARS` / `CPL_CRITICAL_MULTIPLE` (dual-source; change both). Surfaced to briefs as `stats.target_cpl_dollars` (fatigue-monitor) and used by daily-check's bleeder rule.
- `pacing.{weekly_spend_target_dollars, weekly_spend_tolerance_dollars, pacing_tolerance_pct}` — fallback spend goal ($2,100 ± $315) used only when `/exec?action=get_spend_goal` is unreachable
- `fatigue.*` — CTR decline thresholds (early/fatigued), frequency warnings, **CPL inflation** (`cpl_inflation_warning_pct` 25 / `cpl_inflation_critical_pct` 50 — classification inputs for fatigue-monitor and `compute_signals.py`; `CPL_SPIKE_WARNING_PCT` mirrors the warning value in `Code.js`), CPC inflation (reported only), baseline window, min impressions/days active, creative age warning
- `daily_check.*` — winner/bleeder definitions (`winner_min_conversions` is a leads floor, mirrored as `CPL_FLAG_MIN_DAILY_LEADS`), early-fatigue thresholds for the daily briefing
- `creative_intelligence.{confident_min_ads, confident_min_leads, directional_min_ads, directional_min_leads}` — lead-denominated confidence floors (10/100, 5/40)
- `scaling.*` — portfolio-scaling classification thresholds; `min_weekly_conversions` (10 leads) is mirrored as `CPL_FLAG_MIN_WEEKLY_LEADS`
- `pipeline_health.{token_warning_days, endpoint_timeout_seconds, data_freshness_max_gap_weekdays, min_expected_insight_rows}` — used by pipeline-health skill. Optional `zero_lead_spend_floor_usd` (not yet set) turns `snapshot_volume`'s lead report into a WARN when spend ≥ floor with 0 leads.
- `campaign_defaults.type` — `prospecting` vs `retargeting` (affects fatigue frequency thresholds)

### 11.5 Funnel conversion extraction

The funnel is declared once, in `conversions` in `data/config/benchmarks.json`,
and materialised by `funnel_from_config()` in `scripts/lib/meta.py` into a
frozen `FunnelSpec`. `extract_conversions(actions, funnel)` returns one count
per tier:

| Tier | Field | Resolution |
|---|---|---|
| Primary | `leads` | First present type in `conversions.primary.action_type_priority` |
| Quality | `prequal_decisions` | `offsite_conversion.custom.1153878920152279` |
| Subtype | `ic_conversions` | `offsite_conversion.custom.2330338620810873` |
| Subtype | `rewards_conversions` | `offsite_conversion.custom.1527298745037132` |

**Leads resolve by priority, never by sum.** Verified 2026-09-09 against both
live Q3 campaigns, `lead`, `offsite_conversion.fb_pixel_lead` and
`onsite_web_lead` returned identical values (215/215/215 and 180/180/180) —
they are three counts of one event, so summing multiplies leads. The previous
implementation took "the first matching type in array order", which produced
the right number only because the values happened to agree; the priority list
makes it deterministic and order-independent.

`normalize_insights_row` emits canonical `leads` plus `conversions` as a
deprecated alias, so pre-pivot snapshots and the campaign-level `rolling_data`
rollup stay mutually readable until the 250-day backfill lands.

Covered by `scripts/tests/test_funnel_extraction.py`, whose fixtures are
verbatim Meta payloads rather than reimplementations (15 checks).

**Limitation:** the ad-level pipeline still cannot read `campaign_mapping`
(which lives in the Google Sheet), so new custom conversion IDs must be added
to `benchmarks.json` by hand. The impact is smaller than before — the config
now holds a list of tiers rather than one scalar ID — but discovery is still
manual. See tech-debt index §10.4.

### 11.6 Skills (`/skills/`)

Skills are self-contained packages: a `SKILL.md` (with YAML frontmatter — `name`, `description`) plus a `scripts/` directory of Python scripts the skill runs via bash. Scripts emit structured JSON; the skill interprets the JSON and chooses what to send to Slack and what to write to the Sheet.

Every brief follows the lead-first Slack standard in §6.2 (headline leads / CPL, CPL sorts, CPL thresholds, IC only as a secondary `of which N reached an IC decision` line, no stale optimizer copy). Each SKILL.md states it in its own output rules, and the matching `agent-*.yml` prompt repeats the same rules so the two instruction sources cannot disagree (on 2026-09-10 three of the prompts were still pre-pivot while their SKILL.md was not).

| Skill | Status | Purpose |
|---|---|---|
| `pipeline-health` | shipped 2026-05-03; five checks since 2026-09-09 | Checks: data freshness, Meta token, funnel conversions (quality tier first, subtypes trailing under `subtypes (reported only)`, subtype problems capped at WARN and phrased as tracking-config issues), dashboard endpoint (`?action=rollup`), snapshot volume (row counts + `N leads on $S spend`; optional zero-lead WARN floor). Slack lines are `STATUS check_name: detail`, FAIL before WARN, silent on PASS. Deterministic — runs inside `daily-data.yml` with no LLM. |
| `daily-check` | shipped 2026-05-03 | `📊 Daily Lead Check` briefing: totals headline (leads · CPL · spend · prequal decisions, IC parenthetical only when > 0), pacing vs the live weekly target, portfolio sorted by CPL (no-lead campaigns last, `0 leads` explicit), top-3 winners by CPL (`≥5 leads + ≥1,000 impressions`), bleeders by `reason` (spend-without-leads → CPL ≥ 1.5× ad-set CPL → CTR fallback only without lead data), early fatigue flags, learning-phase ad sets, stale creatives. Writes to `daily_check_log` (legacy IC-named keys carrying lead values). |
| `fatigue-monitor` | shipped 2026-05-03; CPL matrix since 2026-09-10 | Three-script pipeline: 14-day fetch, baseline computation (Path A in-range / B historical-batched / C estimated) now carrying `leads_baseline` / `cpl_baseline` / `spend_baseline`, classification across 5 severity classes where CPL inflation ≥ 50% → `fatigued` and ≥ 25% with freq ≥ 2.0 → `early_fatigue` (CPL compared only when both windows bought leads; CPC reported only), pre-sorted output (severity → conflict → zero-lead spend → CPL desc), `stats.{spend_7d, leads_7d, cpl_7d, target_cpl_dollars, non_healthy}` for the headline, lead-first `budget_conflict` line built in Python from the newest pending increase per campaign. Writes to `fatigue_log` (lead keys additive). |
| `creative-intelligence` | shipped 2026-05-05 | Weekly (paused cron) brief on winning copy / visual patterns via corpus-level text aggregation; per-variant `total_leads` / `cpl`, CPL-keyed deciles, lead-denominated confidence floors, `🎯 … N leads at $X median CPL across M ads` headline. Writes to `creative_intelligence_log` (legacy keys lead-valued; `top_body_*` = lowest-CPL confident body). |
| `ad-copy-generator` | shipped 2026-05-05 | `workflow_dispatch` only; drafts new copy for a vertical from the creative dataset split at median CPL; compliance regex backstop; markdown to `data/drafts/`; never auto-published. No Slack. |
| `portfolio-scaling` | shipped 2026-05-08; lead-first brief + ACTIVE-only totals since 2026-09-10 | Tuesday brief: 12-week portfolio headline, per-vertical scaling labels CPL-ascending, strategic reallocation in lead terms with the two-step approval links (working since 2026-09-10), audience actions, last-week evaluation from the rollup. Writes `scaling_log` (IC-named cost columns only — see §3.7) and `budget_queue` strategic rows via `scaling-queue-write`. |

The earlier file-based skills (`budget-optimizer` and earlier versions of the ones above) were built against a less-refined spec and have been replaced. `compute_signals.py`'s `data/derived/` outputs are now an audit trail rather than the canonical signal source — the skills compute their own canonical versions.

### 11.7 Workflow (`.github/workflows/daily-data.yml`)

- **Trigger:** `workflow_dispatch` + active cron `37 12 * * *` UTC (~8:37 AM ET in EDT / ~7:37 AM ET in EST). The minute is deliberately off `:00`: GitHub queues and deprioritizes scheduled runs (worst at the top of the hour), and the old `0 12` cron was observed drifting +1.8 to +5.1 h in this repo (the "8 AM" pull routinely started ~9:50 AM-1 PM ET). An uncommon minute pulls the start time back toward schedule. Still after the reliable 7 AM ET campaign-level pull, so the folded-in pipeline-health freshness check still expects "yesterday".
- **Steps:** checkout → setup Python 3.12 → `pip install requests==2.32.3` → `python scripts/fetch_ad_data.py` → `python scripts/compute_signals.py` → commit `data/` and push to the current branch → **`Run pipeline-health checks`** → post the `daily-data` status to issue #48 (manifest counts plus `leads=N spend=$X` summed from the just-committed `ad_insights.json` with the `leads` → `conversions` fallback, tolerant of a missing or malformed file) → post the `pipeline-health` status to issue #48.
- **Pipeline-health steps (folded in 2026-06-23 from the retired `agent-pipeline-health.yml`):** `Run pipeline-health checks` (`if: always()`, env `META_ACCESS_TOKEN` + `EXEC_SHARED_SECRET` + `META_AD_ACCOUNT_ID` + optional `SLACK_WEBHOOK_URL`) runs `check_health.py > /tmp/health.json` then `report_health.py --input /tmp/health.json --status-file /tmp/health_status.txt`. It always exits 0 so a health hiccup never fails the data pull. `report_health.py` posts the Slack alert on WARN/FAIL (silent on full PASS / no webhook) and writes the one-liner. A dedicated `Post pipeline-health status to tracking issue` step (also `if: always()`, `continue-on-error`) comments `**pipeline-health**` to issue #48 — so this workflow posts two comments per run. `data_freshness` / `meta_token` / `dashboard_endpoint` target the campaign-level `rolling_data` + Meta + dashboard endpoint, independent of the ad-level snapshot; `snapshot_volume` deliberately inspects the snapshot this job just committed, so a failed or empty fetch surfaces as a `snapshot_volume` FAIL/WARN in Slack rather than passing silently. No `claude-code-action`, no `id-token`/OIDC. Note: until `EXEC_SHARED_SECRET` is set on both sides, every run's `health-write` is refused and the reporter posts a `WARN sheet_write: … unauthorized` line to Slack even when all five checks PASS.
- **Secrets:** `META_ACCESS_TOKEN` (GitHub Secret on the repo, separate from the Apps Script Script Property of the same name). `META_AD_ACCOUNT_ID` is also read from env if set, falling back to `account.id` in `benchmarks.json`. `EXEC_SHARED_SECRET` for the `health-write` POST. Optional `SLACK_WEBHOOK_URL` for the pipeline-health alert.
- **Permissions:** `contents: write` (needed to push the daily commit) + `issues: write` (the two issue-#48 status comments).
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
| `extract_conversions(actions, funnel)` | `scripts/lib/meta.py` | Funnel-tier extraction (§11.5): `leads` by priority chain, `prequal_decisions` / `ic_conversions` / `rewards_conversions` by custom-conversion id. Mirrors the lead chain in `collectMetaRows_`. |
| `merge_creatives` | `scripts/fetch_ad_data.py` | Accumulates creative metadata; preserves `first_seen_date` |
| `linear_trend_slope` | `scripts/compute_signals.py` | Best-fit slope for CTR-over-days |
| `compute_ad_metrics` | `scripts/compute_signals.py` | Per-ad rolling metrics |
| `evaluate_fatigue` | `scripts/compute_signals.py` | Apply thresholds → flags + severity + actionable |
| `compute_winner_bleeder` | `scripts/compute_signals.py` | Per-adset CTR/spend ranking |
| `expected_data_date` | `skills/pipeline-health/scripts/check_health.py` | Computes the date `rolling_data` should have, accounting for the 7 AM ET pull cutoff |
| `weekday_gap` | `skills/pipeline-health/scripts/check_health.py` | Counts business days missed between latest data and expected date |
| `check_data_freshness` / `check_meta_token` / `check_funnel_conversions` / `check_dashboard_endpoint` / `check_snapshot_volume` | `skills/pipeline-health/scripts/check_health.py` | Five health checks; each returns `{name, status, detail}`. `check_funnel_conversions` leads with the quality tier and trails `subtypes (reported only): …` most-recently-fired first (subtype problems ≤ WARN). `check_snapshot_volume` sums `leads` (field name from `conversions.primary.field`, `conversions` fallback) and `spend` from the newest `ad_insights.json` and appends `N leads on $S spend` to every detail; optional `zero_lead_spend_floor_usd` WARN. Standalone by design — does not import `scripts/lib`. |
| `print_terminal_summary` / `slack_lines` / `post_to_slack` / `status_one_liner` | `skills/pipeline-health/scripts/report_health.py` | Deterministic formatter for `check_health.py` JSON (added 2026-06-23). Prints the terminal summary, composes + POSTs the WARN/FAIL Slack alert (lines `STATUS name: detail` so the check name is always the first token, FAIL before WARN, `detail` verbatim, silent on full PASS / no webhook), and writes the issue-#48 one-liner (`PASS 5/0/0` shape). Replaced the daily `claude-code-action` invocation when pipeline-health was merged into `daily-data.yml`. |
| `row_leads` / `aggregate` / `classify` / `budget_conflict_line` | `skills/fatigue-monitor/scripts/classify_fatigue.py` | `row_leads` reads `leads` with the `conversions` fallback; `aggregate` sums spend / leads / prequal / IC and derives `cpl`; `classify` applies the CPL + CTR + frequency matrix and returns the `signals` that fired; `budget_conflict_line` composes the lead-first conflict warning (never echoes `signal_reasons`). Output is sorted by `SEVERITY_ORDER` → conflict → zero-lead spend → CPL desc → spend desc. |
| `aggregate_window` / `baseline_fields` | `skills/fatigue-monitor/scripts/compute_baselines.py` | Per-ad baseline aggregation now including `leads` / `cpl`; `baseline_fields` is the one place the `*_baseline` key set is defined for all three paths. |
| `is_active` / `lifetime_leads_per_campaign` | `skills/portfolio-scaling/scripts/compute_scaling_profiles.py` | ACTIVE test shared with `compute_reallocation.is_actionable_campaign`; lifetime leads per campaign (sum of `meta_conversions`) for `optimizer_eligible`, mirroring `LIFETIME_MIN_CONVERSIONS` via `OPTIMIZER_LIFETIME_MIN_LEADS`. |
| `pool_skip_reasons` | `skills/portfolio-scaling/scripts/compute_reallocation.py` | `paused_saturating_verticals` + `increase_skip_reason` so the brief can say why a side of the pool is empty. |
| `compute_features(text)` | `scripts/lib/text_features.py` | Deterministic structural features per variant text (char/word/sentence count, opening word, syntactic markers). Pure Python, no LLM call. |
| `variant_id(text)` | `scripts/lib/text_features.py` | Whitespace-collapsed + lowercased SHA-256 prefix (16 hex chars). Stable join key between dataset builder and categorizer. |
| `atomic_write_json(path, data, ...)` | `scripts/lib/io.py` | Atomic write via tmp + rename. Used by `compute_signals.py`, `build_creative_dataset.save_creatives_cache`, `compute_scaling_profiles.py`, and `compute_reallocation.py`. The `.json.tmp` extension is `.gitignore`d so an interrupted run never commits a partial file. Mirrors the inline pattern in `fetch_ad_data.write_json` (PR #72) and `categorize_creative.save_cache_atomic`. |
| `fetch_json(url, params, ...)` | `scripts/lib/exec_api.py` | Generic GET + 3× retry JSON fetch. Raises `RuntimeError` on exhausted retries. Canonical `/exec` GET wrapper — used by `compute_scaling_profiles.py`. |
| `get_spend_goal(exec_url, *, fallback_target, fallback_tolerance)` | `scripts/lib/exec_api.py` | Canonical Python accessor for the live weekly spend goal + tolerance. Calls `/exec?action=get_spend_goal`; on any failure logs a warning and returns the supplied fallbacks tagged `source="fallback_unreachable"` (never raises). Used by `daily-check/analyze_daily.py` and `portfolio-scaling/compute_scaling_profiles.py`. |
| `MetaClient.resolve_image_hashes(hashes)` | `scripts/lib/meta.py` | Resolves `image_hash` values to full-size URLs via `/act_X/adimages?hashes=[...]`. Auto-chunks at 50 hashes per request. Returns `{hash: {url, width, height, ...}}`. |
| `download_image(creative_id_or_hash, url, dest_dir)` | `scripts/lib/meta.py` | Idempotent atomic download to `<dest_dir>/<key>.jpg`. Skips existing non-empty files; one retry on transient errors; logs and returns None on hard failure rather than aborting the caller. |
| `extract_vertical(campaign_name)` | `skills/creative-intelligence/scripts/build_creative_dataset.py` | Pulls vertical slug from `AD-/ICD-/Rev-<vertical>-Q<N>-<YYYY>` patterns (with optional `PAUSED -` prefix and legacy `Wineries / vineyards` fallback). Lowercased human-readable. |
| `aggregate_ad_performance(snapshot_dates)` | `skills/creative-intelligence/scripts/build_creative_dataset.py` | Sums per-ad impressions / spend / leads (with `prequal_decisions` and reported-only `ic_conversions`) across the snapshot window and derives `cpl`. Tracks first/last active date and `days_active`. |
| `build_variant_corpus(ad_to_creative, cache)` | `skills/creative-intelligence/scripts/build_creative_dataset.py` | For each unique variant text, builds `{variant_id, dimension, text, structural, appears_in_ads}`. The corpus index is the spine of variant-level attribution. |
| `aggregate_variant_performance(variants, ad_performance)` | `skills/creative-intelligence/scripts/build_creative_dataset.py` | In-place: sums spend / impressions / leads across each variant's ad list and derives `cpl` (`total_ic_conversions` / `cpicp` carried as reported-only). The corpus-aggregation attribution model in code form. |
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
| `daily_check_log` | `daily-check` skill via `?action=daily-check-write` (auto-created in `handleDailyCheckWrite_`) | `date, pacing_status, total_spend, total_icps, portfolio_cpicp, fatigue_flag_count, recorded_at` — `total_icps` / `portfolio_cpicp` hold lead / CPL values since 2026-09-09 (legacy names; see §10.4). No reader exists in Code.js or the dashboard. |
| `fatigue_log` | `fatigue-monitor` skill via `?action=fatigue-write` (auto-created in `handleFatigueWrite_`) | `date, ad_id, ad_name, campaign, classification, ctr_baseline, ctr_current, ctr_decline_pct, frequency, cpc_baseline, cpc_current, days_active, baseline_type, budget_conflict, recorded_at` — the lead keys the skill sends are dropped until columns are appended (`budget_conflict` text is now lead-first). |
| `creative_intelligence_log` | `creative-intelligence` skill via `?action=creative-intelligence-write` (auto-created in `handleCreativeIntelligenceWrite_`) | `date, vertical, ad_count, median_cpicp, spend_total, ic_total, top_body_variant_id, top_body_text, top_body_cpicp, top_visual_hash, top_visual_style, bottom_decile_count, recorded_at` |

### 11.10 Shared client (`scripts/lib/meta.py`, added 2026-05-03)

Single Meta Graph API client used by both the snapshot pipeline (`scripts/fetch_ad_data.py`) and skill scripts (`skills/<name>/scripts/`). Contains the `MetaClient` class plus normalization helpers and constants — extracted from `fetch_ad_data.py` so skill scripts don't duplicate the HTTP retry / throttle / paging logic. New skills that need Meta data should `from lib.meta import MetaClient` rather than reimplementing.

| Export | Purpose |
|---|---|
| `MetaClient` | Wrapper with `_request`, `_paginate`, `insights(level, fields, since, until=None, time_increment=1)`, `adsets(filtering=…)`, `ads(filtering=…)`, `creative(id)`. Per-call throttle, exponential backoff to 60s, retries on HTTP 429/5xx and Meta error codes 1, 2, 4, 17, 32, 341, 613, 80000, 80004. |
| `INSIGHTS_FIELDS_CAMPAIGN`, `INSIGHTS_FIELDS_ADSET`, `INSIGHTS_FIELDS_AD` | Field lists per insights level |
| `ADSET_OBJECT_FIELDS`, `AD_OBJECT_FIELDS`, `CREATIVE_FIELDS` | Object-graph field lists |
| `FunnelSpec` / `funnel_from_config(config)` | Frozen three-tier funnel materialised from `benchmarks.json:conversions` (§11.5) — lead action-type priority list plus the quality / subtype custom-conversion ids. Replaced `LEAD_ACTION_TYPES` and `ic_action_type_from_config` on 2026-09-09. |
| `extract_conversions(actions, funnel)` | Returns one count per tier — `leads` (first present type in the priority chain, never summed), `prequal_decisions`, `ic_conversions`, `rewards_conversions` — from a Meta `actions[]` array |
| `normalize_insights_row(row, funnel, …)` / `normalize_adset` / `normalize_ad` / `normalize_creative` | Flatten Meta JSON into the project's row shape; `normalize_insights_row` merges the tier counts and sets the deprecated alias `conversions = leads` |
| `load_config()` | Reads `data/config/benchmarks.json` |
| `yesterday_utc()` | Helper for snapshot pipeline default date |
