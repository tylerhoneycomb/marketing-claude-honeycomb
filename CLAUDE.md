# CLAUDE.md — Honeycomb Credit Marketing Monorepo

This file guides Claude's behavior when working in this repository.

## About Honeycomb Credit

Honeycomb Credit is a community investment platform that helps small businesses raise capital from their own customers and communities through investment crowdfunding. Businesses raise money in exchange for revenue-sharing notes, and everyday investors can participate starting at low minimums.

## Repository Purpose

This monorepo contains marketing automation, ad copy, workflows, and tooling for Honeycomb Credit's marketing team. It is NOT a product codebase — it is a marketing operations repo.

## Tone & Brand Voice

- Warm, community-oriented, and empowering
- Speak to small business owners as entrepreneurs and community pillars
- Avoid financial jargon; keep language accessible
- Never make specific return or investment performance promises
- Always include appropriate disclaimers when referencing investment products

## Key Audiences

- Small business owners seeking capital (restaurants, breweries, gyms, salons, etc.)
- Community investors who want to support local businesses
- Honeycomb Credit internal marketing team

## Living Documentation

Two documents in `/docs/` describe the project's state and internals. **Both MUST be kept in sync with the code.** When you make changes that affect functionality, data model, APIs, deployment, or significant function contracts, update the relevant sections of these docs in the same PR:

- `/docs/STATE_REPORT.md` — plain-English overview of functionality, limitations, and known risks. Audience: non-technical stakeholders. Update when functionality changes, limitations are resolved, or new risks emerge.
- `/docs/TECHNICAL_REFERENCE.md` — engineering reference: architecture, data model, APIs, deployment, function index, technical debt. Audience: developers. Update when schema changes, constants change, new integrations are added, or any function listed in the function reference index is significantly modified.

Both documents have a `_Last updated: YYYY-MM-DD_` line at the top — bump it on every meaningful change. If an update introduces or resolves technical debt, also update the tech-debt index in `TECHNICAL_REFERENCE.md` §10.4.

## Repo Structure

- `/apps-script/` — Full Apps Script intelligence layer, deployed via clasp + GitHub Actions
  - `Code.js` — The complete intelligence script (~3,600 lines). Edit here, never in the Apps Script web editor
  - `.clasp.json` — Points clasp at the Apps Script project (do not edit)
  - `appsscript.json` — Apps Script manifest (scopes, runtime, Web App settings)
- `/docs/` — Living documentation (`STATE_REPORT.md`, `TECHNICAL_REFERENCE.md`) — keep in sync with code changes
- `/webapp/` — Honeycomb Ads Intelligence Dashboard (single-file React SPA on GitHub Pages)
  - `index.html` — The full dashboard app
  - `apps-script-api.gs` — Reference copy of the web API layer (handleDashboardApi_, Hive Mind chat, Slack approval flow). This is a subset of Code.js for documentation purposes — the live deployed version comes from apps-script/Code.js
- `/skills/` — Agent skill definitions (read at the start of every Claude Code session for the agent loop). Each subdirectory has a `SKILL.md`: `daily-check`, `fatigue-monitor`, `budget-optimizer`, `ad-copy-generator`, `pipeline-health`.
- `/scripts/` — Python data-collection + signal-computation scripts for the ad-level pipeline. `fetch_ad_data.py` pulls from Meta; `compute_signals.py` derives fatigue/winner-bleeder; `run_daily.sh` orchestrates the pair.
- `/data/` — Agent data repository.
  - `data/snapshots/<YYYY-MM-DD>/` — daily JSON snapshots from Meta (campaigns, adsets, ads, ad_insights, adset_insights, _manifest)
  - `data/creatives/creatives.json` — creative metadata, accreted over time
  - `data/derived/` — computed signals (`fatigue_signals.json`, `winner_bleeder.json`, `summary.json`)
  - `data/config/benchmarks.json` — all thresholds; never hardcode them in scripts
- `/ad-copy/` — Meta (Facebook/Instagram) ad copy organized by vertical
- `/workflows/` — Automation scripts and marketing workflows
- `/audiences/` — Audience lists and segmentation data (never commit PII)
- `/reports/` — Campaign performance reports
- `.github/workflows/` — GitHub Actions CI/CD
  - `deploy-webapp.yml` — Auto-deploys dashboard to GitHub Pages on changes to webapp/
  - `deploy-apps-script.yml` — Auto-deploys Apps Script via clasp on changes to apps-script/
  - `daily-data.yml` — Manual-only (workflow_dispatch) ad-level data pull; will be flipped to a daily cron once the snapshot output is verified

## Apps Script Deployment (clasp)

The Apps Script project is managed via clasp and deployed automatically through GitHub Actions. **Do not instruct users to copy/paste code into the Apps Script web editor** — that workflow is deprecated. Any direct edit in the web editor will be silently overwritten on the next CI run.

- **To change the script:** Edit `apps-script/Code.js` in a feature branch, open a PR, merge to main. CI runs `clasp push` + `clasp deploy` automatically.
- **To change the manifest:** Edit `apps-script/appsscript.json`, same flow.
- **To verify a deploy:** Check the "Deploy Apps Script" workflow run in GitHub Actions.
- **To roll back:** Revert the commit on main; CI redeploys the prior version.

The `.js` extension on `Code.js` is intentional — clasp uses `.js` locally and converts to `.gs` on push. Do not rename it.

Authentication uses the `CLASPRC_JSON` GitHub secret (OAuth credentials). Do not attempt to read, modify, or rotate this secret programmatically.

### Apps Script deploy: targets a fixed deployment

The `clasp deploy` step in `.github/workflows/deploy-apps-script.yml` uses
`--deploymentId ${{ secrets.CLASP_DEPLOYMENT_ID }}` to update the existing
Web App deployment in place. The dashboard's `/exec` URL is tied to that
deployment ID and never changes across CI runs. Do not remove the
`--deploymentId` flag — without it, every CI run creates a phantom
deployment with a new URL while the live dashboard URL goes stale.

## Code Style

- Python scripts: follow PEP 8, use descriptive variable names
- YAML: 2-space indentation
- Markdown: Use headers, keep docs scannable

## What @claude Can Help With

- Writing and editing ad copy for specific business verticals
- Reviewing campaign briefs and marketing plans
- Drafting email sequences and nurture flows
- Analyzing and summarizing performance data
- Building or improving automation scripts
- Proofreading for brand voice consistency

## Audit Snapshots

The pipeline supports exporting sheet data as JSON to a dedicated `audit-snapshots` branch for Claude Code to read and analyze.

- **Sheets exported:** rolling_data (last 90 days), weekly_rollup, intelligence_log, campaign_mapping
- **Branch:** `audit-snapshots` (never merged to main — data-only branch)
- **Files:** `snapshots/{sheet_name}.json` + `snapshots/_manifest.json`
- **How to export:** Run `exportAuditSnapshot()` from the Apps Script editor. Requires `GITHUB_PAT` in Script Properties (see setup instructions in Code.js).
- **How to audit:** `git fetch origin audit-snapshots`, then read files from that branch. The manifest gives row counts and column lists for a quick health check.

## Compliance Notes

- Honeycomb Credit is a regulated investment platform (Reg CF)
- Do not draft content that guarantees investment returns
- Do not include specific APY/interest rate claims without explicit approval
- All investment-related copy should include: "Investing involves risk"

## Agent Data Constraints

The `/skills/`, `/scripts/`, and `/data/` directories form the ad-level agent loop. The legacy campaign-level Apps Script pipeline keeps running unchanged.

- **Snapshots are read-only.** Files under `data/snapshots/` are committed by the `daily-data.yml` GitHub Action and represent ground truth from Meta. Do NOT manually edit them.
- **Derived signals are regenerable.** Files under `data/derived/` are computed artifacts. Re-running `python3 scripts/compute_signals.py` rebuilds them from the snapshots. They can be deleted and regenerated at any time.
- **Thresholds live in one place.** All fatigue, budget, and performance thresholds live in `data/config/benchmarks.json`. Never hardcode threshold numbers inside scripts or skills — always read from the config.
- **The agent never writes to Meta directly.** All budget recommendations flow through the existing Slack approval pipeline in `apps-script/Code.js`. The agent's role is to surface signals and propose actions, not to execute changes against the Meta API.
- **Learning-phase protection.** Never propose budget changes to ad sets where `learning_stage_info.status == "LEARNING"`. The `compute_signals.py` step already filters these and marks them `actionable: false`; defensively re-check in any skill that proposes ad-set actions.
- **Signal floors.** Fatigue signals require ≥ 3 days of data and ≥ 1,000 impressions before they're considered actionable. Don't promote a row whose `actionable` field is `false`, even if it has a flag set.
- **Daily-data workflow is manual-only for now.** `.github/workflows/daily-data.yml` runs only on workflow_dispatch until we've confirmed the first few snapshot outputs are clean. To enable the schedule, uncomment the `schedule` block in the workflow file.

## Agent Skills

Skills live in `skills/<name>/SKILL.md`. Each skill has Python scripts in
`scripts/` that handle Meta API calls and computation. Run scripts via
bash and interpret their JSON output. Skills are operating instructions,
not documentation — follow the input/output and constraints exactly.

### Shared config

All thresholds and account constants live in `data/config/benchmarks.json`.
Never hardcode thresholds in skill files, scripts, or `Code.js`.

### Sheet write path

Skills write results to the Google Sheet by calling `/exec` action handlers
defined in `Code.js`. To add a new write endpoint:
1. Add an action handler to `doGet` (or `doPost` for bulk JSON payloads) in
   `apps-script/Code.js`
2. Push to `main` — CI/CD deploys automatically via `clasp`
3. Call the endpoint from the skill

### Execution modes

- **Interactive:** Tyler prompts Claude Code directly. Output goes to terminal.
- **Autonomous:** GitHub Action runs `anthropics/claude-code-action@v1` on cron.
  Same skill, same behavior. Output goes to Slack.

### Meta API conventions

- API version: `v21.0` (matches `apps-script/Code.js:25`)
- Account ID: `act_1953544531525812`
- IC conversions: extracted from `actions[]` where `action_type` is
  `offsite_conversion.custom.2330338620810873` (the "Investment Crowdfunding
  Prequal Decision" custom conversion)
- General lead conversions: `lead`, `offsite_conversion.fb_pixel_lead`,
  `onsite_conversion.lead_grouped`
- Always filter on `effective_status=["ACTIVE","PAUSED"]` unless explicitly
  checking for deleted/archived entities

### Current skills

- **pipeline-health** — verifies data freshness, Meta token validity, IC
  conversion event existence, and dashboard endpoint health. Run before any
  other skill so a downstream "all clear" reading isn't masking a broken
  pipeline.
- **daily-check** — morning briefing: pacing vs weekly target, campaign
  portfolio sorted by CPICP, top 3 winners + bleeders, early fatigue flags,
  learning-phase ad sets, and stale creatives (>21 days active).
- **fatigue-monitor** _(coming Session 3)_ — ad-level fatigue classification
  with baseline-aware severity scoring.

### Shared client

`scripts/lib/meta.py` is the single Meta Graph API client used by the
snapshot pipeline AND the skills. It owns: HTTP retries, paging, throttle
error codes (1, 2, 4, 17, 32, 341, 613, 80000, 80004), per-call rate
limiting, IC conversion extraction, and row normalization. New skills that
need Meta data should import from this module rather than duplicate the
client.

### Snapshot pipeline (parallel to skills)

`scripts/fetch_ad_data.py` and `scripts/compute_signals.py` populate
`data/snapshots/` and `data/derived/` daily via `.github/workflows/daily-data.yml`.
This is the **historical backbone** — skills query Meta live for operational
decisions, but the snapshot pipeline preserves a 90-day audit trail and is
how the fatigue monitor will compute baselines for ads older than Meta's
14-day insight window without making a second API call per ad per run.
