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

- `/ad-copy/` — Meta (Facebook/Instagram) ad copy organized by vertical
- `/workflows/` — Automation scripts and marketing workflows
- `/audiences/` — Audience lists and segmentation data (never commit PII)
- `/reports/` — Campaign performance reports
- `.github/workflows/` — GitHub Actions CI/CD

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
