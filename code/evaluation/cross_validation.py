"""Stratified k-fold scoring + binomial confidence intervals, replacing a
single fixed 10/20 split.

Honest framing, stated once here rather than repeated everywhere: this is
NOT classic ML cross-validation, because there is nothing to fit -- the
pipeline (rules + cached LLM calls) has no trainable parameters, so there
is no fold-dependent "training" step. What this actually buys:

1. Every one of the 30 samples gets scored exactly once (pooled), instead
   of only the 20 in a fixed test split -- more data in the final number.
2. Per-fold metrics show how much the estimate moves around under a
   different partition -- variance the single-split number hid completely
   (recall: the single split had TRAIN at 70% and TEST at 90% type
   accuracy, backwards from what overfitting would predict -- that gap was
   noise, not signal, and this makes the noise visible instead of picking
   one split and reporting it as THE number).
3. A Wilson score interval on the pooled result, because "100% (20/20)"
   and "100% (200/200)" are very different claims and a bare percentage
   erases that difference.

This does NOT fix training-set contamination (some rule thresholds were
calibrated while looking at specific sample rows during earlier
development) -- it fixes variance estimation. Both matter; only one is
addressed here.
"""

import math


def stratified_kfold(sample_rows: list, k: int = 5) -> list:
    """Returns k folds (lists of rows), each roughly len(sample_rows)/k,
    stratified by action so every fold has a proportional mix. Deterministic
    -- sorted by message_id, no randomness, so reruns produce identical folds."""
    groups = {}
    for row in sample_rows:
        groups.setdefault(row["action"], []).append(row)

    folds = [[] for _ in range(k)]
    for action, rows in groups.items():
        rows_sorted = sorted(rows, key=lambda r: r["message_id"])
        for i, row in enumerate(rows_sorted):
            folds[i % k].append(row)  # round-robin keeps each fold's action mix proportional
    return folds


def wilson_interval(successes: int, n: int, confidence: float = 0.95) -> tuple:
    """Wilson score interval -- the standard choice for small-n binomial
    proportions (a naive normal-approximation interval misbehaves badly
    near 0% or 100%, which is exactly where our n=30 numbers tend to sit)."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.96 if abs(confidence - 0.95) < 1e-9 else 2.576  # 95% or 99%
    p = successes / n
    denom = 1 + z ** 2 / n
    center = (p + z ** 2 / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2))) / denom
    return (round(max(0.0, center - margin), 3), round(min(1.0, center + margin), 3))


def _mean_std(values: list) -> tuple:
    if not values:
        return (0.0, 0.0)
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return (round(mean, 3), round(math.sqrt(variance), 3))


def cross_validate(evaluate_fn, sample_rows: list, ctx, transcripts, k: int = 5) -> dict:
    """evaluate_fn: the existing per-fold scorer (evaluation.main.evaluate),
    called once per fold so every fold reuses the exact same scoring logic
    as before -- this only changes how rows are partitioned and aggregated."""
    folds = stratified_kfold(sample_rows, k)
    fold_reports = [evaluate_fn(fold, ctx, transcripts, f"fold_{i+1}") for i, fold in enumerate(folds)]

    action_accs = [r["action_accuracy"] for r in fold_reports]
    type_accs = [r["message_type_accuracy"] for r in fold_reports]
    jaccards = [r["evidence_avg_jaccard"] for r in fold_reports]

    total_n = sum(r["n"] for r in fold_reports)
    total_action_correct = round(sum(r["action_accuracy"] * r["n"] for r in fold_reports))
    total_type_correct = round(sum(r["message_type_accuracy"] * r["n"] for r in fold_reports))
    total_critical_misses = sum(r["critical_miss_count"] for r in fold_reports)
    total_spam_pushed = sum(r["spam_pushed_forward_count"] for r in fold_reports)

    action_mean, action_std = _mean_std(action_accs)
    type_mean, type_std = _mean_std(type_accs)
    jaccard_mean, jaccard_std = _mean_std(jaccards)

    return {
        "k": k,
        "total_n": total_n,
        "fold_reports": fold_reports,
        "action_accuracy_mean": action_mean,
        "action_accuracy_std": action_std,
        "type_accuracy_mean": type_mean,
        "type_accuracy_std": type_std,
        "evidence_jaccard_mean": jaccard_mean,
        "evidence_jaccard_std": jaccard_std,
        "pooled_action_accuracy": round(total_action_correct / total_n, 3),
        "pooled_action_ci95": wilson_interval(total_action_correct, total_n),
        "pooled_type_accuracy": round(total_type_correct / total_n, 3),
        "pooled_type_ci95": wilson_interval(total_type_correct, total_n),
        "total_critical_misses": total_critical_misses,
        "total_spam_pushed_forward": total_spam_pushed,
    }


def print_cv_report(cv: dict) -> None:
    print(f"--- {cv['k']}-fold stratified scoring (pooled n={cv['total_n']}) ---")
    for r in cv["fold_reports"]:
        print(f"  {r['label']}: n={r['n']:2d}  action_acc={r['action_accuracy']}  type_acc={r['message_type_accuracy']}")
    print(
        f"action_accuracy: mean={cv['action_accuracy_mean']} std={cv['action_accuracy_std']}  "
        f"pooled={cv['pooled_action_accuracy']} (95% CI {cv['pooled_action_ci95']})"
    )
    print(
        f"type_accuracy:   mean={cv['type_accuracy_mean']} std={cv['type_accuracy_std']}  "
        f"pooled={cv['pooled_type_accuracy']} (95% CI {cv['pooled_type_ci95']})"
    )
    print(f"evidence_jaccard: mean={cv['evidence_jaccard_mean']} std={cv['evidence_jaccard_std']}")
    print(f"critical_misses (pooled, all 30): {cv['total_critical_misses']} [HARD THRESHOLD: 0]")
    print(f"spam_pushed_forward (pooled, all 30): {cv['total_spam_pushed_forward']} [sanity check]")
    print(
        "note: no fold-dependent training occurs (nothing to fit) -- this measures "
        "variance and uses all 30 samples, it does not undo the disclosed train/test "
        "contamination from earlier threshold calibration."
    )
