# Constants sensitivity sweep

Run via `code/evaluation/sensitivity.py`. For each previously-unvalidated
threshold, sweeps a wide range and measures how many of the 110 messages'
rule-firing pattern changes. Flat = not load-bearing, safe as an informal
choice. A cliff = fitted to this corpus and needs justification or removal.

| Constant | Default | Swept range | Result |
|---|---|---|---|
| `HR2_YOUNG_DOMAIN_DAYS` | 30 | 7–180 | **Flat** — 0 messages change |
| `HR5_DISMISSAL_RATE_THRESHOLD` | 0.6 | 0.3–0.8 | **Flat** — 0 messages change |
| `_LEGIT_MIN_ACCOUNT_AGE_DAYS` | 180 | 30–365 | **Flat** — 0 messages change |
| `_LEGIT_MIN_DOMAIN_AGE_DAYS` | 90 | 15–180 | **Flat** — 0 messages change |
| `NEAR_DUPLICATE_SIMILARITY_THRESHOLD` | 0.35 | 0.2–0.55 | **Flat** — 0 messages change |
| `_LEGIT_MAX_REPORTS_30D` | 15 | 3–30 | **Moderate** — flat at ≥10, up to 4 messages change below 5 |

## Reading

5 of 6 guessed constants turn out not to matter on this dataset at all —
the actual data doesn't cluster near any of these boundaries, so the exact
number was never doing real work. That's a genuinely reassuring result: it
means most of what looked like arbitrary tuning wasn't actually fitted to
the corpus, because there was nothing there to fit to.

`_LEGIT_MAX_REPORTS_30D` is the one real exception: business report counts
in this dataset cluster in a way that a threshold below ~10 starts
excluding legitimate businesses from the domain-mismatch exemption. The
chosen default (15) sits comfortably past that point, but this is the one
constant where "it happened to work" is a real possibility worth
remembering if the report-count distribution looks different on other
data — not a hard problem, just the one honest caveat among the six.
