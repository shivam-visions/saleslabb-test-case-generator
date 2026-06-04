# G1 Retroactive Pilot — Evidence & Verdict (2026-06-04)

Gate G1 of NDS-1190 Phase 3b (`MIRO/docs/IPM-Validation-Gate-Proposal-v2.md`,
Confluence https://vitavirtues.atlassian.net/wiki/x/DYAzIw).

**Question:** what fraction of the real, historical dev↔IPM back-and-forth would
the staged gate have caught pre-ticket?

**Method:** pulled every comment thread from 7 historical PROD tickets
(PROD-1845, 1930, 1968, 1985, 2022, 2039, 2179 — Now Health, Morgan Price,
BUPA DAC ×2, Xen ×2, Orient), extracted 26 discrete dev↔IPM data
queries/rework items, classified each against the pipeline stage that would
have caught it.

## Classification

| # | Ticket | Query (condensed) | Catchable by |
|---|---|---|---|
| 1 | 1845 | Ticket incomplete — Confluence/links missing | **B1/T1** intake checklist |
| 2 | 2022 | Provider display name wrong | semantic |
| 3 | 2022 | Benefits must show "Not Available" for male/single-female/45+ | **T2-new** (eligibility-condition pattern rule) |
| 4 | 2022 | List plans in given order | semantic |
| 5 | 2022 | Maternity premium charged to male applicants (post-live!) | **T3-ext** (sandbox verify of modifier conditions) |
| 6 | 2039 | Provider name (dup of #2) | semantic |
| 7 | 2039 | Benefits "Not Available" (dup of #3) | **T2-new** |
| 8 | 2039 | Plan order (dup of #4) | semantic |
| 9 | 1985 | Verification stalled awaiting insurer sample quotes | insurer-side |
| 10 | 1930 | Links missing, plan can't be picked up | **B1/T1** |
| 11 | 1930 | Enhanced Network Module / hospital-list spec ambiguous | semantic/insurer |
| 12 | 1930 | OP-excluded dropdown should read "Not available" | semantic |
| 13 | 1930 | Maternity only with OP module 2 (dependency) | semantic |
| 14 | 1930 | Remove VAT | semantic (advisory WARN possible, §12.10) |
| 15 | 1930 | Enhanced Network Module **rates missing** | **T2-today** (T2-ADD-004 flag→child coverage) |
| 16 | 1930 | IP deductibles out of order | **T2-new** (option-ordering rule) |
| 17 | 1930 | Excess discount applied on base instead of after options | **T2-today** (T2-ADD-005 override-vs-additive flag) |
| 18 | 1968 | Some currencies missing coverages (USD has WW, EUR/GBP don't) | **T2-new** (plan×coverage×currency completeness) |
| 19 | 1968 | Insurer mislabeled WW vs WWX | insurer-side (B2 diff aids visibility) |
| 20 | 1968 | .xlsm source structure ambiguous | **B3** (extraction-confidence review) |
| 21 | 1968 | Duplicate/unclear options — "update only confirmed ones" | **T2-new** (duplicate-option rule) |
| 22 | 1968 | Options missing from raw; family-plan rates inaccurate | **T3** (verify vs raw) |
| 23 | 1968 | Family/promotional discounts per Confluence not factored | semantic |
| 24 | 2179 | Rates PDF has empty copay cells | **B3** (low-confidence extraction flag) |
| 25 | 2179 | Copay listing combinations wrong | **T3** (needs Orient extractor) |
| 26 | 2179 | Rates inaccurate, twice; wrong source file | **T3** (needs Orient extractor) |

## Tally

| Bucket | Count | Share | Cumulative catch-rate |
|---|---|---|---|
| B1/T1 — intake completeness | 2 | 8% | 8% |
| T2 — rules that fire **today** | 2 | 8% | 15% |
| T2 — five new rules (IMPLEMENTED 2026-06-04 evening: T2-ADD-007/008, T2-BEN-010/011, T2-RATE-013, T2-INFO-012) | 5 | 19% | **35% — live** |
| B3 — Stage-1 extraction-confidence review | 2 | 8% | **42%** |
| T3 — sandbox verify vs raw (incl. Orient extractor + condition checks) | 4 | 15% | **58%** |
| Semantic (wording/display/judgment) | 9 | 35% | — |
| Insurer-side (never-sent / mislabeled source) | 2 | 8% | — |

**Baseline (success-metric #1): 26 queries / 7 tickets ≈ 3.7 dev↔IPM exchanges
per ticket.**

## Verdict: CONDITIONAL GO — proceed, re-weighted

- Strict T2-only reading: 35% < the 60% threshold → the static layer alone is
  **not** the product.
- Full-pipeline reading: 58%, within noise of the threshold on n=26 — and the
  miss is concentrated exactly where the proposal predicted: **rate-value
  fidelity (T3) and semantic wording (out of scope by design)**.
- Three of the four T3 items sit on carriers **without extractors** (Orient) or
  without **condition-level verification** — i.e. the Phase E backlog is where
  the catch-rate grows, not more static rules.
- Duplicate queries across residency twins (#6–8 mirror #2–4) mean per-bundle
  gating fixes once what today is queried twice.

**Actions:** (1) add the five identified T2 rules; (2) keep Phase B/C as
planned; (3) **promote Orient + condition-verification up the Phase E
priority list** — the data says that's the highest-value coverage; (4) revisit
catch-rate after the first live month (G3).
