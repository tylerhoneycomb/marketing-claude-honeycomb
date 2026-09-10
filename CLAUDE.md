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
  - `Code.js` — The complete intelligence script (~6,900 lines). Edit here, never in the Apps Script web editor
  - `.clasp.json` — Points clasp at the Apps Script project (do not edit)
  - `appsscript.json` — Apps Script manifest (scopes, runtime, Web App settings)
- `/docs/` — Living documentation (`STATE_REPORT.md`, `TECHNICAL_REFERENCE.md`) — keep in sync with code changes
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
  - `daily-data.yml` — Daily ad-level data pull (cron `37 12 * * *` UTC) plus the folded-in pipeline-health check; `workflow_dispatch` for backfills
  - `agent-*.yml` — one workflow per scheduled skill (see "Autonomous workflows" below)

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

The `/skills/`, `/scripts/`, and `/data/` directories form the ad-level agent loop. The legacy campaign-level Apps Script pipeline keeps running alongside it; since 2026-09-10 its Slack output (daily digest, weekly narrative, budget and scaling notices) follows the same lead-first standard as the skills — see "Slack messages are lead-first" below.

- **Snapshots are read-only.** Files under `data/snapshots/` are committed by the `daily-data.yml` GitHub Action and represent ground truth from Meta. Do NOT manually edit them. To *restate* history after a field's meaning changes, re-pull it: dispatch `daily-data.yml` with `start_date` + `end_date` and `force: true`. Without `force` a backfill over dates that already have a manifest is a silent no-op.
- **Derived signals are regenerable.** Files under `data/derived/` are computed artifacts. Re-running `python3 scripts/compute_signals.py` rebuilds them from the snapshots. They can be deleted and regenerated at any time.
- **Thresholds live in one place.** All fatigue, budget, and performance thresholds live in `data/config/benchmarks.json`. Never hardcode threshold numbers inside scripts or skills — always read from the config.
- **The agent never writes to Meta directly.** All budget recommendations flow through the existing Slack approval pipeline in `apps-script/Code.js`. The agent's role is to surface signals and propose actions, not to execute changes against the Meta API.
- **Learning-phase protection.** Never propose budget changes to ad sets where `learning_stage_info.status == "LEARNING"`. The `compute_signals.py` step already filters these and marks them `actionable: false`; defensively re-check in any skill that proposes ad-set actions.
- **Signal floors.** Fatigue signals require ≥ 3 days of data and ≥ 1,000 impressions before they're considered actionable. Don't promote a row whose `actionable` field is `false`, even if it has a flag set.
- **Daily-data workflow runs autonomously.** `.github/workflows/daily-data.yml` is on a daily ~8:37 AM ET cron (`37 12 * * *` UTC — minute deliberately off `:00` to dodge GitHub's top-of-hour scheduled-run queue delay, which was pushing the old `0 12` run 2-5 hours late) and commits the snapshot directly to main. Manual `workflow_dispatch` is preserved for backfills via the `start_date` / `end_date` inputs.

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
2. Add the action to `PROTECTED_EXEC_ACTIONS` in `Code.js` if it costs money
   or changes state
3. Push to `main` — CI/CD deploys automatically via `clasp`
4. Call the endpoint from the skill, passing `key=exec_key()` (see
   `scripts/lib/exec_api.py`)

### /exec is authenticated _(added 2026-09-09)_

The Web App is deployed `ANYONE_ANONYMOUS` and its URL is committed in
`benchmarks.json`, so before this every caller who had the URL could spend
the Anthropic key through `chat`, trigger `run_budget_analysis`, and POST
`scaling-queue-write` to append budget rows **and get back a valid approval
token**.

Side-effecting actions now require a shared secret:

- **Script Property** `EXEC_SHARED_SECRET` in the Apps Script project
  (Project Settings → Script Properties), at least 16 characters.
- **GitHub secret** `EXEC_SHARED_SECRET` with the same value, so skills
  authenticate.
- **Dashboard**: optional "API key" field in the Connect API dialog, stored
  in that browser only. Needed only for chat / budget analysis / spend
  target; all charts and tables load without it.

The gate **fails closed** — if the Script Property is unset or under 16
characters, protected actions are refused rather than left open. Read-only
actions are never gated. Slack approve/reject links are not gated either:
they already authenticate with a per-proposal token, and a shared key in a
Slack URL would be visible to the channel.

Covered by `scripts/tests/exec_auth.test.js` (`node scripts/tests/exec_auth.test.js`).

> **`apps-script/` has no `.claspignore`,** and `.clasp.json` sets
> `skipSubdirectories: false` — so every `.js` file under `apps-script/` is
> uploaded into the live Apps Script project by `clasp push`. Never put
> tests or helper scripts there; the exec-auth test lives in
> `scripts/tests/` for exactly this reason.

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

- `pipeline-health` — **MERGED INTO `daily-data.yml` 2026-06-23.** The
  standalone `agent-pipeline-health.yml` workflow was retired. The skill's
  script (`check_health.py`) is fully deterministic — it runs the five
  checks, writes the `pipeline_health` Sheet rows, and prints JSON — so it
  no longer needs an LLM. It now runs as two ordinary steps at the end of
  the daily-data job: `check_health.py` then `report_health.py` (terminal
  summary + Slack alert on WARN/FAIL + the issue-#48 one-liner). This
  removed a daily `claude-code-action` invocation (and its Anthropic spend)
  and a whole separate scheduled workflow. The check now runs right after
  the 8 AM ET data pull instead of at 9 AM. The Apps Script fallback
  `triggerAgentPipelineHealthIfNeeded` was repointed to dispatch
  `daily-data.yml` (function name kept so the installed trigger still binds).
- `agent-daily-check.yml` — runs `daily-check` skill. **PAUSED 2026-06-08**
  (Tyler asked to stop the daily Slack briefing). Cron schedule and the
  Apps Script fallback (`triggerAgentDailyCheckIfNeeded`) are both
  commented out / early-returned. Manual `workflow_dispatch` still works.
  Daily cron was 8:30 AM ET (UTC 12:30) — preserved as a comment in the
  YAML so it's obvious how to unpause.
- `agent-fatigue-monitor.yml` — runs `fatigue-monitor` skill.
  **PAUSED 2026-06-10** (Tyler asked to stop the Mon/Thu Slack brief).
  Cron schedule and the Apps Script fallback
  (`triggerAgentFatigueMonitorIfNeeded`) are both commented out /
  early-returned. Manual `workflow_dispatch` still works. Twice-weekly
  cron was Mon + Thu 9:30 AM ET (UTC 13:30) — fatigue moves slowly,
  daily would over-query Meta; preserved as a comment in the YAML so
  it's obvious how to unpause.
- `agent-creative-intelligence.yml` — runs `creative-intelligence` skill.
  **PAUSED 2026-06-08** (Tyler asked to stop the weekly Slack brief and
  the recurring Anthropic categorization spend). Cron schedule and the
  Apps Script fallback (`triggerAgentCreativeIntelligenceIfNeeded`) are
  both commented out / early-returned. Manual `workflow_dispatch` still
  works (useful for refreshing the cache on demand). Weekly cron was
  Mondays at 10 AM ET (UTC 14:00); the cadence matched the
  corpus-aggregation attribution model — variant-level performance
  signals shift over weeks, not days. The `claude-code-action` step
  receives `EXEC_SHARED_SECRET` and sends it as `key` on the
  `creative-intelligence-write` POST (added 2026-09-10 — without it the
  Sheet write was refused while the Slack post still shipped).
- `agent-creative-preview.yml` — `workflow_dispatch` only. $0 alternative
  path: same checkout + Meta + cache-commit mechanics as
  `agent-creative-intelligence.yml` but skips Anthropic calls. Runs the
  dataset builder + a deterministic pure-Python preview script. Used to
  validate cache-commit mechanics without spending model dollars.
- `agent-ad-copy-generator.yml` — `workflow_dispatch` only. Drafts ad
  copy from the Creative Intelligence cache; never auto-published.
- `agent-portfolio-scaling.yml` — runs `portfolio-scaling` skill.
  Weekly cron Tuesdays at ~9:43 AM ET (UTC 13:43 — minute moved off
  `:30` on 2026-06-23 to dodge GitHub's scheduled-run queue contention).
  Two Python steps
  (compute_scaling_profiles → compute_reallocation), commits derived
  JSON to main, then claude-code-action composes the four-section,
  leads-first Slack brief and registers the proposal via
  `scaling-queue-write` (the step receives `EXEC_SHARED_SECRET` and sends
  it as `key`; on `{"error":"unauthorized"}` it posts without approval
  links rather than inventing URLs) for Tyler's two-step approval. Until
  2026-09-10 the brief's approve/reject links could never work — `doGet`
  checked `BUDGET_PENDING_TOKEN` before reaching the `*_scaling` branches;
  they now sit above that gate. The execution side runs daily at 3 AM as
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

The daily-check and fatigue-monitor skills predate these findings. They
run scripts inside the action's prompt and don't commit cache. They work
fine because they don't trigger either failure mode (no Anthropic SDK
subprocess calls; no commit-back). New skills with either dependency
should follow the Creative Intelligence pattern. (pipeline-health was a
third such skill until 2026-06-23, when it was made fully deterministic
and merged into `daily-data.yml` — it no longer uses claude-code-action
at all.)

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
- `EXEC_SHARED_SECRET` — **required for any skill that writes to the Sheet.**
  Must match the Script Property of the same name in the Apps Script
  project. Without it the `*-write` and `scaling-queue-write` actions
  return `{"error": "unauthorized"}`. Pass it to the Python steps AND to
  the `claude-code-action` step whenever the prompt itself does the POST
  (creative-intelligence, portfolio-scaling). See "/exec is authenticated"
  above.

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

Every autonomous workflow run (`daily-data`,
`agent-daily-check`, `agent-fatigue-monitor`, `agent-creative-intelligence`,
`agent-creative-preview`, `agent-ad-copy-generator`,
`agent-portfolio-scaling`) posts a status comment to
[issue #48](https://github.com/tylerhoneycomb/marketing-claude-honeycomb/issues/48)
on completion (`if: always()` so failures report too,
`continue-on-error: true` so a missing/closed issue can't break the
run). Each comment includes:

- Workflow name + run conclusion (`success` / `failure`)
- A one-line skill-specific summary (e.g.
  `PASS 5/0/0` for pipeline-health,
  `evaluated=12 fatigued=2 conflicts=1` for fatigue-monitor,
  `pacing=on_pace spend=$1523 leads=98 cpl=$15.54 winners=3 bleeders=1
  fatigue_flags=2` for daily-check,
  `leads=1240 cpl=$14.19 verticals=15 scalable=4 …` for
  portfolio-scaling, or
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
just-committed `_manifest.json` and appends a lead heartbeat
(`leads=N spend=$X`, summed from `ad_insights.json` with the pre-pivot
`conversions` alias as fallback), and a SECOND status step posts the
`pipeline-health` one-liner (written deterministically by
`report_health.py` to `/tmp/health_status.txt`) — so `daily-data` posts
two comments per run: `**daily-data**` and `**pipeline-health**`. The
pipeline-health one-liner keeps its `PASS p/w/f` shape from when it was
its own workflow; the counts now sum to five checks (`PASS 5/0/0`).
Where a one-liner carries an IC count it is a trailing `ic=N` token,
never the first metric.

Reading the issue comments is the fastest way to verify the agent loop
is firing correctly — sort by oldest-first for a chronological log.
Close + reopen a fresh issue when the comment volume gets noisy
(close the old one, create a new one, update the issue number in all
seven workflow YAML files: daily-data (two comments — daily-data +
pipeline-health), agent-daily-check, agent-fatigue-monitor,
agent-creative-intelligence, agent-creative-preview,
agent-ad-copy-generator, agent-portfolio-scaling).

### Meta API conventions

- API version: `v21.0` (matches `apps-script/Code.js:25`)
- Account ID: `act_1953544531525812`
- The funnel is defined in exactly one place: `conversions` in
  `data/config/benchmarks.json`, read via `funnel_from_config()` in
  `scripts/lib/meta.py`. Never hardcode a conversion ID or action type.

**Leads are the primary metric.** Every ranking, budget, pacing and
classification decision keys on leads and CPL. The account pivoted to
lead-optimized campaigns on 2026-08-19 (`ICD-Broad-Q2-2026` →
`LEADS-Broad-Q3-2026`) and the code followed on 2026-09-09.

| Tier | Field | Source | Role |
|---|---|---|---|
| Primary | `leads` | `lead` → `offsite_conversion.fb_pixel_lead` → `onsite_web_lead` → `onsite_conversion.lead_grouped` | Drives every automated decision |
| Quality | `prequal_decisions` | `custom.1153878920152279` | Reported; a lead that reached a prequal decision (~90% of leads) |
| Subtype | `ic_conversions` | `custom.2330338620810873` | Reported only |
| Subtype | `rewards_conversions` | `custom.1527298745037132` | Reported only |

- **Lead action types are a priority chain, never a sum.** Verified
  2026-09-09 against both live campaigns: `lead`,
  `offsite_conversion.fb_pixel_lead` and `onsite_web_lead` return
  *identical* values (215/215/215 and 180/180/180). They are alternative
  counts of one event; summing them multiplies leads. First present type wins.
- **IC is reported, never optimized on.** It fired once against $6,296 of
  spend and 395 leads over the 30 days to 2026-09-08. The conversion is
  configured correctly and still active — the current broad audience simply
  converts to rewards crowdfunding instead. Do not reintroduce CPICP as a
  sort key, threshold or weight.
- Always filter on `effective_status=["ACTIVE","PAUSED"]` unless explicitly
  checking for deleted/archived entities. **Budget proposals require
  `effective_status == "ACTIVE"`** — a move against a paused campaign is
  inert, and as of 2026-09-08 only 3 of 29 campaigns were active while 86%
  of the reported portfolio budget belonged to paused ones. The same rule
  applies to portfolio totals: `compute_scaling_profiles.py` sums the daily
  budget of ACTIVE campaigns only (`portfolio.current_total_daily_cents`)
  and carries the paused remainder separately (`paused_total_daily_cents`)
  — counting paused budgets made the $2,100/week tolerance band read as
  permanently breached and scaled every increase to zero.

### Slack messages are lead-first _(standard applied 2026-09-10)_

Every Slack-bound message — the Apps Script daily digest and weekly
narrative, the budget proposal / execution / expiry notices, the strategic
approve / reject / execution notices, the pipeline-health alert, and every
skill brief composed by `claude-code-action` — follows one standard:

- **Headline = leads and cost-per-lead.** The first numbers a reader sees
  are lead count, CPL (and usually spend). Titles say Leads
  (`Honeycomb Leads — <date>`, `Daily Lead Check`, `Honeycomb Scaling`).
- **Sort orders and top/bottom rankings are by CPL.** Winners best-CPL
  first, bleeders and reductions worst-CPL first, verticals CPL-ascending
  within class. Rows with no leads sort last (or first when the point is
  "spend without leads") — never by an IC figure.
- **Thresholds and alerts key on CPL** (`lead_economics.target_cpl_dollars`
  × `cpl_critical_multiple`, `fatigue.cpl_inflation_*_pct`) gated on a
  minimum lead count so a one-lead swing cannot fire an alert.
- **IC / ICP / CPICP may appear only as a clearly-secondary line** — e.g.
  `_of which N reached an IC decision_` — never as headline, sort key,
  threshold, title, or the reason to flag or retire anything. Skills omit
  the line when the count is 0.
- **Stale-state copy is banned.** Messages must not describe the daily
  optimizer as running while `BUDGET_OPTIMIZER_PAUSED` is true; the
  lockout / cadence sentences are built by `scalingLockoutStatusLine_` /
  `scalingRejectStatusLine_` and the Monday budget block's footer reads
  the flag.

`Code.js` cannot read `benchmarks.json`, so the lead thresholds it needs are
mirrored as named constants (`TARGET_CPL_DOLLARS`, `CPL_CRITICAL_MULTIPLE`,
`CPL_SPIKE_WARNING_PCT`, `CPL_FLAG_MIN_WEEKLY_LEADS`,
`CPL_FLAG_MIN_DAILY_LEADS`, `ZERO_LEAD_SPEND_WATCH_DOLLARS`) under the same
dual-source rule as `SCALING_MAX_WEEKLY_PCT` — change both places together.
Skill briefs take their targets from the script JSON (e.g.
`stats.target_cpl_dollars`), never from a literal in a prompt.

Wire contracts are unchanged by the pass: every Sheet-write handler in
`Code.js` reads payload keys by name and silently drops keys it has no
column for, so skills keep sending the legacy IC-named keys
(`total_icps`, `portfolio_cpicp`, `cpicp`, `ic_rate`, `median_cpicp`,
`ic_total`, `top_body_cpicp`) carrying lead values and add the lead-named
keys additively. `scaling_log` therefore still has no CPL or lead column;
the Tuesday brief computes week-over-week CPL from `?action=rollup`.

### Current skills

- **pipeline-health** — five checks: data freshness, Meta token validity,
  every configured funnel custom conversion (existence, archived state and
  `last_fired_time` — quality tier first, subtypes trailing under
  `subtypes (reported only)`, a subtype problem is never more than WARN),
  dashboard endpoint health, and snapshot volume, which also reports the
  newest snapshot's lead total and spend (`N leads on $S spend`) — the only
  health signal for the primary tier, since `leads` is a pixel action that
  `customconversions` cannot see. Slack lines are shaped
  `STATUS check_name: detail` so an IC token can never be the first word.
  Run before any other skill so a downstream "all clear" reading isn't
  masking a broken pipeline. Autonomously it runs as two deterministic
  steps inside `daily-data.yml` (`check_health.py` → `report_health.py`);
  there is no separate scheduled workflow and no LLM in the autonomous path.
- **daily-check** — morning briefing titled `📊 Daily Lead Check`: a totals
  headline (leads · CPL · spend · prequal decisions, IC only as a trailing
  parenthetical), pacing vs weekly target, campaign portfolio sorted by
  CPL, top 3 winners (`$CPL, N leads`) + bleeders rendered by `reason`
  (spend-without-leads first, then CPL vs ad-set CPL, CTR only as the
  no-lead-data fallback), early fatigue flags, learning-phase ad sets, and
  stale creatives (>21 days active).
- **fatigue-monitor** — per-ad fatigue classification (saturated / fatigued /
  early_fatigue / underperforming / healthy). CPL inflation vs the ad's
  peak-window baseline (`fatigue.cpl_inflation_warning/critical_pct`) is a
  classification input alongside CTR decline and frequency; CPC is
  diagnostic only. The brief opens with `N ads at risk · $spend / leads
  (CPL $x) last 7d`, each ad's lead line comes first, and `classifications`
  is pre-sorted (severity → conflicts → zero-lead spend → CPL desc).
  Three baseline paths: in-range (no extra API call), historical (one
  consolidated query for all Path-B ads), or estimated. Cross-references
  pending budget proposals via `?action=budget-queue-read` (newest pending
  increase per campaign) and composes a lead-first conflict line in Python
  (e.g. fatiguing ad in a campaign with a pending budget INCREASE).
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
  (`ad_count`, `total_leads`, `cpl`) + honor the confidence labels in
  `benchmarks.json:creative_intelligence` (≥10 ads + ≥100 leads =
  confident; ≥5 + ≥40 = directional; below = insufficient
  hypothesis-only). The Slack post opens with `🎯 Creative Intelligence —
  <until> — N leads at $X median CPL across M ads`; the Sheet row's
  `top_body_*` is the lowest-CPL confident body (never chosen by CPICP).
- **ad-copy-generator** — drafts new ad-copy variants for a target vertical
  from the Creative Intelligence dataset. Splits each dimension at median
  CPL (winners below, losers above) so small variant pools still produce
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
  stable verticals absorb weighted by inverse CPL. The pool is bounded
  by the spend tolerance band; can be net-positive or net-negative.
  **Shares a 12% weekly cap with the daily optimizer** (the cap counts
  optimizer + knockdown + strategic movement summed across the week).
  Wed-Mon lockout window prevents the optimizer from acting on
  affected campaigns immediately after the strategic move; lockout
  expires at next-Tuesday 00:00 UTC so the optimizer's Tuesday cycle
  is free (while the optimizer is paused the lockout is still recorded,
  and the Slack copy says so). Strategic execution path reuses
  `applyBudgetQueueRows_` with a `source: strategic` filter on
  `budget_queue` (a 13th column added to the schema). Tagging: optimizer
  Slack proposals show the campaign's vertical classification inline.
  The Tuesday brief opens with the 12-week portfolio line
  (`portfolio.total_leads / total_spend / cpl / median_cpl`), lists
  verticals CPL-ascending within class, prints ACTIVE-but-`insufficient`
  verticals as a "too new to classify" one-liner so the campaigns Meta is
  delivering never drop out, frames the pool move in lead terms, and
  closes with a single `of which N reached an IC decision` line.
  `LEADS-*` campaign names bucket into their vertical (`LEADS-Broad-Q3-2026`
  → `broad`) and the optimizer-eligibility gate counts lifetime leads.

### Shared client

`scripts/lib/meta.py` is the single Meta Graph API client used by the
snapshot pipeline AND the skills. It owns: HTTP retries, paging, throttle
error codes (1, 2, 4, 17, 32, 341, 613, 80000, 80004), per-call rate
limiting, funnel-tier conversion extraction, and row normalization. New skills that
need Meta data should import from this module rather than duplicate the
client.

### Snapshot pipeline (parallel to skills)

`scripts/fetch_ad_data.py` and `scripts/compute_signals.py` populate
`data/snapshots/` and `data/derived/` daily via `.github/workflows/daily-data.yml`.
This is the **historical backbone** — skills query Meta live for operational
decisions, but the snapshot pipeline preserves a 90-day audit trail and is
how the fatigue monitor will compute baselines for ads older than Meta's
14-day insight window without making a second API call per ad per run.
