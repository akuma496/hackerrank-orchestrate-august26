# Tier-2 judge flag triage

Systematic triage of the advisory judge sweep, so the state isn't ambiguous
at submission. **The judge never edits a decision** — `output.csv` is
identical with or without it.

## Headline finding: flag count is not a quality metric

Across the session the count went **78 → 25 → 22 → 28 → 27 → 29 → 28**.
It does not converge, and it moved *up* on runs where decision quality
measurably improved. The judge always finds something to say. Treat it as a
**discovery tool** (it surfaces candidates for human review) and never as a
score. Real quality is measured by the eval harness against gold labels and
by the Tier-1 validator.

## Triage of the 29-flag sweep

| Category | Count | Verdict | Action |
|---|---|---|---|
| Rule reasons too generic (HR4/HR1 stated the trigger, not why *this* message fits the type) | 7 | **Judge right** | **Fixed** — HR4 now names the report count and the content pattern; HR1 names both domains explicitly |
| Injection reasons named only the injection, not the phishing payload underneath | 5 | **Judge right on the gap, wrong on its conclusion** (these *are* injections — verified in message text) | **Fixed** — reason now names both vectors |
| message_type judgment disagreements (personal vs unknown, promotion vs personal, urgent vs event) | 6 | **Judge is not ground truth** — eval harness scores 90% type accuracy on the held-out split against real labels | Documented, not changed |
| LLM reason cited a specific that FEATURES contradicts (e.g. "consistent engagement" when replied_rate=0.0) | 3 | **Judge right, low severity** — action/type correct, wording imprecise | Mitigated via the grounding section in `decide.md`; residual cases accepted |
| Judge alleged a scam we notify on (msg_096, msg_089 — "lost item + deadline = impersonation scam") | 2 | **Judge wrong** — both verified benign lost-item messages from a known contact | No change |
| Miscellaneous over-reading (wants every converging signal restated in a one-sentence reason) | 6 | **Out of scope** — reason field is one sentence by design and by sample convention | No change |

## What the judge actually earned its keep on

Two real defects that **no other check could see**, because both had correct
`action` and `message_type` and were only wrong in the `reason` — which the
eval harness does not score:

1. The `"nothing urgent"` negation bug (msg_083) — reason claimed
   time-sensitivity for a message that explicitly disclaimed it.
2. The msg_040 mention defect — a chain letter hard-notified purely for
   containing an `@mention`, against the user's own mute history.

Given `reason` is an explicitly graded column, that pays for the ~2 min a
cached sweep costs.

## Recommendation

Keep it, scoped as a **dev-time advisory gate**, run before freeze. It is
not a runtime component and nothing in the submission depends on it.
