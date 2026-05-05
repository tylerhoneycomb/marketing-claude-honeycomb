# Project State Report

_Last updated: 2026-05-05 (Creative Intelligence shipped + workflow-schedule docs corrected)_

This report describes what the `marketing-claude-honeycomb` project is, what it currently does, what's working well, and where the current limitations are. Written in plain English for non-technical stakeholders. For implementation details see [TECHNICAL_REFERENCE.md](./TECHNICAL_REFERENCE.md).

> **Maintenance rule:** This document should be updated whenever a change materially affects functionality, limitations, or operational behavior. See [CLAUDE.md](../CLAUDE.md) for the docs-update rule.

---

## What this project is

A **marketing operations platform** for Honeycomb Credit's small-business investment crowdfunding campaigns. It is NOT product code — it's an automation layer that helps the marketing team run Meta (Facebook/Instagram) ads more efficiently.

Four things live inside the repo:

1. **The "brain"** — a Google Apps Script program (~4,200 lines) that runs every day, pulls data from Meta and HubSpot, does the math, writes summaries, and proposes budget changes.
2. **The "dashboard"** — a web page (hosted on GitHub Pages) where the team can see charts, check campaign health, and ask questions via an AI chat called "Hive Mind."
3. **The "plumbing"** — GitHub Actions that automatically push code changes to the Google Apps Script servers whenever something is merged, so nobody has to copy/paste into the Apps Script web editor.
4. **The "agent layer"** _(new, 2026-05-02)_ — an ad-level data pipeline (`scripts/`) and skill files (`skills/`) that let Claude Code monitor individual ads, detect creative fatigue, and propose budget shifts. Snapshots are stored as JSON files under `data/` (the repo itself acts as the database). The agent layer feeds recommendations into the existing Slack approval pipeline — it never writes to Meta directly.

The campaign-level system is connected through a single Google Spreadsheet. The agent layer is connected through JSON files in the repo.

---

## What it currently does

### Every morning at 7 AM (automatic)

- Pulls yesterday's ad spend, impressions, clicks, and conversions from every active Meta campaign.
- Pulls new "ICP" records from HubSpot (an ICP = a small business that completed the prequal form and got approved for investment crowdfunding).
- Rebuilds the weekly rollup — a big table that tells you, for every campaign in every week: how much was spent, how many ICPs were generated, and the cost per ICP (CPICP — the single most important metric).
- Posts a daily Slack digest summarizing yesterday's performance, this week's pacing, and last 30 days.

### Every Monday at 8 AM (automatic)

- Picks the most recently completed week.
- Sends all the numbers to Claude (Anthropic's AI) with a prompt that asks for a short narrative: what happened, what to watch, what to do.
- Writes the narrative into a log sheet and posts it to Slack.

### Every Wednesday and Friday at 6 AM (automatic)

- Looks at the last 14 days of performance.
- Decides which campaigns are doing well vs. poorly.
- Proposes small budget adjustments (±2% per cycle, max ±4%) to reallocate money toward winners.
- Sends the proposal to Slack with "Approve" and "Reject" buttons.

### Every Thursday and Saturday at 3 AM (automatic)

- If the proposal was approved by a human in Slack, applies the budget changes directly to Meta.
- If rejected or ignored, marks them as cancelled.
- Posts a confirmation to Slack either way.

### On-demand via dashboard

- **Leaderboards** — top 3 / bottom 3 campaigns sortable by different metrics.
- **Trend charts** — CPICP, ICPs, spend, CPL, CTR over time (daily or weekly granularity; per-campaign or portfolio-wide).
- **Campaign performance table** — spend, clicks, CPICP, frequency per campaign, with paused-campaign badges.
- **Goal tracking** — weekly ICP pace vs. target, weekly spend vs. $10K target.
- **Budget controls** — run-analysis-now button, adjust the weekly spend goal via a Slack approval flow.
- **Hive Mind chat** — hidden behind a 5-click easter egg on the 🐝 logo; lets the team ask natural-language questions ("what was our CPICP last Tuesday?") and get answers from Claude with live data.

### On-demand via Apps Script

- **Audit snapshot export** — dumps the four key data sheets as JSON files to a separate branch in the repo (`audit-snapshots`). This is what lets Claude Code (this assistant) inspect the actual data to diagnose issues.

### Ad-level data pipeline (snapshot backbone)

- **`daily-data.yml` GitHub Action** — pulls ad-set + ad-level insights from Meta for yesterday's date (or a `start_date`/`end_date` range for backfills), plus creative metadata for any newly discovered ads, and commits everything to `data/snapshots/<YYYY-MM-DD>/`.
- **Signal computation** — `scripts/compute_signals.py` reads the most recent ~7 days of snapshots and writes derived files (`data/derived/fatigue_signals.json`, `winner_bleeder.json`, `summary.json`) as an audit trail. The new agent skills compute their own canonical signals; these derived files exist for historical analysis and trend lookback beyond Meta's 14-day insight window.
- **Autonomous** — the workflow runs daily at 8 AM ET on cron and commits each snapshot directly to main. Manual `workflow_dispatch` is preserved for backfills.

### Agent skills (new, 2026-05-03)

Skills are self-contained packages under `skills/<name>/` with a `SKILL.md` operating manual and Python scripts that handle Meta API calls and computation. Claude Code reads them at session start and runs the scripts via bash. Three skills are scoped:

- **pipeline-health** _(shipped 2026-05-03)_ — runs four checks (data freshness, Meta token validity, IC conversion event existence, dashboard endpoint health) and writes results to a new `pipeline_health` Sheet tab via `Code.js?action=health-write`. Posts to Slack only on WARN/FAIL.
- **daily-check** _(shipped 2026-05-03)_ — pulls 7 days of campaign/adset/ad insights, computes pacing vs weekly target, portfolio CPICP rankings, top 3 winners + bleeders, early fatigue flags, learning-phase ad sets, and stale creatives. Writes a summary row to a new `daily_check_log` Sheet tab via `Code.js?action=daily-check-write`. Runs alongside the existing campaign-level Apps Script daily digest — does not replace it.
- **fatigue-monitor** _(shipped 2026-05-03)_ — pulls 14 days of ad-level insights, computes each ad's peak-window baseline (days 4–7 after launch), and classifies the current 7 days as `saturated` / `fatigued` / `early_fatigue` / `underperforming` / `healthy`. Cross-references pending budget proposals via `Code.js?action=budget-queue-read` and surfaces conflicts. Writes per-ad rows to a new `fatigue_log` Sheet tab via `Code.js?action=fatigue-write`. Caches creative metadata in `data/creatives/creatives.json` so thumbnails + ad copy are pulled once per creative, not per run.
- **creative-intelligence** _(shipped 2026-05-05)_ — weekly Monday brief on what creative copy and visual patterns are winning across the portfolio. Tells Tyler what to write next by quoting actual winning copy alongside its real numbers (CPICP, IC count, ad count) and structural fingerprint (length, opening word, syntactic markers). The attribution model is corpus-level text aggregation: when the same body text appears across many ads, sum spend + IC across all of them to produce a meaningful per-variant CPICP. Three rounds of Meta API investigation proved that asset-level breakdown insights — the original spec's spine — won't return reliable per-variant conversion data for Honeycomb's `asset_feed_spec` ad mix; the design pivot is captured in [docs/CREATIVE_INTELLIGENCE_DESIGN.md](./CREATIVE_INTELLIGENCE_DESIGN.md). Two-script pipeline: `categorize_creative.py` (Anthropic API once per unique variant text + image, hash-deduped, ~$5 first run on Sonnet 4.5) and `build_creative_dataset.py` (joins snapshots + creative cache + categorizations, downloads full-size images via `/adimages` resolution, finds same-image-different-body side-by-side pairs). Writes per-vertical rollups to a new `creative_intelligence_log` Sheet tab. SKILL.md output rules require briefs that quote actual copy + cite real numbers + honor confidence labels — never recommend categories.

Skills query Meta live for operational decisions; the snapshot pipeline above provides the historical backbone. Both share a single Meta client at `scripts/lib/meta.py` (HTTP retries, paging, throttle handling, IC extraction, row normalization).

### Autonomous skill execution (new, 2026-05-03)

Each skill that needs scheduled runs gets a workflow file under `.github/workflows/agent-<skill>.yml` that wraps `anthropics/claude-code-action@v1`. The action receives a fixed prompt that tells it to run the skill per its `SKILL.md`, pulls `META_ACCESS_TOKEN` (and optional `SLACK_WEBHOOK_URL`) from repo secrets, and surfaces results in the workflow log. Slack posting on WARN/FAIL is opt-in via the secret.

- **`agent-pipeline-health.yml`** _(shipped 2026-05-03)_ — daily cron active at 9 AM ET (UTC 13:00). v1 of the autonomous-agent pattern.
- **`agent-daily-check.yml`** _(shipped 2026-05-03)_ — daily cron active at 8:30 AM ET (UTC 12:30).
- **`agent-fatigue-monitor.yml`** _(shipped 2026-05-03)_ — twice-weekly cron active for Mon + Thu 9:30 AM ET (UTC 13:30) — fatigue moves slowly, daily would over-query Meta.
- **`agent-creative-intelligence.yml`** _(shipped 2026-05-05)_ — weekly cron active for Monday 10 AM ET (UTC 14:00). Weekly cadence matches the corpus-aggregation attribution model — variant-level performance signals shift over weeks, not days. Includes a "commit cache updates" step that pushes the refreshed image cache and categorizations cache back to main so subsequent runs skip the expensive first-time work.

All four use the same template baked from the pipeline-health iteration cycle: `id-token: write` permission for OIDC auth, `--permission-mode bypassPermissions` so Claude can run Bash in CI, `show_full_output: true` + `display_report: true` so Claude's output surfaces in the workflow log, and an `if: always()` step that dumps `claude-execution-output.json` for diagnostics if anything fails.

### Cron fallback via Apps Script (added 2026-05-03)

GitHub Actions cron is best-effort — runs can be delayed, occasionally skipped, and **silently disabled after 60 days of repo inactivity**. To make scheduled runs more reliable, Apps Script time-based triggers (running on Google's cron infrastructure) act as a fallback. Three new functions in `apps-script/Code.js` (`triggerAgent*IfNeeded`) fire ~3 hours after the GitHub cron is supposed to run, check the GitHub API for a recent successful or in-progress run, and dispatch via `workflow_dispatch` only if none exists. If GitHub fired on time, Apps Script skips. If GitHub missed, the fallback picks it up. After running `createAllTriggers()` from the Apps Script editor, the system has two independent schedulers covering each workflow.

---

## What's working well

- **Data integrity.** Six known data-quality issues surfaced in the Q1 2026 audit (week convention drift, duplicate narratives, ID precision loss, CPL handling, spend mismatches, floating-point residuals) have all been fixed, tested, and verified through the audit snapshots.
- **Attribution model.** The hybrid v3 attribution (Meta IC conversions as floor + proportional share of unattributed HubSpot ICPs) is sound and consistent between the weekly rollup and the narrative generator. They now agree to the cent.
- **Human-in-the-loop safety.** No automated system pushes budget changes to Meta without a human clicking Approve in Slack. Two-step confirmation prevents Slack's link-unfurling bots from accidentally approving anything.
- **Idempotency.** The narrative generator won't write a duplicate row if the week already has one. The Meta data fetcher deduplicates by date+campaign_id. The budget system tracks a single "pending" token at a time.
- **Audit trail.** Every budget change proposed, approved, rejected, or executed is recorded in the `budget_queue` sheet with a reason, a timestamp, and who approved it. Every narrative is timestamped in `intelligence_log`.
- **Deployment hygiene.** Code changes go through pull requests on GitHub, get deployed automatically, and never require anyone to edit the Apps Script web editor. This keeps the repo as the single source of truth.
- **Audit snapshot pipeline.** Claude Code can pull the last 90 days of data anytime and do health checks.
- **Campaign rename resilience.** Renaming a campaign in Meta is handled automatically: the sync detects the name change via `campaign_id`, updates the mapping row in place (preserving UTM and conversion settings), normalizes all historical `rolling_data` rows to the new name, and posts a Slack notification. Works for ALL campaigns, including those without URL tags.
- **Two-step budget approval.** Budget proposal links in Slack now show an HTML confirmation page with a button — Slack's link-unfurling bot gets the page but can't click buttons, so only a human can approve or reject.
- **AI upgraded to Claude Opus 4.7.** All 5 Anthropic API call sites (narrative, chat, budget commentary, daily digest, weekly Slack) use a single `ANTHROPIC_MODEL` constant — future upgrades are one line.
- **Dashboard line chart accuracy.** Daily granularity shows the full selected date range (no collapsed x-axis), and per-campaign lines break on paused days instead of drawing misleading straight lines across gaps.

---

## Current limitations and gaps

### Data-quality risks still open

- **~~Attribution quality dropped sharply week of 4/13.~~** _Diagnosed and fixed 2026-04-21._ The 33% attribution quality that week was NOT a Meta tracking problem — it was a code bug. A pattern constant (`IC_CONVERSION_EVENT_PATTERN`) stopped matching the `conversion_event` values in `campaign_mapping` after `syncCampaignMappings_` auto-populated them with Meta's plain-text names. IC Conversions recorded 0 for 5 consecutive days (4/15–4/19). The pattern has been corrected. Post-fix IC Conversions reflect "Investment Crowdfunding Prequal Decision" — a cleaner decision-level signal than the pre-4/15 series, which was capturing "Prequal results page view." The two series are not directly comparable; CPICP baselines reset at the fix date.
- **Campaign mapping is partially populated.** Only 4 of 20 campaigns in the mapping sheet have the "Prequal results page view" conversion event configured, and only 2 have the newer "Investment Crowdfunding Prequal Decision" event. Campaigns without custom_conversion_id set fall back to Meta's generic lead conversion, which gives fuzzier ICP attribution.
- **One narrative row uses an older attribution model.** The 3/30 row was written under the v2 "blended" model before the v3 hybrid fix. Its numbers are correct for that week under the old method but aren't directly comparable to surrounding weeks.

### Operational gaps

- **Ad-level pipeline is autonomous.** `.github/workflows/daily-data.yml` runs daily at 8 AM ET on cron and commits each snapshot directly to main. Manual `workflow_dispatch` is preserved for backfills via the `start_date` / `end_date` inputs.
- **Two Meta tokens to keep current.** The legacy campaign-level pipeline reads `META_ACCESS_TOKEN` from Apps Script Script Properties; the new ad-level pipeline reads it from a GitHub Secret with the same name. Token rotation now has to happen in two places.
- **No alerting on pipeline failures.** If the daily 7 AM pull breaks (e.g., expired Meta token), you only find out when someone notices the Slack digest didn't arrive or the dashboard shows stale data. No proactive "hey, this job failed" alert.
- **No alerting on attribution-quality drops.** The 33% collapse that week could have gone unnoticed for days. A threshold-based alert ("IC attribution below 50% — investigate") would catch this earlier.
- **Manual steps for new campaigns.** When the team launches a new Meta campaign, the mapping sheet auto-discovers the UTM tag and campaign_id, but conversion event mapping often needs manual verification. If a campaign's custom_conversion_id doesn't get filled in, its ICPs won't be properly tracked. Campaign renames in Meta are now handled automatically — the sync detects when a campaign_id's name has changed, updates it in place, and preserves all manually-set UTM and conversion settings.
- **Audit snapshot is manual.** Someone has to run `exportAuditSnapshot()` from the Apps Script editor to refresh data for Claude Code. Adding a weekly time trigger would make this automatic.
- **No recurring health check.** The Q1 audit uncovered 6 issues only because someone did a deep-dive. Without a scheduled audit — weekly or monthly — similar drift could accumulate again.

### Content / copy gaps

- **The `/ad-copy/`, `/workflows/`, `/audiences/`, and `/reports/` directories are empty placeholders.** CLAUDE.md describes them as if populated, but no content exists. If the team wants to use this repo as their content library too (not just automation), those directories need work.

### Technical debt

- **Hybrid attribution math is duplicated.** The weekly rollup and the budget analyzer each compute hybrid ICPs independently. If one is updated and the other isn't, budget decisions could drift from reported numbers. Worth extracting into a single shared function.
- **Multiple Meta campaigns map to one UTM value.** "for ag" covers 3 Meta campaigns, "for ICrev2test" covers 2. Not a bug — just means segment-level rollups combine spend across these.
- **Campaign-mapping typo.** One row reads "Q4 2205" instead of "Q4 2025." Cosmetic but worth cleaning up.
- ~~**Hardcoded Claude model in 3 places.**~~ _Resolved 2026-04-22._ Extracted to `ANTHROPIC_MODEL` constant (Code.js:45). Upgraded to Opus 4.7. All 5 call sites reference the constant.
- **No rate limiting on the chat endpoint.** Someone could hammer the Hive Mind chat and run up Anthropic API costs. Low likelihood given it's a hidden feature, but worth knowing. Cost impact is higher now with Opus 4.7 (more capable but more expensive per token).
- **Audit snapshot uses GitHub's low-level Git API directly.** Works, but code is verbose and has no retry logic on GitHub API errors.

### Compliance / security

- **Credentials rotation is manual.** Meta tokens, HubSpot keys, Anthropic keys, GitHub PATs all live in Apps Script Properties as plain text. No automatic expiration reminder, no rotation schedule.
- **Logs live in Apps Script only.** If you need to audit what happened 6 months ago, you have to dig through the Apps Script execution log, which has limited retention and no search.
- **Web App is publicly accessible (ANYONE_ANONYMOUS).** The `/exec` URL has no auth. Anyone who knows the URL can hit the dashboard API. Dashboard data isn't super-sensitive (campaign metrics) but it's worth knowing.

---

## Known risks worth watching

1. **IC tracking pattern is still a string-match.** The 4/15 outage was rooted in a fragile `indexOf` check against a human-readable event name. Any future rename of the "Investment Crowdfunding Prequal Decision" event in Meta — or a change to the `conversion_event` column values — would break tracking again. Longer-term fix: key IC tracking off `custom_conversion_id` (the stable numeric Meta ID) instead of the event name string. Deferred for now.
2. **Scheduled triggers can silently stop.** Apps Script occasionally revokes triggers after script updates. A weekly "is the pipeline still running?" check would be worthwhile — currently relies on noticing the digest didn't arrive.
3. **Meta access token expiration.** Long-lived Meta access tokens eventually expire. When it happens, every data pull fails until someone regenerates it. No proactive warning.
4. **Budget automation could over-react in low-volume weeks.** The eligibility gate (≥10 lifetime conversions) prevents new campaigns from getting changes, but in quiet weeks the rules engine could still move money based on small-sample signals. The ±2% cap limits damage per cycle, but repeated cycles compound.

---

## Recommended next steps (ranked by impact / effort)

### High impact, low effort
- Add a weekly time trigger for `exportAuditSnapshot()` so audit data refreshes automatically.
- Add a threshold alert for attribution quality dropping below 50% (Slack message).
- Add a pipeline-health check: if the daily digest hasn't posted by 8 AM, something's broken — alert.
- Fix the "Q4 2205" typo in campaign_mapping.

### High impact, medium effort
- ~~Investigate the 4/13 attribution collapse directly in Meta.~~ **Done 2026-04-21** — root cause was in code (`IC_CONVERSION_EVENT_PATTERN`), not Meta. Fix deployed.
- Extract the shared hybrid attribution math into one function used by both the weekly rollup and the budget analyzer.
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

This is a mature, working automation platform. The core data pipeline runs daily without intervention, the budget optimizer has two-step human-in-the-loop safeguards (Slack confirmation page defeats link-unfurling bots), and campaign renames in Meta are handled automatically without manual mapping cleanup. The Q1 audit's 6 data-quality issues are all resolved, IC conversion tracking has been restored (now using the cleaner "Investment Crowdfunding Prequal Decision" event), and the AI layer runs on Claude Opus 4.7 via a single configurable constant. As of 2026-05-02, an additive ad-level agent layer (`scripts/`, `skills/`, `data/`) sits alongside the campaign-level pipeline — it gives Claude Code per-ad fatigue signals and creative metadata to power the Berman-style monitor → detect → propose loop, while still routing all real budget changes through the existing human approval flow. The biggest remaining ROI improvements are around observability: proactive alerts on pipeline failures, attribution-quality drops, and token expiration. The system does not currently tell you when it's broken — you have to notice.
