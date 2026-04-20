# Project State Report

_Last updated: 2026-04-20_

This report describes what the `marketing-claude-honeycomb` project is, what it currently does, what's working well, and where the current limitations are. Written in plain English for non-technical stakeholders. For implementation details see [TECHNICAL_REFERENCE.md](./TECHNICAL_REFERENCE.md).

> **Maintenance rule:** This document should be updated whenever a change materially affects functionality, limitations, or operational behavior. See [CLAUDE.md](../CLAUDE.md) for the docs-update rule.

---

## What this project is

A **marketing operations platform** for Honeycomb Credit's small-business investment crowdfunding campaigns. It is NOT product code — it's an automation layer that helps the marketing team run Meta (Facebook/Instagram) ads more efficiently.

Three things live inside the repo:

1. **The "brain"** — a Google Apps Script program (~4,200 lines) that runs every day, pulls data from Meta and HubSpot, does the math, writes summaries, and proposes budget changes.
2. **The "dashboard"** — a web page (hosted on GitHub Pages) where the team can see charts, check campaign health, and ask questions via an AI chat called "Hive Mind."
3. **The "plumbing"** — GitHub Actions that automatically push code changes to the Google Apps Script servers whenever something is merged, so nobody has to copy/paste into the Apps Script web editor.

Everything is connected through a single Google Spreadsheet that stores all the data.

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

---

## What's working well

- **Data integrity.** Six known data-quality issues surfaced in the Q1 2026 audit (week convention drift, duplicate narratives, ID precision loss, CPL handling, spend mismatches, floating-point residuals) have all been fixed, tested, and verified through the audit snapshots.
- **Attribution model.** The hybrid v3 attribution (Meta IC conversions as floor + proportional share of unattributed HubSpot ICPs) is sound and consistent between the weekly rollup and the narrative generator. They now agree to the cent.
- **Human-in-the-loop safety.** No automated system pushes budget changes to Meta without a human clicking Approve in Slack. Two-step confirmation prevents Slack's link-unfurling bots from accidentally approving anything.
- **Idempotency.** The narrative generator won't write a duplicate row if the week already has one. The Meta data fetcher deduplicates by date+campaign_id. The budget system tracks a single "pending" token at a time.
- **Audit trail.** Every budget change proposed, approved, rejected, or executed is recorded in the `budget_queue` sheet with a reason, a timestamp, and who approved it. Every narrative is timestamped in `intelligence_log`.
- **Deployment hygiene.** Code changes go through pull requests on GitHub, get deployed automatically, and never require anyone to edit the Apps Script web editor. This keeps the repo as the single source of truth.
- **Audit snapshot pipeline.** Claude Code can pull the last 90 days of data anytime and do health checks.

---

## Current limitations and gaps

### Data-quality risks still open

- **Attribution quality dropped sharply week of 4/13.** 33% attribution quality vs. 85% the week before. That's a tracking problem in Meta (probably a pixel or custom-conversion setup), not a code bug, but it means recent CPICP numbers carry significant uncertainty until investigated.
- **Campaign mapping is partially populated.** Only 4 of 20 campaigns in the mapping sheet have the "Prequal results page view" conversion event configured, and only 2 have the newer "Investment Crowdfunding Prequal Decision" event. Campaigns without custom_conversion_id set fall back to Meta's generic lead conversion, which gives fuzzier ICP attribution.
- **One narrative row uses an older attribution model.** The 3/30 row was written under the v2 "blended" model before the v3 hybrid fix. Its numbers are correct for that week under the old method but aren't directly comparable to surrounding weeks.

### Operational gaps

- **No alerting on pipeline failures.** If the daily 7 AM pull breaks (e.g., expired Meta token), you only find out when someone notices the Slack digest didn't arrive or the dashboard shows stale data. No proactive "hey, this job failed" alert.
- **No alerting on attribution-quality drops.** The 33% collapse that week could have gone unnoticed for days. A threshold-based alert ("IC attribution below 50% — investigate") would catch this earlier.
- **Manual steps for new campaigns.** When the team launches a new Meta campaign, the mapping sheet auto-discovers the UTM tag, but conversion event mapping often needs manual verification. If a campaign's custom_conversion_id doesn't get filled in, its ICPs won't be properly tracked.
- **Audit snapshot is manual.** Someone has to run `exportAuditSnapshot()` from the Apps Script editor to refresh data for Claude Code. Adding a weekly time trigger would make this automatic.
- **No recurring health check.** The Q1 audit uncovered 6 issues only because someone did a deep-dive. Without a scheduled audit — weekly or monthly — similar drift could accumulate again.

### Content / copy gaps

- **The `/ad-copy/`, `/workflows/`, `/audiences/`, and `/reports/` directories are empty placeholders.** CLAUDE.md describes them as if populated, but no content exists. If the team wants to use this repo as their content library too (not just automation), those directories need work.

### Technical debt

- **Hybrid attribution math is duplicated.** The weekly rollup and the budget analyzer each compute hybrid ICPs independently. If one is updated and the other isn't, budget decisions could drift from reported numbers. Worth extracting into a single shared function.
- **Multiple Meta campaigns map to one UTM value.** "for ag" covers 3 Meta campaigns, "for ICrev2test" covers 2. Not a bug — just means segment-level rollups combine spend across these.
- **Campaign-mapping typo.** One row reads "Q4 2205" instead of "Q4 2025." Cosmetic but worth cleaning up.
- **Hardcoded Claude model in 3 places.** If we upgrade from Sonnet 4.6 to 4.7, we'd need to change it in three spots. Should be a constant.
- **No rate limiting on the chat endpoint.** Someone could hammer the Hive Mind chat and run up Anthropic API costs. Low likelihood given it's a hidden feature, but worth knowing.
- **Audit snapshot uses GitHub's low-level Git API directly.** Works, but code is verbose and has no retry logic on GitHub API errors.

### Compliance / security

- **Credentials rotation is manual.** Meta tokens, HubSpot keys, Anthropic keys, GitHub PATs all live in Apps Script Properties as plain text. No automatic expiration reminder, no rotation schedule.
- **Logs live in Apps Script only.** If you need to audit what happened 6 months ago, you have to dig through the Apps Script execution log, which has limited retention and no search.
- **Web App is publicly accessible (ANYONE_ANONYMOUS).** The `/exec` URL has no auth. Anyone who knows the URL can hit the dashboard API. Dashboard data isn't super-sensitive (campaign metrics) but it's worth knowing.

---

## Known risks worth watching

1. **Attribution collapse of 4/13 could repeat.** The underlying Meta tracking setup needs a once-over. If the IC conversion pixel or event configuration is partially broken, all subsequent CPICP numbers get noisy.
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
- Investigate the 4/13 attribution collapse directly in Meta. This is a marketing-operations task, not a code task, but it's the single most valuable thing right now.
- Extract the shared hybrid attribution math into one function used by both the weekly rollup and the budget analyzer.
- Populate `custom_conversion_id` for all active campaigns in campaign_mapping.

### Medium impact, low effort
- Move the Claude model name to a constant/config at the top of Code.js.
- Add a Meta token expiration warning (check validity at start of daily pipeline, alert Slack if close to expiring).

### Lower priority
- Populate `/ad-copy/`, `/workflows/`, `/audiences/`, `/reports/` directories if the repo is meant to host content too.
- Consider an external log sink (Cloud Logging) for long-term auditability.
- Add rate limiting to the chat endpoint.

---

## Summary in one paragraph

This is a mature, working automation platform. The core data pipeline runs daily without intervention, the budget optimizer has appropriate human-in-the-loop safeguards, and the recent Q1 audit confirmed the data-quality fixes are holding. The most pressing concern is an operational tracking issue in Meta (not code) that crashed attribution quality in the most recent week. After that, the biggest ROI improvements are all around observability: proactive alerts on pipeline failures, attribution drops, and token expiration. The system does not currently tell you when it's broken — you have to notice.
