# Matching approach (M3)

For each open lost report, candidates are open/in-custody found reports of the **other type**, within the configured time window, not reported by the same person. Each pair gets five factor scores in [0, 1]:

| Factor | Rule |
|---|---|
| Category | 1.0 same (sub)category, 0.6 same parent category, 0 otherwise |
| Text | trigram similarity of the *public* search text (title, description, category, attributes). On PostgreSQL computed by `pg_trgm` `similarity()`; elsewhere by a pure-Python implementation of the same algorithm (a test confirms they agree) |
| Attributes | share of attributes present on both reports whose normalised values are equal |
| Location | 1.0 same place; 0.7 parent/child/sibling in the tree; 0.4 same wider area; or distance decay to 0 at 1 km when coordinates exist — the higher of these |
| Time | `1 − |Δdays| / window`; 0 if the "found" time is more than a day before the "lost" time |

`score = Σ weight·factor / Σ weight`. Default weights: text 0.35, category 0.25, attributes 0.15, location 0.15, time 0.10; threshold 0.45. System administrators change them in *Policies & settings*. Every suggestion stores its factor breakdown, shown to the owner as bars.

Runs: on report creation, on receipt into custody, nightly (`refresh_matches`), and on demand. Dismissed suggestions stay dismissed; the owner is notified once per pair.

**Acceptance check:** `test_matching.py` seeds 10 true pairs plus 10 distractors and requires ≥70% recall and no distractor matches; it currently achieves 10/10 recall with the true pair ranked first for all 10. Precision is looser: other same-category items in the window also clear the default 0.45 threshold (50 suggestions for 10 lost reports in that fixture), shown lower in the list. Raise `match_threshold` or the text weight once you see real campus data.

Private details are never used for matching (also asserted by a test). Image similarity is not implemented (optional in the specification).

Public board search (PostgreSQL): `websearch_to_tsquery` full-text ranking on title (weight A) and search text (weight B), OR trigram word similarity ≥ 0.45 for typos.
