# marketing-claude-honeycomb

Marketing automation monorepo for [Honeycomb Credit](https://www.honeycombcredit.com/) — a community investment platform that helps small businesses raise capital through investment crowdfunding (Reg CF).

## What lives here

This is a **marketing operations repo**, not a product codebase. It contains four systems:

| System | Where | What it does |
|---|---|---|
| **Apps Script intelligence layer** | `apps-script/Code.js` (~4,200 lines) | Daily Meta + HubSpot pulls, weekly rollup, AI narrative, budget optimizer with Slack approval flow |
| **React dashboard** | `webapp/index.html` (single file) | GitHub Pages SPA — campaign charts, leaderboards, goal tracker, Hive Mind AI chat |
| **Ad-level agent pipeline** | `scripts/` + `data/` | Python scripts that pull per-ad Meta insights daily and write JSON snapshots to the repo (the repo is the database) |
| **Agent skills** | `skills/<name>/SKILL.md` | Self-contained operating manuals + Python scripts run by Claude Code to monitor fatigue, check pipeline health, and generate ad copy |

## Key directories

```
apps-script/       Apps Script source (deploy via clasp + GitHub Actions — never edit in the web editor)
webapp/            Single-file React dashboard (auto-deployed to GitHub Pages)
docs/              Living documentation — keep in sync with code changes
  STATE_REPORT.md      Plain-English project state (for non-technical stakeholders)
  TECHNICAL_REFERENCE.md  Engineering reference (architecture, data model, APIs, function index)
skills/            Agent skill packages: pipeline-health, daily-check, fatigue-monitor,
                   creative-intelligence, ad-copy-generator
scripts/           Python pipeline: fetch_ad_data.py, compute_signals.py, lib/meta.py (shared client)
data/
  config/benchmarks.json   All thresholds — never hardcode numbers elsewhere
  snapshots/<YYYY-MM-DD>/  Daily Meta snapshots committed by daily-data.yml
  derived/                 Computed signals (regenerable; delete and rerun compute_signals.py)
  creatives/               Creative metadata + LLM categorizations + full-size images
  drafts/                  Ad-copy drafts written by ad-copy-generator skill
.github/workflows/ CI/CD: deploy-apps-script, deploy-webapp, daily-data, agent-* workflows
```

## Authoritative documentation

For a complete description of how the system works, read:

- **[docs/STATE_REPORT.md](docs/STATE_REPORT.md)** — what the system does, what's working, limitations, risks, recommended next steps. Written for non-technical stakeholders.
- **[docs/TECHNICAL_REFERENCE.md](docs/TECHNICAL_REFERENCE.md)** — architecture, data model, API integrations, deployment, function index, tech debt. Written for engineers.
- **[CLAUDE.md](CLAUDE.md)** — operating instructions for Claude Code agents working in this repo, including brand voice, compliance rules, agent skill constraints, and Meta API conventions.

## Deployment

- **Apps Script:** merge to `main` → CI runs `clasp push` + `clasp deploy --deploymentId <fixed-id>`. The dashboard `/exec` URL never changes.
- **Dashboard:** merge to `main` → CI publishes `webapp/` to GitHub Pages at `https://tylerhoneycomb.github.io/marketing-claude-honeycomb/`.
- **Ad-level pipeline:** `daily-data.yml` cron fires at 8 AM ET, commits a new snapshot under `data/snapshots/<YYYY-MM-DD>/`.

## Autonomous agent workflows

Five GitHub Actions wrap `claude-code-action@v1` to run skills on schedule:

| Workflow | Schedule | Skill |
|---|---|---|
| `agent-pipeline-health.yml` | Daily 9 AM ET | Four health checks; Slack on WARN/FAIL only |
| `agent-daily-check.yml` | Daily 8:30 AM ET | Morning pacing + portfolio + fatigue briefing |
| `agent-fatigue-monitor.yml` | Mon + Thu 9:30 AM ET | Per-ad fatigue classification with budget-conflict cross-reference |
| `agent-creative-intelligence.yml` | Monday 10 AM ET | Weekly creative copy + visual pattern brief |
| `agent-ad-copy-generator.yml` | Manual (`workflow_dispatch`) | Drafts new ad copy from winning patterns; never auto-published |

All agent workflow runs post a status comment to [issue #48](https://github.com/tylerhoneycomb/marketing-claude-honeycomb/issues/48).

## Compliance

Honeycomb Credit is a regulated investment platform (Reg CF). All ad copy and marketing content must:
- Never guarantee investment returns or cite specific APY/interest rates without explicit approval
- Include "Investing involves risk" in investment-related content
- Pass the compliance regex backstop in `ad-copy-generator` before human review
