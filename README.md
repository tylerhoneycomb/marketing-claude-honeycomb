# marketing-claude-honeycomb

Marketing automation, ad intelligence, and Claude Code AI tooling for [Honeycomb Credit](https://honeycombcredit.com) — a community investment platform that helps small businesses raise capital from their own customers through investment crowdfunding.

This is a **marketing operations repo**, not product code. It automates Meta (Facebook/Instagram) ad management, surfaces creative performance signals, and routes budget recommendations through a human-in-the-loop Slack approval flow.

---

## Documentation

| Document | Audience | Contents |
|---|---|---|
| [`docs/STATE_REPORT.md`](docs/STATE_REPORT.md) | Non-technical stakeholders | What the system does, what's working, current limitations, known risks, recommended next steps |
| [`docs/TECHNICAL_REFERENCE.md`](docs/TECHNICAL_REFERENCE.md) | Developers / AI coding tools | Architecture, data model, API integrations, deployment, full function index, technical debt |
| [`CLAUDE.md`](CLAUDE.md) | Claude Code agent | Operating instructions, repo conventions, agent skill and workflow details |

**Start with `docs/STATE_REPORT.md` for a plain-English overview. Use `docs/TECHNICAL_REFERENCE.md` to understand or modify any specific component.**

---

## What's in this repo

### Campaign-level pipeline (`apps-script/`)
A Google Apps Script program (~4,200 lines) that runs on Google's infrastructure. It pulls daily data from Meta and HubSpot, builds weekly rollups, generates AI narratives, proposes budget changes, and routes approvals through Slack. Deployed automatically via `clasp` on every merge to `main`.

### Ad-intelligence dashboard (`webapp/`)
A single-file React SPA hosted on GitHub Pages. Connects to the Apps Script Web App via `/exec` — no separate backend. Includes leaderboards, trend charts, goal tracking, budget controls, and a hidden "Hive Mind" AI chat.

### Ad-level agent pipeline (`scripts/` + `data/`)
A Python pipeline that pulls ad-set and ad-level insights from Meta daily and commits JSON snapshots to `data/snapshots/<YYYY-MM-DD>/`. Signals (fatigue, winner/bleeder) are computed to `data/derived/`. The repo acts as the database.

### Agent skills (`skills/`)
Six self-contained skills that Claude Code reads and executes:

| Skill | Description | Schedule |
|---|---|---|
| `pipeline-health` | Four health checks (data freshness, Meta token, IC event, dashboard endpoint) | Daily inside `daily-data.yml` |
| `daily-check` | Morning briefing: pacing, CPICP rankings, fatigue flags | **PAUSED** (was daily 8:30 AM ET) |
| `fatigue-monitor` | Per-ad fatigue classification + budget-queue conflict detection | **PAUSED** (was Mon+Thu 9:30 AM ET) |
| `creative-intelligence` | Weekly corpus analysis of winning copy and visual patterns | **PAUSED** (was Mon 10 AM ET) |
| `ad-copy-generator` | Drafts new ad-copy variants from the creative dataset | Manual only |
| `portfolio-scaling` | Weekly vertical elasticity diagnosis + pool-based budget reallocation | **Active** Tuesdays ~9:43 AM ET |

### GitHub Actions (`.github/workflows/`)
- `daily-data.yml` — daily Meta snapshot + pipeline-health checks (~8:37 AM ET)
- `agent-portfolio-scaling.yml` — weekly Tuesday brief
- `deploy-apps-script.yml` — clasp push on merge to main
- `deploy-webapp.yml` — GitHub Pages deploy on merge to main
- `claude.yml` — `@claude` mentions in issues/PRs
- Six `agent-*.yml` files for the skills above (three currently paused, two dispatch-only)

---

## Key constraints

- **Never edit the Apps Script web editor directly.** `apps-script/Code.js` is the sole source of truth; CI overwrites anything edited in the editor.
- **No budget changes go to Meta without human approval.** Every proposal flows through a two-step Slack confirmation (defeats link-unfurling bots).
- **The agent never writes to Meta directly.** All budget recommendations surface via the existing Slack approval pipeline.
- **Secrets live in two places.** `META_ACCESS_TOKEN` is in both Apps Script Script Properties (campaign-level pipeline) and the GitHub Secret (ad-level pipeline). Rotate both.
- **Thresholds live in one place.** `data/config/benchmarks.json` is the single source for all fatigue/budget/scaling thresholds — never hardcode them in scripts.

---

## Compliance

Honeycomb Credit is a regulated investment platform (Reg CF). All content must avoid:
- Specific return or investment performance promises
- APY/interest rate claims without explicit approval
- Any language that guarantees investment outcomes

All investment-related copy must include: *"Investing involves risk."*
