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
  - `Code.js` — The complete intelligence script (~4,200 lines). Edit here, never in the Apps Script web editor
  - `.clasp.json` — Points clasp at the Apps Script project (do not edit)
  - `appsscript.json` — Apps Script manifest (scopes, runtime, Web App settings)
- `/docs/` — Living documentation (`STATE_REPORT.md`, `TECHNICAL_REFERENCE.md`) — keep in sync with code changes. Also contains `CREATIVE_INTELLIGENCE_DESIGN.md` (read-only decision history for the attribution model pivot — do not update).
- `/webapp/` — Honeycomb Ads Intelligence Dashboard (single-file React SPA on GitHub Pages)
  - `index.html` — The full dashboard app
  - `apps-script-api.gs` — Reference copy of the web API layer (handleDashboardApi_, Hive Mind chat, Slack approval flow). This is a subset of Code.js for documentation purposes — the live deployed version comes from apps-script/Code.js
- `/skills/` — Agent skill definitions (read at the start of every Claude Code session for the agent loop). Each subdirectory has a `SKILL.md` with YAML frontmatter (`name`, `description`) plus a `scripts/` directory of Python scripts the skill runs via bash. Current: `pipeline-health`, `daily-check`, `fatigue-monitor`, `creative-intelligence`, `ad-copy-generator`, `portfolio-scaling`.
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
- **Daily-data workflow runs autonomously.** `.github/workflows/daily-data.yml` is on a daily 8 AM ET cron and commits the snapshot directly to main. Manual `workflow_dispatch` is preserved for backfills via the `start_date` / `end_date` inputs.

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
- **Autonomous:** GitHub Action runs `anthropics/claude-code-action@v1` on
  cron or manual dispatch. Same skill files, same behavior. Output goes to
  Slack on WARN/FAIL (silent on PASS) and to the workflow log either way.

### Autonomous workflows

Each skill that needs a scheduled run gets its own workflow file under
`.github/workflows/agent-<skill>.yml`. Current:

> **DST drift caveat:** GitHub cron expressions are UTC-only. All cron
> times below are tuned for **Eastern Daylight Time** (UTC-4, ~Mar-Nov).
> During Eastern Standard Time (UTC-5, ~Nov-Mar) every workflow runs
> **one hour earlier** than the documented ET time — e.g.
> "9 AM ET" becomes 8 AM during EST. Most workflow YAMLs note this
> inline; centralizing the caveat here so a winter-time stakeholder
> isn't surprised. To fix permanently we'd need a DST-aware scheduler
> (CRON_TZ isn't supported by GitHub Actions); accepted as a known
> drift.

- `agent-pipeline-health.yml` — runs `pipeline-health` skill. Daily cron
  active at 9 AM ET (UTC 13:00).
- `agent-daily-check.yml` — runs `daily-check` skill. Daily cron active
  at 8:30 AM ET (UTC 12:30).
- `agent-fatigue-monitor.yml` — runs `fatigue-monitor` skill. Twice-
  weekly cron active for Mon + Thu 9:30 AM ET (UTC 13:30) — fatigue
  moves slowly, daily would over-query Meta.
- `agent-creative-intelligence.yml` — runs `creative-intelligence` skill.
  Weekly cron active for Mondays at 10 AM ET (UTC 14:00). Weekly cadence
  matches the corpus-aggregation attribution model — variant-level
  performance signals shift over weeks, not days.
- `agent-creative-preview.yml` — `workflow_dispatch` only. $0 alternative
  path: same checkout + Meta + cache-commit mechanics as
  `agent-creative-intelligence.yml` but skips Anthropic calls. Runs the
  dataset builder + a deterministic pure-Python preview script. Used to
  validate cache-commit mechanics without spending model dollars.
- `agent-ad-copy-generator.yml` — `workflow_dispatch` only. Drafts ad
  copy from the Creative Intelligence cache; never auto-published.
- `agent-portfolio-scaling.yml` — runs `portfolio-scaling` skill.
  Weekly cron Tuesdays at 9:30 AM ET (UTC 13:30). Two Python steps
  (compute_scaling_profiles → compute_reallocation), commits derived
  JSON to main, then claude-code-action composes the four-section
  Slack brief and registers the proposal via /exec for Tyler's
  two-step approval. The execution side runs daily at 3 AM as
  `executeStrategicChanges` in Code.js — daily-with-cheap-no-op
  rather than weekly Wed-only because daily is more robust against
  missed-window risk at the same cost (one Script Property read on
  no-op days).

### New-skill architectural pattern _(established 2026-05-05)_

Two distinct production-run findings established a recommended pattern
for any new skill that involves either Anthropic SDK calls from a
subprocess OR committing artifacts back to main:

1. **Run Python scripts as ordinary workflow steps**, not inside
   `claude-code-action`'s Bash prompt. Verified: 526/526
   APIConnectionError when scripts run inside the action's prompt;
   0/526 when they run as separate workflow steps. Suspected cause is
   subprocess inheritance of an `ANTHROPIC_BASE_URL` or HTTP-proxy env
   var the action sets.
2. **Commit any cache/artifact changes BEFORE invoking
   claude-code-action**. The action strips or invalidates the http
   extraheader credentials that `actions/checkout@v4` persists; pushes
   AFTER it fail with `Password authentication is not supported`.
3. **Use prompt caching on identical-across-run system messages.**
   `cache_control: {"type": "ephemeral"}` cuts effective per-call token
   cost ~10× after the first call. Saves money AND keeps total
   tokens-per-min under Anthropic's 30k limit on workflows with many
   parallel calls. Confirmed for `categorize_creative.py`: cost dropped
   from ~$5 to ~$1-2/run, rate-limit failures from 18% to ~1%.

The pipeline-health, daily-check, and fatigue-monitor skills predate
these findings. They run scripts inside the action's prompt and don't
commit cache. They work fine because they don't trigger either failure
mode (no Anthropic SDK subprocess calls; no commit-back). New skills
with either dependency should follow the Creative Intelligence pattern.

Every agent workflow uses the same template (lessons learned from the
agent-pipeline-health iteration cycle):

- `permissions: contents:read + id-token:write` (latter required by
  claude-code-action@v1 for OIDC auth)
- `claude_args: "--permission-mode bypassPermissions"` (workflow is the
  trust boundary; without this Claude can't run any Bash command in CI)
- `show_full_output: "true"` and `display_report: "true"` (surface
  Claude's output in the workflow log instead of saving it silently)
- A "Dump Claude execution log" step with `if: always()` that cats
  `/tmp/claude-execution-output.json` (belt-and-suspenders diagnostic
  fallback)

Each agent workflow needs these GitHub Secrets on the repo:
- `ANTHROPIC_API_KEY` — already set (used by the existing `claude.yml` too)
- `META_ACCESS_TOKEN` — same secret used by `daily-data.yml`
- `SLACK_WEBHOOK_URL` — optional; if unset, skills skip Slack and surface
  output in the workflow log only

### Dual scheduling: GitHub cron + Apps Script fallback

Each agent workflow has TWO scheduling paths:

1. **GitHub Actions cron** (primary) — the `schedule:` block in each
   `agent-*.yml` file fires daily/twice-weekly. Best-effort: runs can
   be delayed up to 30+ minutes, occasionally skipped during GitHub
   incidents, and silently disabled after 60 days of zero pushes.
2. **Apps Script trigger** (fallback) — `triggerAgent*IfNeeded()`
   functions in `Code.js` fire ~3 hours later (noon-2 PM ET) and
   dispatch via the GitHub workflow_dispatch API only if no recent
   successful run exists for that workflow. Apps Script's cron runs
   on Google's infrastructure and is more reliable.

The fallback functions share the existing `GITHUB_PAT` Script Property
already used by `exportAuditSnapshot()`. Run `testAgentDispatch()` from
the Apps Script editor to verify scopes; classic PAT with `repo` works,
fine-grained needs Actions: Read + Write on this repo.

If both paths fire simultaneously (rare — Apps Script triggers always
check first), the workflow's `concurrency:` group queues the second
run rather than racing.

### Agent loop status tracking — issue #48

Every autonomous workflow run (`daily-data`, `agent-pipeline-health`,
`agent-daily-check`, `agent-fatigue-monitor`, `agent-creative-intelligence`,
`agent-creative-preview`, `agent-ad-copy-generator`,
`agent-portfolio-scaling`) posts a status comment to
[issue #48](https://github.com/tylerhoneycomb/marketing-claude-honeycomb/issues/48)
on completion (`if: always()` so failures report too,
`continue-on-error: true` so a missing/closed issue can't break the
run). Each comment includes:

- Workflow name + run conclusion (`success` / `failure`)
- A one-line skill-specific summary (e.g.
  `PASS 4/0/0` for pipeline-health,
  `evaluated=12 fatigued=2 conflicts=1` for fatigue-monitor, or
  `verticals=15 variants=426 confident=4 winners_top=benefit_led
  cache_commit=ok` for creative-intelligence — the latter combines
  Claude's brief one-liner with the cache-commit step's outcome on a
  single line, since the commit step writes to a separate file
  (`/tmp/cache_commit_status.txt`) so Claude can't accidentally
  overwrite it)
- Direct link to the workflow run

Agent workflow prompts instruct Claude to write the one-liner summary
to `/tmp/agent_status.txt` before exiting; the status step picks it up.
For `daily-data.yml`, the status step reads counts directly from the
just-committed `_manifest.json`.

Reading the issue comments is the fastest way to verify the agent loop
is firing correctly — sort by oldest-first for a chronological log.
Close + reopen a fresh issue when the comment volume gets noisy
(close the old one, create a new one, update the issue number in all
eight workflow YAML files: daily-data, agent-pipeline-health,
agent-daily-check, agent-fatigue-monitor, agent-creative-intelligence,
agent-creative-preview, agent-ad-copy-generator, agent-portfolio-scaling).

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
- **fatigue-monitor** — per-ad fatigue classification (saturated / fatigued /
  early_fatigue / underperforming / healthy) with baseline-aware severity
  scoring. Three baseline paths: in-range (no extra API call), historical
  (one consolidated query for all Path-B ads), or estimated. Cross-references
  pending budget proposals via `?action=budget-queue-read` and flags
  conflicts (e.g. fatiguing ad in a campaign with a pending budget INCREASE).
- **creative-intelligence** — weekly Monday brief on what creative copy and
  visual patterns are winning across the portfolio. Per [docs/CREATIVE_INTELLIGENCE_DESIGN.md](./docs/CREATIVE_INTELLIGENCE_DESIGN.md)
  the attribution spine is corpus-level text aggregation, not per-ad asset_id
  breakdown — three rounds of Meta investigation showed asset breakdowns
  don't return reliable per-variant conversion data for asset_feed_spec ads.
  Two-script pipeline: `categorize_creative.py` calls Anthropic API once
  per unique variant text + image (hash-deduped, atomic incremental writes
  to `data/creatives/categorizations.json`); `build_creative_dataset.py`
  joins snapshots + creative cache + categorizations and emits the
  variant-grain corpus to `/tmp/creative_dataset.json`. SKILL.md output
  rules require briefs that quote actual winning copy + cite real numbers
  + honor confidence labels (≥10 ads + ≥25 IC = confident; ≥5 + ≥10 =
  directional; below = insufficient hypothesis-only).
- **ad-copy-generator** — drafts new ad-copy variants for a target vertical
  from the Creative Intelligence dataset. Splits each dimension at median
  CPICP (winners below, losers above) so small variant pools still produce
  distinct cohorts. Forces tool_use on a `draft_ads` tool returning
  `(patterns_observed, drafts[])` where each draft is a body + title +
  description + pattern_followed. Compliance regex backstop catches
  quantified-return language, guarantee language, FDIC comparisons, and
  multiple-x return claims; drafts are tagged ⚠️ when flagged. Output is
  human-readable markdown at `data/drafts/<date>-<vertical>.md` with a
  6-item reviewer checklist appended. **Drafts are never auto-published**
  — every draft requires human review per the compliance checklist. The
  skill is `workflow_dispatch`-only; Tyler runs it after the Monday
  Creative Intelligence brief.
- **portfolio-scaling** — weekly Tuesday brief that adds a structural
  diagnosis layer on top of the existing budget optimizer. Classifies
  each vertical as scalable / stable / saturating / over-invested over a
  12-week trailing window using elasticity (Pearson r of weekly spend vs
  weekly CPL), median-split CPL degradation, and 4-week
  frequency/CPM trends. Modifier `new_audience_needed` fires when
  frequency + CPM both rise over 4+ weeks (vertical-level early warning,
  before any single campaign hits the optimizer's freq=2.0 threshold).
  Produces a pool-based budget reallocation: saturating + over-invested
  verticals contribute decreases sized by elasticity severity, scalable +
  stable verticals absorb weighted by inverse CPICP. The pool is bounded
  by the spend tolerance band; can be net-positive or net-negative.
  **Shares a 12% weekly cap with the daily optimizer** (the cap counts
  optimizer + knockdown + strategic movement summed across the week).
  Wed-Mon lockout window prevents the optimizer from acting on
  affected campaigns immediately after the strategic move; lockout
  expires at next-Tuesday 00:00 UTC so the optimizer's Tuesday cycle
  is free. Strategic execution path reuses `applyBudgetQueueRows_`
  with a `source: strategic` filter on `budget_queue` (a 13th column
  added to the schema). Tagging: optimizer Slack proposals now show
  the campaign's vertical classification inline.

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
