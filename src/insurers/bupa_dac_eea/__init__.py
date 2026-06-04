"""BUPA DAC EEA region (PROD-1968) — adapter sub-module.

EEA PDF layout is completely different from Monaco/France (PROD-1985):
- 1 page = 1 (plan, currency, deductible, coverage) — single annual frequency
- Columns are 11 Pricing Zones (Zone 1 Incl US through Zone 11)
- 1 row per age (0-4, 5, 6, ..., 85+)

Zone semantics (validated against insurer-confirmed values 2026-05-20):
- Zone 1 (Incl US): always = "Worldwide" rate (includes USA), regardless of
  the page's coverage label
- Zones 2-11: the page-labeled coverage's tiers ("Europe" or "WWExclUS")
- Per BUPA pricing zones reference (BGEEA), Zone 8 corresponds to the
  standard EEA country band (excluding Ireland/Germany/Poland/Austria
  which are excluded from the EEA product entirely)

Known PDF defects (Nishma Prakash, 2026-05-19):
1. EUR/USD pages in Select/Premier/Elite say "WW" but rates are actually
   "WWExclUS" content — Zone 1 of those pages IS the genuine WW.
2. Select/Premier/Elite EUR pages have DUPLICATE OP-IP combinations under
   the same "Europe" title (e.g., 2× pages claiming OP EUR 625 IP 625
   Europe with different rates).
3. Major Medical PDF lacks any genuine "WW" page; Zone 1 of WWExclUS pages
   carries the WW rate.

The parser preserves what's actually in the PDF and emits warnings for
each duplicate-title encountered. Disambiguation is done by page order
(first instance = Europe, second = WWExclUS, per the file convention).
"""
