# marketing-claude-honeycomb

Marketing operations monorepo for [Honeycomb Credit](https://honeycombcredit.com) — a community investment platform that helps small businesses raise capital from their own customers through investment crowdfunding.

This is **not product code**. It is an automation layer that helps the marketing team run Meta (Facebook/Instagram) ads more efficiently, monitor creative fatigue, and propose data-driven budget changes — all with human approval required before any money moves.

---

## What's in here

| Directory | What it is |
|---|---|
| `apps-script/Code.js` | The "brain" — ~4,200-line Google Apps Script that runs daily, pulls Meta + HubSpot data, proposes budget changes, and powers the dashboard API |
| `webapp/index.html` | The "dashboard" — single-file React SPA hosted on GitHub Pages; shows charts, leaderboards, and an AI chat called Hive Mind |
| `scripts/` | Python pipeline that pulls ad-level Meta data into daily JSON snapshots under `data/snapshots/` |
| `skills/` | Six Claude Code skill packages (SKILL.md + Python scripts) that run autonomously via GitHub Actions |
| `data/` | Agent data repository: `snapshots/` (read-only), `derived/` (regenerable signals), `creatives/` (LLM categorization cache), `drafts/` (ad copy for human review) |
| `.github/workflows/` | Eleven GitHub Actions workflows: 2 deploy pipelines, 1 daily data pull, 1 @claude handler, 7 autonomous agent skills |
| `docs/` | Living documentation — keep in sync with code |

---

## How it runs

Two parallel data pipelines, one presentation layer, one agent layer:

1. **Campaign-level pipeline** (Apps Script + Google Sheets) — daily 7 AM pull, Monday narrative, daily 6 AM budget proposals, daily 3 AM execution. Source of truth for the dashboard and approval flow.
2. **Ad-level pipeline** (Python + GitHub Actions) — daily 8 AM ET pull of ad/adset-level insights into `data/snapshots/`. Powers the six agent skills.
3. **Dashboard** — React SPA on GitHub Pages talking to the Apps Script `/exec` endpoint.
4. **Agent layer** — Six Claude Code skills run on cron via GitHub Actions. All budget recommendations route through Slack approval; the agent never writes to Meta directly.

### Agent skills

| Skill | Schedule | What it does |
|---|---|---|
| `pipeline-health` | Daily 9 AM ET | Checks data freshness, Meta token, IC conversion event, dashboard endpoint |
| `daily-check` | Daily 8:30 AM ET | Morning briefing: pacing, portfolio CPICP, winners/bleeders, fatigue flags |
| `fatigue-monitor` | Mon + Thu 9:30 AM ET | Per-ad fatigue classification with budget-conflict cross-reference |
| `creative-intelligence` | Monday 10 AM ET | What copy/visual patterns are winning; corpus-level attribution |
| `ad-copy-generator` | On-demand only | Drafts new ad variants from Creative Intelligence data; never auto-published |
| `portfolio-scaling` | Tuesday 9:30 AM ET | Vertical-level scaling diagnosis + pool-based strategic reallocation proposal |

---

## Key rules

- **Never edit the Apps Script web editor directly** — CI overwrites it. Edit `apps-script/Code.js` and open a PR.
- **Snapshots are read-only.** `data/snapshots/` is written by the daily GitHub Action. Do not hand-edit.
- **Thresholds live in one place.** `data/config/benchmarks.json` is the single source for all signal thresholds. Never hardcode numbers in scripts.
- **Drafts are never auto-published.** Every file in `data/drafts/` requires human review per the compliance checklist before going live.
- **No PII in the repo.** The `audiences/` directory exists as a placeholder but nothing that identifies a person may be committed here.

---

## Documentation

- [`docs/STATE_REPORT.md`](docs/STATE_REPORT.md) — plain-English overview for non-technical stakeholders. What it does, what's working, current limitations.
- [`docs/TECHNICAL_REFERENCE.md`](docs/TECHNICAL_REFERENCE.md) — engineering reference: architecture, data model, all API endpoints, deployment, function index, technical debt.
- [`docs/CREATIVE_INTELLIGENCE_DESIGN.md`](docs/CREATIVE_INTELLIGENCE_DESIGN.md) — design rationale for the corpus-aggregation attribution model used by the creative-intelligence skill.
- [`CLAUDE.md`](CLAUDE.md) — instructions for Claude Code (this assistant) when working in this repo.

Both `STATE_REPORT.md` and `TECHNICAL_REFERENCE.md` must be kept in sync with code changes. When you make a change, update the relevant doc in the same PR.

---

## Deployment

- **Apps Script:** merge to `main` → CI runs `clasp push` + `clasp deploy` automatically. Live in ~60 seconds.
- **Dashboard:** merge to `main` affecting `webapp/` → CI publishes to GitHub Pages. URL: `https://tylerhoneycomb.github.io/marketing-claude-honeycomb/`.
- **Agent skills:** no deployment needed — GitHub Actions read skill files directly from `main` on each run.

## Compliance

Honeycomb Credit is a regulated investment platform (Reg CF). Content rules:
- Never guarantee investment returns or cite specific APY/interest rates without explicit approval.
- All investment-related copy must include: "Investing involves risk."
- Ad copy drafts require the 6-item reviewer checklist in `SKILL.md` before going live.
