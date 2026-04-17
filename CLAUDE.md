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

## Repo Structure

- `/apps-script/` — Full Apps Script intelligence layer, deployed via clasp + GitHub Actions
  - `Code.js` — The complete intelligence script (~3,600 lines). Edit here, never in the Apps Script web editor
  - `.clasp.json` — Points clasp at the Apps Script project (do not edit)
  - `appsscript.json` — Apps Script manifest (scopes, runtime, Web App settings)
- `/webapp/` — Honeycomb Ads Intelligence Dashboard (single-file React SPA on GitHub Pages)
  - `index.html` — The full dashboard app
  - `apps-script-api.gs` — Reference copy of the web API layer (handleDashboardApi_, Hive Mind chat, Slack approval flow). This is a subset of Code.js for documentation purposes — the live deployed version comes from apps-script/Code.js
- `/ad-copy/` — Meta (Facebook/Instagram) ad copy organized by vertical
- `/workflows/` — Automation scripts and marketing workflows
- `/audiences/` — Audience lists and segmentation data (never commit PII)
- `/reports/` — Campaign performance reports
- `.github/workflows/` — GitHub Actions CI/CD
  - `deploy-webapp.yml` — Auto-deploys dashboard to GitHub Pages on changes to webapp/
  - `deploy-apps-script.yml` — Auto-deploys Apps Script via clasp on changes to apps-script/

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

## Compliance Notes

- Honeycomb Credit is a regulated investment platform (Reg CF)
- Do not draft content that guarantees investment returns
- Do not include specific APY/interest rate claims without explicit approval
- All investment-related copy should include: "Investing involves risk"
