# Message Notification Router

A rules-first, LLM-assisted router: for every message in `dataset/messages.csv`,
decides `notify` / `digest` / `mute`, with a type, a reason, a confidence,
and cited evidence. See `dataset/output.csv` for the current output and
`PLAN.md` for the full design writeup.

## Setup

```bash
pip install -r requirements.txt
```

Copy `.env.example` (repo root) to `.env` and fill in:

```
GEMINI_API_KEY=...      # media transcription (OCR/ASR) -- only needed on a cache miss
ANTHROPIC_API_KEY=...   # decisions, perception, judge -- only needed on a cache miss
```

**The submitted cache is warm.** `code/cache/*.json` is committed to the
repo, so `python main.py` makes **zero API calls** out of the box and
reproduces `dataset/output.csv` exactly. Keys are only needed if you delete
the cache or change a prompt (which invalidates the affected cache entries).

## Run

```bash
python main.py                          # generates dataset/output.csv
python -m pytest tests/                  # 98 tests, no API calls, ~0.1s
python evaluation/main.py                # scores against the 30 labeled samples
python evaluation/run_all_checks.py      # everything above + Tier-2 judge + determinism check
python evaluation/sensitivity.py         # constants sensitivity sweep
```

## Architecture

```
message -> hard rules (deterministic, abstain-by-default)
             |
             | no rule fires
             v
           LLM decision (Claude, cached) -> certainty engine -> policy guards -> output row
```

- **Hard rules** (`router/rules.py`, HR1-HR8) fire only on high-precision
  structural facts (domain mismatch, OTP/payment ask, direct mention,
  reported history, prompt-injection pattern) -- currently ~40% of the 110
  messages. Abstain by default; everything else falls through to the LLM.
- **Perception layer** (`router/perception.py`) is an *additive* LLM signal
  that OR-merges into the same features a regex would set, so it can catch
  what a keyword list misses (different phrasing, a non-English language)
  without ever removing something regex already found. Perception-only
  findings are capped at `weak` authority, not full hard-rule authority --
  see "Known limitations" below for why.
- **Certainty engine** (`router/certainty.py`) computes confidence from
  measured signal-family agreement (trust/relationship/behavior/content +
  LLM), not from the LLM self-reporting a number, and not from
  hand-picked constants fitted to the sample labels.
- **Evidence** (`router/evidence.py`) is deterministic token-overlap
  ranking over `message_history.csv` -- no embeddings, no LLM.
- **Determinism**: every LLM/OCR/ASR call is cached to disk by content
  hash. A warm-cache rerun is byte-identical and API-free.

## Known limitations (stated plainly, not buried)

- **No ground truth on the graded 110.** `dataset/sample_messages.csv` (30
  labeled examples) and `dataset/messages.csv` (110 graded messages) share
  **zero message IDs**. All accuracy/calibration numbers below measure the
  30 samples, not the actual submission.
- **Sample-set contamination, disclosed.** Some rule thresholds (e.g. the
  domain-mismatch legitimacy exemption in `rules.py`) were calibrated while
  looking at specific sample rows during development, including some in
  what was then the "test" split. Each fix was grounded in an externally
  verifiable fact (real account-age/domain data, objective negation
  patterns) rather than reverse-engineered to match a label, but this is a
  real, not fully eliminable, risk at n=30. See `TESTING_PLAN.md` and
  `JUDGE_TRIAGE.md` for the specific disclosure.
- **Calibration is measured, not asserted.** `evaluation/calibration.py`
  computes real Brier score and Expected Calibration Error against the 30
  samples (`python evaluation/main.py` to see it). Result: Brier scores are
  strong (0.01-0.06, well below the 0.25 coin-flip baseline), and the
  system reads as *under-confident* relative to a currently-100%-ish
  sample accuracy -- not tuned to close that gap, since doing so would mean
  fitting the confidence formula to a near-perfect score on 30 rows.
- **Statistical honesty at n=30.** `evaluation/cross_validation.py` reports
  5-fold pooled accuracy with a Wilson 95% confidence interval instead of a
  single point estimate from one fixed split -- e.g. "96.7% (83.3%-99.4%)"
  rather than a bare "100%". This fixes variance visibility, not the
  contamination point above.
- **Perception is not fully cross-lingually consistent.** During
  development, the same message in English vs. Hindi (romanized) got
  different `credential_or_payment_request` answers from the identical
  prompt after an initial bug was fixed for one language but not the
  other. Mitigated architecturally (perception-only findings are capped at
  `weak` authority, never a unilateral hard-rule mute) rather than assumed
  fixed by the prompt edit.
- **Injection detection is deliberately regex-only.** Asking an LLM "is
  this trying to manipulate you" invites the exact confusion a
  prompt-injection attempt is designed to cause; this one signal is kept
  out of the perception/LLM path on purpose (see `router/perception.py`).
- **The Tier-2 judge (`evaluation/judge.py`) is advisory, not required.**
  It never edits a decision -- `output.csv` is identical with or without
  it. It's a dev-time QA gate that caught two real bugs (a negation
  handling bug, a mention-quality defect) that the eval harness couldn't
  see, because both had correct action/type and only the `reason` was
  wrong. Its flag count does not converge run to run (`JUDGE_TRIAGE.md`)
  and should never be read as a score.
- **`rules.py` is a hand-written priority chain, not a declarative engine.**
  Considered refactoring it to a config-driven table; decided against it
  this close to freeze as a risk/time tradeoff, not an oversight. Covered
  by 98 pairwise/boundary/guard/certainty/evidence tests (`tests/`) instead.
- **Threshold constants are swept, not just asserted.** `evaluation/sensitivity.py`
  varies each previously-informal threshold (domain-age cutoffs, dismissal
  rate, similarity floor) across a wide range and measures how much the
  110-message output actually moves. Result in `CONSTANTS_SENSITIVITY.md`:
  5 of 6 are flat (the exact value never mattered), 1 has a real cliff
  below its default with comfortable margin above it.

## Testing

`tests/` (pytest, 98 tests, no network calls) covers the deterministic
rule/guard/certainty/evidence layer with pairwise rule-precedence coverage,
boundary-value tests for every negation bug found during development, and
guard-interaction tests. See `TESTING_PLAN.md` for the design rationale and
what's deliberately out of scope (LLM judgment quality is scored by
`evaluation/main.py` against real labels, not by unit tests).
