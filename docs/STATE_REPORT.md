# Project State Report

_Last updated: 2026-09-10 (**Every Slack message now leads with leads and cost-per-lead.** The daily "Honeycomb Ads" digest and the Monday narrative are retitled "Honeycomb Leads" and headline leads, CPL against the $16 target and spend, with the IC count demoted to one "of which N reached an IC decision" line; the campaign breakdown, the winners/bleeders lists and the Tuesday scaling brief all sort by CPL; every alert threshold is a CPL rule gated on a minimum lead count. The paused budget optimizer's ranking was rebuilt on CPL so it can be switched back on without shipping IC-first proposals (it stays off until Tyler signs off), and its stale approval tokens are cleared. The pass also fixed three things it found: the Tuesday brief's approve/reject links had never worked (the web app checked the optimizer's token first), the scaling portfolio total counted paused campaigns' budgets, and the messages still described the optimizer as running. Prior: 2026-09-09 **pivoted the whole system from Investment Crowdfunding conversions to leads**, then acted on three things the pivot surfaced. (1) **Turned off the daily budget optimizer** — it ranked campaigns by cost-per-IC, a number that fired once in 30 days, and was moving real money on it. Strategic reallocation from the weekly portfolio brief is unaffected. (2) **Closed the open `/exec` endpoint** — the web app was reachable by anyone with the URL, which is public in this repo; anyone could spend the Anthropic key, trigger a budget analysis, or queue budget rows and receive a valid approval token. Money-spending and state-changing actions now require a shared secret. (3) **Set the spend target to $300/day ($2,100/week)** to match the account's actual run rate, replacing a $10,000/week figure that no longer reflected anything. Prior: 2026-06-23 consolidated the pipeline-health check into `daily-data.yml`; 2026-06-10 PAUSED `agent-fatigue-monitor.yml`; daily-check, creative-intelligence and fatigue-monitor remain paused)_

This report describes what the `marketing-claude-honeycomb` project is, what it currently does, what's working well, and where the current limitations are. Written in plain English for non-technical stakeholders. For implementation details see [TECHNICAL_REFERENCE.md](./TECHNICAL_REFERENCE.md).

> **Maintenance rule:** This document should be updated whenever a change materially affects functionality, limitations, or operational behavior. See [CLAUDE.md](../CLAUDE.md) for the docs-update rule.

---

## What this project is

A **marketing operations platform** for Honeycomb Credit's small-business investment crowdfunding campaigns. It is NOT product code — it's an automation layer that helps the marketing team run Meta (Facebook/Instagram) ads more efficiently.

Four things live inside the repo:

1. **The "brain"** — a Google Apps Script program (~6,900 lines) that runs every day, pulls data from Meta and HubSpot, does the math, writes summaries, and proposes budget changes.
2. **The "dashboard"** — a web page (hosted on GitHub Pages) where the team can see charts, check campaign health, and ask questions via an AI chat called "Hive Mind."
3. **The "plumbing"** — GitHub Actions that automatically push code changes to the Google Apps Script servers whenever something is merged, so nobody has to copy/paste into the Apps Script web editor.
4. **The "agent layer"** _(new, 2026-05-02)_ — an ad-level data pipeline (`scripts/`) and skill files (`skills/`) that let Claude Code monitor individual ads, detect creative fatigue, and propose budget shifts. Snapshots are stored as JSON files under `data/` (the repo itself acts as the database). The agent layer feeds recommendations into the existing Slack approval pipeline — it never writes to Meta directly.

The campaign-level system is connected through a single Google Spreadsheet. The agent layer is connected through JSON files in the repo.

---

## What we measure, and why it changed

For most of 2026 this system optimized on one number: the cost of an
**Investment Crowdfunding prequal decision** (CPICP). That was the right
choice while IC decisions were 5-12% of all leads. It stopped being the right
choice in August.

On 2026-08-19 the ad account was rebuilt around lead-optimized campaigns
(`ICD-Broad-Q2-2026` gave way to `LEADS-Broad-Q3-2026`). The new broad audience
converts to *rewards* crowdfunding, not investment crowdfunding. The result:

| Month | Spend | Leads | Cost per lead | IC decisions |
|---|---|---|---|---|
| June | $39,862 | 2,998 | $13.30 | 244 |
| July | $22,340 | 1,591 | $14.04 | 189 |
| August | $8,970 | 576 | $15.57 | 23 |
| Sept (first 7 days) | $2,245 | 178 | $12.61 | 1 |

The IC tracking was never broken — the conversion is live and correctly
configured, and last fired on 2026-09-03. The audience simply changed. But
because the code still ranked everything by cost-per-IC, it was sorting on a
number that was undefined for nearly every campaign.

As of 2026-09-09 the funnel is measured in three tiers:

- **Leads** — the primary metric. Everything automated keys on this.
- **Prequal decisions** — a lead that reached a decision. Fires on about 90%
  of leads, so it is dense enough to be trustworthy. Reported, not optimized on.
- **Decision subtypes** — investment crowdfunding and rewards crowdfunding.
  Reported only, on their own dashboard tab.

Nothing was lost in the change: leads were already being collected on every
one of the 250 days of history, so the full record is intact.

## What it currently does

### Every morning at 7 AM (automatic)

- Pulls yesterday's ad spend, impressions, clicks, and conversions from every active Meta campaign.
- Pulls new "ICP" records from HubSpot (an ICP = a small business that completed the prequal form and got approved for investment crowdfunding).
- Rebuilds the weekly rollup — a big table that tells you, for every campaign in every week: how much was spent, how many leads were generated, and the cost per lead (CPL — the primary metric since 2026-09-09; cost per IC decision is still recorded alongside it, as a reported subtype).
- Posts the daily **"Honeycomb Leads"** Slack digest _(lead-first since 2026-09-10)_. Each of its three rows — yesterday, week-to-date, last 30 days — reads `Spend | Leads | CPL`; the week-to-date row compares CPL to the prior week and shows pacing against the weekly spend target, the 30-day row shows the $16 CPL target and a leads-per-week run rate, and yesterday's best and worst campaign by CPL are named. IC appears once, as a single italic "of which reached an IC decision" line. The one "watch" line picks, in order: a campaign that spent $50+ with zero leads, a campaign whose CPL is more than double the target on 5+ leads, or a campaign with frequency above 3.0. The two-sentence AI commentary is told to judge leads and CPL and never mention ICPs or CPICP. The digest still annotates its data source — `rolling_data` sheet (this morning's pipeline snapshot) — so the user can reconcile against the parallel `daily-check` ad-level skill that fetches fresh from Meta later in the morning. The two reports may show different "yesterday spend" values because Meta's attribution can shift between the snapshot and the live read; the footer makes the source unambiguous.

### Every Monday at 8 AM (automatic)

- Picks the most recently completed week.
- Sends all the numbers to Claude (Anthropic's AI) with a prompt that asks for a short narrative: what happened, what to watch, what to do. _(Lead-first since 2026-09-10.)_ The numbers Claude sees open with total leads, overall CPL against the $16 target and spend, then one "IC prequal decisions (reported only)" line; the campaign breakdown is sorted by CPL, best first, with no-lead campaigns last; the alert lists are "CPL above target" (more than 2× the target on 10+ leads), "CPL spike" (25%+ above the campaign's own 4-week average, 10+ leads), "frequency above 3.0" and "spend without leads". The prompt tells Claude that IC prequal decisions are a reported subtype — never the verdict, never a ranking — and that ICPs and CPICP are not to be mentioned.
- Writes the narrative into a log sheet and posts it to Slack under the title **"Honeycomb Leads — Week of …"**, with yesterday / this week / last 30 days rows that read `Spend | Leads | CPL` (week-over-week and vs-prior-30 comparisons are on CPL), one "of which reached an IC decision" line, and a **Budget Activity** block that now separates daily-optimizer proposals from strategic reallocations, ties the block to the week's lead result, and — while the optimizer is off — says so instead of promising "next proposal: tomorrow morning". If the LLM call fails for any reason (HTTP error, malformed response, exception), the Slack post inlines the error detail (`[LLM call failed: HTTP 529: ...]`) instead of just pointing at the sheet — so Tyler doesn't have to open `intelligence_log` to see what went wrong.

### Every day at 6 AM — the daily budget optimizer (PAUSED since 2026-09-09)

- The trigger still fires, but the function returns immediately, clears any leftover approval tokens from the last pre-pause run (so a stale "Approve" link in Slack can no longer post a confirmation nothing will honour), and does nothing else. The dashboard's "run analysis now" button reports the pause instead of sending you to Slack for a proposal that never arrives.
- _(2026-09-10)_ The logic behind the guard was rebuilt on leads so it is safe to switch back on: it looks at the last 14 days, ranks campaigns by CPL (70%) and lead-count trend (30%), forces a reduction on any campaign that spent with zero leads, and skips the "pump-up" for campaigns whose CPL is above $32 (twice the target). When re-enabled, the Slack proposal will open with a `N leads | CPL $X | $S spend` line, list reductions worst-CPL-first and increases best-CPL-first with each campaign's CPL and lead count, and mention IC only as "of which N reached an IC decision". The re-enable checklist lives in the technical reference's tech-debt index.
- Everything below still applies once it is back on: small adjustments (±2% per cycle, max ±4%), **hysteresis** _(added 2026-05-11)_ — a campaign must be in the same actionable tier (top or bottom quartile) for two consecutive cycles before a direction is applied — and Slack "Approve" / "Reject" links with a confirmation page (defeats Slack link-unfurl auto-clicks) and an optional approver-name field.

### Every day at 3 AM (automatic)

- The optimizer's executor is paused alongside it (same token cleanup). When re-enabled: if yesterday's proposal was approved by a human in Slack, it applies the budget changes directly to Meta; if rejected or ignored within the ~21-hour approval window, it marks them as cancelled and the expiry message says how many CPL-ranked changes lapsed and their net $/day. The message labels itself with the **proposal's** date plus the executor's wall-clock time so the audit trail isn't ambiguous.
- _(new, 2026-05-08)_ A second 3 AM job applies any **strategic reallocation** that was approved earlier in the week — this one is live. On most days it no-ops because no strategic proposal is pending. After applying changes it posts the per-campaign moves with the reason each was proposed (vertical, classification, CPL on N leads), and records a lockout on the affected campaigns through end-of-Monday. While the daily optimizer is paused the message says the lockout is "recorded for when it resumes" rather than claiming the optimizer is being held back. Rejected/expired notices name "the 3:00 AM run" (the trigger is daily, not Wednesday-only) and include the campaign count and net $/day.

### Every Tuesday at ~9:43 AM ET (automatic, new 2026-05-08)

- Pulls 12 weeks of trailing data and classifies each business vertical (breweries, bakeries, wineries, etc.) as **scalable**, **stable**, **saturating**, or **over-invested** based on how its cost-per-lead behaves as spend goes up. Verticals where frequency AND CPM are both rising over the last 4 weeks get an additional **new-audience-needed** flag — a warning that more budget won't fix the problem, the audience needs to expand.
- Composes a four-section Slack brief titled **"Honeycomb Scaling — <date>"** _(lead-first since 2026-09-10)_: it opens with one portfolio line (`N leads · $S spend · CPL $X · median vertical CPL $Y` over 12 weeks), lists verticals best-CPL-first within each class with their CPL and lead count (verticals too new to classify but with a live campaign get a "too new to classify" one-liner instead of vanishing), frames the reallocation as "moving $F/day out of <verticals at CPL $A> into <verticals at CPL $B>", lists audience action items for any flagged verticals, and evaluates last week's reallocation on CPL and lead movement (computed from the weekly rollup, because the scaling log sheet still stores only the old IC columns). IC appears once, as a trailing "of which N reached an IC decision" line.
- Sends the brief to Slack with Approve/Reject links (same two-step confirmation as the daily optimizer). **Until 2026-09-10 those links never worked** — the web app checked for the daily optimizer's approval token before it looked at the scaling one, so every Tuesday's proposal quietly expired at the next 3 AM run. Fixed; the confirmation now states how many campaigns will move and executes at the next 3 AM run.
- Two campaign-level fixes landed with the pass: `LEADS-…` campaign names now bucket into their vertical (before, the only two campaigns actually spending fell into one-campaign "verticals" and were skipped as too new), and the portfolio total counts ACTIVE campaigns only (before, paused campaigns' budgets made the total read ~$24k/week against a $2,100 target, so the brief scaled every increase to zero and warned of a knockdown every week).
- The reallocation respects a **12% weekly cap** per campaign — the total of all budget movement (daily optimizer + portfolio knockdown + strategic reallocation) cannot exceed 12% in a week. This keeps Meta's ad-set learning phase from being reset by stacked changes.

### On-demand via dashboard

- **Leaderboards** — top 3 / bottom 3 campaigns sortable by different metrics.
- **Trend charts** — CPL (the default), leads, spend, CTR, plus CPICP and ICPs over time (daily or weekly granularity; per-campaign or portfolio-wide).
- **Campaign performance table** — spend, leads, CPL (the default sort), then the IC columns, frequency per campaign, with paused-campaign badges.
- **Goal tracking** — weekly ICP pace vs. target (IC tab), weekly spend vs. the dashboard-managed target ($2,100/week since 2026-09-09).
- **Budget controls** — run-analysis-now button (reports "paused" while the optimizer is off), adjust the weekly spend goal via a Slack approval flow. The spend-target Slack messages now say the target is used by pacing and the Tuesday reallocation (and by the daily optimizer once re-enabled).
- **Hive Mind chat** — hidden behind a 5-click easter egg on the 🐝 logo; lets the team ask natural-language questions ("what was our CPL last Tuesday?") and get answers from Claude with live data. Its instructions were rewritten on 2026-09-10 to rank and recommend on leads and CPL (with the $16 target) and to treat IC counts as a reported subtype — previously it was told CPICP was the primary metric and CPL "less accurate".

### On-demand via Apps Script

- **Audit snapshot export** — dumps the four key data sheets as JSON files to a separate branch in the repo (`audit-snapshots`). This is what lets Claude Code (this assistant) inspect the actual data to diagnose issues.

### Ad-level data pipeline (snapshot backbone)

- **`daily-data.yml` GitHub Action** — pulls ad-set + ad-level insights from Meta for yesterday's date (or a `start_date`/`end_date` range for backfills), plus creative metadata for any newly discovered ads, and commits everything to `data/snapshots/<YYYY-MM-DD>/`.
- **Signal computation** — `scripts/compute_signals.py` reads the most recent ~7 days of snapshots and writes derived files (`data/derived/fatigue_signals.json`, `winner_bleeder.json`, `summary.json`) as an audit trail. The new agent skills compute their own canonical signals; these derived files exist for historical analysis and trend lookback beyond Meta's 14-day insight window.
- **Autonomous** — the workflow runs daily at ~8:37 AM ET on cron and commits each snapshot directly to main. Manual `workflow_dispatch` is preserved for backfills.

### Agent skills (new, 2026-05-03)

Skills are self-contained packages under `skills/<name>/` with a `SKILL.md` operating manual and Python scripts that handle Meta API calls and computation. Claude Code reads them at session start and runs the scripts via bash. Three skills are scoped:

- **pipeline-health** _(shipped 2026-05-03)_ — runs five checks (data freshness, Meta token validity, every configured funnel custom conversion including when each last fired, snapshot row volume, dashboard endpoint health) and writes results to a new `pipeline_health` Sheet tab via `Code.js?action=health-write`. Posts to Slack only on WARN/FAIL. _(2026-09-10)_ The snapshot-volume check now also reports the newest snapshot's lead total and spend (`20 leads on $268.05 spend`) — the only health signal for the lead pipeline, since the pixel "lead" event is not a custom conversion Meta can report on — and can WARN when spend crosses a configurable floor with zero leads (the floor is not set yet, so today it reports only). Every Slack line is labelled by check (`WARN funnel_conversions: …`), the quality-tier conversion always leads the funnel line, and an archived IC subtype reads as a "(reported only)" tracking-config warning, never as an IC performance alert.
- **daily-check** _(shipped 2026-05-03)_ — pulls 7 days of campaign/adset/ad insights, computes pacing vs weekly target, portfolio CPL rankings, top 3 winners + bleeders, early fatigue flags, learning-phase ad sets, and stale creatives. _(2026-09-10)_ The Slack brief is titled "Daily Lead Check" and opens with a totals line (leads · CPL · spend · prequal decisions, IC only as a trailing parenthetical); winners read `$CPL, N leads`, bleeders are rendered by the reason they qualified (spent with zero leads first, then CPL 1.5× the ad set's, CTR only when the ad set has no lead data yet). Writes a summary row to a new `daily_check_log` Sheet tab via `Code.js?action=daily-check-write`. Runs alongside the existing campaign-level Apps Script daily digest — does not replace it. The weekly spend goal used for pacing is fetched live from `/exec?action=get_spend_goal` (the dashboard-managed value), so changing the goal in the dashboard is reflected in the next briefing without a code change; `benchmarks.json` holds a fallback used only if `/exec` is unreachable.
- **fatigue-monitor** _(shipped 2026-05-03)_ — pulls 14 days of ad-level insights, computes each ad's peak-window baseline (days 4–7 after launch), and classifies the current 7 days as `saturated` / `fatigued` / `early_fatigue` / `underperforming` / `healthy`. _(2026-09-10)_ Baselines and current windows now carry leads and CPL, and CPL inflation is a classification input: leads 50%+ more expensive than in the ad's peak window is `fatigued` regardless of click-through, 25%+ with frequency above 2.0 is `early_fatigue` (CPL is only compared when both windows bought at least one lead). The Slack post opens with `N ads at risk · $spend / leads (CPL $x) last 7d`, each ad's lead line comes before its CTR/frequency diagnostics, ads are ordered by severity, then budget conflicts, then zero-lead spend, then most expensive leads, and an all-healthy run posts one lead-first line instead of four empty sections. Cross-references pending budget proposals via `Code.js?action=budget-queue-read` (newest pending increase per campaign) and composes a lead-first conflict warning ("this ad bought 5 leads at CPL $22.40 … consider pausing before approval"). Writes per-ad rows to a new `fatigue_log` Sheet tab via `Code.js?action=fatigue-write`. Caches creative metadata in `data/creatives/creatives.json` so thumbnails + ad copy are pulled once per creative, not per run.
- **creative-intelligence** _(shipped 2026-05-05)_ — weekly Monday brief on what creative copy and visual patterns are winning across the portfolio. Tells Tyler what to write next by quoting actual winning copy alongside its real numbers (CPL, lead count, ad count) and structural fingerprint (length, opening word, syntactic markers). The attribution model is corpus-level text aggregation: when the same body text appears across many ads, sum spend + leads across all of them to produce a meaningful per-variant CPL. _(2026-09-10)_ The Slack post opens with `N leads at $X median CPL across M ads`, ranks findings by CPL, applies lead-denominated confidence floors (≥10 ads + ≥100 leads confident; ≥5 + ≥40 directional), and may mention IC only as one "of which N reached an IC decision" line; the workflow prompt — which still carried the old "18 IC, $42 CPICP" instructions — was brought into line with the skill file, and the Sheet write now sends the shared secret it had been missing. Three rounds of Meta API investigation proved that asset-level breakdown insights — the original spec's spine — won't return reliable per-variant conversion data for Honeycomb's `asset_feed_spec` ad mix; the design pivot is captured in [docs/CREATIVE_INTELLIGENCE_DESIGN.md](./CREATIVE_INTELLIGENCE_DESIGN.md) (that document predates the lead pivot — read its CPICP references as CPL). Two-script pipeline: `categorize_creative.py` (Anthropic API once per unique variant text + image, hash-deduped, ~$5 first run on Sonnet 4.5) and `build_creative_dataset.py` (joins snapshots + creative cache + categorizations, downloads full-size images via `/adimages` resolution, finds same-image-different-body side-by-side pairs). Writes per-vertical rollups to a new `creative_intelligence_log` Sheet tab. SKILL.md output rules require briefs that quote actual copy + cite real numbers + honor confidence labels — never recommend categories.
- **ad-copy-generator** _(shipped 2026-05-05)_ — drafts new ad-copy variants for a target vertical from the Creative Intelligence dataset, closing the loop from "what's working" to "what to write next." Reads `/tmp/creative_dataset.json`, splits each dimension (body / title / description) at median CPL so winners and losers are always distinct cohorts even on small variant pools, asks Claude to draft N new (body, title, description) triples following the winning patterns, runs a compliance regex backstop (catches quantified returns, guarantee language, FDIC comparisons, multiple-x returns, dollar-return testimonials), and writes a human-readable markdown file to `data/drafts/<date>-<vertical>.md` with a 6-item reviewer checklist appended. **Drafts are never auto-published** — every draft requires human review per the compliance checklist before going live in any campaign. The skill is `workflow_dispatch`-only; Tyler invokes it after the Monday Creative Intelligence brief, picking which verticals warrant new drafts. Cost: ~$0.05-0.10 per Anthropic call, ~$0.50-0.80 for `--all-verticals` × 8 verticals.
- **portfolio-scaling** _(shipped 2026-05-08)_ — weekly Tuesday brief that adds a structural diagnosis layer on top of the daily optimizer. Classifies verticals over a 12-week window using elasticity (Pearson correlation of weekly spend vs weekly CPL), median-split CPL degradation between high- and low-spend weeks, and 4-week frequency/CPM trends. Produces a pool-based budget reallocation: saturating + over-invested verticals contribute decreases sized by elasticity severity; scalable + stable verticals absorb the pool weighted by inverse CPL. Bounded by the spend tolerance band so total portfolio spend stays within `target ± tolerance` per week. Two scripts (`compute_scaling_profiles.py` + `compute_reallocation.py`) commit deterministic JSON to `data/derived/`, then Claude composes the four-section, lead-first Slack brief from that JSON (see "Every Tuesday" above). The Slack brief uses the same two-step approval as the daily optimizer; on approval, the next 3 AM run applies the changes via Meta API and writes a Wed-Mon optimizer-lockout window on the affected campaigns. **Shares a 12% weekly cap with the daily optimizer.** All thresholds in `data/config/benchmarks.json:scaling`. Dependencies on the creative-intelligence cache are optional — audience action items work without it (and the `creative-intelligence-read` endpoint they would use does not exist yet, so the "top body" prescription line never renders today).

Skills query Meta live for operational decisions; the snapshot pipeline above provides the historical backbone. Both share a single Meta client at `scripts/lib/meta.py` (HTTP retries, paging, throttle handling, funnel-tier conversion extraction, row normalization).

### Autonomous skill execution (new, 2026-05-03)

Each skill that needs scheduled runs gets a workflow file under `.github/workflows/agent-<skill>.yml` that wraps `anthropics/claude-code-action@v1`. The action receives a fixed prompt that tells it to run the skill per its `SKILL.md`, pulls `META_ACCESS_TOKEN` (and optional `SLACK_WEBHOOK_URL`) from repo secrets, and surfaces results in the workflow log. Slack posting on WARN/FAIL is opt-in via the secret.

- **`agent-pipeline-health.yml`** _(shipped 2026-05-03, retired 2026-06-23)_ — v1 of the autonomous-agent pattern; ran daily at 9 AM ET. **Merged into `daily-data.yml` on 2026-06-23** and deleted. The health check is fully deterministic, so it no longer runs through `claude-code-action` (which had cost a daily Anthropic call just to reformat the script's JSON). It now runs as two ordinary steps at the end of the daily-data job — right after the 8 AM ET data pull — preserving every check, the Slack-on-WARN/FAIL behavior, the `pipeline_health` Sheet log, and the issue-#48 status comment.
- **`agent-daily-check.yml`** _(shipped 2026-05-03)_ — **cron paused 2026-06-08** (was daily 8:30 AM ET / UTC 12:30; preserved as a comment in the YAML). Manual `workflow_dispatch` still works and, since 2026-09-10, composes the lead-first "Daily Lead Check" brief — the prompt had still said "one line per campaign with IC conversions".
- **`agent-fatigue-monitor.yml`** _(shipped 2026-05-03)_ — **cron paused 2026-06-10** (was Mon + Thu 9:30 AM ET / UTC 13:30 — fatigue moves slowly, daily would over-query Meta). Manual `workflow_dispatch` still works and composes the lead-first brief.
- **`agent-creative-intelligence.yml`** _(shipped 2026-05-05, validated end-to-end 2026-05-05)_ — **cron paused 2026-06-08** (was Monday 10 AM ET / UTC 14:00; weekly cadence matches the corpus-aggregation attribution model). Manual `workflow_dispatch` still works. Three production runs on 2026-05-05 surfaced two distinct architectural findings that forced this skill's workflow to depart from the other agents' template:
  1. **Run 1**: Categorizer hit `APIConnectionError` on 526/526 Anthropic calls when running inside `claude-code-action`'s Bash subprocess (Meta calls from the same context worked fine; only Anthropic SDK calls failed). Fix: scripts run as ordinary workflow steps BEFORE the action, not inside its prompt.
  2. **Run 2**: Categorize succeeded but the cache `git push` failed with `Password authentication is not supported`. Credentials persisted by `actions/checkout` survive Python script steps but get stripped after `claude-code-action` runs. Fix: commit step runs BEFORE the action too.
  3. **Run 3 (validated)**: cache_commit=ok, 4 confident portfolio findings, 525 LLM tags committed to main, ~$1-2 cost (down from ~$5 pre-fix thanks to prompt caching on the system message). The skill is fully operational.
- **`agent-creative-preview.yml`** _(shipped 2026-05-05)_ — `workflow_dispatch` only, $0 alternative path. Same checkout + Meta + cache-commit mechanics as `agent-creative-intelligence.yml` but skips Anthropic calls entirely. Runs `build_creative_dataset.py` (Meta only, free) → `preview_dataset.py` (pure Python, free) → commits a deterministic Markdown brief to `data/previews/<date>.md`. Used to validate cache-commit mechanics without spending model dollars.
- **`agent-ad-copy-generator.yml`** _(shipped 2026-05-05, validated 2026-05-06)_ — `workflow_dispatch` only. Inputs: `vertical` (single-vertical mode) or blank for `--all-verticals`, plus `num_drafts`, `min_vertical_ads`, `model`. Re-emits the dataset from the locally-cached creatives.json (no Meta calls — uses the cache committed by the most recent `agent-creative-intelligence` run) and runs the drafting script. Commits the resulting markdown files in `data/drafts/` back to main. Status comment to issue #48 reports `drafts_written` and `files_with_compliance_flags`. First validated run on 2026-05-06 produced 5 brewery drafts at `data/drafts/2026-05-06-breweries.md` for ~$0.10.
- **`agent-portfolio-scaling.yml`** _(shipped 2026-05-08)_ — weekly cron Tuesdays at ~9:43 AM ET (UTC 13:43; minute moved off `:30` on 2026-06-23 to dodge GitHub's scheduled-run queue contention) — the only skill still on a schedule. Two Python steps run as ordinary workflow steps, then commit `data/derived/scaling_profiles.json` + `reallocation.json` BEFORE invoking `claude-code-action`. Claude reads the committed JSON, registers the proposal via the `/exec?action=scaling-queue-write` endpoint (sending the shared secret; if the write is refused it posts without approval links rather than inventing them) to receive an approval token, composes the four-section lead-first Slack brief, and posts it. The execution side runs as `executeStrategicChanges` in Code.js — daily 3 AM trigger that no-ops cheaply when no pending strategic token exists (rather than weekly Wed-only) so a manual `workflow_dispatch` on a non-Tuesday still executes on the next 3 AM after approval. Status comment to issue #48 now opens with `leads=N cpl=$X` before the verticals classification breakdown + freed/allocated dollars + knockdown_risk flag.

The agent workflows fall into a few patterns:
- **`daily-check` / `fatigue-monitor`** — predate the architectural findings above. They run scripts inside `claude-code-action`'s Bash prompt (works fine — they don't make Anthropic SDK subprocess calls or commit cache back to main). Same template as v1 of the autonomous-agent pattern: `id-token: write` permission for OIDC auth, `--permission-mode bypassPermissions` so Claude can run Bash in CI, `show_full_output: true` + `display_report: true` so Claude's output surfaces in the workflow log, and an `if: always()` step that dumps `claude-execution-output.json` for diagnostics. (`pipeline-health` was a third workflow in this group until 2026-06-23, when it was made fully deterministic and merged into `daily-data.yml` — it no longer uses `claude-code-action` at all.)
- **`agent-creative-intelligence` / `agent-creative-preview` / `agent-ad-copy-generator`** — established the new pattern: scripts run as workflow steps; cache commits happen BEFORE `claude-code-action` (or instead of it for the preview/drafter, neither of which uses claude-code-action at all). New skills with Anthropic-subprocess or commit-back needs should follow this pattern.

### Cron fallback via Apps Script (added 2026-05-03)

GitHub Actions cron is best-effort — runs can be delayed, occasionally skipped, and **silently disabled after 60 days of repo inactivity**. To make scheduled runs more reliable, Apps Script time-based triggers (running on Google's cron infrastructure) act as a fallback. Five functions in `apps-script/Code.js` (`triggerAgent*IfNeeded`) fire ~3 hours after the GitHub cron is supposed to run, check the GitHub API for a recent successful or in-progress run, and dispatch via `workflow_dispatch` only if none exists. If GitHub fired on time, Apps Script skips. If GitHub missed, the fallback picks it up. After running `createAllTriggers()` from the Apps Script editor, the system has two independent schedulers covering each workflow. The daily-check, fatigue-monitor and creative-intelligence fallbacks are early-returned to match their paused crons; the pipeline-health fallback now re-dispatches `daily-data.yml`; the portfolio-scaling fallback is live.

---

## What's working well

- **Data integrity.** Six known data-quality issues surfaced in the Q1 2026 audit (week convention drift, duplicate narratives, ID precision loss, CPL handling, spend mismatches, floating-point residuals) have all been fixed, tested, and verified through the audit snapshots.
- **Attribution model.** The hybrid v3 IC attribution (Meta IC conversions as floor + proportional share of unattributed HubSpot ICPs) is sound and consistent between the weekly rollup and the narrative log. Since 2026-09-09 it is a reported, legacy figure — leads and CPL are what every message and decision uses — but the rollup columns and the `intelligence_log` totals are still filled in so history stays comparable.
- **Human-in-the-loop safety.** No automated system pushes budget changes to Meta without a human clicking Approve in Slack. Two-step confirmation prevents Slack's link-unfurling bots from accidentally approving anything. As of 2026-09-10 the strategic-reallocation links actually reach their confirmation page (see "Every Tuesday").
- **One metric everywhere.** Every Slack message, prompt and ranking in the system now headlines leads and CPL, sorts by CPL, and mentions IC only as "of which N reached an IC decision" — the daily digest, the Monday narrative, the (paused) budget proposal, the Tuesday brief, the health alert and all five skill briefs read the same way.
- **Idempotency.** The narrative generator won't write a duplicate row if the week already has one. The Meta data fetcher deduplicates by date+campaign_id. The budget system tracks a single "pending" token at a time.
- **Audit trail.** Every budget change proposed, approved, rejected, or executed is recorded in the `budget_queue` sheet with a reason, a timestamp, and who approved it. Every narrative is timestamped in `intelligence_log`.
- **Deployment hygiene.** Code changes go through pull requests on GitHub, get deployed automatically, and never require anyone to edit the Apps Script web editor. This keeps the repo as the single source of truth.
- **Audit snapshot pipeline.** Claude Code can pull the last 90 days of data anytime and do health checks.
- **Campaign rename resilience.** Renaming a campaign in Meta is handled automatically: the sync detects the name change via `campaign_id`, updates the mapping row in place (preserving UTM and conversion settings), normalizes all historical `rolling_data` rows to the new name, and posts a Slack notification. Works for ALL campaigns, including those without URL tags.
- **Two-step budget approval.** Budget proposal links in Slack now show an HTML confirmation page with a button — Slack's link-unfurling bot gets the page but can't click buttons, so only a human can approve or reject.
- **AI upgraded to Claude Opus 4.7.** All four production Anthropic call sites (weekly narrative, daily digest commentary, budget commentary, Hive Mind chat) plus the connection test use a single `ANTHROPIC_MODEL` constant — future upgrades are one line. Every one of those prompts is lead-first as of 2026-09-10.
- **Dashboard line chart accuracy.** Daily granularity shows the full selected date range (no collapsed x-axis), and per-campaign lines break on paused days instead of drawing misleading straight lines across gaps.

---

## What changed on 2026-09-09, in plain terms

**The daily budget optimizer is off.** Every morning at 6 it ranked campaigns
by cost-per-IC-decision and nudged budgets up or down a few percent. That
number is now essentially zero, so the ranking was close to meaningless and it
was still moving money. It is switched off until the ranking is rebuilt around
cost per lead. The **weekly** strategic reallocation is untouched and still
runs.

**The dashboard's API is no longer open to the world.** The Google Apps Script
web app accepts requests from anyone, and its address is written down in this
public repository. That meant a stranger with the link could run up an
Anthropic bill through the chat box, kick off a budget analysis, or file budget
changes and be handed the approval code for them. Anything that spends money or
changes state now needs a password that only this repo's automation and your
browser hold. Reading charts still needs nothing, so the dashboard works as
before.

**The spend target matches reality.** It said $10,000/week; the account runs
about $300/day. Pacing was measuring against a number that had not been true
for months.

## What changed on 2026-09-10, in plain terms

**Every Slack message now talks about leads.** The 2026-09-09 pivot changed
what the code optimizes on; this pass changed what people read. The daily
digest and the Monday narrative are titled "Honeycomb Leads" and open with
leads, cost per lead against the $16 target, and spend. Campaign lists are in
CPL order. Every "watch this" rule is a CPL rule that needs a minimum number
of leads before it fires, so a single expensive lead cannot trigger an alert.
The IC count still appears — once, as "of which N reached an IC decision" —
and never as the headline, the sort key or the reason something is flagged.
The same standard was applied to the paused budget proposal (so it cannot
come back IC-first), the Tuesday scaling brief, the pipeline-health alert and
all five skill briefs, and to the instructions behind the Hive Mind chat.

**The Tuesday approve/reject links never worked.** The web app checked for
the daily optimizer's approval token before it looked at the scaling one, and
the two are never equal, so every strategic proposal since May silently
expired at the next 3 AM run. The check order is fixed. This is the most
consequential finding of the pass: the weekly reallocation had been proposed
every Tuesday and applied never.

**The scaling brief was doing math on paused campaigns.** Its "current
portfolio" total added up the daily budgets of all 29 campaigns, 26 of them
paused, and read ~$24,000/week against a $2,100 target — so it scaled every
proposed increase to zero and warned of a knockdown every week. It now counts
active campaigns only. Separately, the two campaigns actually spending
(`LEADS-…`) had been falling into one-campaign "verticals" and being skipped
as too new; they now bucket with their vertical.

**Stale copy is gone.** Messages no longer promise "next proposal: tomorrow
morning" or say "optimizer continues normal cadence" while the optimizer is
off, and the paused optimizer clears the approval token it left behind so an
old Slack "Approve" link cannot post a confirmation nothing will act on.

## Current limitations and gaps

### Data-quality risks still open

- **~~Attribution quality dropped sharply week of 4/13.~~** _Diagnosed and fixed 2026-04-21._ The 33% attribution quality that week was NOT a Meta tracking problem — it was a code bug. A pattern constant (`IC_CONVERSION_EVENT_PATTERN`) stopped matching the `conversion_event` values in `campaign_mapping` after `syncCampaignMappings_` auto-populated them with Meta's plain-text names. IC Conversions recorded 0 for 5 consecutive days (4/15–4/19). The pattern has been corrected. Post-fix IC Conversions reflect "Investment Crowdfunding Prequal Decision" — a cleaner decision-level signal than the pre-4/15 series, which was capturing "Prequal results page view." The two series are not directly comparable; CPICP baselines reset at the fix date.
- **Campaign mapping is partially populated.** Only 4 of 20 campaigns in the mapping sheet have the "Prequal results page view" conversion event configured, and only 2 have the newer "Investment Crowdfunding Prequal Decision" event. Campaigns without custom_conversion_id set fall back to Meta's generic lead conversion, which gives fuzzier ICP attribution.
- **One narrative row uses an older attribution model.** The 3/30 row was written under the v2 "blended" model before the v3 hybrid fix. Its numbers are correct for that week under the old method but aren't directly comparable to surrounding weeks.

### Operational gaps

- **Ad-level pipeline is autonomous.** `.github/workflows/daily-data.yml` runs daily at ~8:37 AM ET on cron and commits each snapshot directly to main. Manual `workflow_dispatch` is preserved for backfills via the `start_date` / `end_date` inputs.
- **Two Meta tokens to keep current.** The legacy campaign-level pipeline reads `META_ACCESS_TOKEN` from Apps Script Script Properties; the new ad-level pipeline reads it from a GitHub Secret with the same name. Token rotation now has to happen in two places.
- **Pipeline-failure alerting covers the ad-level pipeline better than the 7 AM one.** The daily pipeline-health check (inside `daily-data.yml`) posts to Slack on a stale `rolling_data`, an invalid Meta token, an archived funnel conversion, an unreachable dashboard endpoint or an empty snapshot — but it runs hours after the 7 AM Apps Script pull, and a broken Apps Script trigger still shows up only as "the digest didn't arrive".
- **No hard alert on lead volume collapsing.** The health check reports the newest snapshot's lead total every day, but the optional "spend with zero leads" WARN floor (`pipeline_health.zero_lead_spend_floor_usd`) is not set, so a day of spend with no leads is visible only to someone reading the number. The digest's "spend without leads" watch line and the narrative's "SPEND WITHOUT LEADS" list cover the campaign level.
- **The scaling log sheet still stores IC columns only.** `scaling_log` keeps `ic_rate` / `cpicp` and has no CPL or lead-count column (the scripts send them, the sheet drops them). The Tuesday brief therefore computes week-over-week CPL from the weekly rollup instead of the log. Adding the columns needs an Apps Script change and a redeploy.
- **Manual steps for new campaigns.** When the team launches a new Meta campaign, the mapping sheet auto-discovers the UTM tag and campaign_id, but conversion event mapping often needs manual verification. If a campaign's custom_conversion_id doesn't get filled in, its ICPs won't be properly tracked. Campaign renames in Meta are now handled automatically — the sync detects when a campaign_id's name has changed, updates it in place, and preserves all manually-set UTM and conversion settings.
- **Audit snapshot is manual.** Someone has to run `exportAuditSnapshot()` from the Apps Script editor to refresh data for Claude Code. Adding a weekly time trigger would make this automatic.
- **No recurring data audit.** The daily health check catches broken plumbing, but the Q1 audit uncovered 6 data-quality issues only because someone did a deep-dive. Without a scheduled audit — weekly or monthly — similar drift could accumulate again.
- **Nothing has been deployed to Apps Script since the pivot.** The 2026-09-09 and 2026-09-10 `Code.js` changes (optimizer pause, `/exec` secret, lead-first digest and narrative, the approve-link fix) ship only when the branch merges to `main` and the "Deploy Apps Script" workflow runs. Until then production still posts the IC-first digest and narrative every day, and the Tuesday links are still dead. Set `EXEC_SHARED_SECRET` in both places before merging, or every Sheet write fails closed.

### Content / copy gaps

- **The `/ad-copy/`, `/workflows/`, `/audiences/`, and `/reports/` directories are empty placeholders.** CLAUDE.md describes them as if populated, but no content exists. If the team wants to use this repo as their content library too (not just automation), those directories need work.

### Technical debt

- ~~**Hybrid attribution math is duplicated.**~~ _Resolved 2026-09-10._ The budget analyzer no longer computes hybrid ICPs at all — it ranks on leads and CPL straight from `rolling_data` — so only the weekly rollup carries the attribution math.
- **Week-over-week CPL math is duplicated.** The daily digest, the weekly Slack post and the narrative each compute their own CPL comparisons (prior week, 4-week average) from the rollup rows, because the rollup sheet still has trend columns only for CPICP. One shared helper — or CPL trend columns in the rollup — would remove the drift risk.
- **Multiple Meta campaigns map to one UTM value.** "for ag" covers 3 Meta campaigns, "for ICrev2test" covers 2. Not a bug — just means segment-level rollups combine spend across these.
- **Campaign-mapping typo.** One row reads "Q4 2205" instead of "Q4 2025." Cosmetic but worth cleaning up.
- ~~**Hardcoded Claude model in 3 places.**~~ _Resolved 2026-04-22._ Extracted to `ANTHROPIC_MODEL` constant (Code.js:45). Upgraded to Opus 4.7. All 5 call sites reference the constant.
- **No rate limiting on the chat endpoint.** Someone could hammer the Hive Mind chat and run up Anthropic API costs. Low likelihood given it's a hidden feature, but worth knowing. Cost impact is higher now with Opus 4.7 (more capable but more expensive per token).
- **Audit snapshot uses GitHub's low-level Git API directly.** Works, but code is verbose and has no retry logic on GitHub API errors.

### Compliance / security

- **Credentials rotation is manual.** Meta tokens, HubSpot keys, Anthropic keys, GitHub PATs all live in Apps Script Properties as plain text. No automatic expiration reminder, no rotation schedule.
- **Logs live in Apps Script only.** If you need to audit what happened 6 months ago, you have to dig through the Apps Script execution log, which has limited retention and no search.
- **Web App is publicly reachable (ANYONE_ANONYMOUS).** Anyone who knows the `/exec` URL can read the dashboard data (campaign metrics — not especially sensitive). Since 2026-09-09 anything that spends money or changes state requires the `EXEC_SHARED_SECRET`; the Slack approve/reject links authenticate with their own per-proposal token instead.

---

## Known risks worth watching

1. **~~IC tracking pattern is still a string-match.~~** _Resolved on the Python side 2026-09-09._ The ad-level pipeline and all six skills now resolve every conversion from stable numeric IDs held in `data/config/benchmarks.json`, so a rename in Meta can no longer break tracking there. **Still open in Apps Script:** `Code.js` retains the fragile `IC_CONVERSION_EVENT_PATTERN` string match, and the campaign-level pipeline still depends on it.
2. **Scheduled triggers can silently stop.** Apps Script occasionally revokes triggers after script updates. A weekly "is the pipeline still running?" check would be worthwhile — currently relies on noticing the digest didn't arrive.
3. **Meta access token expiration.** Long-lived Meta access tokens eventually expire. When it happens, every data pull fails until someone regenerates it. No proactive warning.
4. **Budget automation could over-react in low-volume weeks.** The eligibility gate (≥10 lifetime leads) prevents new campaigns from getting changes, but in quiet weeks the rules engine could still move money based on small-sample signals. The ±2% cap limits damage per cycle, but repeated cycles compound. Moot while the daily optimizer is paused; when it is re-enabled, clear its hysteresis history first (the pause guard already does this) so it does not honour tiers computed under the old IC ranking.
5. **The Tuesday brief has never been exercised end-to-end.** Because its approve links were dead until 2026-09-10, no strategic reallocation has ever been approved and executed. The first real approval will be the first test of `executeStrategicChanges` against Meta.

---

## Recommended next steps (ranked by impact / effort)

### High impact, low effort
- Merge the pivot branch and verify the "Deploy Apps Script" run, with `EXEC_SHARED_SECRET` set on both sides first — nothing from 2026-09-09/10 is live until then.
- Set `pipeline_health.zero_lead_spend_floor_usd` in `benchmarks.json` so a day of spend with zero leads posts a WARN instead of a number nobody reads.
- Add a weekly time trigger for `exportAuditSnapshot()` so audit data refreshes automatically.
- Fix the "Q4 2205" typo in campaign_mapping.

### High impact, medium effort
- ~~Investigate the 4/13 attribution collapse directly in Meta.~~ **Done 2026-04-21** — root cause was in code (`IC_CONVERSION_EVENT_PATTERN`), not Meta. Fix deployed.
- ~~Extract the shared hybrid attribution math into one function.~~ **Moot 2026-09-10** — the budget analyzer no longer computes it.
- Decide whether to re-enable the daily optimizer now that its ranking is CPL-based (remove the two guards, flip `BUDGET_OPTIMIZER_PAUSED`).
- Add `cpl` / `total_leads` columns to `scaling_log` (and the matching `Code.js` handler change) so the Tuesday brief can evaluate last week from the log instead of recomputing from the rollup.
- Populate `custom_conversion_id` for all active campaigns in campaign_mapping.

### Medium impact, low effort
- ~~Move the Claude model name to a constant/config at the top of Code.js.~~ **Done 2026-04-22.**
- Add a Meta token expiration warning (check validity at start of daily pipeline, alert Slack if close to expiring).

### Lower priority
- Populate `/ad-copy/`, `/workflows/`, `/audiences/`, `/reports/` directories if the repo is meant to host content too.
- Consider an external log sink (Cloud Logging) for long-term auditability.
- Add rate limiting to the chat endpoint.

---

## Summary in one paragraph

This is a mature, working automation platform that now measures one thing — leads and cost per lead — and says so in every message it sends. The core data pipeline runs daily without intervention, budget changes go through two-step human-in-the-loop approval (Slack confirmation page defeats link-unfurling bots), and campaign renames in Meta are handled automatically without manual mapping cleanup. The Q1 audit's 6 data-quality issues are all resolved, the AI layer runs on Claude Opus 4.7 via a single configurable constant with lead-first prompts, and IC conversion tracking is intact but demoted to a reported subtype. As of 2026-05-02, an additive ad-level agent layer (`scripts/`, `skills/`, `data/`) sits alongside the campaign-level pipeline — it gives Claude Code per-ad fatigue signals and creative metadata to power the monitor → detect → propose loop, while still routing all real budget changes through the existing human approval flow. The daily budget optimizer is paused with a CPL-ranked replacement ready behind the guard; the weekly strategic reallocation is live and, as of 2026-09-10, its approval links finally work. The biggest remaining items are operational: merging and deploying the pivot, a hard alert on lead volume collapsing, and a lead column in the scaling log.
