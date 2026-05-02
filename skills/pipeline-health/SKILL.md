# Skill: Pipeline Health

## Purpose

Verify that the data pipelines feeding the agent are working: ad-level snapshots are fresh, derived signals are recent, the campaign-level Apps Script pipeline is still running, and tokens haven't silently expired.

## When to invoke

- Tyler asks "is the pipeline OK?" / "did the data come in?" / "why is the dashboard stale?"
- The daily-check skill detected a missing or > 36-hour-old snapshot
- After any deployment that touched `scripts/`, `apps-script/Code.js`, or workflows

## What to check

### 1. Ad-level snapshot freshness

```
ls data/snapshots/ | sort | tail -3
```

- Most recent dir should be yesterday's UTC date (or today's, depending on when this runs)
- Open `data/snapshots/<latest>/_manifest.json`. Verify `exported_at` is within the last 30 hours.
- Verify `counts` is non-zero across `campaigns`, `adsets`, `ads`, `ad_insights`.

### 2. Derived signals freshness

- `data/derived/summary.json` `computed_at` should match (or be later than) the latest snapshot's `exported_at`.
- If the snapshot exists but `summary.json` is older, `compute_signals.py` failed silently. Re-run it locally:
  ```
  python3 scripts/compute_signals.py
  ```

### 3. Workflow run history

Check the GitHub Actions UI (the agent can use `mcp__github__list_pull_requests`-adjacent tools, or Tyler runs this manually):
- "Daily Ad Data Collection" workflow run on the expected schedule (manual-only currently — confirm it ran when triggered)
- "Deploy Apps Script" workflow last green run
- "Deploy Webapp" workflow last green run

### 4. Apps Script pipeline (campaign-level)

The legacy campaign-level pipeline is the source of truth for the dashboard. Check via the audit-snapshots branch:

```
git fetch origin audit-snapshots
git show origin/audit-snapshots:snapshots/_manifest.json
```

- `_manifest.json` should show recent `exported_at` (manual export — see `STATE_REPORT.md` § "Operational gaps")
- `rolling_data` row count should match daily growth (~20 campaigns × 1 row/day × 90 days ≈ 1,800)

### 5. Token expiration risks

Meta long-lived tokens expire periodically. Symptoms:
- `fetch_ad_data.py` returns HTTP 400 with `OAuthException`
- The campaign-level `fetchMetaAdsData` posts a 400 to Slack

If suspected, ask Tyler to regenerate `META_ACCESS_TOKEN` in:
- Apps Script → Project Settings → Script Properties (for the legacy pipeline)
- GitHub → repo Settings → Secrets → Actions → `META_ACCESS_TOKEN` (for the new ad-level pipeline)

## Output format

```
# Pipeline health — <YYYY-MM-DD>

## Ad-level snapshot
- Latest: <date> (<age in hours> ago)  ✓ | ✗
- Counts: campaigns=<n>, adsets=<n>, ads=<n>

## Derived signals
- Last computed: <iso>  ✓ | ✗
- Severity counts: critical=<n>, warning=<n>, ok=<n>

## Apps Script pipeline (legacy, campaign-level)
- audit-snapshots last refresh: <date> (<age> ago)
- Note: manual export, may be stale (see STATE_REPORT § Operational gaps)

## Workflows
- Daily Ad Data Collection: <green / red / not run>
- Deploy Apps Script: <green / red>

## Recommendations
- <only if issues found>
```

If everything is healthy, the response should be one paragraph: "All pipelines current as of <timestamp>. Latest ad snapshot <date> with N ads. Derived signals last computed <Y minutes ago>." Do not pad.

## Constraints

- This skill is **read-only**. Do not push commits, do not regenerate snapshots, do not edit `Code.js`. If a pipeline is broken, surface the diagnosis and let Tyler decide.
- Do not call the Meta API directly to check token validity. The fact that the latest snapshot has data is the indirect health signal.
- If `data/snapshots/` is completely empty, that's expected on a fresh setup. Say so explicitly: "No snapshots yet — first run of the daily-data workflow has not completed."
