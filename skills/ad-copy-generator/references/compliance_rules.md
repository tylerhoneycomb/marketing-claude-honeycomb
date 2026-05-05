# Compliance Rules for Generated Copy

Honeycomb Credit operates under SEC Regulation Crowdfunding (Reg CF). Every piece of investment-related ad copy is subject to disclosure rules, prohibitions on certain claims, and required disclaimers. The drafts this skill generates are **draft only** — they need a human reviewer (and likely a compliance-team check) before going live in any campaign.

## Hard prohibitions (drafts containing these are non-shippable as written)

1. **No specific return promises.** Do not write "Earn 12% annually," "10% APY," "5x returns," or any phrasing that quantifies future returns. Honeycomb securities are revenue-sharing notes — the actual return depends on the issuer's revenue, which is not guaranteed.

2. **No guarantee language.** Avoid "guaranteed," "risk-free," "promised," "certain," "ensure your return," or any synonym. Investment outcomes are inherently uncertain.

3. **No solicitation of accredited-investor-only language for general audiences.** Reg CF is open to non-accredited investors at low minimums; do not write copy implying restrictions that don't exist, or that the campaign is exclusive in a way that misleads.

4. **No projection without basis.** "This brewery will double its revenue" is a forward-looking statement. Generic optimism ("help them grow") is fine; specific financial projections aren't.

5. **No comparisons to FDIC-insured products.** Honeycomb investments are not bank deposits; do not phrase them in a way that implies similar safety profiles.

6. **No testimonials that reference specific returns.** "Sarah earned $X on her investment" is regulated content. Stick to operational testimonials (the business raised money, not what investors earned).

## Required framings

7. **"Investing involves risk."** This phrase or its equivalent should appear in every investment-related piece of copy at the campaign or asset level. The drafts this skill produces should either include the phrase explicitly OR be flagged for the reviewer to add it before publication.

8. **The investment offering, not the loan.** Write to small-business owners about raising capital from their customers. The customer-side copy (the side that says "invest in this business") is governed by stricter rules and is NOT the audience for this skill — this skill drafts owner-facing acquisition copy.

## Soft guidelines (don't fail compliance, but watch the line)

9. **"500+ businesses funded" / "$50M+ raised" — verify before publishing.** These are real Honeycomb stats and have been cleared, but if the numbers go stale (e.g. "1,000+" or "$100M+"), update at the source rather than letting the draft go live with old figures.

10. **"No MCA. No bank. Choose your own rate."** This phrasing has been cleared and is in the corpus. The phrase "choose your own rate" is owner-facing (the small business sets the terms of the offering subject to platform rules), not a return promise to the investor.

11. **"Prequalify instantly. Zero commitment."** Cleared. The prequalification doesn't bind the owner to a raise.

## Reviewer's checklist

When the human reviewer reads a generated draft, they should confirm:

- [ ] No specific return numbers
- [ ] No guarantee language
- [ ] "Investing involves risk" is included or queued to be added
- [ ] Stats (500+ / $50M+) match current platform totals
- [ ] CTA is a prequalification or learn-more invitation, not an investment commitment
- [ ] Reads as targeted to a small-business owner, not an end investor

## Dropping drafts that fail

The script flags drafts that contain prohibited phrases at generation time (regex match on a small block-list including "guaranteed," "guarantee," "% APY," numeric percentage returns, etc.). Flagged drafts are still printed for visibility but tagged with a `compliance_flag` field — the reviewer should NOT publish those. Treat the regex as a backstop, not a substitute for human review.
